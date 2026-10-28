"""Offline LFM2.5 Instruct translation using the ORIGINAL safetensors checkpoint.

No GGUF, no llama.cpp, no network or silently chosen model ID. The model stays
loaded on the translation worker thread and never blocks the audio callback.
"""
from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
import re
import threading
import time


class LocalLfmTranslator:
    REQUIRED_FILES = (
        "config.json", "model.safetensors", "tokenizer.json",
        "tokenizer_config.json", "special_tokens_map.json", "chat_template.jinja",
    )
    LANGS = {"ja": "Japanese", "en": "English"}

    def __init__(
        self,
        model_path: str,
        *,
        cpu_threads: int = 2,
        max_input_tokens: int = 192,
        max_new_tokens: int = 112,
        cpu_dtype: str = "bfloat16",
    ) -> None:
        self.model_path = Path(model_path)
        self.cpu_threads = max(1, int(cpu_threads))
        self.max_input_tokens = max(32, int(max_input_tokens))
        self.max_new_tokens = max(16, int(max_new_tokens))
        self.cpu_dtype = str(cpu_dtype).lower()
        if self.cpu_dtype not in {"bfloat16", "float32"}:
            raise ValueError("translation.lfm_cpu_dtype must be 'bfloat16' or 'float32'")
        missing = [f for f in self.REQUIRED_FILES if not (self.model_path / f).is_file()]
        if missing:
            raise FileNotFoundError(
                f"LFM checkpoint incomplete at {self.model_path}: {', '.join(missing)}. "
                "Use option 9 in SoundText.bat to download it."
            )
        if (self.model_path / "model.safetensors").stat().st_size < 2_000_000_000:
            raise FileNotFoundError("LFM model.safetensors is incomplete (<2GB). Use menu 9 to resume download.")
        for dependency in ("torch", "transformers"):
            if find_spec(dependency) is None:
                raise RuntimeError(f"Missing {dependency}; install Python packages using menu 11.")
        self._lock = threading.Lock()
        self._tokenizer = None
        self._model = None
        self._torch = None

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import os
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            import torch
            from transformers import AutoTokenizer, AutoModelForCausalLM
            torch.set_num_threads(self.cpu_threads)
            # BF16 is faithful to the source checkpoint; float32 can be selected
            # for CPUs whose BF16 arithmetic is unsupported or impractically slow.
            torch_dtype = torch.bfloat16 if self.cpu_dtype == "bfloat16" else torch.float32
            print(f"\n[LFM] Loading local model on CPU ({self.cpu_dtype}): {self.model_path}", flush=True)
            tokenizer = AutoTokenizer.from_pretrained(
                str(self.model_path), local_files_only=True, trust_remote_code=False,
            )
            model = AutoModelForCausalLM.from_pretrained(
                str(self.model_path), local_files_only=True,
                trust_remote_code=False, torch_dtype=torch_dtype,
                low_cpu_mem_usage=True,
            )
            model.to("cpu")
            model.eval()
            self._tokenizer, self._model, self._torch = tokenizer, model, torch
            print("[LFM] Loaded, ready for translation.", flush=True)

    @staticmethod
    def _clean_output(text: str) -> str:
        value = str(text or "").strip()
        # Avoid including wrapper labels or a template's role tokens in subtitles.
        value = re.sub(r"^(?:Bản dịch(?: tiếng Việt)?|Tiếng Việt|Vietnamese(?: translation)?)\s*[:：]\s*", "", value, flags=re.I)
        value = re.split(r"<\|im_end\|>|<\|im_start\|>|<\|endoftext\|>", value)[0]
        value = value.strip().strip('"“”')
        return value

    def translate(self, text: str, source_language: str, target_language: str) -> tuple[str, float]:
        source = str(source_language).lower().strip()
        target = str(target_language).lower().strip()
        if source not in self.LANGS or target != "vi":
            raise ValueError(f"LFM translation supports ja/en -> vi only; got {source}->{target}")
        original = text.strip()
        if not original or not any(ch.isalnum() for ch in original):
            return "", 0.0

        # Time includes tokenization and generation, but not initial model load.
        self._load()
        tokenizer, model, torch = self._tokenizer, self._model, self._torch
        assert tokenizer is not None and model is not None and torch is not None
        messages = [
            {"role": "system", "content": (
                "You are a precise subtitle translator. Translate the user's "
                "Japanese or English utterance into natural Vietnamese. "
                "Output ONLY the Vietnamese translation. Do not explain, add "
                "information, guess missing words, or continue the dialogue. "
                "Keep names as names. If unclear, translate conservatively."
            )},
            {"role": "user", "content": (
                f"Translate from {self.LANGS[source]} to Vietnamese. "
                "Reply with the translation only.\n"
                f"<source>\n{original}\n</source>"
            )},
        ]
        start = time.perf_counter()
        tokens = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True,
            tokenize=True, return_dict=True, return_tensors="pt",
        )
        input_ids = tokens["input_ids"]
        if input_ids.shape[-1] > self.max_input_tokens:
            # Hard cap for real-time CPU inference. More robust than silently
            # truncating the SYSTEM instruction / beginning of the chat template.
            raise ValueError(
                f"LFM prompt is {input_ids.shape[-1]} tokens (limit {self.max_input_tokens}). "
                "Increase translation.max_input_tokens for longer utterances."
            )
        with torch.inference_mode():
            output = model.generate(
                **tokens,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                num_beams=1,
                repetition_penalty=1.05,
                use_cache=True,
                pad_token_id=tokenizer.eos_token_id,
            )
        decoded = tokenizer.decode(output[0][input_ids.shape[-1]:], skip_special_tokens=True)
        result = self._clean_output(decoded)
        return result, time.perf_counter() - start
