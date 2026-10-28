"""Experimental dual-lane speaker diarization using local ONNX models.

Only speech *segments* are attributed; mixed overlapping voices cannot be
separated or independently transcribed.  All inference runs outside capture.
The audio passed to this module is mono, 16-kHz float PCM from VAD finals.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(slots=True)
class SpeakerSpan:
    start_s: float
    end_s: float
    speaker_id: str | None = None
    overlap: bool = False


# pyannote powerset classes are sorted by cardinality, then speaker index:
# none, {0}, {1}, {2}, {0,1}, {0,2}, {1,2}.
POWERSET = ((), (0,), (1,), (2,), (0, 1), (0, 2), (1, 2))


def speaker_spans_from_scores(
    scores: np.ndarray,
    *,
    duration_s: float,
    min_turn_s: float = 0.50,
    frame_offset_s: float = 0.0,
) -> list[tuple[float, float, int | None, bool]]:
    """Extract conservative local-speaker spans from pyannote powerset logits.

    This is *not* text alignment. Speaker changes are only accepted if the
    adjacent speaker runs are sufficiently long. Returns (start,end,local_id,
    overlap) with each span covering continuous speech, including overlaps.
    """
    x = np.asarray(scores)
    if x.ndim == 3:
        x = x[0]
    if x.ndim != 2 or x.shape[1] != len(POWERSET) or x.shape[0] == 0:
        return [(0.0, max(0, duration_s), None, False)]
    frame_s = max(0, duration_s) / x.shape[0]
    frames = np.argmax(x, axis=1).astype(np.int32)
    labels = np.array([int(POWERSET[i][0]) if len(POWERSET[i]) == 1 else (-2 if len(POWERSET[i]) == 2 else -1) for i in frames], dtype=np.int32)

    # Suppress transient changes (< ~350 ms) before accepting new speaker.
    min_frames = max(1, round(0.35 / max(frame_s, 1e-4)))
    runs = []
    st = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[st]:
            runs.append([st, i, int(labels[st])])
            st = i
    for idx in range(1, len(runs) - 1):
        start, end, value = runs[idx]
        # A short, real overlap can be exactly the interruption the user wants
        # to see. Do not smooth away overlap into a single speaker. A few noisy
        # frames are still suppressed by the minimum overlap detection below.
        if (value != -2 and end - start < min_frames
                and runs[idx - 1][2] == runs[idx + 1][2]):
            labels[start:end] = runs[idx - 1][2]

    # Merge silence with neighbouring turns instead of losing ASR audio.
    real = labels.copy()
    last = None
    for i, value in enumerate(real):
        if value >= 0:
            last = value
        elif value == -1 and last is not None:
            real[i] = last
    last = None
    for i in range(len(real) - 1, -1, -1):
        if real[i] >= 0:
            last = int(real[i])
        elif real[i] == -1 and last is not None:
            real[i] = last

    runs = []
    st = 0
    for i in range(1, len(real) + 1):
        if i == len(real) or real[i] != real[st]:
            runs.append([st, i, int(real[st])])
            st = i

    # Overlap is represented as a marked span, not falsely assigned to A/B.
    result: list[tuple[float, float, int | None, bool]] = []
    for s, e, lab in runs:
        a, b = max(0, s * frame_s + frame_offset_s), min(duration_s, e * frame_s + frame_offset_s)
        if b <= a:
            continue
        result.append((a, b, lab if lab >= 0 else None, lab == -2))

    # Merge short uncertain turns into adjacent segments, prioritizing recall of
    # audio and avoiding floods of 100-ms ASR requests on short interruptions.
    merged: list[tuple[float, float, int | None, bool]] = []
    for a, b, lab, ov in result:
        if merged and (b - a < min_turn_s or lab == merged[-1][2]) and not ov and not merged[-1][3]:
            pa, _, pl, _ = merged[-1]
            merged[-1] = (pa, b, pl if b - a < min_turn_s else lab, False)
        else:
            merged.append((a, b, lab, ov))
    return merged or [(0.0, max(0, duration_s), None, False)]


class SpeakerDiarizer:
    """pyannote segmentation + raw-waveform ReDimNet speaker embeddings.

    Models live at configured ./models paths. Speaker IDs are namespaced to
    their capture lane; without independent tracks, a person speaking through
    two devices is NOT automatically unified.
    """

    def __init__(
        self,
        segmentation_path: str,
        embedding_path: str,
        *,
        sample_rate: int = 16000,
        threads: int = 1,
        similarity_threshold: float = 0.60,
        min_turn_s: float = 0.50,
    ) -> None:
        import onnxruntime as ort

        if sample_rate != 16000:
            raise ValueError('Speaker models require 16-kHz audio')
        for path in (segmentation_path, embedding_path):
            if not Path(path).is_file():
                raise FileNotFoundError(f'Speaker model missing: {path}')
        opt = ort.SessionOptions()
        opt.intra_op_num_threads = max(1, min(int(threads), 2))
        opt.inter_op_num_threads = 1
        self.seg = ort.InferenceSession(segmentation_path, sess_options=opt, providers=['CPUExecutionProvider'])
        self.emb = ort.InferenceSession(embedding_path, sess_options=opt, providers=['CPUExecutionProvider'])
        self.seg_input = self.seg.get_inputs()[0].name
        self.emb_input = self.emb.get_inputs()[0]
        self.sample_rate = sample_rate
        self.similarity_threshold = similarity_threshold
        self.min_turn_s = min_turn_s
        # A weak match is NOT evidence of a new person. New speaker enrollment
        # requires a long, clean solo stretch and a clearly *different* voice.
        self.new_speaker_threshold = max(0.0, similarity_threshold - 0.16)
        self.min_enroll_s = 2.4
        self.min_match_s = 0.9
        self._speaker_centers: dict[str, list[np.ndarray]] = {'mic': [], 'system': []}
        self._speaker_hits: dict[str, list[int]] = {'mic': [], 'system': []}
        # A new voice must recur in a second sufficiently long solo sample
        # before creating another permanent speaker ID.
        self._pending_new: dict[str, np.ndarray | None] = {'mic': None, 'system': None}

    def _segment_scores(self, x: np.ndarray) -> np.ndarray:
        # 10-second input is supported by exported PyanNet. Pad short sections,
        # stitch long ones with an 8-second stride (2 seconds of overlap).
        window = 10 * self.sample_rate
        stride = 8 * self.sample_rate
        if len(x) <= window:
            audio = np.pad(x, (0, window - len(x)))
            return self.seg.run(None, {self.seg_input: audio[None, None, :]})[0][0]
        chunks: list[np.ndarray] = []
        for offset in range(0, len(x), stride):
            part = x[offset:offset + window]
            if len(part) < self.sample_rate:
                break
            padded = np.pad(part, (0, max(0, window - len(part))))
            scores = self.seg.run(None, {self.seg_input: padded[None, None, :]})[0][0]
            # For each window use up to the first 8 s except the last one.
            usable = len(part) if offset + window >= len(x) else stride
            limit = max(1, round(scores.shape[0] * usable / window))
            chunks.append(scores[:limit])
        return np.concatenate(chunks, axis=0)

    def detect_interruption(self, audio: np.ndarray) -> float | None:
        """Locate a *candidate* mid-utterance speaker switch in recent audio.

        Segmentation labels are local to this window: only transitions inside the
        window matter. This probe never assigns a speaker identity, transcribes,
        or claims to separate overlapping voices. Conservative timing guards
        keep music/noise flicker from generating 100-ms ASR fragments.
        """
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        dur = len(x) / self.sample_rate
        if dur < 2.5:
            return None
        scores = self._segment_scores(x)
        if dur < 10.0:
            frames = max(1, min(len(scores), round(len(scores) * dur / 10.0)))
            scores = scores[:frames]
        spans = speaker_spans_from_scores(scores, duration_s=dur, min_turn_s=0.50)
        # An overlap onset alone does NOT prove a new speaker has taken over.
        # Cutting a mixed region used to create 0.2s ASR finals (".", "Y.") and
        # more false speaker identities. Keep it intact unless there is a
        # sustained, non-overlapping A -> B switch below.
        speech = [(a, b, sid) for a, b, sid, overlap in spans
                  if sid is not None and not overlap and b - a >= 0.45]
        if len(speech) < 2:
            return None
        a, b, new_id = speech[-1]
        preceding = [(pa, pb, ps) for pa, pb, ps in speech[:-1]
                     if ps != new_id and pb - pa >= 0.65]
        if not preceding:
            return None
        # Last confident different speaker must be near the new turn; if
        # unrelated early speech was found, do not infer a switch.
        pa, pb, _ = preceding[-1]
        if a - pb > 1.3:
            return None
        if b < dur - 0.22 or b - a < 0.58:
            return None
        if a < 0.90 or dur - a < 0.55:
            return None
        return float(a)

    def _embed(self, x: np.ndarray) -> np.ndarray | None:
        if x.size < self.sample_rate:
            return None
        shape = self.emb_input.shape
        # ReDimNet ONNX with built-in Mel frontend accepts raw [B, T] PCM.
        if len(shape) == 2:
            feed = x[None, :]
        elif len(shape) == 3:
            feed = x[None, None, :]
        else:
            return None
        value = np.asarray(self.emb.run(None, {self.emb_input.name: feed})[0], dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(value))
        if not np.isfinite(norm) or norm <= 1e-8:
            return None
        return value / norm

    def _identify(self, source: str, audio: np.ndarray) -> str | None:
        """Conservative speaker clustering, avoiding one-off speaker IDs.

        Short or uncertain speech never seeds a new identity. Existing speakers
        are matched without updating their prototypes on weak evidence, so an
        overlap/noisy snippet cannot corrupt a known voice's centroid.
        """
        duration = len(audio) / self.sample_rate
        if duration < self.min_match_s:
            return None
        vector = self._embed(audio)
        if vector is None:
            return None
        centers = self._speaker_centers[source]
        hits = self._speaker_hits[source]
        if centers:
            scores = np.array([float(np.dot(center, vector)) for center in centers])
            idx = int(np.argmax(scores))
            best = float(scores[idx])
            if best >= self.similarity_threshold:
                # Only update centroids from solid, reasonably long matches;
                # speculative short samples still reuse the existing identity.
                if duration >= self.min_enroll_s and best >= self.similarity_threshold + 0.08:
                    n = hits[idx]
                    v = (centers[idx] * min(n, 15) + vector) / (min(n, 15) + 1)
                    centers[idx] = v / max(1e-8, float(np.linalg.norm(v)))
                    hits[idx] += 1
                self._pending_new[source] = None
                return f'S{idx + 1}'
            # Gray zone: don't label as known OR create a new person.
            if best > self.new_speaker_threshold:
                self._pending_new[source] = None
                return None
        if duration < (self.min_enroll_s if centers else 1.6):
            return None
        if len(centers) >= 12:
            return None
        if centers:
            pending = self._pending_new[source]
            if pending is None or float(np.dot(pending, vector)) < 0.70:
                self._pending_new[source] = vector.copy()
                return None
            # Two independent confident long samples of the same new voice.
            self._pending_new[source] = None
        centers.append(vector)
        hits.append(1)
        return f'S{len(centers)}'

    def process(self, source: str, audio: np.ndarray) -> list[tuple[np.ndarray, float, float, str | None, bool]]:
        """Return bounded splits with lane-local speaker labels and overlap flags.

        start/end are seconds relative to audio; a minimum turn length and
        disabled cutting within overlap prevent spurious tiny ASR chunks.
        """
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        dur = len(x) / self.sample_rate
        if dur < self.min_turn_s:
            return [(x, 0.0, dur, None, False)]
        scores = self._segment_scores(x)
        # Scores may include padded tail. Crop by original duration.
        if dur < 10:
            frames = max(1, min(len(scores), int(round(len(scores) * dur / 10.0))))
            scores = scores[:frames]
        spans = speaker_spans_from_scores(scores, duration_s=dur, min_turn_s=self.min_turn_s)
        # Inference is intentionally conservative: an overlap region is a
        # property of a speech turn, not an independent 200ms sentence. Split
        # only if there are clearly different *solo* runs on both sides,
        # each long enough for standalone ASR. This preserves all PCM samples.
        reliable = [(a, b, sid) for a, b, sid, ov in spans
                    if sid is not None and not ov and b - a >= 1.10]
        cuts: list[float] = []
        for previous, current in zip(reliable, reliable[1:]):
            pa, pb, psid = previous
            ca, cb, csid = current
            if psid == csid or ca - pb > 0.85:
                continue
            # If there is overlap between solo turns, attach the mixed speech
            # to the preceding one rather than creating a standalone ASR job.
            boundary = ca
            if boundary < 1.2 or dur - boundary < 1.2:
                continue
            if cuts and boundary - cuts[-1] < 1.2:
                continue
            cuts.append(boundary)
            if len(cuts) >= 3:
                break
        boundaries = [0.0, *cuts, dur]
        result: list[tuple[np.ndarray, float, float, str | None, bool]] = []
        for start, end in zip(boundaries, boundaries[1:]):
            a = max(0, int(round(start * self.sample_rate)))
            b = min(len(x), int(round(end * self.sample_rate)))
            if b <= a:
                continue
            chunk = x[a:b]
            # Any mixed section is carried with the neighboring ASR chunk; it
            # does not get speaker-embedded or isolated as a tiny fragment.
            overlapping = any(ov and min(sb, end) - max(sa, start) >= 0.35
                              for sa, sb, _sid, ov in spans)
            clean_by_local: dict[int, float] = {}
            for sa, sb, sid, ov in spans:
                if not ov and sid is not None and sb > start and sa < end:
                    clean_by_local[sid] = clean_by_local.get(sid, 0.0) + (min(sb, end) - max(sa, start))
            # Accumulate uninterrupted parts of the same voice across an
            # overlap; don't mislabel a mostly-solo turn as Unknown.
            sid = max(clean_by_local, key=clean_by_local.get) if clean_by_local else None
            clean_s = clean_by_local.get(sid, 0.0) if sid is not None else 0.0
            if sid is not None and clean_s >= 1.25 and clean_s >= 0.55 * (end - start):
                # Embed only the longest uncontaminated solo interval.
                matching = [(sa, sb) for sa, sb, local, ov in spans
                            if local == sid and not ov and sb > start and sa < end]
                best_a, best_b = max(matching,
                                     key=lambda pair: min(pair[1], end) - max(pair[0], start))
                ia = max(a, int(round(best_a * self.sample_rate)))
                ib = min(b, int(round(best_b * self.sample_rate)))
                # Bound CPU cost for long turns. A central clean 4s sample is
                # enough to match a prototype; avoid embedding full 12s turns.
                if ib - ia > self.sample_rate * 4:
                    middle = (ia + ib) // 2
                    ia = middle - self.sample_rate * 2
                    ib = ia + self.sample_rate * 4
                speaker = self._identify(source, x[ia:ib]) if ib > ia else None
            else:
                speaker = None
            result.append((chunk, a / self.sample_rate, b / self.sample_rate, speaker, overlapping))
        return result or [(x, 0.0, dur, None, False)]
