from __future__ import annotations

from abc import ABC, abstractmethod
import numpy as np


class AudioSource(ABC):
    sample_rate: int

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def read(self, timeout: float = 0.5) -> np.ndarray | None: ...

    @abstractmethod
    def stop(self) -> None: ...
