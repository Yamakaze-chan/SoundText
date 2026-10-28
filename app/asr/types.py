from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Transcription:
    text: str
    language: str | None
    elapsed_s: float
    audio_s: float

    @property
    def rtf(self) -> float:
        """Realtime factor computed from inference time / audio duration.

        Kept as a property so all ASR backends share one compatible metric API.
        """
        if self.audio_s <= 0:
            return 0.0
        return float(self.elapsed_s) / float(self.audio_s)
