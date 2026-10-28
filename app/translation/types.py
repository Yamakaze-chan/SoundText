from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TranslationJob:
    source: str
    utterance_id: int
    text: str
    source_language: str
    target_language: str = "vi"
    asr_completed_monotonic: float = 0.0
    queued_monotonic: float = 0.0


@dataclass(slots=True)
class TranslationResult:
    source: str
    utterance_id: int
    source_text: str
    translated_text: str
    source_language: str
    target_language: str
    inference_s: float
    completed_monotonic: float
    asr_completed_monotonic: float = 0.0
    queue_wait_s: float = 0.0
