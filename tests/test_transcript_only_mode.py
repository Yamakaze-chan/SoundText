"""Transcription-only runs never initialize the translation engine."""
from pathlib import Path
from types import ModuleType
import sys
from threading import Event

from app.config import AppConfig
from app.ui.meeting_window import MeetingUIBus
from app.ui.recording_controller import RecordingController


def test_core_already_gates_translation_at_creation():
    source = (Path(__file__).parents[1] / 'app' / 'pipeline_meeting.py').read_text(encoding='utf-8')
    assert 'if config.translation.enabled:' in source
    assert 'if result.kind == "final" and source_language != "vi" and self.translation_worker is not None' in source


def test_transcript_only_and_translated_sessions_use_independent_config(monkeypatch):
    captured = []
    fake = ModuleType('app.pipeline_meeting')
    started = Event()

    class Store:
        session_dir = '/tmp/meeting_transcript_only'
        def close(self, status): pass

    class Pipeline:
        def __init__(self, cfg, ui_bus=None):
            captured.append(cfg.translation.enabled)
            self.store = Store()
            self._stop = Event()
        def run_capture(self):
            started.set()
            self._stop.wait(2)
        def request_stop(self): self._stop.set()

    fake.MeetingSTTPipeline = Pipeline
    monkeypatch.setitem(sys.modules, 'app.pipeline_meeting', fake)
    controller = RecordingController(MeetingUIBus())
    config = AppConfig()
    config.translation.enabled = False
    assert controller.start(config)
    assert started.wait(2)
    config.translation.enabled = True   # Must not affect a running session
    controller.stop()
    controller.join(2)
    assert captured == [False]
    started.clear()
    assert controller.start(config)
    assert started.wait(2)
    controller.stop()
    controller.join(2)
    assert captured == [False, True]


def test_mode_selector_controls_config_before_start():
    ui_code = (Path(__file__).parents[1] / 'app' / 'ui' / 'ctk_window.py').read_text(encoding='utf-8')
    assert '("Chỉ ghi lời thoại", False)' in ui_code
    assert 'self.config.translation.enabled = self._session_translate' in ui_code
    assert 'self.record_mode.configure(state="normal" if state in ("idle","stopped","failed") else "disabled")' in ui_code
    assert 'if not self.translation_enabled:' in ui_code
    assert 'self.translation.configure(text=d.get("original", ""))' in ui_code
    assert '"XỬ LÝ CỤC BỘ"' not in ui_code
    assert 'SenseVoice · Gipformer' not in ui_code


def test_gui_recording_selection_updates_overlay_without_touching_core(monkeypatch):
    from tests.test_dual_overlay_metadata import load_ui
    from unittest.mock import MagicMock
    ui = load_ui(monkeypatch)
    win = object.__new__(ui.SoundTextWindow)
    win.record_mode_by_name = dict(ui.SoundTextWindow.RECORD_MODES)
    win.record_mode_hint = MagicMock()
    win.overlay = MagicMock()
    win._record_mode_changed('Chỉ ghi lời thoại')
    win.overlay.set_translation_enabled.assert_called_with(False, choose_default=True)
    win.record_mode_hint.configure.assert_called_with(text='Chỉ lưu lời thoại gốc')
    win._record_mode_changed('Ghi & dịch')
    win.overlay.set_translation_enabled.assert_called_with(True, choose_default=True)
