from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Protocol

from .types import TranslationJob, TranslationResult


class TranslationEngine(Protocol):
    def translate(self, text: str, source_language: str, target_language: str) -> tuple[str, float]: ...


class TranslationWorker:
    """Single CPU worker for FINAL translations only.

    ASR never waits for translation. Final jobs are retained so the saved
    bilingual transcript does not silently lose utterances.
    """

    def __init__(
        self,
        engine: TranslationEngine,
        callback: Callable[[TranslationResult], None],
        error_callback: Callable[[str, TranslationJob], None] | None = None,
    ) -> None:
        self.engine = engine
        self.callback = callback
        self.error_callback = error_callback
        self._queue: queue.Queue[TranslationJob] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="translation-worker", daemon=True)
        self._thread.start()

    def submit(self, job: TranslationJob) -> None:
        if job.text.strip():
            job.queued_monotonic = time.monotonic()
            self._queue.put(job)

    def clear_pending(self, source: str | None = None) -> None:
        kept: list[TranslationJob] = []
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            if source is not None and job.source != source:
                kept.append(job)
        for job in kept:
            self._queue.put(job)

    def pending_jobs(self) -> int:
        return self._queue.qsize()

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                job = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                wait_s = max(0.0, time.monotonic() - job.queued_monotonic) if job.queued_monotonic else 0.0
                text, elapsed = self.engine.translate(
                    job.text, job.source_language, job.target_language
                )
                self.callback(TranslationResult(
                    source=job.source,
                    utterance_id=job.utterance_id,
                    source_text=job.text,
                    translated_text=text,
                    source_language=job.source_language,
                    target_language=job.target_language,
                    inference_s=elapsed,
                    completed_monotonic=time.monotonic(),
                    asr_completed_monotonic=job.asr_completed_monotonic,
                    queue_wait_s=wait_s,
                ))
            except Exception as exc:
                print(f"\n[TRANSLATION ERROR] {exc}")
                if self.error_callback is not None:
                    try:
                        self.error_callback(str(exc), job)
                    except Exception as cb_exc:
                        print(f"[TRANSLATION WARN] error callback failed: {cb_exc}")

    def stop(self, drain: bool = True) -> None:
        if not drain:
            self.clear_pending(None)
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
