from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable, Protocol


_CJK_LANGS = {"zh", "yue", "ja"}
_NO_CASE_LANGS = {"zh", "yue", "ja", "ko"}
_TERMINAL_RE = re.compile(r"[.!?。！？…]+[\"'”’）)】》」』]*$")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%…。！？、；：])")
_SPACE_AFTER_PUNCT = re.compile(r"([,.;:!?])(?=[^\s\d])")
_MULTI_SPACE = re.compile(r"[ \t\u00A0]+")
_MULTI_PUNCT = re.compile(r"([!?！？])\1{1,}")
_WORD_RE = re.compile(r"\S+")


class _TranscriptLike(Protocol):
    start_s: float
    end_s: float
    text: str
    language: str | None
    source: str | None
    source_text: str | None
    speaker_id: str | None
    speaker_overlap: bool


@dataclass(slots=True)
class CleanTurn:
    start_s: float
    end_s: float
    text: str
    language: str | None = None
    source: str | None = None
    speaker_id: str | None = None
    speaker_overlap: bool = False


class TextCleaner:
    """Conservative, language-aware transcript cleanup.

    This module deliberately does not guess missing words or proper nouns. It only
    changes presentation-level artifacts that are safe to repair without a language
    model: Unicode/spacing, obvious repetitions, segment joins and terminal marks.
    """

    def __init__(
        self,
        *,
        case_mode: str = "conservative",
        add_terminal_punctuation: bool = True,
        collapse_repetitions: bool = True,
    ) -> None:
        self.case_mode = case_mode
        self.add_terminal_punctuation = bool(add_terminal_punctuation)
        self.collapse_repetitions = bool(collapse_repetitions)

    @staticmethod
    def normalize_language(language: str | None) -> str:
        x = (language or "").strip().lower().replace("_", "-")
        aliases = {
            "cmn": "zh", "zh-cn": "zh", "zh-tw": "zh", "cn": "zh",
            "jp": "ja", "jpn": "ja", "kr": "ko", "kor": "ko",
            "eng": "en", "vie": "vi", "vn": "vi",
        }
        return aliases.get(x, x)

    @staticmethod
    def _join_cjk_spacing(text: str) -> str:
        # Remove spaces inserted between adjacent CJK characters while preserving
        # spaces around Latin words/numbers when they are genuinely present.
        cjk = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af"
        text = re.sub(fr"(?<=[{cjk}])\s+(?=[{cjk}])", "", text)
        text = re.sub(fr"(?<=[{cjk}])\s+([、。！？；：])", r"\1", text)
        return text

    @staticmethod
    def _collapse_adjacent_duplicate_words(text: str) -> str:
        # Exact adjacent duplicates are frequent at ASR chunk boundaries:
        # "đoạn đoạn clip" -> "đoạn clip". Keep the rule intentionally narrow.
        words = text.split()
        if len(words) < 2:
            return text
        out: list[str] = []
        for word in words:
            norm = re.sub(r"[^\wÀ-ỹĐđ]+", "", word.casefold(), flags=re.UNICODE)
            prev = re.sub(r"[^\wÀ-ỹĐđ]+", "", out[-1].casefold(), flags=re.UNICODE) if out else ""
            if norm and prev and norm == prev:
                continue
            out.append(word)
        return " ".join(out)

    @staticmethod
    def _collapse_repeated_phrase(text: str) -> str:
        # Collapse immediately repeated exact phrases of 2-5 words. This avoids
        # deleting merely similar natural speech.
        words = text.split()
        if len(words) < 4:
            return text
        changed = True
        while changed:
            changed = False
            for n in range(min(5, len(words) // 2), 1, -1):
                i = 0
                while i + 2 * n <= len(words):
                    a = [re.sub(r"\W+", "", w.casefold(), flags=re.UNICODE) for w in words[i:i+n]]
                    b = [re.sub(r"\W+", "", w.casefold(), flags=re.UNICODE) for w in words[i+n:i+2*n]]
                    if a == b and any(a):
                        del words[i+n:i+2*n]
                        changed = True
                    else:
                        i += 1
        return " ".join(words)

    @staticmethod
    def _uppercase_ratio(text: str) -> float:
        letters = [c for c in text if c.isalpha()]
        if not letters:
            return 0.0
        return sum(c.isupper() for c in letters) / len(letters)

    @staticmethod
    def _sentence_case_all_caps(text: str) -> str:
        # Opt-in mode only. Preserve dotted/alphanumeric acronyms because they are
        # high-confidence formatting signals; do not try to infer ordinary names.
        protected: dict[str, str] = {}
        pattern = re.compile(r"\b(?:[A-ZĐ]{1,6}(?:\.[A-ZĐ]{1,6})+|[A-Z]{2,6}\d+[A-Z0-9-]*)\b")

        def repl(m: re.Match[str]) -> str:
            key = f"__P{len(protected)}__"
            protected[key] = m.group(0)
            return key

        work = pattern.sub(repl, text)
        work = work.lower()
        chars = list(work)
        cap_next = True
        for i, ch in enumerate(chars):
            if cap_next and ch.isalpha():
                chars[i] = ch.upper()
                cap_next = False
            elif ch in ".!?。！？":
                cap_next = True
        work = "".join(chars)
        for key, value in protected.items():
            work = work.replace(key.lower(), value)
            work = work.replace(key, value)
        return work

    def decapitalize_joined_segment(self, normalized_text: str, raw_text: str, language: str | None = None) -> str:
        """Undo artificial sentence-start capitalization when joining an all-caps chunk.

        Only applies when sentence-case mode transformed an all-caps Latin segment;
        naturally cased ASR text and CJK/Korean are left untouched.
        """
        lang = self.normalize_language(language)
        if self.case_mode != "sentence" or lang in _NO_CASE_LANGS:
            return normalized_text
        if self._uppercase_ratio(raw_text or "") < 0.92:
            return normalized_text
        chars = list(normalized_text)
        for i, ch in enumerate(chars):
            if ch.isalpha():
                chars[i] = ch.lower()
                break
        return "".join(chars)

    def normalize_segment(self, text: str, language: str | None = None, *, terminal: bool = False) -> str:
        text = unicodedata.normalize("NFC", text or "")
        text = text.replace("\r", " ").replace("\n", " ")
        text = _MULTI_SPACE.sub(" ", text).strip()
        if not text:
            return ""

        lang = self.normalize_language(language)
        if lang in _CJK_LANGS or lang == "ko":
            text = self._join_cjk_spacing(text)
        text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
        if lang not in _CJK_LANGS:
            text = _SPACE_AFTER_PUNCT.sub(r"\1 ", text)
        text = _MULTI_PUNCT.sub(r"\1", text)

        if self.collapse_repetitions:
            text = self._collapse_adjacent_duplicate_words(text)
            text = self._collapse_repeated_phrase(text)

        # Default conservative mode preserves ASR capitalization because guessing
        # proper nouns can corrupt transcripts. Users may opt into sentence mode.
        if self.case_mode == "sentence" and lang not in _NO_CASE_LANGS and self._uppercase_ratio(text) >= 0.92:
            text = self._sentence_case_all_caps(text)

        if terminal and self.add_terminal_punctuation and text and not _TERMINAL_RE.search(text):
            text += "。" if lang in _CJK_LANGS else "."
        return text.strip()

    def join_segments(self, left: str, right: str, language: str | None = None) -> str:
        left = self.normalize_segment(left, language, terminal=False)
        right = self.normalize_segment(right, language, terminal=False)
        if not left:
            return right
        if not right:
            return left

        # If the same exact boundary token appears on both sides, remove only one.
        lw = left.split()
        rw = right.split()
        if lw and rw:
            a = re.sub(r"\W+", "", lw[-1].casefold(), flags=re.UNICODE)
            b = re.sub(r"\W+", "", rw[0].casefold(), flags=re.UNICODE)
            if a and a == b:
                rw = rw[1:]
                right = " ".join(rw)
        if not right:
            return left
        sep = "" if self.normalize_language(language) in _CJK_LANGS else " "
        return self.normalize_segment(left + sep + right, language, terminal=False)


def _entry_source_text(entry: _TranscriptLike) -> str:
    return (entry.source_text or entry.text or "").strip()


def build_clean_turns(
    entries: Iterable[_TranscriptLike],
    cleaner: TextCleaner,
    *,
    merge_gap_s: float = 1.25,
    max_turn_s: float = 45.0,
    max_turn_chars: int = 900,
) -> list[CleanTurn]:
    rows = sorted(entries, key=lambda e: (float(e.start_s), float(e.end_s), str(e.source or "")))
    turns: list[CleanTurn] = []

    for entry in rows:
        raw = _entry_source_text(entry)
        raw_original = (getattr(entry, "raw_text", None) or raw).strip()
        text = cleaner.normalize_segment(raw, entry.language, terminal=False)
        if not text:
            continue
        start = float(entry.start_s)
        end = float(entry.end_s)
        source = entry.source
        speaker_id = getattr(entry, "speaker_id", None)
        speaker_overlap = bool(getattr(entry, "speaker_overlap", False))
        language = cleaner.normalize_language(entry.language) or entry.language

        can_merge = False
        if turns:
            prev = turns[-1]
            gap = start - prev.end_s
            can_merge = (
                prev.source == source
                and prev.speaker_id == speaker_id
                and prev.speaker_overlap == speaker_overlap
                and (prev.language or "") == (language or "")
                and gap <= merge_gap_s
                and gap >= -0.20
                and (end - prev.start_s) <= max_turn_s
                and (len(prev.text) + len(text) + 1) <= max_turn_chars
            )
        if can_merge:
            prev = turns[-1]
            if not _TERMINAL_RE.search(prev.text):
                text = cleaner.decapitalize_joined_segment(text, raw_original, language)
            prev.text = cleaner.join_segments(prev.text, text, language)
            prev.end_s = max(prev.end_s, end)
        else:
            turns.append(CleanTurn(start, end, text, language=language, source=source, speaker_id=speaker_id, speaker_overlap=speaker_overlap))

    for turn in turns:
        turn.text = cleaner.normalize_segment(turn.text, turn.language, terminal=True)
    return turns


