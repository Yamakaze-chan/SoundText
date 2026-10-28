"""Unit tests that do not need microphone, Windows or ONNX checkpoints."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
import threading
import time
import unittest

import numpy as np

from app.asr.worker import ASRJob, ASRResult
from app.speakers.diarizer import speaker_spans_from_scores, POWERSET
from app.speakers.worker import SpeakerTask, SpeakerWorker
from app.storage.session_store import SessionStore, TranscriptEntry
from app.postprocess.text_cleaner import TextCleaner, build_clean_turns
from app.ui.meeting_window import UIEvent


def logits_for(labels):
    arr = np.full((len(labels), 7), -10.0, dtype=np.float32)
    for i, lab in enumerate(labels):
        arr[i,lab] = 10.0
    return arr


class SpeakerSegmentationTests(unittest.TestCase):
    def test_three_speakers_consecutive(self):
        # 100 frames = 5s, 2s of A, 1.5s B, 1.5s C.
        scores = logits_for([1] * 40 + [2] * 30 + [3] * 30)
        spans = speaker_spans_from_scores(scores, duration_s=5.0, min_turn_s=0.75)
        self.assertEqual([sp[2] for sp in spans], [0, 1, 2])
        self.assertAlmostEqual(spans[-1][1], 5.0)
        self.assertAlmostEqual(spans[0][1], spans[1][0])

    def test_transient_change_ignored(self):
        spans = speaker_spans_from_scores(logits_for([1] * 20 + [2] * 2 + [1] * 28), duration_s=2.5)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0][2], 0)

    def test_overlap_marked_not_assigned_to_one_speaker(self):
        spans = speaker_spans_from_scores(logits_for([1] * 25 + [4] * 30 + [2] * 25), duration_s=4.0)
        self.assertTrue(any(sp[3] for sp in spans))
        self.assertTrue(any(sp[2] is None for sp in spans))

    def test_invalid_scores_fall_back_to_undetermined(self):
        spans = speaker_spans_from_scores(np.zeros((5, 3)), duration_s=2.0)
        self.assertEqual(spans, [(0, 2.0, None, False)])

    def test_store_persists_speaker_and_does_not_merge_other_speaker(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            try:
                a = TranscriptEntry(utterance_id=1, start_s=0, end_s=1.5, text='A',
                                    source='mic', language='vi', source_text='Hello', speaker_id='S1')
                b = TranscriptEntry(utterance_id=2, start_s=1.5, end_s=2.5, text='B',
                                    source='mic', language='vi', source_text='World', speaker_id='S2')
                store.append(a)
                store.append(b)
                turns = store._clean_turns()
                self.assertEqual(len(turns), 2)
                store.refresh(force=True)
                data = store.txt_path.read_text(encoding='utf-8')
                self.assertIn('YOU:S1', data)
                self.assertIn('YOU:S2', data)
                self.assertIn('"speaker_id": "S1"', store.jsonl_path.read_text(encoding='utf-8'))
            finally:
                store.close()

    def test_ui_event_speaker_metadata(self):
        event = UIEvent(source='system', utterance_id=2, speaker_id='S3', speaker_overlap=True)
        self.assertEqual(event.speaker_id, 'S3')
        self.assertTrue(event.speaker_overlap)


class FakeDiarizer:
    def process(self, source, audio):
        n = len(audio) // 2
        return [(audio[:n], 0.0, 1.0, 'S1', False), (audio[n:], 1.0, 2.0, 'S2', False)]


class SpeakerWorkerTests(unittest.TestCase):
    def test_splits_on_background_thread(self):
        results = []
        worker = SpeakerWorker(FakeDiarizer(), lambda task, x, a, b, sp, ov: results.append((task.source, a, b, sp)))
        worker.start()
        worker.submit(SpeakerTask('mic', np.zeros(32000, dtype=np.float32), 0.0, 2.0))
        worker.stop(drain=True)
        self.assertEqual(results, [('mic', 0., 1., 'S1'), ('mic', 1., 2., 'S2')])

    def test_backlog_falls_back_without_losing_audio(self):
        results = []
        worker = SpeakerWorker(FakeDiarizer(), lambda task, x, a, b, sp, ov: results.append((len(x), sp)), max_backlog=0)
        worker.submit(SpeakerTask('mic', np.zeros(32000, dtype=np.float32), 0.0, 2.0))
        worker.submit(SpeakerTask('mic', np.zeros(32000, dtype=np.float32), 2.0, 4.0))
        worker.start()
        worker.stop(drain=True)
        self.assertEqual(sum(n for n, sp in results), 64000)
        self.assertGreaterEqual(worker.skipped_busy, 1)


if __name__ == '__main__':
    unittest.main()

class ConservativeClusteringTests(unittest.TestCase):
    @staticmethod
    def new_model():
        from app.speakers.diarizer import SpeakerDiarizer
        model = object.__new__(SpeakerDiarizer)
        model.sample_rate = 16000
        model.min_turn_s = 0.50
        model.similarity_threshold = 0.60
        model.new_speaker_threshold = 0.44
        model.min_match_s = 0.90
        model.min_enroll_s = 2.40
        model._speaker_centers = {'mic': [], 'system': []}
        model._speaker_hits = {'mic': [], 'system': []}
        model._pending_new = {'mic': None, 'system': None}
        return model

    def test_short_fragments_never_spawn_new_speakers(self):
        model = self.new_model()
        model._embed = lambda audio: np.array([1.,0.,0.], dtype=np.float32)
        for length in (0.2, 0.7, 1.0, 1.3):
            self.assertIsNone(model._identify('system', np.zeros(int(16000*length), dtype=np.float32)))
        self.assertEqual(len(model._speaker_centers['system']),0)
        self.assertEqual(model._identify('system',np.zeros(2*16000, dtype=np.float32)),'S1')
        for i in range(20):
            self.assertEqual(model._identify('system',np.zeros(16000, dtype=np.float32)),'S1')
        self.assertEqual(len(model._speaker_centers['system']),1)

    def test_uncertain_voice_does_not_create_s2(self):
        model = self.new_model()
        voices = iter((np.array([1.,0.,0.],np.float32),
                       np.array([.51,np.sqrt(1-.51**2),0.],np.float32),
                       np.array([0.,1.,0.],np.float32)))
        model._embed = lambda audio: next(voices)
        audio=np.zeros(3*16000,np.float32)
        self.assertEqual(model._identify('mic',audio),'S1')
        self.assertIsNone(model._identify('mic',audio))
        self.assertIsNone(model._identify('mic',audio))
        self.assertEqual(len(model._speaker_centers['mic']),1)

    def test_new_speaker_requires_repeat_long_turn(self):
        model = self.new_model()
        embeddings = iter((np.array([1.,0.,0.],np.float32),
                           np.array([0.,1.,0.],np.float32),
                           np.array([0.,1.,0.],np.float32)))
        model._embed = lambda audio: next(embeddings)
        audio = np.zeros(3*16000, np.float32)
        self.assertEqual(model._identify('mic', audio), 'S1')
        self.assertIsNone(model._identify('mic', audio))
        self.assertEqual(len(model._speaker_centers['mic']), 1)
        self.assertEqual(model._identify('mic', audio), 'S2')

    def test_overlap_stays_in_one_asr_chunk(self):
        model=self.new_model()
        frames=500
        scores=logits_for([1]*200+[4]*40+[1]*260)
        model._segment_scores=lambda audio: scores
        model._embed=lambda audio: np.array([1.,0.,0.],np.float32)
        audio=np.zeros(10*16000,np.float32)
        result=model.process('system',audio)
        self.assertEqual(len(result),1)
        self.assertEqual(len(result[0][0]),len(audio))
        self.assertEqual(result[0][3],'S1')
        self.assertTrue(result[0][4])

    def test_switch_of_two_sustained_voices_splits_without_loss(self):
        model=self.new_model()
        scores=logits_for([1]*200+[2]*300)
        model._segment_scores=lambda audio:scores
        model._embed=lambda audio: np.array([1.,0.,0.],np.float32) if len(audio)<100000 else np.array([0.,1.,0.],np.float32)
        audio=np.zeros(10*16000,np.float32)
        result=model.process('system',audio)
        self.assertEqual(len(result),2)
        self.assertEqual(sum(len(r[0]) for r in result),len(audio))
        self.assertAlmostEqual(result[0][2],4.0,places=2)
        self.assertEqual(result[0][3],'S1')

    def test_noisy_overlap_frames_do_not_mark_overlapping(self):
        model=self.new_model()
        scores=logits_for([1]*200+[4]*10+[1]*290)
        model._segment_scores=lambda audio: scores
        model._embed=lambda audio: np.array([1.,0.,0.],np.float32)
        result=model.process('system',np.zeros(10*16000,np.float32))
        self.assertEqual(len(result),1)
        self.assertFalse(result[0][4])
