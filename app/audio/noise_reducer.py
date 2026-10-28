from __future__ import annotations

import math
import numpy as np


class AdaptiveNoiseReducer:
    """Conservative stateful CPU denoiser for microphone speech.

    V2 avoids the old per-block sqrt-Hann analysis window, which could shape the
    edges of independent capture blocks and sound/behave "warbly" without proper
    overlap-add.  The new path keeps block amplitude intact and uses:

    - DC removal + stateful high-pass
    - minimum/statistical background spectrum tracking
    - frequency-smoothed soft spectral gain
    - temporal attack/release smoothing of the gain
    - speech-preserving dry/wet blending

    It remains NumPy-only and returns exactly the same number of samples it gets.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        enabled: bool = True,
        highpass_hz: float = 80.0,
        strength: float = 0.65,
        spectral_floor: float = 0.28,
        learn_rate: float = 0.025,
        speech_protect_ratio: float = 2.4,
        min_learn_dbfs: float = -58.0,
        max_learn_dbfs: float = -28.0,
    ) -> None:
        self.sample_rate = max(8000, int(sample_rate))
        self.enabled = bool(enabled)
        self.highpass_hz = max(0.0, float(highpass_hz))
        self.strength = float(np.clip(strength, 0.0, 1.5))
        self.spectral_floor = float(np.clip(spectral_floor, 0.08, 0.95))
        self.learn_rate = float(np.clip(learn_rate, 0.001, 0.20))
        self.speech_protect_ratio = max(1.2, float(speech_protect_ratio))
        self.min_learn_dbfs = float(min_learn_dbfs)
        self.max_learn_dbfs = float(max_learn_dbfs)

        self._hp_prev_x = 0.0
        self._hp_prev_y = 0.0
        self._noise_mag: np.ndarray | None = None
        self._noise_rms: float | None = None
        self._prev_gain: np.ndarray | None = None

        self.learned_chunks = 0
        self.processed_chunks = 0
        self.last_input_dbfs = -96.0
        self.last_output_dbfs = -96.0
        self.last_noise_dbfs = -96.0

    def reset(self) -> None:
        self._hp_prev_x = self._hp_prev_y = 0.0
        self._noise_mag = None
        self._noise_rms = None
        self._prev_gain = None
        self.learned_chunks = 0
        self.processed_chunks = 0
        self.last_input_dbfs = -96.0
        self.last_output_dbfs = -96.0
        self.last_noise_dbfs = -96.0

    def _highpass(self, x: np.ndarray) -> np.ndarray:
        if self.highpass_hz <= 0.0 or x.size == 0:
            return x
        dt = 1.0 / self.sample_rate
        rc = 1.0 / (2.0 * math.pi * self.highpass_hz)
        a = rc / (rc + dt)

        y = np.empty_like(x)
        px, py = self._hp_prev_x, self._hp_prev_y
        for i, value in enumerate(x):
            v = float(value)
            py = a * (py + v - px)
            px = v
            y[i] = py
        self._hp_prev_x, self._hp_prev_y = px, py
        return y

    @staticmethod
    def _dbfs(x: np.ndarray) -> float:
        if x.size == 0:
            return -96.0
        rms = float(np.sqrt(np.mean(x * x, dtype=np.float64) + 1e-12))
        return max(-96.0, 20.0 * math.log10(max(rms, 1e-9)))

    @staticmethod
    def _smooth_frequency(gain: np.ndarray) -> np.ndarray:
        if gain.size < 5:
            return gain
        # Small symmetric smoother reduces isolated musical-noise bins.
        out = gain.copy()
        out[1:-1] = (
            0.18 * gain[:-2]
            + 0.64 * gain[1:-1]
            + 0.18 * gain[2:]
        )
        return out

    def _update_noise_profile(self, mag: np.ndarray, rms: float, dbfs: float) -> None:
        if self._noise_mag is not None and self._noise_mag.shape != mag.shape:
            self._noise_mag = None
            self._noise_rms = None
            self._prev_gain = None
            self.learned_chunks = 0

        # Bootstrap by keeping spectral minima. This is safer than averaging the
        # first few blocks, which might contain speech.
        if self._noise_mag is None:
            self._noise_mag = mag.copy()
            self._noise_rms = rms
            self.learned_chunks = 1
            return

        if self.learned_chunks < 8:
            self._noise_mag = np.minimum(self._noise_mag, mag)
            self._noise_rms = min(float(self._noise_rms or rms), rms)
            self.learned_chunks += 1
            return

        noise_rms = max(float(self._noise_rms or rms), 1e-9)
        background_like = (
            self.min_learn_dbfs <= dbfs <= self.max_learn_dbfs
            and rms <= noise_rms * self.speech_protect_ratio
        )

        # Always allow the floor to move down reasonably quickly if the room gets
        # quieter. Move it upward only while a block looks background-like.
        lower = mag < self._noise_mag
        self._noise_mag[lower] = (
            0.82 * self._noise_mag[lower] + 0.18 * mag[lower]
        )

        if background_like:
            lr = self.learn_rate
            upper = ~lower
            self._noise_mag[upper] = (
                (1.0 - lr) * self._noise_mag[upper] + lr * mag[upper]
            )
            self._noise_rms = (1.0 - lr) * noise_rms + lr * rms
            self.learned_chunks += 1
        elif rms < noise_rms:
            self._noise_rms = 0.90 * noise_rms + 0.10 * rms

    def process(self, samples) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if x.size == 0 or not self.enabled:
            return x

        # Preserve amplitude continuity across independent capture blocks.
        x = x - np.float32(np.mean(x, dtype=np.float64))
        x = self._highpass(x)
        self.processed_chunks += 1
        self.last_input_dbfs = self._dbfs(x)

        rms = float(np.sqrt(np.mean(x * x, dtype=np.float64) + 1e-12))
        n = x.size
        n_fft = 1 << (max(256, n) - 1).bit_length()

        # No analysis window here: without overlap-add a Hann-derived window
        # modulates every block. Zero padding alone is safe for block continuity.
        spec = np.fft.rfft(x, n=n_fft)
        mag = np.abs(spec).astype(np.float32)

        self._update_noise_profile(mag, rms, self.last_input_dbfs)
        if self._noise_mag is None or self.learned_chunks < 4:
            self.last_output_dbfs = self.last_input_dbfs
            return x.astype(np.float32, copy=False)

        noise = np.maximum(self._noise_mag, 1e-7)
        noise_rms = max(float(self._noise_rms or rms), 1e-9)
        self.last_noise_dbfs = max(
            -96.0, 20.0 * math.log10(noise_rms)
        )

        # Soft subtraction. The spectral floor prevents zeroing consonants.
        subtraction = (0.78 + 0.62 * self.strength) * noise
        residual = np.maximum(mag - subtraction, 0.0)
        desired = residual / (mag + 1e-7)
        desired = self.spectral_floor + (1.0 - self.spectral_floor) * desired

        # Protect bins that clearly rise above the stationary floor.
        snr_ratio = mag / noise
        speech_bins = snr_ratio >= max(1.6, self.speech_protect_ratio * 0.72)
        desired[speech_bins] = np.maximum(desired[speech_bins], 0.62)

        # Extra rumble attenuation below ~120 Hz. Speech fundamentals remain,
        # while traffic/desk/air-conditioner rumble is reduced.
        freqs = np.fft.rfftfreq(n_fft, d=1.0 / self.sample_rate)
        low = freqs < 120.0
        desired[low] = np.minimum(desired[low], max(self.spectral_floor, 0.42))

        desired = np.clip(
            self._smooth_frequency(desired),
            self.spectral_floor,
            1.0,
        ).astype(np.float32)

        # Temporal smoothing: gain opens quickly for speech onsets, closes slowly
        # for noise, avoiding the "chattering" gate effect.
        if self._prev_gain is None or self._prev_gain.shape != desired.shape:
            gain = desired
        else:
            rising = desired > self._prev_gain
            gain = self._prev_gain.copy()
            gain[rising] += 0.72 * (desired[rising] - self._prev_gain[rising])
            gain[~rising] += 0.20 * (desired[~rising] - self._prev_gain[~rising])
        self._prev_gain = gain.astype(np.float32, copy=True)

        cleaned = np.fft.irfft(spec * gain, n=n_fft).real[:n].astype(np.float32)

        # When speech is much louder than the learned floor, preserve more dry
        # signal. During mostly-background blocks, allow stronger cleaning.
        level_ratio = rms / noise_rms
        if level_ratio >= 3.0:
            wet = 0.58
        elif level_ratio >= 1.8:
            wet = 0.68
        else:
            wet = min(0.86, 0.58 + 0.30 * self.strength)

        out = wet * cleaned + (1.0 - wet) * x
        out = np.clip(out, -1.0, 1.0).astype(np.float32, copy=False)
        self.last_output_dbfs = self._dbfs(out)
        return out
