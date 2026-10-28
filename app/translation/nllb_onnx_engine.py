from __future__ import annotations

import os
from pathlib import Path
import threading
import time


class LocalNllbOnnxTranslator:
    """Lazy-loaded INT8 NLLB ONNX translator.

    Runtime is strictly local: tokenizer and ONNX graphs are loaded only from
    model_path. The heavy model is loaded on the translation worker's first job,
    so microphone/system capture and ASR can start immediately.
    """

    LANG_CODES = {
        "en": "eng_Latn",
        "vi": "vie_Latn",
        "ja": "jpn_Jpan",
    }

    REQUIRED_FILES = (
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "sentencepiece.bpe.model",
        "encoder_model_quantized.onnx",
        "decoder_model_quantized.onnx",
        "decoder_with_past_model_quantized.onnx",
    )

    def __init__(
        self,
        model_path: str,
        *,
        cpu_threads: int = 2,
        max_input_tokens: int = 192,
        max_new_tokens: int = 192,
        num_beams: int = 1,
    ) -> None:
        self.model_path = Path(model_path)
        self.cpu_threads = max(1, int(cpu_threads))
        self.max_input_tokens = max(32, int(max_input_tokens))
        self.max_new_tokens = max(16, int(max_new_tokens))
        self.num_beams = 1  # realtime CPU policy: beam=1
        self._load_lock = threading.Lock()
        self._tokenizer = None
        self._model = None
        self._low_ram = False
        self._memory_error = None

        missing = [name for name in self.REQUIRED_FILES if not (self.model_path / name).is_file()]
        if missing:
            short = ", ".join(missing[:3]) + ("..." if len(missing) > 3 else "")
            raise FileNotFoundError(
                f"NLLB ONNX model is not prepared at {self.model_path} ({short}). "
                "Select option 10 in SoundText.bat."
            )

        # Keep the constructor lightweight. Importing Transformers/Optimum here
        # pulls Torch into RAM even when the first translation has not arrived.
        # Optional MT errors are reported by the translation worker, not by
        # a seemingly unrelated recording-session constructor.

    @staticmethod
    def _normalize_language(language: str) -> str:
        value = str(language or "").strip().lower()
        aliases = {
            "english": "en", "eng": "en",
            "vietnamese": "vi", "vn": "vi",
            "japanese": "ja", "jp": "ja",
        }
        value = aliases.get(value, value)
        if value not in {"en", "vi", "ja"}:
            raise ValueError(f"Unsupported translation language: {language!r}")
        return value

    def _load(self) -> None:
        if self._model is not None:
            return
        if self._memory_error is not None:
            raise self._memory_error
        with self._load_lock:
            if self._model is not None:
                return
            if self._memory_error is not None:
                raise self._memory_error

            from app.runtime_memory import (
                ensure_available_memory, is_low_memory_frozen, log_memory,
            )
            self._low_ram = is_low_memory_frozen()
            try:
                # The disabled arena/cache profile uses less RAM, at the cost of
                # extra latency for some NLLB sentences. A 6 GiB free-RAM gate
                # was impossible on a normal 8 GiB computer after ASR loaded.
                ensure_available_memory(
                    "tải NLLB", minimum_gib=3.0 if self._low_ram else 6.0
                )
                log_memory("before NLLB import")
                os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

                import onnxruntime as ort
                from transformers import AutoTokenizer
                from optimum.onnxruntime import ORTModelForSeq2SeqLM

                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 1 if self._low_ram else self.cpu_threads
                opts.inter_op_num_threads = 1
                opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                opts.graph_optimization_level = (
                    ort.GraphOptimizationLevel.ORT_ENABLE_BASIC if self._low_ram
                    else ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                )
                if self._low_ram:
                    opts.enable_cpu_mem_arena = False
                    opts.enable_mem_pattern = False
                log_memory("before NLLB ONNX sessions")
                model_dir = str(self.model_path)
                tokenizer = AutoTokenizer.from_pretrained(
                    model_dir, local_files_only=True,
                )
                # With cache disabled, Optimum should load only encoder and
                # decoder (not the decoder-with-past graph) on 8 GiB systems.
                # The model is still the same NLLB INT8 ONNX model.
                model = ORTModelForSeq2SeqLM.from_pretrained(
                    model_dir,
                    local_files_only=True,
                    provider="CPUExecutionProvider",
                    session_options=opts,
                    encoder_file_name="encoder_model_quantized.onnx",
                    decoder_file_name="decoder_model_quantized.onnx",
                    decoder_with_past_file_name="decoder_with_past_model_quantized.onnx",
                    use_cache=not self._low_ram,
                )
                self._tokenizer = tokenizer
                self._model = model
                log_memory("after NLLB ONNX sessions")
            except MemoryError as exc:
                self._memory_error = exc
                log_memory("NLLB skipped - insufficient RAM")
                raise

    @staticmethod
    def _dynamic_max_new_tokens(input_tokens: int) -> int:
        """Cap conversational translations tightly enough to stop runaway loops.

        Japanese NLLB output can use more subword tokens than the source, so
        allow ~1.8x input plus a small margin, but never the old fixed 192.
        """
        n = max(1, int(input_tokens))
        return max(24, min(112, int(n * 1.8) + 12))

    @staticmethod
    def _collapse_pathological_repetition(text: str) -> tuple[str, bool]:
        """Detect/cut obvious decoder loops without rewriting normal prose."""
        import re

        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return clean, False

        # Sentence/clause-level loops are the failure mode seen in the user's
        # JA->VI log ("không đọc, không đọc..." dozens of times).
        parts = [p.strip() for p in re.split(r"(?<=[.!?。！？;；])\s*|,\s*", clean) if p.strip()]
        out: list[str] = []
        last_norm = ""
        consecutive = 0
        looped = False
        for part in parts:
            norm = re.sub(r"\W+", " ", part.lower(), flags=re.UNICODE).strip()
            if norm and norm == last_norm:
                consecutive += 1
            else:
                consecutive = 1
                last_norm = norm

            # Keep at most two identical adjacent clauses; the third is a
            # strong signal of autoregressive degeneration.
            if consecutive > 2:
                looped = True
                continue
            out.append(part)

        if looped and out:
            clean = ", ".join(out).strip(" ,")

        # Also catch repeated multi-word tails that punctuation splitting misses.
        words = clean.split()
        if len(words) >= 18:
            for size in range(3, min(13, len(words) // 3 + 1)):
                if words[-size:] == words[-2*size:-size] == words[-3*size:-2*size]:
                    clean = " ".join(words[:-2*size]).strip()
                    looped = True
                    break

        return clean, looped

    @staticmethod
    def _split_long_japanese_unit(text: str, *, target_chars: int = 46, hard_chars: int = 68) -> list[str]:
        """Split a long unpunctuated Japanese ASR span at conservative clause boundaries.

        We prefer punctuation and common conversational transition boundaries.
        If no safe boundary is found, the span is kept intact instead of cutting
        blindly in the middle of a Japanese phrase.
        """
        import re

        rest = str(text or "").strip()
        if not rest or len(rest) <= hard_chars:
            return [rest] if rest else []

        pieces: list[str] = []
        # Boundaries *after* these forms are usually tolerable for conversational MT.
        after_patterns = (
            r"、", r"，", r",", r"；", r";",
            r"けれど", r"けど", r"から", r"ので", r"なら", r"たら",
            r"と思った", r"と思って", r"と思う", r"よね", r"だよね",
        )
        # Boundaries *before* discourse markers keep the marker with the next clause.
        before_patterns = (
            r"でも", r"だから", r"それで", r"じゃあ", r"じゃ",
            r"なんか", r"やっぱり", r"やっぱ", r"今も",
        )

        while len(rest) > hard_chars:
            lo = max(20, target_chars - 18)
            hi = min(len(rest) - 12, target_chars + 20)
            if hi <= lo:
                break

            candidates: list[int] = []
            window = rest[lo:hi]

            for pat in after_patterns:
                for m in re.finditer(pat, window):
                    candidates.append(lo + m.end())

            for pat in before_patterns:
                for m in re.finditer(pat, window):
                    candidates.append(lo + m.start())

            # Remove unsafe edge candidates and choose the one closest to target.
            candidates = [p for p in candidates if 16 <= p <= len(rest) - 12]
            if not candidates:
                break

            cut = min(candidates, key=lambda p: abs(p - target_chars))
            left = rest[:cut].strip()
            right = rest[cut:].strip()
            if not left or not right:
                break

            pieces.append(left)
            rest = right

        if rest:
            pieces.append(rest)

        return pieces

    @classmethod
    def _split_japanese_for_mt(cls, text: str) -> list[str]:
        """Split long JA ASR finals into 2-3 MT-sized chunks.

        Strong sentence punctuation is preferred. Long punctuation-poor spans
        are split only at conservative Japanese conversational boundaries.
        Short utterances are left untouched.
        """
        import re

        clean = str(text or "").strip()
        if not clean or len(clean) <= 62:
            return [clean] if clean else []

        # Keep Japanese sentence-ending punctuation attached to its sentence.
        sentences = [
            x.strip()
            for x in re.findall(r".+?(?:[。！？!?]+|$)", clean)
            if x.strip()
        ]
        if not sentences:
            return [clean]

        atomic: list[str] = []
        for sentence in sentences:
            atomic.extend(cls._split_long_japanese_unit(sentence))

        # Pack small neighboring units so we don't create too many generate() calls.
        packed: list[str] = []
        current = ""
        target_pack = 62
        for unit in atomic:
            if not current:
                current = unit
                continue
            if len(current) + len(unit) <= target_pack:
                current += unit
            else:
                packed.append(current)
                current = unit
        if current:
            packed.append(current)

        # 12-second ASR finals should normally become 2 chunks. Cap at 3 calls
        # for CPU stability; merge any unexpected tail into the third chunk.
        if len(packed) > 3:
            packed = packed[:2] + ["".join(packed[2:])]

        return packed or [clean]

    def _translate_one_chunk(
        self,
        chunk: str,
        *,
        tokenizer,
        model,
        source_language: str,
        target_language: str,
    ) -> tuple[str, int, int, bool]:
        tokenizer.src_lang = self.LANG_CODES[source_language]
        inputs = tokenizer(
            chunk,
            return_tensors="pt",
            truncation=True,
            max_length=self.max_input_tokens,
        )
        target_id = tokenizer.convert_tokens_to_ids(self.LANG_CODES[target_language])
        input_tokens = int(inputs["input_ids"].shape[-1])
        dynamic_max = min(
            self.max_new_tokens,
            self._dynamic_max_new_tokens(input_tokens),
        )
        outputs = model.generate(
            **inputs,
            forced_bos_token_id=target_id,
            max_new_tokens=dynamic_max,
            min_new_tokens=1,
            num_beams=1,
            do_sample=False,
            use_cache=not self._low_ram,
            repetition_penalty=1.12,
            no_repeat_ngram_size=4,
            early_stopping=False,
        )
        translated = tokenizer.decode(outputs[0], skip_special_tokens=True)
        translated = " ".join(translated.split()).strip()
        translated, looped = self._collapse_pathological_repetition(translated)
        return translated, input_tokens, dynamic_max, looped

    def translate(self, text: str, source_language: str, target_language: str = "vi") -> tuple[str, float]:
        clean = " ".join(str(text or "").split()).strip()
        src = self._normalize_language(source_language)
        tgt = self._normalize_language(target_language)
        if not clean or src == tgt:
            return clean, 0.0
        if tgt != "vi":
            raise ValueError("This SoundText build fixes the translation target to Vietnamese (vi).")

        self._load()
        tokenizer = self._tokenizer
        model = self._model
        assert tokenizer is not None and model is not None

        started = time.perf_counter()

        chunks = self._split_japanese_for_mt(clean) if src == "ja" else [clean]
        if len(chunks) > 1:
            print(
                f"\n[MT SPLIT] Japanese final divided into {len(chunks)} chunks "
                f"({len(clean)} chars -> {', '.join(str(len(x)) for x in chunks)} chars)"
            )

        translated_parts: list[str] = []
        looped_any = False
        for chunk in chunks:
            translated_part, input_tokens, dynamic_max, looped = self._translate_one_chunk(
                chunk,
                tokenizer=tokenizer,
                model=model,
                source_language=src,
                target_language=tgt,
            )
            if translated_part:
                translated_parts.append(translated_part)
            if looped:
                looped_any = True
                print(
                    f"\n[MT GUARD] repetitive JA/EN decode was truncated "
                    f"(input={input_tokens} tok, cap={dynamic_max})"
                )

        translated = " ".join(translated_parts).strip()
        translated, combined_looped = self._collapse_pathological_repetition(translated)
        looped_any = looped_any or combined_looped

        elapsed = time.perf_counter() - started
        return translated, elapsed
