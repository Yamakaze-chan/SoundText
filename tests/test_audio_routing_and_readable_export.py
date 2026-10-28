"""Regression tests for selecting sources and producing a single readable TXT."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.audio.pyaudio_capture import PyAudioCapture
from app.pipeline_meeting import MeetingSTTPipeline
from app.storage.session_store import SessionStore, TranscriptEntry, TranslationEntry
from app.ui.meeting_window import MeetingUIBus


class FakeCapture:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.is_open = False
        self.read_count = 0
    def start(self):
        self.started += 1
        self.is_open = True
    def stop(self):
        self.stopped += 1
        self.is_open = False
    def read(self, timeout=0):
        self.read_count += 1
        if not self.is_open:
            raise AssertionError('Reading an inactive device')
        return None
    def pending_chunks(self):
        return 0


class FakeWorker:
    def start(self): pass
    def stop(self, drain=False): pass
    def clear_pending(self, *_args, **_kwargs): pass


class FakeSegmenter:
    def discard(self): pass
    def flush(self): return None
    def push(self, *_args, **_kwargs): return []


def make_pipeline(mode):
    p = object.__new__(MeetingSTTPipeline)
    p._audio_mode_lock = threading.Lock()
    p._capture_switch_lock = threading.Lock()
    p._capture_started = False
    p._capture_error = None
    p._active_capture_sources = set()
    p._audio_mode = mode
    p._pending_lane_resets = set()
    p._speaker_generation = {'mic': 0, 'system': 0}
    p._stop = threading.Event()
    p._session_start = 0
    p.speaker_worker = None
    p.translation_worker = None
    p.ui_bus = MeetingUIBus()
    p.lanes = {name: SimpleNamespace(source=FakeCapture(), worker=FakeWorker(), segmenter=FakeSegmenter())
               for name in ('mic', 'system')}
    return p


def wait_until(predicate, timeout=1.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return True
        time.sleep(0.01)
    return predicate()


def test_remote_only_never_starts_microphone():
    p = make_pipeline('remote')
    p._start()
    assert p._active_capture_sources == {'system'}
    assert p.lanes['system'].source.is_open
    assert not p.lanes['mic'].source.is_open
    p._shutdown()
    assert p.lanes['mic'].source.started == 0


def test_dual_mode_opens_both_then_switching_closes_unused_device():
    p = make_pipeline('both')
    p._start()
    assert p._active_capture_sources == {'mic','system'}
    p.ui_bus.publish_audio_level('mic', dbfs=-5, peak_dbfs=-4)
    p.set_audio_mode('remote')
    assert wait_until(lambda: p._active_capture_sources == {'system'})
    assert not p.lanes['mic'].source.is_open
    assert 'mic' not in p.ui_bus.snapshot_audio_levels()
    p.set_audio_mode('you')
    assert wait_until(lambda: p._active_capture_sources == {'mic'})
    assert not p.lanes['system'].source.is_open
    p._shutdown()


def test_repeated_mode_changes_end_in_last_requested_state():
    p = make_pipeline('remote')
    p._start()
    for mode in ('both', 'you', 'remote', 'both', 'remote'):
        p.set_audio_mode(mode)
    assert wait_until(lambda: p._active_capture_sources == {'system'})
    assert p._get_audio_mode() == 'remote'
    p._shutdown()


def test_inactive_mic_not_read_during_remote_loop(monkeypatch):
    p = make_pipeline('remote')
    p._capture_started = True
    p._active_capture_sources = {'system'}
    p.lanes['system'].source.start()
    p._last_health = time.monotonic()
    p.config = SimpleNamespace(reliability=SimpleNamespace(health_interval_s=10000))
    p._last_backlog_warn = time.monotonic()
    p._take_mode_and_resets = lambda: ('remote', set())
    p._publish_audio_level = lambda *args: None
    # One pass through the actual loop, then terminate.
    original_read = p.lanes['system'].source.read
    def first_read(timeout=0):
        p._stop.set()
        return original_read(timeout=timeout)
    p.lanes['system'].source.read = first_read
    p._audio_loop_inner()
    assert p.lanes['mic'].source.read_count == 0
    p._shutdown()


class FakePA:
    def get_device_info_by_index(self, index):
        return {'index':index,'name':'Stereo Mix (Realtek)', 'maxInputChannels':2,
                'maxOutputChannels':0, 'isLoopbackDevice':False}
    def get_default_input_device_info(self):
        return self.get_device_info_by_index(8)
    def get_device_info_generator(self):
        return iter([])


def test_explicit_system_mix_cannot_be_used_as_microphone():
    cap = PyAudioCapture('mic', device_index=8)
    with pytest.raises(RuntimeError, match='not a microphone'):
        cap._resolve_device(FakePA())


def test_default_system_mix_without_real_mic_is_rejected():
    cap = PyAudioCapture('mic')
    with pytest.raises(RuntimeError, match='physical microphone'):
        cap._resolve_device(FakePA())


def test_one_readable_txt_for_translation_and_transcription(tmp_path):
    store = SessionStore(tmp_path)
    store.append(TranscriptEntry(utterance_id=0,start_s=1,end_s=3,text='Hello team',
                                 source='system',language='en',speaker_id='S1'))
    store.append_translation(TranslationEntry(utterance_id=0,source='system',
                                             source_language='en',target_language='vi',
                                             source_text='Hello team',translated_text='Chào cả nhóm'))
    store.append(TranscriptEntry(utterance_id=1,start_s=4,end_s=6,text='Xin chào',
                                 source='mic',language='vi'))
    store.close()
    files = list((store.session_dir/'Noi_dung').glob('*'))
    assert files == [store.readable_txt_path]
    content = store.readable_txt_path.read_text(encoding='utf-8')
    assert 'Chào cả nhóm' in content
    assert 'Hello team' in content
    assert 'Xin chào' in content
    assert 'S1' in content
    assert 'translations.jsonl' not in content
    assert store.jsonl_path.exists()  # debug/recovery retained
    assert store.translation_jsonl_path.exists()


def test_one_readable_txt_without_translation(tmp_path):
    store = SessionStore(tmp_path)
    store.append(TranscriptEntry(utterance_id=1,start_s=0,end_s=2,text='Xin chào',
                                 source='mic',language='vi'))
    store.close()
    text = store.readable_txt_path.read_text(encoding='utf-8')
    assert 'Xin chào' in text
    assert 'Tiếng Việt:' not in text  # no fabricated translation

@pytest.mark.parametrize('delay_samples',[60,140,250,440,800])
def test_guard_detects_echo_with_non_aligned_audio_delay(delay_samples):
    from app.audio.mic_guard import MicDuplexGuard
    rng = np.random.default_rng(42)
    loopback = (rng.standard_normal(16000) * 0.09).astype(np.float32)
    mic = np.pad(loopback, (delay_samples,0))[:16000]
    guard = MicDuplexGuard(sample_rate=16000)
    muted = 0
    for i in range(0,9600,320):
        guard.observe_system(loopback[i:i+320])
        _,reason=guard.process(mic[i:i+320],remote_active=True)
        muted += reason == 'echo'
    assert muted >= 10


def test_guard_keeps_distinct_local_speech_overlapping_remote():
    from app.audio.mic_guard import MicDuplexGuard
    rng = np.random.default_rng(99)
    loopback = (rng.standard_normal(16000) * .06).astype(np.float32)
    local = (rng.standard_normal(16000) * .16).astype(np.float32)
    mic = np.pad(loopback,(130,0))[:16000] + local
    guard = MicDuplexGuard(sample_rate=16000)
    muted = 0
    for i in range(0,9600,320):
        guard.observe_system(loopback[i:i+320])
        _,reason=guard.process(mic[i:i+320],remote_active=True)
        muted += reason == 'echo'
    assert muted == 0


def test_capture_failure_on_switch_is_visible_not_silently_ignored():
    p = make_pipeline('remote')
    p._start()
    def fail_start():
        raise RuntimeError('microphone permission denied')
    p.lanes['mic'].source.start = fail_start
    p.set_audio_mode('both')
    assert wait_until(lambda: p._capture_error is not None)
    assert p._stop.is_set()
    assert 'MIC' in str(p._capture_error)
    p._shutdown()
