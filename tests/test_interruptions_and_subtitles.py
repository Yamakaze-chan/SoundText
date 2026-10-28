"""Regression tests for long previews and bounded early speaker splits."""
from __future__ import annotations

import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

from app.vad.segmenter import StreamingVadSegmenter
from app.speakers.diarizer import SpeakerDiarizer
from app.speakers.worker import SpeakerProbe, SpeakerTask, SpeakerWorker
from app.ui.meeting_window import MeetingWindow


class AlwaysSpeech:
    def probability(self, frame):
        return 0.99


class SplitTests(unittest.TestCase):
    def segmenter(self):
        return StreamingVadSegmenter(AlwaysSpeech(), partial_interval_ms=250,
                                     min_silence_ms=600, max_utterance_s=14.,
                                     pre_roll_ms=0, hard_split_overlap_ms=0)

    def test_request_is_applied_on_capture_thread_to_matching_turn_only(self):
        vad = self.segmenter()
        vad.push(np.ones(48000, np.float32) * .1)
        current = vad.active_utterance_id
        vad.request_speaker_split(current, 1.70)
        generated = vad.push(np.ones(16000, np.float32) * .1)
        final = [x for x in generated if x.kind == 'final']
        self.assertEqual(len(final), 1)
        self.assertAlmostEqual(final[0].end_s, 1.70, delta=0.04)
        self.assertNotEqual(vad.active_utterance_id, current)
        remaining = vad.flush()
        self.assertIsNotNone(remaining)
        self.assertGreater(remaining.audio.size, 16000)
        self.assertLessEqual(remaining.start_s, final[0].end_s)
        self.assertAlmostEqual(remaining.end_s, 4.0, delta=0.1)

    def test_obsolete_request_does_not_cut_new_turn(self):
        vad = self.segmenter()
        vad.push(np.ones(32000, np.float32))
        old_id = vad.active_utterance_id
        vad.flush()
        vad.push(np.ones(32000, np.float32))
        vad.request_speaker_split(old_id, 1.2)
        generated = vad.push(np.ones(16000, np.float32))
        self.assertFalse(any(x.kind == 'final' for x in generated))

    def test_interruption_probe_within_ongoing_speech(self):
        fake = object.__new__(SpeakerDiarizer)
        fake.sample_rate = 16000
        fake.min_turn_s = 0.5
        # 4s: A 2s, B 2s; sufficient time on both speakers.
        scores = np.full((500, 7), -5, np.float32)
        scores[:100, 1] = 5
        scores[100:200, 2] = 5
        fake._segment_scores = lambda audio: scores
        # Duration is 4s, so fake scores need to simulate already cropped model.
        change = fake.detect_interruption(np.zeros(4 * 16000, dtype=np.float32))
        self.assertAlmostEqual(change, 2.0, delta=0.05)

    def test_no_probe_on_same_voice(self):
        fake = object.__new__(SpeakerDiarizer)
        fake.sample_rate = 16000
        fake.min_turn_s = 0.5
        scores = np.full((500, 7), -5, np.float32)
        scores[:, 1] = 5
        fake._segment_scores = lambda audio: scores
        self.assertIsNone(fake.detect_interruption(np.zeros(4 * 16000, dtype=np.float32)))


class SpeakerPriorityTests(unittest.TestCase):
    def test_probes_coalesce_and_do_not_generate_extra_asr_segments(self):
        class Diarizer:
            def process(self, source, audio):
                return [(audio, 0.0, len(audio)/16000, 'S1', False)]
            def detect_interruption(self, audio):
                return 1.2
        finals, probes = [], []
        worker = SpeakerWorker(Diarizer(), lambda *x: finals.append(x),
                               probe_callback=lambda p, t: probes.append((p.source,t)))
        for j in range(5):
            worker.submit_probe(SpeakerProbe('mic', np.zeros(64000,np.float32),
                                            0.,1,0,time.monotonic()))
        worker.submit(SpeakerTask('mic', np.zeros(32000,np.float32), 0.,2.))
        worker.start()
        worker.stop(drain=True)
        self.assertEqual(len(finals), 1)
        self.assertLessEqual(len(probes), 1)


class SubtitleWidgetTests(unittest.TestCase):
    def test_preview_wraps_into_many_lines_and_scrolls(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest('No DISPLAY')
        try:
            text = tk.Text(root, wrap='word', width=18, height=3)
            text.pack()
            root.update()
            long = 'Xin chào mọi người, đây là phần văn bản đang được nhận diện. ' * 12
            MeetingWindow._set_subtitle_preview(text, long)
            root.update()
            self.assertEqual(text.get('1.0','end-1c'), long)
            self.assertTrue(text.count('1.0','end','displaylines')[0] > 3)
            self.assertAlmostEqual(text.yview()[1],1.0,delta=0.1)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()

class OverlapAndFloatingOverlayTests(unittest.TestCase):
    def test_overlap_alone_does_not_split_asr(self):
        fake = object.__new__(SpeakerDiarizer)
        fake.sample_rate = 16000
        fake.min_turn_s = .5
        scores = np.full((500, 7), -5., np.float32)
        scores[:175, 1] = 5.  # speaker A from 0s to 3.5s of a 4s window
        scores[175:200, 4] = 10.  # A+B overlap, B never gets a solo turn
        fake._segment_scores = lambda audio: scores
        self.assertIsNone(fake.detect_interruption(np.zeros(64000,np.float32)))

    def test_temporary_overlap_then_original_resumes_does_not_cut(self):
        fake = object.__new__(SpeakerDiarizer)
        fake.sample_rate = 16000
        fake.min_turn_s = .5
        scores = np.full((500,7), -5., np.float32)
        scores[:300,1] = 6.  # A 0-6 seconds
        scores[175:205,4] = 12.  # overlap from 3.5 to 4.1 seconds
        fake._segment_scores = lambda audio: scores
        self.assertIsNone(fake.detect_interruption(np.zeros(6*16000,np.float32)))

    def test_short_noise_does_not_trigger_false_overlap_cut(self):
        fake = object.__new__(SpeakerDiarizer)
        fake.sample_rate = 16000
        fake.min_turn_s = .5
        scores = np.full((500, 7), -5., np.float32)
        scores[:, 1] = 5.
        scores[175:181, 4] = 10.  # just 120ms
        fake._segment_scores = lambda audio: scores
        self.assertIsNone(fake.detect_interruption(np.zeros(64000,np.float32)))

    def test_floating_live_vietnamese_preview_is_multiline(self):
        from app.ui.meeting_window import MeetingUIBus
        try:
            window = MeetingWindow(MeetingUIBus(), title='Live subtitle test')
        except Exception as exc:
            if 'display' in str(exc).lower():
                self.skipTest('No graphical display')
            raise
        try:
            window.subtitle_enabled.set(True)
            window._ensure_subtitle_overlay()
            long = 'Đây là một đoạn phát biểu dài khi người nói chưa dừng lời. ' * 14
            window._update_subtitle_overlay(source='mic', utterance_id=5,
                source_text=long, final=False, source_language='vi')
            window.root.update()
            preview = window._subtitle_vi_live_preview
            self.assertEqual(preview.get('1.0','end-1c'), long.strip())
            self.assertGreater(int(preview.cget('height')), 4)
            self.assertTrue(preview._preview_container.winfo_manager())
        finally:
            window.root.destroy()

    def test_floating_translation_window_expands_and_wraps(self):
        from app.ui.meeting_window import MeetingUIBus
        try:
            window = MeetingWindow(MeetingUIBus(), title='Test floating subtitles')
        except Exception as exc:
            if 'display' in str(exc).lower():
                self.skipTest('No graphical display')
            raise
        try:
            window.subtitle_enabled.set(True)
            window._ensure_subtitle_overlay()
            widget = window._subtitle_vi_text
            floating = window.subtitle_vi_window
            self.assertEqual(widget.cget('wrap'), 'word')
            floating.geometry('420x240+30+300')
            long_text = 'Cuộc họp thảo luận về kế hoạch và các nhiệm vụ quan trọng trong tuần này. ' * 14
            window._update_subtitle_overlay(source='mic',utterance_id=17,source_text=long_text,final=True,source_language='vi')
            window.root.update()
            self.assertEqual(window._subtitle_rows[-1]['translated_text'],long_text.strip())
            self.assertGreater(floating.winfo_height(),240)
            self.assertGreater(widget.count('1.0','end','displaylines')[0],5)
        finally:
            window.root.destroy()
