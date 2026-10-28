from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from app.postprocess.text_cleaner import TextCleaner, build_clean_turns
from app.reliability.journal import SessionJournal


@dataclass(slots=True)
class TranscriptEntry:
    utterance_id: int
    start_s: float
    end_s: float
    text: str
    language: str | None = None
    source: str | None = None
    source_text: str | None = None
    raw_text: str | None = None
    speaker_id: str | None = None
    speaker_overlap: bool = False


@dataclass(slots=True)
class TranslationEntry:
    utterance_id: int
    source: str
    source_language: str
    target_language: str
    source_text: str
    translated_text: str
    inference_s: float = 0.0


def _clock(seconds: float, srt: bool = False) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    sep = "," if srt else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{milli:03d}"


def _label(source: str | None) -> str:
    if source == "mic":
        return "YOU"
    if source == "system":
        return "REMOTE"
    return (source or "").upper()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


class SessionStore:
    """Append-first long-session store for source and translated meeting text."""

    def __init__(self, root: str | Path, *, postprocess: Any | None = None,
                 reliability: Any | None = None, session_dir: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._dirty_events = 0
        self._last_refresh = time.monotonic()
        self._last_checkpoint = 0.0

        rel = reliability
        self.reliability_enabled = bool(getattr(rel, "enabled", True)) if rel is not None else True
        self.refresh_interval_s = max(0.25, float(getattr(rel, "artifact_refresh_interval_s", 600.0))) if rel is not None else 600.0
        self.refresh_events = max(1, int(getattr(rel, "artifact_refresh_events", 128))) if rel is not None else 128
        self.checkpoint_interval_s = max(0.5, float(getattr(rel, "checkpoint_interval_s", 10.0))) if rel is not None else 10.0
        self.atomic_artifacts = bool(getattr(rel, "atomic_artifacts", True)) if rel is not None else True
        fsync_interval = float(getattr(rel, "journal_fsync_interval_s", 2.0)) if rel is not None else 2.0

        if session_dir:
            self.session_dir = Path(session_dir)
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.resumed = True
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.session_dir = Path(root) / f"meeting_{stamp}"
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.resumed = False

        self.txt_path = self.session_dir / "transcript.txt"
        self.jsonl_path = self.session_dir / "transcript.jsonl"
        self.srt_path = self.session_dir / "transcript.srt"
        self.raw_txt_path = self.session_dir / "raw_transcript.txt"
        self.raw_jsonl_path = self.session_dir / "raw_transcript.jsonl"
        self.raw_srt_path = self.session_dir / "raw_transcript.srt"
        self.translation_jsonl_path = self.session_dir / "translations.jsonl"
        self.bilingual_txt_path = self.session_dir / "bilingual_transcript.txt"
        # One obvious, non-technical TXT for everyday use. Existing session
        # artifacts remain unchanged for recovery, diagnostics and compatibility.
        self.readable_dir = self.session_dir / "Noi_dung"
        self.readable_txt_path = self.readable_dir / "Noi_dung_cuoc_hop.txt"
        self.readable_dir.mkdir(parents=True, exist_ok=True)
        if not self.readable_txt_path.exists():
            self.readable_txt_path.write_text(
                "NỘI DUNG CUỘC HỌP\n\nBản ghi sẽ được cập nhật sau khi nhấn Dừng & lưu.\n",
                encoding="utf-8",
            )
        self.entries: list[TranscriptEntry] = []
        self.translations: dict[tuple[str, int], TranslationEntry] = {}

        pp = postprocess
        self.postprocess_enabled = bool(getattr(pp, "enabled", True)) if pp is not None else True
        self.preserve_raw = bool(getattr(pp, "preserve_raw", True)) if pp is not None else True
        self.merge_gap_s = float(getattr(pp, "merge_gap_s", 1.25)) if pp is not None else 1.25
        self.max_turn_s = float(getattr(pp, "max_turn_s", 45.0)) if pp is not None else 45.0
        self.max_turn_chars = int(getattr(pp, "max_turn_chars", 900)) if pp is not None else 900
        if self.postprocess_enabled:
            self.cleaner = TextCleaner(
                case_mode=str(getattr(pp, "case_mode", "sentence")) if pp is not None else "sentence",
                add_terminal_punctuation=bool(getattr(pp, "add_terminal_punctuation", True)) if pp is not None else True,
                collapse_repetitions=bool(getattr(pp, "collapse_repetitions", True)) if pp is not None else True,
            )
        else:
            self.cleaner = TextCleaner(case_mode="conservative", add_terminal_punctuation=False, collapse_repetitions=False)
            self.merge_gap_s = -1.0

        self.journal = SessionJournal(self.session_dir, fsync_interval_s=fsync_interval)
        if self.resumed:
            self._load_existing()
            self.journal.append("session_resumed", self._state_payload(), force=True)
            self.refresh(force=True)
        else:
            self.journal.append("session_started", self._state_payload(), force=True)
            self._checkpoint(force=True)

    @staticmethod
    def _raw_for(entry: TranscriptEntry) -> str:
        return (entry.raw_text or entry.source_text or entry.text or "").strip()

    def _load_existing(self) -> None:
        self.entries.clear()
        for row in _read_jsonl(self.jsonl_path):
            try:
                self.entries.append(TranscriptEntry(
                    utterance_id=int(row.get("utterance_id", 0)),
                    start_s=float(row.get("start_s", 0.0)),
                    end_s=float(row.get("end_s", 0.0)),
                    text=str(row.get("text", "")),
                    language=row.get("language"), source=row.get("source"),
                    source_text=row.get("source_text"), raw_text=row.get("raw_text"),
                    speaker_id=row.get("speaker_id"), speaker_overlap=bool(row.get("speaker_overlap", False)),
                ))
            except Exception:
                continue
        self.translations.clear()
        for row in _read_jsonl(self.translation_jsonl_path):
            try:
                item = TranslationEntry(
                    utterance_id=int(row.get("utterance_id", 0)),
                    source=str(row.get("source", "")),
                    source_language=str(row.get("source_language", "")),
                    target_language=str(row.get("target_language", "")),
                    source_text=str(row.get("source_text", "")),
                    translated_text=str(row.get("translated_text", "")),
                    inference_s=float(row.get("inference_s", 0.0)),
                )
                self.translations[(item.source, item.utterance_id)] = item
            except Exception:
                continue

    @property
    def timeline_end_s(self) -> float:
        with self._lock:
            return max((e.end_s for e in self.entries), default=0.0)

    def next_utterance_id(self, source: str) -> int:
        with self._lock:
            ids = [e.utterance_id for e in self.entries if e.source == source]
            return max(ids, default=-1) + 1

    def _state_payload(self) -> dict[str, Any]:
        return {
            "version": 16,
            "mode": "realtime_translation",
            "status": "closed" if self._closed else "running",
            "resumed": self.resumed,
            "transcript_entries": len(self.entries),
            "translation_entries": len(self.translations),
            "timeline_end_s": self.timeline_end_s,
            "session_dir": str(self.session_dir),
        }

    def _checkpoint(self, *, force: bool = False) -> None:
        if not self.reliability_enabled:
            return
        now = time.monotonic()
        if force or now - self._last_checkpoint >= self.checkpoint_interval_s:
            self.journal.checkpoint(self._state_payload())
            self._last_checkpoint = now

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            f.flush()

    def _atomic_text(self, path: Path, text: str) -> None:
        if not self.atomic_artifacts:
            path.write_text(text, encoding="utf-8")
            return
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _mark_dirty(self) -> None:
        self._dirty_events += 1
        now = time.monotonic()
        if self._dirty_events >= self.refresh_events or now - self._last_refresh >= self.refresh_interval_s:
            self.refresh(force=True)
        self._checkpoint()

    def append(self, entry: TranscriptEntry) -> None:
        with self._lock:
            clean_source = (entry.source_text or entry.text or "").strip()
            raw = self._raw_for(entry)
            if not clean_source and not raw:
                return
            clean_source = self.cleaner.normalize_segment(clean_source, entry.language, terminal=False) or raw
            entry.source_text = clean_source
            who = _label(entry.source)
            if entry.speaker_id:
                who += ":" + entry.speaker_id
            if entry.speaker_overlap:
                who += "/OVERLAP"
            entry.text = f"[{who}] {clean_source}" if entry.source in {"mic", "system"} else clean_source
            if entry.raw_text is None:
                entry.raw_text = raw
            self.entries.append(entry)
            payload = asdict(entry)
            self._append_jsonl(self.jsonl_path, payload)
            if self.preserve_raw:
                who = _label(entry.source) + (":" + entry.speaker_id if entry.speaker_id else "")
                label = f"[{who}] " if entry.source in {"mic", "system"} else ""
                lang = f"[{(entry.language or '').upper()}] " if entry.language else ""
                with self.raw_txt_path.open("a", encoding="utf-8") as f:
                    f.write(f"[{_clock(entry.start_s)}] {label}{lang}{raw}\n")
                    f.flush()
                self._append_jsonl(self.raw_jsonl_path, payload)
            self.journal.append("asr_final", {
                "source": entry.source, "utterance_id": entry.utterance_id,
                "start_s": entry.start_s, "end_s": entry.end_s, "language": entry.language,
            })
            self._mark_dirty()

    def append_translation(self, entry: TranslationEntry) -> None:
        with self._lock:
            if not entry.translated_text.strip():
                return
            key = (entry.source, entry.utterance_id)
            self.translations[key] = entry
            self._append_jsonl(self.translation_jsonl_path, asdict(entry))
            self.journal.append("translation_final", {
                "source": entry.source,
                "utterance_id": entry.utterance_id,
                "source_language": entry.source_language,
                "target_language": entry.target_language,
                "inference_s": entry.inference_s,
            })
            self._mark_dirty()

    def record_health(self, payload: dict[str, Any]) -> None:
        with self._lock:
            row = {"wall_time": datetime.now().isoformat(), **payload}
            self._append_jsonl(self.session_dir / "health.jsonl", row)
            self.journal.append("health", payload)
            self._checkpoint()

    def _clean_turns(self, entries: list[TranscriptEntry] | None = None):
        return build_clean_turns(
            entries if entries is not None else self.entries,
            self.cleaner,
            merge_gap_s=self.merge_gap_s,
            max_turn_s=self.max_turn_s,
            max_turn_chars=self.max_turn_chars,
        )

    def refresh(self, *, force: bool = False) -> None:
        with self._lock:
            if not force and self._dirty_events <= 0:
                return
            self._write_clean_transcript()
            self._write_clean_srt()
            self._write_raw_srt()
            self._write_lane_transcripts()
            self._write_bilingual_transcript()
            self._write_readable_transcript()
            self._dirty_events = 0
            self._last_refresh = time.monotonic()
            self.journal.append("artifacts_refreshed", {"transcript_entries": len(self.entries)})
            self._checkpoint(force=True)

    def _write_clean_transcript(self) -> None:
        chunks: list[str] = []
        for turn in self._clean_turns():
            who = _label(turn.source) + (":" + turn.speaker_id if turn.speaker_id else "") + ("/OVERLAP" if turn.speaker_overlap else "")
            label = f"[{who}] " if turn.source in {"mic", "system"} else ""
            lang = f"[{(turn.language or '').upper()}] " if turn.language else ""
            chunks.append(f"[{_clock(turn.start_s)} - {_clock(turn.end_s)}] {label}{lang}{turn.text}\n")
        self._atomic_text(self.txt_path, "".join(chunks))

    def _write_lane_transcripts(self) -> None:
        for source, lane in (("mic", "you"), ("system", "remote")):
            chunks: list[str] = []
            for turn in self._clean_turns([e for e in self.entries if e.source == source]):
                lang = f"[{(turn.language or '').upper()}] " if turn.language else ""
                who = f"[{turn.speaker_id}] " if turn.speaker_id else ""
                if turn.speaker_overlap: who += "[OVERLAP] "
                chunks.append(f"[{_clock(turn.start_s)} - {_clock(turn.end_s)}] {who}{lang}{turn.text}\n")
            self._atomic_text(self.session_dir / f"{lane}_transcript.txt", "".join(chunks))

    def _write_bilingual_transcript(self) -> None:
        chunks: list[str] = []
        rows = sorted(self.entries, key=lambda e: (e.start_s, e.end_s, e.source or ""))
        for entry in rows:
            label = _label(entry.source) + (":" + entry.speaker_id if entry.speaker_id else "") + ("/OVERLAP" if entry.speaker_overlap else "")
            src_lang = (entry.language or "").upper()
            source_text = (entry.source_text or entry.text or "").strip()
            chunks.append(f"[{_clock(entry.start_s)} - {_clock(entry.end_s)}] [{label}] [{src_lang}] {source_text}\n")
            tr = self.translations.get((entry.source or "", entry.utterance_id))
            if tr is not None and tr.translated_text.strip():
                chunks.append(f"    -> [{tr.target_language.upper()}] {tr.translated_text.strip()}\n")
            chunks.append("\n")
        self._atomic_text(self.bilingual_txt_path, "".join(chunks))

    def _write_readable_transcript(self) -> None:
        """Human-readable meeting record; preserves original text and any translation."""
        rows = sorted(self.entries, key=lambda e: (e.start_s, e.end_s, e.source or ""))
        parts = ["NỘI DUNG CUỘC HỌP\n", "=" * 38 + "\n\n"]
        if not rows:
            parts.append("Chưa có lời thoại được nhận diện.\n")
        for entry in rows:
            who = "Microphone" if entry.source == "mic" else ("Âm thanh máy" if entry.source == "system" else "Nguồn khác")
            if entry.speaker_id:
                who += f" · {entry.speaker_id}"
            if entry.speaker_overlap:
                who += " · Có thể nói chồng"
            lang = {"vi": "Tiếng Việt", "ja": "Tiếng Nhật", "en": "Tiếng Anh"}.get((entry.language or "").lower(), (entry.language or "").upper())
            parts.append(f"[{_clock(entry.start_s)} – {_clock(entry.end_s)}] {who}" + (f" · {lang}" if lang else "") + "\n")
            original = (entry.source_text or entry.text or "").strip()
            tr = self.translations.get((entry.source or "", entry.utterance_id))
            if tr is not None and tr.translated_text.strip():
                parts.append(f"Lời thoại: {original}\n")
                parts.append(f"Tiếng Việt: {tr.translated_text.strip()}\n")
            else:
                parts.append(original + "\n")
            parts.append("\n")
        self.readable_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_text(self.readable_txt_path, "".join(parts))

    def _write_clean_srt(self) -> None:
        chunks: list[str] = []
        rows = sorted(self.entries, key=lambda e: (e.start_s, e.end_s, e.source or ""))
        for idx, entry in enumerate(rows, 1):
            text = self.cleaner.normalize_segment(entry.source_text or entry.text, entry.language, terminal=True)
            if entry.source in {"mic", "system"}:
                who = _label(entry.source) + (":" + entry.speaker_id if entry.speaker_id else "")
                text = f"[{who}] {text}"
            chunks.append(f"{idx}\n{_clock(entry.start_s, True)} --> {_clock(entry.end_s, True)}\n{text}\n\n")
        self._atomic_text(self.srt_path, "".join(chunks))

    def _write_raw_srt(self) -> None:
        if not self.preserve_raw:
            return
        chunks: list[str] = []
        rows = sorted(self.entries, key=lambda e: (e.start_s, e.end_s, e.source or ""))
        for idx, entry in enumerate(rows, 1):
            text = self._raw_for(entry)
            if entry.source in {"mic", "system"}:
                who = _label(entry.source) + (":" + entry.speaker_id if entry.speaker_id else "")
                text = f"[{who}] {text}"
            chunks.append(f"{idx}\n{_clock(entry.start_s, True)} --> {_clock(entry.end_s, True)}\n{text}\n\n")
        self._atomic_text(self.raw_srt_path, "".join(chunks))

    def close(self, *, status: str = "completed") -> None:
        with self._lock:
            if self._closed:
                return
            self.refresh(force=True)
            self._closed = True
            self.journal.append("session_closed", {**self._state_payload(), "status": status}, force=True)
            self._checkpoint(force=True)

    def recover(self) -> dict[str, Any]:
        with self._lock:
            self._load_existing()
            self.refresh(force=True)
            state = self._state_payload()
            self.journal.append("session_recovered", state, force=True)
            return state
