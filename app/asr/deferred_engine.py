"""Load an inactive ASR lane only when it first receives real speech.

Never schedules work on Tk's event thread.  One sequential ASRWorker owns each
instance, so a lock around first use is enough to prevent duplicate loads.
"""
from __future__ import annotations

import threading


class DeferredASREngine:
    def __init__(self, config, *, language: str, threads: int):
        self._config = config
        self._language = language
        self.threads = threads
        self._lock = threading.Lock()
        self._engine = None
        self.backend = "gipformer" if language == "vi" else "sensevoice"

    @property
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        self._language = value
        if self._engine is not None and self.backend == "sensevoice":
            self._engine.language = value

    def transcribe(self, audio, final: bool = True):
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    from app.runtime_memory import ensure_available_memory, log_memory
                    from app.asr.factory import build_asr_engine

                    log_memory(f"before deferred ASR {self.backend}")
                    ensure_available_memory("tải ASR vừa bật", minimum_gib=1.7)
                    engine, _backend = build_asr_engine(
                        self._config, language=self._language, threads=self.threads
                    )
                    self._engine = engine
                    log_memory(f"after deferred ASR {self.backend}")
        return self._engine.transcribe(audio, final=final)
