from __future__ import annotations

from pathlib import Path
import re
import time

import numpy as np

from .types import Transcription


_LANGUAGE_TAG = re.compile(r"<\|(zh|yue|en|ja|ko|nospeech)\|>", re.IGNORECASE)


class LocalSenseVoiceEngine:
    """Local-only SenseVoiceSmall adapter using FunASR.

    The model directory must already exist locally. We disable SDK update checks
    and do not configure FunASR's own VAD because SoundText already segments audio
    with Silero before it reaches this adapter.
    """

    SUPPORTED_LANGUAGES = {"auto", "zh", "yue", "en", "ja", "ko"}

    def __init__(
        self,
        model_path: str | Path,
        language: str = "auto",
        device: str = "cpu",
        num_threads: int = 4,
        sample_rate: int = 16000,
        partial_use_itn: bool = False,
        final_use_itn: bool = True,
        ban_emo_unk: bool = False,
        rich_transcription: bool = False,
    ) -> None:
        path = Path(model_path)
        if not path.is_dir():
            raise FileNotFoundError(
                f"SenseVoice model directory not found: {path}. "
                "Run scripts/prepare_models.py --asr sensevoice-small first."
            )

        lang = (language or "ja").strip().lower().replace("_", "-")
        lang = {"ja-jp": "ja", "jp": "ja", "japanese": "ja"}.get(lang, lang)
        if lang not in self.SUPPORTED_LANGUAGES:
            raise ValueError(
                f"SenseVoice language '{language}' is unsupported. "
                "Use auto/zh/yue/en/ja/ko. Vietnamese should use Gipformer."
            )

        try:
            from funasr import AutoModel
            from funasr.utils.postprocess_utils import rich_transcription_postprocess
        except ImportError as exc:
            raise RuntimeError(
                "SenseVoice runtime is not installed. Run: "
                "pip install -r requirements-cjk.txt"
            ) from exc

        self.language = lang
        self.sample_rate = int(sample_rate)
        self.partial_use_itn = bool(partial_use_itn)
        self.final_use_itn = bool(final_use_itn)
        self.ban_emo_unk = bool(ban_emo_unk)
        self.rich_transcription = bool(rich_transcription)
        self._postprocess = rich_transcription_postprocess

        # `model` is a local path. `trust_remote_code=False` uses FunASR's built-in
        # SenseVoice implementation, while `disable_update=True` prevents its SDK
        # version/update check. No model download occurs at runtime.
        self.model = AutoModel(
            model=str(path),
            device=str(device),
            ncpu=max(1, int(num_threads)),
            disable_update=True,
            trust_remote_code=False,
        )

    @staticmethod
    def _language_from_raw(raw_text: str, configured: str) -> str | None:
        # V15 supports fixed Japanese only on this backend. Never allow an
        # internal SenseVoice LID tag to relabel a Japanese utterance as KO/ZH.
        if configured != "auto":
            return configured
        match = _LANGUAGE_TAG.search(raw_text or "")
        if match:
            value = match.group(1).lower()
            return None if value == "nospeech" else value
        return None

    @staticmethod
    def _fallback_clean(raw_text: str) -> str:
        # rich_transcription_postprocess is authoritative when FunASR is present;
        # this fallback mainly makes unit testing/tag handling deterministic.
        text = re.sub(r"<\|[^|]+\|>", "", str(raw_text or ""))
        return " ".join(text.split()).strip()

    def transcribe(self, audio: np.ndarray, final: bool) -> Transcription:
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        audio_s = x.size / float(self.sample_rate)
        if x.size == 0:
            return Transcription(text="", language=None, elapsed_s=0.0, audio_s=0.0)

        use_itn = self.final_use_itn if final else self.partial_use_itn
        started = time.perf_counter()
        results = self.model.generate(
            input=x,
            fs=self.sample_rate,
            cache={},
            language=self.language,
            use_itn=use_itn,
            ban_emo_unk=self.ban_emo_unk,
            batch_size=1,
        )
        elapsed = time.perf_counter() - started

        item = results[0] if isinstance(results, list) and results else results
        if isinstance(item, dict):
            raw = str(item.get("text", "") or "")
        else:
            raw = str(item or "")
        language = self._language_from_raw(raw, self.language)
        if self.rich_transcription:
            try:
                text = self._postprocess(raw)
            except Exception:
                text = self._fallback_clean(raw)
        else:
            # Keep SoundText transcript text-only by default. The official rich
            # postprocessor converts emotion/audio-event tags to emojis, which is
            # useful for rich ASR demos but noisy for meeting/interpreter output.
            text = self._fallback_clean(raw)
        text = " ".join(str(text or "").split()).strip()

        return Transcription(
            text=text,
            language=language,
            elapsed_s=elapsed,
            audio_s=audio_s,
        )
