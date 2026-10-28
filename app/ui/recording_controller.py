"""UI-neutral, on-demand recording session controller.

Opening SoundText preloads model sessions while the splash is shown, but it
never opens an audio device or creates a recording session until Start is pressed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from queue import SimpleQueue
from threading import Event, Lock, Thread
from typing import Any

from app.ui.meeting_window import MeetingUIBus


@dataclass(frozen=True)
class SessionStatus:
    kind: str                       # starting | recording | stopping | stopped | failed
    message: str = ""
    session_dir: str = ""


class RecordingController:
    def __init__(self, bus: MeetingUIBus | None = None) -> None:
        self._prepared_models = None
        self.bus = bus or MeetingUIBus()
        self.statuses: SimpleQueue[SessionStatus] = SimpleQueue()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._pipeline: Any = None
        self._stop = Event()
        self._busy = False

    def set_prepared_models(self, models) -> None:
        """Called only from Tk after the splash background loader finishes."""
        with self._lock:
            if self._busy:
                raise RuntimeError("Cannot install prepared models during recording")
            self._prepared_models = models

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def start(self, config: Any) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._stop.clear()
            prepared_models = self._prepared_models
            self._prepared_models = None
        cfg = deepcopy(config)
        self.statuses.put(SessionStatus("starting", "Đang chuẩn bị model và thiết bị..."))
        self._thread = Thread(target=self._run, args=(cfg, prepared_models), name="soundtext-recording", daemon=False)
        self._thread.start()
        return True

    def _run(self, config: Any, prepared_models=None) -> None:
        path = ""
        error = ""
        try:
            # Build the session off the Tk event thread; hand over the inference
            # objects prepared by the splash without reloading their weights.
            from app.pipeline_meeting import MeetingSTTPipeline
            if self._stop.is_set():
                return
            if prepared_models is None:
                pipeline = MeetingSTTPipeline(config, ui_bus=self.bus)
            else:
                pipeline = MeetingSTTPipeline(config, ui_bus=self.bus, prepared_models=prepared_models)
            path = str(pipeline.store.session_dir)
            with self._lock:
                self._pipeline = pipeline
            if self._stop.is_set():
                pipeline.store.close(status="cancelled")
                return
            self.statuses.put(SessionStatus("recording", "Đang ghi âm và nhận diện", path))
            pipeline.run_capture()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.statuses.put(SessionStatus("failed", error, path))
        finally:
            with self._lock:
                self._pipeline = None
                self._busy = False
            self.statuses.put(SessionStatus("stopped", error, path))

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            p = self._pipeline
        if p is not None:
            p.request_stop()
        if self.busy:
            self.statuses.put(SessionStatus("stopping", "Đang lưu và hoàn tất phiên ghi âm..."))

    def set_mode(self, value: str) -> None:
        with self._lock:
            p = self._pipeline
        if p is not None:
            p.set_audio_mode(value)

    def set_language(self, source: str, value: str) -> None:
        with self._lock:
            p = self._pipeline
        if p is not None:
            # The JA/EN/AUTO fast-switch remains in the core; a different
            # engine may load, so never execute this on Tk's event thread.
            Thread(target=p.set_lane_language, args=(source, value), daemon=True,
                   name=f"soundtext-language-{source}").start()

    def join(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)
