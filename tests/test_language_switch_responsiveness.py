"""Regression coverage for mid-meeting language switching and overlay redraw."""
from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.pipeline_meeting import MeetingSTTPipeline
from app.ui.meeting_window import MeetingUIBus, MeetingWindow


class _FakeWorker:
    def __init__(self):
        self.live_engine = SimpleNamespace(language='ja')
        self.stop_calls = 0
        self.clear_calls = 0

    def clear_pending(self, *_args, **_kwargs):
        self.clear_calls += 1

    def stop(self, *_args, **_kwargs):
        self.stop_calls += 1


class LanguageSwitchRegressionTests(unittest.TestCase):
    def _pipeline(self):
        obj = object.__new__(MeetingSTTPipeline)
        obj._lane_config_lock = threading.RLock()
        obj._audio_mode_lock = threading.Lock()
        obj._pending_lane_resets = set()
        obj._speaker_generation = {'mic': 0, 'system': 0}
        obj._last_speaker_probe = {'mic': -100., 'system': -100.}
        obj._auto_language_state = {'mic': {}, 'system': {}}
        obj._language_requests = {'mic': 0, 'system': 0}
        obj.speaker_worker = None
        obj.translation_worker = None
        obj.ui_bus = None
        obj._stop = threading.Event()
        obj.config = SimpleNamespace(meeting=SimpleNamespace(mic_language='vi', system_language='ja'))
        obj.lanes = {'system': SimpleNamespace(language='ja', backend='sensevoice', worker=_FakeWorker(), threads=2)}
        return obj

    def test_japanese_to_auto_does_not_restart_model_or_touch_segmenter(self):
        pipe = self._pipeline()
        segmenter = SimpleNamespace(discard=lambda: self.fail('VAD modified from wrong thread'))
        pipe.lanes['system'].segmenter = segmenter
        worker = pipe.lanes['system'].worker
        with patch('app.pipeline_meeting.build_asr_engine', side_effect=AssertionError('must reuse SenseVoice')):
            pipe.set_lane_language('system', 'auto')
            pipe.set_lane_language('system', 'ja')
            pipe.set_lane_language('system', 'auto')
        self.assertIs(pipe.lanes['system'].worker, worker)
        self.assertEqual(worker.stop_calls, 0)
        self.assertEqual(worker.live_engine.language, 'auto')
        self.assertIn('system', pipe._pending_lane_resets)
        self.assertEqual(pipe._auto_language_state['system']['stable'], 'ja')

    def test_slow_model_loading_never_holds_audio_submission_lock(self):
        pipe = self._pipeline()
        pipe.lanes['system'].segmenter = SimpleNamespace(discard=lambda: None)
        loading = threading.Event()
        release = threading.Event()

        def slow_factory(*_args, **_kwargs):
            loading.set()
            self.assertTrue(release.wait(2))
            return SimpleNamespace(language='vi'), 'gipformer'

        class FastNewWorker(_FakeWorker):
            def __init__(self, *_args, **_kwargs):
                super().__init__()
            def start(self):
                pass

        with patch('app.pipeline_meeting.build_asr_engine', slow_factory), \
             patch.object(pipe, '_new_asr_worker', side_effect=lambda *_: FastNewWorker()):
            thread = threading.Thread(target=pipe.set_lane_language, args=('system', 'vi'))
            thread.start()
            self.assertTrue(loading.wait(1))
            start = time.monotonic()
            with pipe._lane_config_lock:
                _ = pipe.lanes['system'].worker
            self.assertLess(time.monotonic() - start, .1)
            release.set()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(pipe.lanes['system'].language, 'vi')


class OverlayRegressionTests(unittest.TestCase):
    def _window(self):
        try:
            return MeetingWindow(MeetingUIBus(), title='SoundText v5 test')
        except Exception as exc:
            if 'display' in str(exc).lower():
                self.skipTest('No graphical display')
            raise

    def test_overlay_open_is_deferred_and_rebuild_expands_only_once(self):
        window = self._window()
        try:
            for i in range(90):
                window._subtitle_rows.append({
                    'source': 'system', 'utterance_id': i, 'sequence': i+1,
                    'clock': '12:00:00', 'source_language': 'ja',
                    'source_text': 'テストの発言', 'translated_text': 'Một câu dịch dài từ cuộc họp',
                })
            expands = []
            original = window._schedule_subtitle_expand
            window._schedule_subtitle_expand = lambda: expands.append(1)
            window.subtitle_enabled.set(True)
            window._toggle_subtitle_overlay()
            self.assertIsNone(window.subtitle_vi_window)
            window.root.update()
            self.assertIsNotNone(window.subtitle_vi_window)
            self.assertLessEqual(len(expands), 2)
            self.assertIn('Một câu dịch dài', window._subtitle_vi_text.get('1.0', 'end'))
            window._schedule_subtitle_expand = original
        finally:
            window.root.destroy()


if __name__ == '__main__':
    unittest.main()
