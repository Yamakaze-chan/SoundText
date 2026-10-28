from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

import numpy as np


@dataclass(slots=True)
class MicGuardStats:
    suppressed_low_energy_chunks: int = 0
    suppressed_echo_chunks: int = 0
    passed_chunks: int = 0
    max_echo_correlation: float = 0.0


class MicDuplexGuard:
    """Lightweight microphone activity + loopback-echo guard.

    The meeting pipeline captures the same remote speech twice on laptops that play
    audio through speakers: once from WASAPI loopback and again acoustically through
    the microphone array. A normal VAD correctly considers that acoustic leakage
    "speech", so the Vietnamese ASR can hallucinate Vietnamese words from Japanese
    remote audio even while the user is silent.

    This guard keeps the two physical lanes separate without requiring an AEC model:
    * very-low-energy microphone chunks are zeroed before VAD;
    * while REMOTE is active, recent mic audio is compared with recent loopback audio
      over a small range of acoustic delays. Strongly correlated audio is treated as
      speaker leakage and zeroed;
    * unrelated/overlapping local speech is preserved because its correlation with the
      loopback signal is low.

    Zeroing rather than dropping chunks preserves the microphone timeline and lets the
    VAD close an active utterance naturally.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        enabled: bool = True,
        min_dbfs: float = -52.0,
        noise_margin_db: float = 8.0,
        echo_enabled: bool = True,
        echo_correlation: float = 0.38,
        echo_window_ms: int = 160,
        echo_max_delay_ms: int = 220,
        echo_lag_step_ms: int = 20,
        downsample: int = 4,
    ) -> None:
        self.sample_rate = max(8000, int(sample_rate))
        self.enabled = bool(enabled)
        self.min_dbfs = float(min_dbfs)
        self.noise_margin_db = max(0.0, float(noise_margin_db))
        self.echo_enabled = bool(echo_enabled)
        self.echo_correlation = float(np.clip(echo_correlation, 0.05, 0.99))
        self.window_samples = max(256, int(self.sample_rate * max(80, int(echo_window_ms)) / 1000))
        self.max_delay_samples = max(0, int(self.sample_rate * max(0, int(echo_max_delay_ms)) / 1000))
        self.lag_step_samples = max(1, int(self.sample_rate * max(5, int(echo_lag_step_ms)) / 1000))
        self.downsample = max(1, int(downsample))
        self._max_reference_samples = self.window_samples + self.max_delay_samples + self.sample_rate // 4
        self._system: deque[np.ndarray] = deque()
        self._system_samples = 0
        self._mic: deque[np.ndarray] = deque()
        self._mic_samples = 0
        # Conservative initial floor. It is only adapted while REMOTE is inactive,
        # so speaker leakage cannot teach the gate that echo is "normal silence".
        self._noise_floor_dbfs = -62.0
        self.system_recent_dbfs = -120.0
        self.stats = MicGuardStats()

    @staticmethod
    def dbfs(samples: np.ndarray) -> float:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return -120.0
        rms = float(np.sqrt(np.mean(x * x, dtype=np.float64) + 1e-12))
        return 20.0 * math.log10(max(rms, 1e-9))

    @staticmethod
    def _append(buffer: deque[np.ndarray], current_samples: int, x: np.ndarray, limit: int) -> int:
        if x.size:
            copy = np.asarray(x, dtype=np.float32).reshape(-1).copy()
            buffer.append(copy)
            current_samples += copy.size
        while buffer and current_samples > limit:
            first = buffer[0]
            excess = current_samples - limit
            if excess >= first.size:
                current_samples -= first.size
                buffer.popleft()
            else:
                buffer[0] = first[excess:].copy()
                current_samples -= excess
                break
        return current_samples

    @staticmethod
    def _tail(buffer: deque[np.ndarray], n: int) -> np.ndarray:
        if n <= 0 or not buffer:
            return np.empty(0, dtype=np.float32)
        x = np.concatenate(tuple(buffer))
        return x[-n:] if x.size > n else x

    def observe_system(self, samples: np.ndarray) -> None:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        self.system_recent_dbfs = self.dbfs(x)
        self._system_samples = self._append(
            self._system, self._system_samples, x, self._max_reference_samples
        )

    def _echo_similarity(self) -> float:
        """Find the best echo delay with sample-accurate normalized correlation.

        An old 20 ms step missed normal audio-device/acoustic offsets of only
        3–15 ms, so system audio appeared again in the MIC transcript. Correlate
        all valid delays after downsampling and use a rolling energy estimate;
        this stays inexpensive on CPU and avoids a heavy echo-cancellation model.
        """
        mic = self._tail(self._mic, self.window_samples)
        needed = self.window_samples + self.max_delay_samples
        system = self._tail(self._system, needed)
        if mic.size < self.window_samples or system.size < self.window_samples:
            return 0.0

        ds = self.downsample
        m = mic[::ds].astype(np.float64, copy=False)
        m = m - float(np.mean(m))
        m_norm = float(np.linalg.norm(m))
        if m_norm < 1e-7:
            return 0.0

        # Check all downsampling phases: an acoustic delay rarely happens to
        # be a multiple of 4 samples (e.g. 250 samples at 16 kHz).
        n = m.size
        best = 0.0
        for phase in range(ds):
            s = system[phase::ds].astype(np.float64, copy=False)
            if s.size < n:
                continue
            dot = np.correlate(s, m, mode="valid")
            totals = np.r_[0.0, np.cumsum(s)]
            squares = np.r_[0.0, np.cumsum(s * s)]
            window_sum = totals[n:] - totals[:-n]
            power = squares[n:] - squares[:-n] - (window_sum * window_sum) / n
            denom = np.sqrt(np.maximum(power, 0.0)) * m_norm
            scores = np.divide(np.abs(dot), denom, out=np.zeros_like(dot), where=denom > 1e-8)
            if scores.size:
                best = max(best, float(np.max(scores)))
        return best

    def process(self, samples: np.ndarray, *, remote_active: bool) -> tuple[np.ndarray, str | None]:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not self.enabled or x.size == 0:
            return x, None

        mic_db = self.dbfs(x)
        # Learn only likely ambient/noise samples when the remote speaker is inactive.
        if not remote_active and mic_db < -32.0:
            alpha = 0.04 if mic_db > self._noise_floor_dbfs else 0.12
            self._noise_floor_dbfs = (1.0 - alpha) * self._noise_floor_dbfs + alpha * mic_db

        gate_db = max(self.min_dbfs, self._noise_floor_dbfs + self.noise_margin_db)
        if mic_db < gate_db:
            self.stats.suppressed_low_energy_chunks += 1
            # Keep a zeroed mic tail so stale speech does not affect correlation.
            z = np.zeros_like(x)
            self._mic_samples = self._append(self._mic, self._mic_samples, z, self.window_samples)
            return z, "noise"

        self._mic_samples = self._append(self._mic, self._mic_samples, x, self.window_samples)
        if self.echo_enabled and remote_active:
            corr = self._echo_similarity()
            self.stats.max_echo_correlation = max(self.stats.max_echo_correlation, corr)
            # Preserve a simultaneous local speaker unless loopback audio
            # dominates the mic waveform. Loose correlation on mixed voices
            # would erase real interruptions and damage speaker diarization.
            if corr >= max(0.85, self.echo_correlation):
                self.stats.suppressed_echo_chunks += 1
                return np.zeros_like(x), "echo"

        self.stats.passed_chunks += 1
        return x, None
