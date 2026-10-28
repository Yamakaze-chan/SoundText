from __future__ import annotations

from difflib import SequenceMatcher
import re
import shutil
import threading

from app.asr.worker import ASRResult
from app.storage.session_store import SessionStore, TranscriptEntry


class TranscriptManager:
    def __init__(self, store: SessionStore, *, show_metrics: bool = True,
                 show_live: bool = True, live_max_chars: int = 180,
                 live_show_metrics: bool = False, meeting_mode: bool = True,
                 show_language: bool = True) -> None:
        self.store = store
        self.show_metrics = show_metrics
        self.show_live = show_live
        self.live_max_chars = max(40, int(live_max_chars))
        self.live_show_metrics = bool(live_show_metrics)
        self.meeting_mode = meeting_mode
        self.show_language = bool(show_language)
        self._lock = threading.Lock()
        self._finalized_ids: set[tuple[str, int]] = set()
        self._last_partial_width = 0
        self._last_final: dict[str, tuple[str, float, str | None]] = {}
        self.cleaner = store.cleaner

    @staticmethod
    def _label(source: str) -> str:
        if source == "mic":
            return "YOU"
        if source == "system":
            return "REMOTE"
        return source.upper()

    @staticmethod
    def _normalize_token(token: str) -> str:
        return re.sub(r"[^\wÀ-ỹĐđ]+", "", token.lower(), flags=re.UNICODE)

    @classmethod
    def _dedupe_leading_overlap(cls, previous: str, current: str) -> str:
        prev_raw = previous.split()
        cur_raw = current.split()
        if not prev_raw or not cur_raw:
            return current
        prev_norm = [cls._normalize_token(t) for t in prev_raw]
        cur_norm = [cls._normalize_token(t) for t in cur_raw]
        best: tuple[float, int] | None = None
        for p_count in range(1, min(10, len(prev_norm)) + 1):
            a = " ".join(t for t in prev_norm[-p_count:] if t)
            if len(a) < 8:
                continue
            for c_count in range(1, min(10, len(cur_norm)) + 1):
                b = " ".join(t for t in cur_norm[:c_count] if t)
                if len(b) < 8:
                    continue
                length_balance = min(len(a), len(b)) / max(len(a), len(b))
                if length_balance < 0.62:
                    continue
                ratio = SequenceMatcher(None, a, b).ratio()
                score = ratio * (0.75 + 0.25 * length_balance)
                if ratio >= 0.84 and (best is None or score > best[0]):
                    best = (score, c_count)
        if best is None:
            return current
        trimmed = " ".join(cur_raw[best[1]:]).strip()
        return trimmed or current

    def _clear_live_line(self) -> None:
        if self._last_partial_width:
            width = min(self._last_partial_width, shutil.get_terminal_size((120, 20)).columns - 1)
            print("\r" + " " * max(1, width) + "\r", end="", flush=True)
            self._last_partial_width = 0

    def _live_line(self, result: ASRResult, text: str) -> str:
        label = f":{self._label(result.source)}" if self.meeting_mode else ""
        lang = f":{result.language.upper()}" if self.show_language and result.language else ""
        metric = f" [RTF {result.rtf:.2f}]" if self.live_show_metrics else ""
        prefix = f"[live{label}{lang}] "
        cols = max(60, shutil.get_terminal_size((120, 20)).columns)
        budget = min(self.live_max_chars, max(32, cols - len(prefix) - len(metric) - 2))
        if len(text) > budget:
            text = "…" + text[-(budget - 1):]
        return f"{prefix}{text}{metric}"

    def handle(self, result: ASRResult) -> TranscriptEntry | None:
        text = result.text.strip()
        if not text:
            return None
        key = (result.source, result.utterance_id)
        with self._lock:
            if result.kind == "partial":
                if key in self._finalized_ids:
                    return None
                shown = self.cleaner.normalize_segment(text, result.language, terminal=False) or text
                if self.show_live:
                    display = self._live_line(result, shown)
                    self._clear_live_line()
                    print("\r" + display, end="", flush=True)
                    self._last_partial_width = len(display)
                return None

            self._finalized_ids.add(key)
            self._clear_live_line()
            raw_text = text
            start_s = result.start_s
            if result.leading_overlap_s > 0:
                prior = self._last_final.get(result.source)
                if prior is not None:
                    prior_text, prior_end, prior_speaker = prior
                    if prior_speaker == result.speaker_id and not result.speaker_overlap:
                        text = self._dedupe_leading_overlap(prior_text, text)
                    start_s = max(result.start_s + result.leading_overlap_s, prior_end - 0.05)

            text = self.cleaner.normalize_segment(text, result.language, terminal=False) or text
            metric = (
                f"  [ASR-{result.model_role} {result.inference_s:.2f}s / {result.audio_s:.1f}s, RTF {result.rtf:.2f}]"
                if self.show_metrics else ""
            )
            label = f"[{self._label(result.source):6}] " if self.meeting_mode else ""
            lang_label = f"[{result.language.upper():4}] " if self.show_language and result.language else ""
            speaker = f" [{result.speaker_id}]" if result.speaker_id else ""
            overlap = " [OVERLAP]" if result.speaker_overlap else ""
            print(f"[{start_s:8.2f}s] {label}{lang_label}{speaker}{overlap} {text}{metric}")
            entry = TranscriptEntry(
                utterance_id=result.utterance_id,
                start_s=start_s,
                end_s=result.end_s,
                text=(f"[{self._label(result.source)}] {text}" if self.meeting_mode else text),
                language=result.language,
                source=result.source,
                source_text=text,
                raw_text=raw_text,
                speaker_id=result.speaker_id,
                speaker_overlap=result.speaker_overlap,
            )
            self.store.append(entry)
            self._last_final[result.source] = (text, result.end_s, result.speaker_id)
            return entry
