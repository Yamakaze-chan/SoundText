from __future__ import annotations

import numpy as np


class StreamingLinearResampler:
    """Dependency-free fallback streaming resampler.

    Kept as a fallback for environments where python-soxr is unavailable.
    The preferred runtime path is StreamingSoxrResampler below.
    """

    def __init__(self, source_rate: int, target_rate: int) -> None:
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Sample rates must be positive")
        self.source_rate = source_rate
        self.target_rate = target_rate
        self._step = source_rate / target_rate
        self._buffer = np.empty(0, dtype=np.float32)
        self._next_pos = 0.0

    def push(self, samples: np.ndarray, last: bool = False) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return x
        if self.source_rate == self.target_rate:
            return x.copy()

        self._buffer = np.concatenate((self._buffer, x))
        if self._buffer.size < 2:
            return np.empty(0, dtype=np.float32)

        max_pos = self._buffer.size - 1
        if self._next_pos >= max_pos:
            return np.empty(0, dtype=np.float32)

        positions = np.arange(self._next_pos, max_pos, self._step, dtype=np.float64)
        left = np.floor(positions).astype(np.int64)
        frac = (positions - left).astype(np.float32)
        out = self._buffer[left] * (1.0 - frac) + self._buffer[left + 1] * frac

        self._next_pos = float(positions[-1] + self._step) if positions.size else self._next_pos
        drop = max(int(np.floor(self._next_pos)) - 1, 0)
        if drop:
            self._buffer = self._buffer[drop:]
            self._next_pos -= drop
        return out.astype(np.float32, copy=False)


class StreamingSoxrResampler:
    """High-quality low-overhead streaming resampler using libsoxr."""

    def __init__(self, source_rate: int, target_rate: int, quality: str = "HQ") -> None:
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Sample rates must be positive")
        self.source_rate = source_rate
        self.target_rate = target_rate
        self.quality = quality
        self._passthrough = source_rate == target_rate
        self._stream = None
        if not self._passthrough:
            try:
                import soxr
            except ImportError as exc:
                raise RuntimeError(
                    "python-soxr is required for the high-quality resampler. "
                    "Run: pip install -r requirements-runtime.txt"
                ) from exc
            self._stream = soxr.ResampleStream(
                source_rate,
                target_rate,
                1,
                dtype="float32",
                quality=quality,
            )

    def push(self, samples: np.ndarray, last: bool = False) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return x
        if self._passthrough:
            return x.copy()
        assert self._stream is not None
        y = self._stream.resample_chunk(x, last=last)
        return np.asarray(y, dtype=np.float32).reshape(-1)


def create_streaming_resampler(
    source_rate: int,
    target_rate: int,
    backend: str = "soxr",
    quality: str = "HQ",
):
    backend = backend.lower().strip()
    if backend == "linear":
        return StreamingLinearResampler(source_rate, target_rate)
    if backend != "soxr":
        raise ValueError("resampler_backend must be 'soxr' or 'linear'")
    try:
        return StreamingSoxrResampler(source_rate, target_rate, quality=quality)
    except RuntimeError:
        # The fallback keeps the tool usable if an old environment has not yet
        # installed the new dependency. A warning is emitted by the caller.
        return StreamingLinearResampler(source_rate, target_rate)
