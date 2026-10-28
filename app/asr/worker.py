from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time
from typing import Callable, Literal, Protocol

import numpy as np

from .types import Transcription


class ASREngine(Protocol):
    def transcribe(self, audio: np.ndarray, final: bool) -> Transcription: ...


@dataclass(slots=True)
class ASRJob:
    kind: Literal["partial", "final"]
    utterance_id: int
    audio: np.ndarray
    start_s: float
    end_s: float
    source: str = "audio"
    leading_overlap_s: float = 0.0
    # Monotonic timestamp for when the newest audio sample in this job occurred.
    # V10+ uses this to measure audio -> ASR end-to-end delay.
    audio_end_monotonic: float = 0.0
    speaker_id: str | None = None
    speaker_overlap: bool = False


@dataclass(slots=True)
class ASRResult:
    kind: Literal["partial", "final"]
    utterance_id: int
    text: str
    start_s: float
    end_s: float
    inference_s: float
    audio_s: float
    rtf: float
    source: str = "audio"
    leading_overlap_s: float = 0.0
    model_role: str = "asr"
    language: str | None = None
    audio_end_monotonic: float = 0.0
    completed_monotonic: float = 0.0
    speaker_id: str | None = None
    speaker_overlap: bool = False


class ASRWorker:
    """Realtime ASR scheduler.

    In parallel mode, live previews and final decoding have independent workers.
    The live queue keeps only the newest preview for each source, so latency cannot
    grow without bound. Finals are never dropped.

    In sequential mode, one worker is used to minimize memory usage.
    """

    def __init__(
        self,
        live_engine: ASREngine,
        callback: Callable[[ASRResult], None],
        final_engine: ASREngine | None = None,
        parallel: bool = False,
    ) -> None:
        self.live_engine = live_engine
        self.final_engine = final_engine or live_engine
        self.callback = callback
        self.parallel = bool(parallel)
        self._finals: queue.Queue[ASRJob] = queue.Queue()
        self._partials: queue.Queue[ASRJob] = queue.Queue(maxsize=4)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._state_lock = threading.Lock()
        self._live_busy = False
        self._final_busy = False
        self.dropped_partials = 0

    def start(self) -> None:
        self._stop.clear()
        self._threads = []
        if self.parallel:
            self._threads = [
                threading.Thread(target=self._run_live, name="asr-live", daemon=True),
                threading.Thread(target=self._run_final, name="asr-final", daemon=True),
            ]
        else:
            self._threads = [threading.Thread(target=self._run_sequential, name="asr-worker", daemon=True)]
        for thread in self._threads:
            thread.start()

    def _set_busy(self, role: str, value: bool) -> None:
        with self._state_lock:
            if role == "live":
                self._live_busy = value
            else:
                self._final_busy = value

    def _is_live_busy(self) -> bool:
        with self._state_lock:
            return self._live_busy

    def _clear_partials_for(self, source: str | None = None) -> None:
        kept: list[ASRJob] = []
        while True:
            try:
                job = self._partials.get_nowait()
            except queue.Empty:
                break
            if source is not None and job.source != source:
                kept.append(job)
            else:
                self.dropped_partials += 1
        for job in kept:
            try:
                self._partials.put_nowait(job)
            except queue.Full:
                self.dropped_partials += 1

    def clear_pending(self, source: str | None = None, *, include_finals: bool = True) -> None:
        """Discard queued work for a disabled lane. Running inference is not cancelled."""
        self._clear_partials_for(source)
        if not include_finals:
            return
        kept: list[ASRJob] = []
        while True:
            try:
                job = self._finals.get_nowait()
            except queue.Empty:
                break
            if source is not None and job.source != source:
                kept.append(job)
        for job in kept:
            self._finals.put(job)

    def submit(self, job: ASRJob) -> None:
        if job.kind == "final":
            # A final closes this utterance. Any queued preview for the same source
            # is obsolete, but in parallel mode an already-running preview may finish;
            # TranscriptManager ignores it once the final is committed.
            self._clear_partials_for(job.source)
            self._finals.put(job)
            return

        # Never let old partials accumulate. Keep at most the newest preview per source.
        old: list[ASRJob] = []
        while True:
            try:
                p = self._partials.get_nowait()
            except queue.Empty:
                break
            if p.source == job.source:
                self.dropped_partials += 1
            else:
                old.append(p)
        for p in old:
            try:
                self._partials.put_nowait(p)
            except queue.Full:
                self.dropped_partials += 1
        try:
            self._partials.put_nowait(job)
        except queue.Full:
            self.dropped_partials += 1

    def _emit(self, job: ASRJob, engine: ASREngine, *, final: bool, role: str) -> None:
        try:
            result = engine.transcribe(job.audio, final=final)
            self.callback(
                ASRResult(
                    kind=job.kind,
                    utterance_id=job.utterance_id,
                    text=result.text,
                    start_s=job.start_s,
                    end_s=job.end_s,
                    inference_s=result.elapsed_s,
                    audio_s=result.audio_s,
                    rtf=result.rtf,
                    source=job.source,
                    leading_overlap_s=job.leading_overlap_s,
                    model_role=role,
                    language=result.language,
                    audio_end_monotonic=job.audio_end_monotonic,
                    completed_monotonic=time.monotonic(),
                    speaker_id=job.speaker_id,
                    speaker_overlap=job.speaker_overlap,
                )
            )
        except Exception as exc:
            print(f"\n[ASR ERROR/{role}] {exc}")

    def _run_live(self) -> None:
        while not self._stop.is_set() or not self._partials.empty():
            try:
                job = self._partials.get(timeout=0.08)
            except queue.Empty:
                continue
            self._set_busy("live", True)
            try:
                self._emit(job, self.live_engine, final=False, role="live")
            finally:
                self._set_busy("live", False)

    def _run_final(self) -> None:
        while not self._stop.is_set() or not self._finals.empty():
            try:
                job = self._finals.get(timeout=0.08)
            except queue.Empty:
                continue
            self._set_busy("final", True)
            try:
                self._emit(job, self.final_engine, final=True, role="final")
            finally:
                self._set_busy("final", False)

    def _run_sequential(self) -> None:
        while not self._stop.is_set() or not self._finals.empty() or not self._partials.empty():
            # Authoritative finals must never sit behind a stream of previews.  On
            # CPU-only SenseVoice this is especially important: a partial every ~1s
            # can otherwise keep the final queue tens of seconds behind wall clock.
            try:
                f = self._finals.get_nowait()
            except queue.Empty:
                f = None
            if f is not None:
                self._set_busy("final", True)
                try:
                    self._emit(f, self.final_engine, final=True, role="final")
                finally:
                    self._set_busy("final", False)
                continue

            try:
                p = self._partials.get_nowait()
            except queue.Empty:
                p = None
            if p is not None:
                self._set_busy("live", True)
                try:
                    self._emit(p, self.live_engine, final=False, role="live")
                finally:
                    self._set_busy("live", False)
            else:
                self._stop.wait(0.05)

    def stop(self, drain: bool = True) -> None:
        if not drain:
            while not self._finals.empty():
                try:
                    self._finals.get_nowait()
                except queue.Empty:
                    break
            self._clear_partials_for(None)
        self._stop.set()
        for thread in self._threads:
            thread.join()
