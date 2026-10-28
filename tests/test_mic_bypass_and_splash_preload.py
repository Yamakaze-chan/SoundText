"""Focused regression checks for v10.3 (no model downloads needed)."""
from __future__ import annotations

from types import ModuleType, SimpleNamespace
import sys
import threading
from unittest.mock import MagicMock

import numpy as np

from app.config import AppConfig
from app.ui.model_preloader import PreparedModels, prepare_models
from app.pipeline_meeting import MeetingSTTPipeline


def test_preloader_builds_real_objects_once_and_forces_nllb_weights(monkeypatch):
    created = []
    def fake_asr(_config, *, language, threads):
        obj = SimpleNamespace(language=language)
        created.append((language, threads))
        return obj, 'stub'

    import app.asr.factory as asr_factory
    import app.vad.silero_onnx as vad_module
    import app.translation.factory as mt_factory
    import app.speakers.diarizer as speakers_module

    monkeypatch.setattr(asr_factory, 'build_asr_engine', fake_asr)
    class Vad:
        def __init__(self, *args, **kwargs): self.resets = 0
        def reset(self): self.resets += 1
    monkeypatch.setattr(vad_module, 'SileroOnnxVad', Vad)
    class Speaker:
        def __init__(self, *args, **kwargs): pass
    monkeypatch.setattr(speakers_module, 'SpeakerDiarizer', Speaker)
    class Translator:
        def __init__(self): self.loads = 0
        def _load(self): self.loads += 1
    translator = Translator()
    monkeypatch.setattr(mt_factory, 'build_translation_engine', lambda config: translator)
    config = AppConfig()
    config.meeting.mic_language = 'vi'
    config.meeting.system_language = 'ja'
    config.translation.enabled = True
    progress = []
    ready = prepare_models(config, progress.append)
    assert len(created) == 2
    assert len(ready.vad) == 2
    assert isinstance(ready.speaker, Speaker)
    assert translator.loads == 1
    assert ready.take_asr('system', 'ja', 3)[0].language == 'ja'
    assert ready.take_vad('mic').resets == 1
    assert ready.take_speaker(True) is not None
    assert ready.take_translator(True) is translator
    assert ready.take_translator(True) is None
    assert any('dịch' in text for text in progress)


def test_preloader_skips_translation_when_disabled(monkeypatch):
    config = AppConfig()
    config.translation.enabled = False
    config.speakers.enabled = False
    monkeypatch.setattr('app.asr.factory.build_asr_engine', lambda *_a, **_kw: (object(), 'stub'))
    monkeypatch.setattr('app.vad.silero_onnx.SileroOnnxVad', lambda *_a, **_kw: SimpleNamespace(reset=lambda: None))
    ready = prepare_models(config)
    assert ready.translator is None
    assert ready.speaker is None


def test_mic_avoids_speaker_worker_even_when_system_uses_it():
    p = object.__new__(MeetingSTTPipeline)
    p.speaker_worker = SimpleNamespace(submit=MagicMock(), submit_probe=MagicMock())
    p.config = SimpleNamespace(speakers=SimpleNamespace(early_split_enabled=True, probe_interval_s=1.8))
    p._last_speaker_probe = {'mic': -100., 'system': -100.}
    p._speaker_generation = {'mic': 0, 'system': 0}
    p._utterance_offsets = {'mic': 0, 'system': 0}
    p._timeline_offset_s = 0.
    p._session_start = 0.
    p._lane_config_lock = threading.RLock()
    mic_worker = SimpleNamespace(submit=MagicMock())
    sys_worker = SimpleNamespace(submit=MagicMock())
    p.lanes = {'mic': SimpleNamespace(worker=mic_worker),
               'system': SimpleNamespace(worker=sys_worker)}
    final = SimpleNamespace(kind='final', utterance_id=0, audio=np.ones(1600,dtype=np.float32),
                            start_s=0., end_s=0.1, leading_overlap_s=0.)
    p._submit('mic', final)
    p.speaker_worker.submit.assert_not_called()
    mic_job=mic_worker.submit.call_args.args[0]
    assert mic_job.speaker_id is None
    assert mic_job.utterance_id == 0
    p._submit('system', final)
    p.speaker_worker.submit.assert_called_once()


def test_mic_audio_loop_does_not_use_echo_guard_in_both_mode():
    import inspect
    src = inspect.getsource(MeetingSTTPipeline._audio_loop_inner)
    assert '.mic_guard.process(' not in src
    assert '.mic_guard.observe_system(' not in src
    assert 'segmenter.push(audio)' in src


def test_subtitles_compact_consecutive_remote_sentences(monkeypatch):
    from tests.test_dual_overlay_metadata import load_ui
    ui = load_ui(monkeypatch)
    overlay = object.__new__(ui.FloatingSubtitles)
    overlay.entries = [
        dict(source='system', speaker='S1', overlap=False, original='Hello.', translation='Xin chào.', lang='en'),
        dict(source='system', speaker='S1', overlap=False, original='How are you?', translation='Bạn khỏe không?', lang='en'),
        dict(source='mic', speaker='', overlap=False, original='Chào.', translation='', lang='vi'),
        dict(source='system', speaker='S2', overlap=False, original='Fine.', translation='Tốt.', lang='en'),
    ]
    overlay.partials = {'mic': ('', ''), 'system': ('', '')}
    stt = overlay._text_for('stt')
    assert 'REMOTE · S1  Hello. How are you?' in stt
    assert stt.count('REMOTE · S1') == 1
    assert '\n\n' not in stt
    assert 'REMOTE · S2  Fine.' in stt
    vi = overlay._text_for('vi')
    assert 'Xin chào. Bạn khỏe không?' in vi


def test_splash_preloader_before_main_window_visible():
    from pathlib import Path
    src = (Path(__file__).parents[1] / 'app/ui/ctk_window.py').read_text(encoding='utf8')
    assert 'SoundTextWindow(config, startup_hidden=True)' in src
    assert 'prepare_models(config,' in src
    assert 'Thread(target=load_models' in src
    assert 'app.controller.set_prepared_models(payload)' in src
    assert src.index('app.controller.set_prepared_models(payload)') < src.index('finish()\n            app._set_state("idle"')
