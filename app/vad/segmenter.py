from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal
import threading

import numpy as np

from .silero_onnx import SileroOnnxVad


@dataclass(slots=True)
class SpeechEvent:
    kind: Literal["partial", "final"]
    utterance_id: int
    audio: np.ndarray
    start_s: float
    end_s: float
    leading_overlap_s: float = 0.0


class StreamingVadSegmenter:
    FRAME_SAMPLES = 512

    def __init__(
        self,
        vad: SileroOnnxVad,
        sample_rate: int = 16000,
        threshold: float = 0.45,
        negative_threshold: float = 0.30,
        min_silence_ms: int = 600,
        pre_roll_ms: int = 450,
        post_roll_ms: int = 220,
        min_speech_ms: int = 300,
        partial_interval_ms: int = 2400,
        partial_window_s: float = 7.0,
        max_utterance_s: float = 12.0,
        hard_split_overlap_ms: int = 1200,
    ) -> None:
        self.vad = vad
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.negative_threshold = negative_threshold
        self.min_silence_samples = int(sample_rate * min_silence_ms / 1000)
        self.post_roll_samples = int(sample_rate * post_roll_ms / 1000)
        self.min_speech_samples = int(sample_rate * min_speech_ms / 1000)
        self.partial_interval_samples = int(sample_rate * partial_interval_ms / 1000)
        self.partial_window_samples = int(sample_rate * partial_window_s)
        self.max_utterance_samples = int(sample_rate * max_utterance_s)
        self.hard_split_overlap_samples = int(sample_rate * hard_split_overlap_ms / 1000)

        self._normal_pre_frames = max(
            0, int(np.ceil(sample_rate * pre_roll_ms / 1000 / self.FRAME_SAMPLES))
        )
        self._hard_overlap_frames = max(
            0, int(np.ceil(self.hard_split_overlap_samples / self.FRAME_SAMPLES))
        )
        history_frames = max(1, self._normal_pre_frames, self._hard_overlap_frames)
        self._pre_roll: deque[np.ndarray] = deque(maxlen=history_frames)

        self._pending = np.empty(0, dtype=np.float32)
        self._active: list[np.ndarray] = []
        self._triggered = False
        self._silence_samples = 0
        self._stream_pos = 0
        self._start_sample = 0
        self._last_partial_len = 0
        self._utterance_id = 0
        self._speaker_split_lock = threading.Lock()
        self._speaker_split_request: tuple[int, int] | None = None

        # A hard split cuts continuous speech only to keep ASR chunks bounded.
        # Keep ~1.2 s of tail audio for the next chunk, then de-duplicate text
        # downstream. This prevents words at the exact split point from being cut.
        self._forced_carry_pending = False
        self._forced_idle_samples = 0
        self._current_leading_overlap_samples = 0

    def request_speaker_split(self, utterance_id: int, boundary_s: float) -> None:
        """Only queue a hint; capture thread owns the active audio buffers."""
        with self._speaker_split_lock:
            self._speaker_split_request = (int(utterance_id), int(round(boundary_s * self.sample_rate)))

    def _apply_speaker_split(self) -> SpeechEvent | None:
        with self._speaker_split_lock:
            request = self._speaker_split_request
        if request is None or not self._triggered:
            return None
        expected_id, boundary_sample = request
        if expected_id != self._utterance_id:
            with self._speaker_split_lock:
                if self._speaker_split_request == request:
                    self._speaker_split_request = None
            return None
        offset = (boundary_sample - self._start_sample) // self.FRAME_SAMPLES * self.FRAME_SAMPLES
        active_size = len(self._active) * self.FRAME_SAMPLES
        if offset < max(self.sample_rate, self.min_speech_samples):
            return None
        # Require enough subsequent audio to confirm a real interruption.
        if active_size - offset < int(self.sample_rate * 0.40):
            return None
        with self._speaker_split_lock:
            if self._speaker_split_request != request:
                return None
            self._speaker_split_request = None
        audio = np.concatenate(self._active)
        cut = max(1, min(audio.size - 1, offset))
        finalized = self._make_audio_event('final', audio[:cut], self._start_sample)
        # Preserve a short acoustic bridge to avoid chopping off consonants.
        carry = min(5 * self.FRAME_SAMPLES, cut)
        start = cut - carry
        tail = audio[start:]
        self._active = [tail[i:i + self.FRAME_SAMPLES].copy()
                        for i in range(0, tail.size, self.FRAME_SAMPLES)]
        # Both cut and carry are frame-aligned; do not duplicate/pad audio.
        self._start_sample += start
        self._utterance_id += 1
        self._current_leading_overlap_samples = carry
        self._last_partial_len = 0
        self._silence_samples = 0
        print(f'\n[SPEAKER CUT] Possible voice/overlap boundary at {boundary_sample / self.sample_rate:.2f}s')
        return finalized

    @property
    def active_utterance_id(self) -> int | None:
        return self._utterance_id if self._triggered else None

    @property
    def is_triggered(self) -> bool:
        return bool(self._triggered)

    def push(self, samples: np.ndarray) -> list[SpeechEvent]:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return []
        self._pending = np.concatenate((self._pending, x))
        events: list[SpeechEvent] = []

        while self._pending.size >= self.FRAME_SAMPLES:
            frame = self._pending[: self.FRAME_SAMPLES]
            self._pending = self._pending[self.FRAME_SAMPLES :]
            events.extend(self._process_frame(frame))
        return events

    def _process_frame(self, frame: np.ndarray) -> list[SpeechEvent]:
        events: list[SpeechEvent] = []
        frame_start = self._stream_pos
        self._stream_pos += self.FRAME_SAMPLES
        prob = self.vad.probability(frame)

        if not self._triggered:
            if prob >= self.threshold:
                self._triggered = True
                self._utterance_id += 1

                if self._forced_carry_pending:
                    n = self._hard_overlap_frames
                    previous = list(self._pre_roll)[-n:] if n else []
                    self._current_leading_overlap_samples = len(previous) * self.FRAME_SAMPLES
                else:
                    n = self._normal_pre_frames
                    previous = list(self._pre_roll)[-n:] if n else []
                    self._current_leading_overlap_samples = 0

                self._active = previous + [frame.copy()]
                self._start_sample = max(0, frame_start - len(previous) * self.FRAME_SAMPLES)
                self._silence_samples = 0
                self._last_partial_len = 0
                self._pre_roll.clear()
                self._forced_carry_pending = False
                self._forced_idle_samples = 0
            else:
                self._pre_roll.append(frame.copy())
                if self._forced_carry_pending:
                    self._forced_idle_samples += self.FRAME_SAMPLES
                    # If a genuine silence lasts longer than the carry window,
                    # this is no longer the continuation of the forced split.
                    if self._forced_idle_samples >= self.hard_split_overlap_samples:
                        self._forced_carry_pending = False
                        self._forced_idle_samples = 0
            return events

        self._active.append(frame.copy())
        speaker_final = self._apply_speaker_split()
        if speaker_final is not None:
            events.append(speaker_final)
            return events
        if prob < self.negative_threshold:
            self._silence_samples += self.FRAME_SAMPLES
        elif prob >= self.threshold:
            self._silence_samples = 0

        active_len = len(self._active) * self.FRAME_SAMPLES
        speechish_len = max(0, active_len - self._silence_samples)

        if (
            speechish_len >= self.min_speech_samples
            and active_len - self._last_partial_len >= self.partial_interval_samples
        ):
            self._last_partial_len = active_len
            events.append(self._make_partial_event())

        if self._silence_samples >= self.min_silence_samples:
            keep_silence = min(self._silence_samples, self.post_roll_samples)
            trim = self._silence_samples - keep_silence
            audio = np.concatenate(self._active)
            if trim > 0 and trim < audio.size:
                audio = audio[:-trim]
            if audio.size >= self.min_speech_samples:
                events.append(self._make_audio_event("final", audio, self._start_sample))
            self._reset_utterance(clear_history=True)

        elif active_len >= self.max_utterance_samples:
            audio = np.concatenate(self._active)
            events.append(self._make_audio_event("final", audio, self._start_sample))

            # Keep more overlap than ordinary pre-roll at a forced split. A news
            # reader or fast meeting speaker can otherwise have a word cut exactly
            # at the boundary, which hurts Vietnamese recognition disproportionately.
            tail_frames = (
                list(self._active[-self._hard_overlap_frames :])
                if self._hard_overlap_frames
                else []
            )
            self._reset_utterance(clear_history=True)
            self._pre_roll.extend(frame.copy() for frame in tail_frames)
            self._forced_carry_pending = bool(tail_frames)
            self._forced_idle_samples = 0

        return events

    def _make_partial_event(self) -> SpeechEvent:
        audio = np.concatenate(self._active)
        if self.partial_window_samples > 0 and audio.size > self.partial_window_samples:
            offset = audio.size - self.partial_window_samples
            audio = audio[offset:]
            start_sample = self._start_sample + offset
            # A bounded live-preview tail should not participate in final overlap
            # de-duplication; it is never stored as transcript.
            overlap_samples = 0
        else:
            start_sample = self._start_sample
            overlap_samples = self._current_leading_overlap_samples
        return self._make_audio_event(
            "partial", audio, start_sample, leading_overlap_samples=overlap_samples
        )

    def _make_audio_event(
        self,
        kind: Literal["partial", "final"],
        audio: np.ndarray,
        start_sample: int,
        leading_overlap_samples: int | None = None,
    ) -> SpeechEvent:
        if leading_overlap_samples is None:
            leading_overlap_samples = self._current_leading_overlap_samples
        return SpeechEvent(
            kind=kind,
            utterance_id=self._utterance_id,
            audio=audio.astype(np.float32, copy=True),
            start_s=start_sample / self.sample_rate,
            end_s=(start_sample + audio.size) / self.sample_rate,
            leading_overlap_s=leading_overlap_samples / self.sample_rate,
        )

    def discard(self) -> None:
        """Drop any in-progress utterance without emitting a final event.

        Used when the operator disables an audio lane from the desktop UI.
        """
        self._pending = np.empty(0, dtype=np.float32)
        self._reset_utterance(clear_history=True)
        self._forced_carry_pending = False
        self._forced_idle_samples = 0
        with self._speaker_split_lock:
            self._speaker_split_request = None

    def flush(self) -> SpeechEvent | None:
        if not self._triggered or not self._active:
            return None
        audio = np.concatenate(self._active)
        if audio.size < self.min_speech_samples:
            self._reset_utterance(clear_history=True)
            return None
        event = self._make_audio_event("final", audio, self._start_sample)
        self._reset_utterance(clear_history=True)
        return event

    def _reset_utterance(self, clear_history: bool) -> None:
        self._active = []
        self._triggered = False
        self._silence_samples = 0
        self._last_partial_len = 0
        self._current_leading_overlap_samples = 0
        if clear_history:
            self._pre_roll.clear()
        with self._speaker_split_lock:
            self._speaker_split_request = None
