"""Background diarization: never infer on the PyAudio capture thread."""
from __future__ import annotations

from dataclasses import dataclass
import queue
import time
import threading
from typing import Callable

import numpy as np

from app.speakers.diarizer import SpeakerDiarizer


@dataclass(slots=True)
class SpeakerTask:
    source: str
    audio: np.ndarray
    start_s: float
    end_s: float
    leading_overlap_s: float = 0.0
    generation: int = 0


@dataclass(slots=True)
class SpeakerProbe:
    source: str
    audio: np.ndarray
    start_s: float
    utterance_id: int
    generation: int
    submitted: float


class SpeakerWorker:
    def __init__(
        self,
        diarizer: SpeakerDiarizer,
        callback: Callable[[SpeakerTask, np.ndarray, float, float, str | None, bool], None],
        *,
        max_backlog: int = 3,
        probe_callback: Callable[[SpeakerProbe, float], None] | None = None,
    ) -> None:
        self.diarizer = diarizer
        self.callback = callback
        self.max_backlog = max_backlog
        self.probe_callback = probe_callback
        self._probe_lock = threading.Lock()
        self._probes: dict[str, SpeakerProbe] = {}
        self.probe_switches = 0
        self.probe_skipped = 0
        self._probes_too_slow = False
        self._queue: queue.Queue[SpeakerTask] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.skipped_busy = 0
        self.errors = 0
        self.overlap_segments = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="speaker-diarization", daemon=True)
        self._thread.start()

    def submit(self, task: SpeakerTask) -> None:
        self._queue.put(task)

    def submit_probe(self, probe: SpeakerProbe) -> None:
        # Overwrite-only: no stack of obsolete in-progress utterance probes.
        if self.probe_callback is None or self._probes_too_slow or self._queue.qsize() > 0:
            return
        with self._probe_lock:
            self._probes[probe.source] = probe

    def clear_pending(self, source: str) -> None:
        with self._probe_lock:
            self._probes.pop(source, None)
        kept = []
        while True:
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                break
            if task.source != source:
                kept.append(task)
            self._queue.task_done()
        for task in kept:
            self._queue.put(task)

    def pending(self) -> int:
        return self._queue.qsize()

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                task = self._queue.get(timeout=0.025)
            except queue.Empty:
                # Run only when no final job is queued. No probe backlog.
                if self.probe_callback is not None and not self._stop.is_set():
                    with self._probe_lock:
                        probe = next(iter(self._probes.values()), None)
                        if probe is not None:
                            self._probes.pop(probe.source, None)
                    if probe is not None:
                        if time.monotonic() - probe.submitted > 1.6:
                            self.probe_skipped += 1
                        else:
                            try:
                                started = time.monotonic()
                                change = self.diarizer.detect_interruption(probe.audio)
                                took = time.monotonic() - started
                                # On a slow i5, optional probes must not block
                                # authoritative Final diarization repeatedly.
                                if took > 1.2:
                                    self._probes_too_slow = True
                                    with self._probe_lock:
                                        self._probes.clear()
                                    print(f'\n[SPEAKER] Early probes disabled: {took:.2f}s/probe is too slow; final-only fallback')
                                elif change is not None:
                                    self.probe_callback(probe, change)
                                    self.probe_switches += 1
                            except Exception as exc:
                                self.errors += 1
                                print(f'\n[SPEAKER PROBE WARN] {exc}')
                continue
            # Low-priority CPU policy: don't indefinitely stall ASR if diarizer
            # becomes slower than the live meeting. Still process every audio job.
            if self._queue.qsize() > self.max_backlog:
                self.skipped_busy += 1
                pieces = [(task.audio, 0.0, task.end_s - task.start_s, None, False)]
            else:
                try:
                    pieces = self.diarizer.process(task.source, task.audio)
                except Exception as exc:
                    self.errors += 1
                    print(f'\n[SPEAKER WARN] {task.source}: {exc}; normal ASR fallback')
                    pieces = [(task.audio, 0.0, task.end_s - task.start_s, None, False)]
            try:
                for part, a, b, speaker, overlap in pieces:
                    if overlap:
                        self.overlap_segments += 1
                    try:
                        self.callback(task, part, a, b, speaker, overlap)
                    except Exception as exc:
                        self.errors += 1
                        print(f'\n[SPEAKER CALLBACK WARN] {exc}')
            finally:
                self._queue.task_done()

    def stop(self, *, drain: bool = True) -> None:
        if self._thread is None:
            drain = False
        if drain:
            self._queue.join()
        else:
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    break
        self._stop.set()
        with self._probe_lock:
            self._probes.clear()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
