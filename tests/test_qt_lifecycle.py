"""Checks for the new idle-by-default UI lifecycle without loading models/Qt."""
from __future__ import annotations

from types import ModuleType
from threading import Event
import sys
import time

from app.ui.meeting_window import MeetingUIBus
from app.ui.recording_controller import RecordingController


def _drain(controller):
    statuses=[]
    while not controller.statuses.empty():
        statuses.append(controller.statuses.get_nowait())
    return statuses


def test_initial_state_does_not_construct_or_record(monkeypatch):
    construction=[]
    fake=ModuleType('app.pipeline_meeting')
    class Pipeline:
        def __init__(self,*args,**kwargs):
            construction.append(True)
    fake.MeetingSTTPipeline=Pipeline
    monkeypatch.setitem(sys.modules,'app.pipeline_meeting',fake)
    c=RecordingController(MeetingUIBus())
    assert not c.busy
    assert construction == []
    assert _drain(c)==[]


def test_start_stop_repeated_sessions_without_ui_block(monkeypatch):
    construction=[]
    was_started=Event()
    fake=ModuleType('app.pipeline_meeting')
    class Store:
        session_dir='/tmp/fake_session'
        def close(self,status):pass
    class Pipeline:
        def __init__(self,*args,**kwargs):
            self.store=Store()
            self.stop_requested=Event()
            construction.append(self)
        def run_capture(self):
            was_started.set()
            self.stop_requested.wait(timeout=4)
        def request_stop(self):self.stop_requested.set()
        def set_audio_mode(self,value):self.mode=value
        def set_lane_language(self,source,value):self.language=(source,value)
    fake.MeetingSTTPipeline=Pipeline
    monkeypatch.setitem(sys.modules,'app.pipeline_meeting',fake)
    c=RecordingController(MeetingUIBus())
    assert c.start(object())
    assert was_started.wait(timeout=2)
    assert c.busy
    assert not c.start(object())  # Cannot launch twice while recording.
    c.set_mode('remote')
    c.set_language('system','auto')
    c.stop(); c.join(timeout=2)
    assert not c.busy
    assert len(construction)==1
    assert construction[0].mode=='remote'
    statuses=_drain(c)
    assert any(s.kind=='recording' for s in statuses)
    assert any(s.kind=='stopped' for s in statuses)
    was_started.clear()
    assert c.start(object())
    assert was_started.wait(timeout=2)
    c.stop();c.join(timeout=2)
    assert len(construction)==2


def test_stop_during_startup_does_not_record(monkeypatch):
    loaded=Event(); allow=Event(); runs=[]
    fake=ModuleType('app.pipeline_meeting')
    class Store:
        session_dir='/tmp/fake_session'
        def close(self,status):runs.append(('closed',status))
    class Pipeline:
        def __init__(self,*args,**kwargs):
            loaded.set()
            allow.wait(timeout=2)
            self.store=Store()
        def run_capture(self):runs.append(('started',None))
        def request_stop(self):pass
    fake.MeetingSTTPipeline=Pipeline
    monkeypatch.setitem(sys.modules,'app.pipeline_meeting',fake)
    c=RecordingController(MeetingUIBus())
    assert c.start(object())
    assert loaded.wait(timeout=2)
    c.stop();allow.set();c.join(timeout=2)
    assert ('started',None) not in runs
    assert ('closed','cancelled') in runs
