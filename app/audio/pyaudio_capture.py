from __future__ import annotations

import queue
import re
import threading
import os
from dataclasses import dataclass

import numpy as np

from .base import AudioSource
from .resampler import StreamingLinearResampler, create_streaming_resampler


@dataclass(slots=True)
class DeviceSummary:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: int
    is_loopback: bool = False


def boost_current_audio_thread_priority() -> None:
    """Best-effort Windows thread priority boost for realtime capture/VAD.

    Uses only WinAPI already present on Windows; failure is intentionally ignored.
    This prevents CPU-heavy local inference from starving short PortAudio reads.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetCurrentThread()
        # THREAD_PRIORITY_HIGHEST = 2 (not TIME_CRITICAL).
        kernel32.SetThreadPriority(handle, 2)
    except Exception:
        pass


def _import_pyaudio():
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise RuntimeError(
            "PyAudioWPatch is not installed. Run: pip install -r requirements-runtime.txt"
        ) from exc
    return pyaudio


def list_devices() -> list[DeviceSummary]:
    pyaudio = _import_pyaudio()
    devices: list[DeviceSummary] = []
    with pyaudio.PyAudio() as pa:
        loopback_indices = {int(d["index"]) for d in pa.get_loopback_device_info_generator()}
        for d in pa.get_device_info_generator():
            devices.append(
                DeviceSummary(
                    index=int(d["index"]),
                    name=str(d["name"]),
                    max_input_channels=int(d.get("maxInputChannels", 0)),
                    max_output_channels=int(d.get("maxOutputChannels", 0)),
                    default_sample_rate=int(float(d.get("defaultSampleRate", 0))),
                    is_loopback=int(d["index"]) in loopback_indices,
                )
            )
    return devices


def _downmix(pcm: np.ndarray, channels: int, mode: str) -> np.ndarray:
    if channels <= 1:
        return pcm.reshape(-1).astype(np.float32, copy=False)
    x = pcm.reshape(-1, channels)
    mode = mode.lower().strip()
    if mode == "left":
        return x[:, 0].astype(np.float32, copy=False)
    if mode == "right":
        return x[:, min(1, channels - 1)].astype(np.float32, copy=False)
    if mode == "loudest":
        rms = np.sqrt(np.mean(np.square(x), axis=0) + 1e-12)
        return x[:, int(np.argmax(rms))].astype(np.float32, copy=False)
    if mode != "mean":
        raise ValueError("channel_mode must be mic_speech, stereo_speech, mean, loudest, left, or right")

    # For normal stereo/system audio, averaging is desirable. If channels are
    # strongly out of phase, averaging can nearly erase speech, so fall back to
    # the louder channel in that rare case.
    if channels == 2 and x.shape[0] >= 32:
        left = x[:, 0]
        right = x[:, 1]
        denom = float(np.linalg.norm(left) * np.linalg.norm(right)) + 1e-12
        corr = float(np.dot(left, right) / denom)
        if corr < -0.35:
            l_rms = float(np.sqrt(np.mean(left * left) + 1e-12))
            r_rms = float(np.sqrt(np.mean(right * right) + 1e-12))
            return (left if l_rms >= r_rms else right).astype(np.float32, copy=False)
    return np.mean(x, axis=1, dtype=np.float32)


class _AdaptiveStereoSpeechDownmixer:
    """Conservative stereo -> mono selector for system/loopback audio.

    Normal stereo dialogue is usually common to L/R, so center/mean remains the
    preferred path.  If the channels are strongly unbalanced, decorrelated, or
    phase-opposed, we can hold the clearer speech-bearing channel instead.  A
    small hysteresis prevents channel flapping every PortAudio buffer.
    """

    def __init__(self, sample_rate: int, hold_chunks: int = 6) -> None:
        self.sample_rate = max(8000, int(sample_rate))
        self.hold_chunks = max(2, int(hold_chunks))
        self.current = "mean"
        self._candidate = "mean"
        self._candidate_count = 0

    def _speech_ratio(self, x: np.ndarray) -> float:
        y = np.asarray(x, dtype=np.float32).reshape(-1)
        if y.size < 64:
            return 0.0
        y = y - np.mean(y, dtype=np.float32)
        # Cap FFT work; capture buffers are small anyway, but this keeps the
        # selector effectively free on long/native buffers.
        if y.size > 2048:
            y = y[-2048:]
        rms = float(np.sqrt(np.mean(y * y) + 1e-12))
        if rms < 1e-5:
            return 0.0
        window = np.hanning(y.size).astype(np.float32)
        spec = np.abs(np.fft.rfft(y * window)) ** 2
        freqs = np.fft.rfftfreq(y.size, 1.0 / self.sample_rate)
        total_mask = (freqs >= 80.0) & (freqs <= min(10000.0, self.sample_rate * 0.48))
        speech_mask = (freqs >= 180.0) & (freqs <= min(4200.0, self.sample_rate * 0.48))
        total = float(np.sum(spec[total_mask])) + 1e-12
        speech = float(np.sum(spec[speech_mask]))
        return speech / total

    def _candidate_mode(self, left: np.ndarray, right: np.ndarray) -> str:
        l_rms = float(np.sqrt(np.mean(left * left) + 1e-12))
        r_rms = float(np.sqrt(np.mean(right * right) + 1e-12))
        denom = float(np.linalg.norm(left) * np.linalg.norm(right)) + 1e-12
        corr = float(np.dot(left, right) / denom)

        # Same/centered programme: averaging improves SNR and is the safest path.
        ratio = max(l_rms, r_rms) / max(min(l_rms, r_rms), 1e-8)
        if corr >= 0.60 and ratio < 1.8:
            return "mean"

        # Strong anti-phase stereo can cancel speech in a mean downmix.
        if corr <= -0.20:
            return "left" if l_rms >= r_rms else "right"

        l_voice = self._speech_ratio(left)
        r_voice = self._speech_ratio(right)
        # Prefer a side only when it is materially more speech-like.  This avoids
        # selecting a loud music/effect channel merely because its RMS is higher.
        if l_voice > r_voice * 1.30 and l_voice > 0.35:
            return "left"
        if r_voice > l_voice * 1.30 and r_voice > 0.35:
            return "right"

        # If one side is much stronger and neither has a clear speech advantage,
        # use that side rather than diluting it by 6+dB through averaging.
        if ratio >= 2.2:
            return "left" if l_rms >= r_rms else "right"
        return "mean"

    def process(self, pcm: np.ndarray, channels: int) -> np.ndarray:
        if channels <= 1:
            return pcm.reshape(-1).astype(np.float32, copy=False)
        x = np.asarray(pcm, dtype=np.float32).reshape(-1, channels)
        left = x[:, 0]
        right = x[:, min(1, channels - 1)]
        wanted = self._candidate_mode(left, right)
        if wanted == self.current:
            self._candidate = wanted
            self._candidate_count = 0
        else:
            if wanted == self._candidate:
                self._candidate_count += 1
            else:
                self._candidate = wanted
                self._candidate_count = 1
            if self._candidate_count >= self.hold_chunks:
                self.current = wanted
                self._candidate_count = 0
        if self.current == "left":
            return left.astype(np.float32, copy=False)
        if self.current == "right":
            return right.astype(np.float32, copy=False)
        return np.mean(x[:, :2], axis=1, dtype=np.float32)


class PyAudioCapture(AudioSource):
    """Windows microphone or WASAPI loopback capture.

    Output is mono float32 at target_sample_rate. Capture runs on its own thread,
    so ASR inference cannot block PortAudio reads.
    """

    def __init__(
        self,
        source: str,
        target_sample_rate: int = 16000,
        device_index: int | None = None,
        frames_per_buffer: int = 1024,
        queue_seconds: float = 6.0,
        resampler_backend: str = "soxr",
        resampler_quality: str = "HQ",
        channel_mode: str = "mean",
    ) -> None:
        if source not in {"mic", "system"}:
            raise ValueError("source must be 'mic' or 'system'")
        self.source = source
        self.sample_rate = target_sample_rate
        self.device_index = device_index
        self.frames_per_buffer = frames_per_buffer
        self.queue_seconds = queue_seconds
        self.resampler_backend = resampler_backend
        self.resampler_quality = resampler_quality
        self.channel_mode = channel_mode
        # Input chunk rate depends on the device native sample rate; this queue
        # cap is intentionally conservative and bounded.
        max_items = max(32, int(queue_seconds * 100))
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_items)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._error: Exception | None = None
        self._info_line: str | None = None
        self._resolution_note: str | None = None
        self._ready = threading.Event()
        self.dropped_chunks = 0

    @staticmethod
    def _device_name_key(name: str) -> str:
        value = re.sub(r"\[\s*loopback\s*\]", "", str(name), flags=re.I)
        value = re.sub(r"\s+", " ", value).strip().casefold()
        return value

    @staticmethod
    def _is_loopback(info) -> bool:
        return bool(info.get("isLoopbackDevice", False))

    @staticmethod
    def _is_output(info) -> bool:
        return int(info.get("maxOutputChannels", 0) or 0) > 0

    def _loopbacks(self, pa) -> list[dict]:
        try:
            return [dict(d) for d in pa.get_loopback_device_info_generator()]
        except Exception:
            return []

    def _match_loopback(self, pa, output_info, loopbacks: list[dict] | None = None):
        """Map a render/output endpoint to its WASAPI loopback capture endpoint."""
        if self._is_loopback(output_info):
            return output_info
        if not self._is_output(output_info):
            return None

        # PyAudioWPatch exposes helpers for this mapping. They can fail on some
        # Windows/driver combinations, so name matching below remains the final
        # compatibility path (also used by the project's official example).
        try:
            mapped = pa.get_wasapi_loopback_analogue_by_dict(output_info)
            if mapped is not None:
                return mapped
        except Exception:
            pass
        try:
            mapped = pa.get_wasapi_loopback_analogue_by_index(int(output_info["index"]))
            if mapped is not None:
                return mapped
        except Exception:
            pass

        loopbacks = loopbacks if loopbacks is not None else self._loopbacks(pa)
        wanted = self._device_name_key(output_info.get("name", ""))
        if not wanted:
            return None
        exact = [d for d in loopbacks if self._device_name_key(d.get("name", "")) == wanted]
        if exact:
            return exact[0]
        partial = [d for d in loopbacks if wanted in self._device_name_key(d.get("name", "")) or self._device_name_key(d.get("name", "")) in wanted]
        if partial:
            return partial[0]
        return None

    def _default_output_candidates(self, pa) -> list[dict]:
        """Return plausible Windows output endpoints without trusting one API call."""
        candidates: list[dict] = []
        seen: set[int] = set()

        def add(info) -> None:
            if not info or not self._is_output(info):
                return
            idx = int(info.get("index", -1))
            if idx >= 0 and idx not in seen:
                seen.add(idx)
                candidates.append(dict(info))

        # This mirrors PyAudioWPatch's official loopback example: obtain the
        # WASAPI host's defaultOutputDevice first. It avoids the helper failure
        # where get_default_wasapi_device() can unexpectedly return an input.
        try:
            for host in pa.get_host_api_info_generator():
                if "wasapi" not in str(host.get("name", "")).casefold():
                    continue
                idx = int(host.get("defaultOutputDevice", -1))
                if idx >= 0:
                    add(pa.get_device_info_by_index(idx))
        except Exception:
            pass

        try:
            add(pa.get_default_output_device_info())
        except Exception:
            pass

        try:
            add(pa.get_default_wasapi_device(d_out=True))
        except Exception:
            pass
        return candidates

    def _resolve_system_device(self, pa):
        loopbacks = self._loopbacks(pa)
        if not loopbacks:
            raise RuntimeError(
                "No WASAPI loopback capture devices were found. Run `python main.py devices` "
                "and verify that Windows has an enabled playback device."
            )

        for output in self._default_output_candidates(pa):
            mapped = self._match_loopback(pa, output, loopbacks)
            if mapped is not None:
                if self._device_name_key(mapped.get("name", "")) != self._device_name_key(output.get("name", "")):
                    self._resolution_note = f"WASAPI fallback matched output '{output.get('name', '')}' to loopback '{mapped.get('name', '')}'."
                return mapped

        # Some drivers expose only the synthetic loopback endpoint to PortAudio.
        # With a single loopback there is no ambiguity, so it is safe to use it.
        if len(loopbacks) == 1:
            self._resolution_note = "WASAPI default output was unavailable; using the only loopback device found."
            return loopbacks[0]

        choices = ", ".join(f"{int(d.get('index', -1))}: {d.get('name', '?')}" for d in loopbacks)
        raise RuntimeError(
            "Could not determine the default WASAPI loopback device. "
            f"Available loopbacks: {choices}. Run `python main.py devices` and set "
            "--system-device-index <IDX> (or audio.system_device_index in the config)."
        )

    @staticmethod
    def _looks_like_system_mix(name: str) -> bool:
        value = str(name).casefold()
        return any(token in value for token in (
            "stereo mix", "what u hear", "wave out mix", "stereo mixer",
        ))

    def _resolve_device(self, pa):
        if self.device_index is not None:
            info = pa.get_device_info_by_index(self.device_index)
            if self.source == "system":
                if self._is_loopback(info):
                    return info
                if not self._is_output(info):
                    raise RuntimeError(
                        f"Device {self.device_index} ('{info.get('name', '?')}') is not an output/loopback device. "
                        "Run `python main.py devices` and choose a device with LOOP=yes, or choose its speaker output device."
                    )
                mapped = self._match_loopback(pa, info)
                if mapped is None:
                    raise RuntimeError(
                        f"No WASAPI loopback analogue was found for device {self.device_index} ('{info.get('name', '?')}'). "
                        "Run `python main.py devices` and pass a device with LOOP=yes."
                    )
                return mapped
            # A loopback/Stereo Mix selected as microphone silently duplicates
            # all REMOTE sound into MIC; refuse it rather than fake user speech.
            name = str(info.get("name", "")).casefold()
            if self._is_loopback(info) or self._looks_like_system_mix(name):
                raise RuntimeError(
                    f"MIC device {self.device_index} ('{info.get('name', '?')}') is system audio, "
                    "not a microphone. Select a real microphone or use REMOTE-only."
                )
            return info
        if self.source == "system":
            return self._resolve_system_device(pa)
        info = pa.get_default_input_device_info()
        # Do not silently use Windows "Stereo Mix"/"What U Hear" as the YOU
        # microphone. Those devices are render-loopback sources and duplicate the
        # REMOTE lane, which produces phantom Vietnamese ASR while the user is mute.
        name = str(info.get("name", "")).casefold()
        if self._is_loopback(info) or self._looks_like_system_mix(name):
            candidates = []
            try:
                for d in pa.get_device_info_generator():
                    d = dict(d)
                    dname = str(d.get("name", "")).casefold()
                    if int(d.get("maxInputChannels", 0) or 0) <= 0:
                        continue
                    if self._is_loopback(d) or self._looks_like_system_mix(dname):
                        continue
                    score = 0
                    if "microphone" in dname or "mic" in dname:
                        score += 4
                    if "array" in dname:
                        score += 2
                    candidates.append((score, int(d.get("index", -1)), d))
            except Exception:
                candidates = []
            if candidates:
                candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
                selected = candidates[0][2]
                self._resolution_note = (
                    f"Default input '{info.get('name', '?')}' looks like Stereo Mix; "
                    f"using physical input '{selected.get('name', '?')}' instead."
                )
                return selected
            raise RuntimeError(
                f"Default recording device ('{info.get('name', '?')}') captures system sound. "
                "Select a physical microphone or choose REMOTE-only."
            )
        return info

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            if not self._stop.is_set():
                return
            # Switching back very quickly must not pretend that a closing
            # PortAudio stream has already restarted.
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                raise RuntimeError("Previous audio capture has not stopped; retry switching source")
        self._stop.clear()
        self._error = None
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="audio-capture", daemon=True)
        self._thread.start()
        # Fail during pipeline startup instead of surfacing later from the audio
        # worker thread. This makes device/config errors actionable and ensures
        # partially-started meeting workers can be shut down cleanly.
        self._ready.wait(timeout=5.0)
        if self._error is not None:
            error, self._error = self._error, None
            raise RuntimeError(f"Audio capture failed: {error}") from error
        if not self._ready.is_set():
            raise RuntimeError("Audio device did not become ready within 5 seconds")

    def _run(self) -> None:
        boost_current_audio_thread_priority()
        pyaudio = _import_pyaudio()
        try:
            with pyaudio.PyAudio() as pa:
                info = self._resolve_device(pa)
                native_rate = int(float(info["defaultSampleRate"]))
                channels = int(info.get("maxInputChannels", 0))
                if channels <= 0:
                    raise RuntimeError(f"Selected device has no input channels: {info['name']}")
                channels = min(channels, 2)
                resampler = create_streaming_resampler(
                    native_rate,
                    self.sample_rate,
                    backend=self.resampler_backend,
                    quality=self.resampler_quality,
                )
                actual_backend = (
                    "linear" if isinstance(resampler, StreamingLinearResampler) else "soxr"
                )
                actual_channel_mode = self.channel_mode.lower().strip()
                if actual_channel_mode == "auto":
                    # Keep laptop microphone-array channel selection stable.  The old
                    # "loudest" mode re-selected a channel every ~20 ms buffer, which
                    # can splice two microphone elements together and hurt ASR.
                    actual_channel_mode = "mic_speech" if self.source == "mic" and channels >= 2 else ("stereo_speech" if channels >= 2 else "mean")
                stereo_downmixer = (
                    _AdaptiveStereoSpeechDownmixer(
                        native_rate,
                        hold_chunks=14 if actual_channel_mode == "mic_speech" else 6,
                    )
                    if actual_channel_mode in {"mic_speech", "stereo_speech"} and channels >= 2
                    else None
                )
                self._info_line = (
                    f"Audio device : {info['name']} | {native_rate} Hz -> {self.sample_rate} Hz "
                    f"| {channels} ch | downmix={actual_channel_mode} | resampler={actual_backend}"
                )
                if self._resolution_note:
                    print(f"Audio select : {self._resolution_note}")
                print(self._info_line)

                with pa.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=native_rate,
                    input=True,
                    input_device_index=int(info["index"]),
                    frames_per_buffer=self.frames_per_buffer,
                ) as stream:
                    self._ready.set()
                    while not self._stop.is_set():
                        raw = stream.read(self.frames_per_buffer, exception_on_overflow=False)
                        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                        if stereo_downmixer is not None:
                            pcm = stereo_downmixer.process(pcm, channels)
                        else:
                            pcm = _downmix(pcm, channels, actual_channel_mode)
                        # Remove a small DC offset. This is cheap and avoids wasting
                        # dynamic range on some laptop microphone drivers.
                        if pcm.size:
                            pcm = pcm - np.mean(pcm, dtype=np.float32)
                        out = resampler.push(pcm)
                        if out.size:
                            try:
                                self._queue.put(out, timeout=0.05)
                            except queue.Full:
                                # Realtime policy: discard oldest audio rather than
                                # allowing latency/RAM to grow without bound.
                                try:
                                    self._queue.get_nowait()
                                    self.dropped_chunks += 1
                                except queue.Empty:
                                    pass
                                try:
                                    self._queue.put_nowait(out)
                                except queue.Full:
                                    self.dropped_chunks += 1
        except Exception as exc:
            self._error = exc
            self._ready.set()

    def pending_chunks(self) -> int:
        return int(self._queue.qsize())

    def read(self, timeout: float = 0.5) -> np.ndarray | None:
        if self._error is not None:
            error, self._error = self._error, None
            raise RuntimeError(f"Audio capture failed: {error}") from error
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
