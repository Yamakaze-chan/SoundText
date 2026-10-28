from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np

from .types import Transcription


@dataclass(slots=True)
class GipformerModelFiles:
    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path
    bpe: Path | None = None


def resolve_gipformer_files(model_dir: str | Path, precision: str = "int8") -> GipformerModelFiles:
    root = Path(model_dir)
    if not root.is_dir():
        raise FileNotFoundError(
            f"Gipformer model directory not found: {root}. "
            "Run scripts/prepare_models.py --asr gipformer first."
        )
    suffix = ".int8.onnx" if precision.lower() == "int8" else ".onnx"
    files = GipformerModelFiles(
        encoder=root / f"encoder{suffix}",
        decoder=root / f"decoder{suffix}",
        joiner=root / f"joiner{suffix}",
        tokens=root / "tokens.txt",
        bpe=(root / "bpe.model") if (root / "bpe.model").is_file() else None,
    )
    missing = [
        str(p)
        for p in (files.encoder, files.decoder, files.joiner, files.tokens)
        if not p.is_file()
    ]
    if missing:
        raise FileNotFoundError("Missing Gipformer runtime file(s):\n  " + "\n  ".join(missing))
    return files


class LocalGipformerOfflineEngine:
    """Local Gipformer ONNX decoder.

    Important: the official Gipformer ONNX example currently uses
    sherpa_onnx.OfflineRecognizer.from_transducer(). The released encoder does
    not expose the streaming metadata expected by sherpa_onnx.OnlineRecognizer.

    Near-realtime captions are therefore implemented by repeatedly decoding the
    newest short VAD window in a dedicated preview worker. Final utterances are
    decoded by a separate recognizer so preview work cannot block final output.
    """

    def __init__(
        self,
        model_dir: str | Path,
        precision: str = "int8",
        num_threads: int = 4,
        decoding_method: str = "modified_beam_search",
        max_active_paths: int = 4,
        sample_rate: int = 16000,
        hotwords_file: str = "",
        hotwords_score: float = 1.5,
        blank_penalty: float = 0.0,
        simple_punctuation: bool = True,
    ) -> None:
        files = resolve_gipformer_files(model_dir, precision)
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "sherpa-onnx is not installed. Run: "
                "pip install sherpa-onnx sherpa-onnx-core"
            ) from exc

        hotwords_path = str(hotwords_file or "").strip()
        if hotwords_path and not Path(hotwords_path).is_file():
            raise FileNotFoundError(f"Gipformer hotwords file not found: {hotwords_path}")

        kwargs = dict(
            encoder=str(files.encoder),
            decoder=str(files.decoder),
            joiner=str(files.joiner),
            tokens=str(files.tokens),
            num_threads=max(1, int(num_threads)),
            sample_rate=int(sample_rate),
            feature_dim=80,
            decoding_method=str(decoding_method),
            max_active_paths=max(1, int(max_active_paths)),
            hotwords_file=hotwords_path,
            hotwords_score=float(hotwords_score),
            blank_penalty=float(blank_penalty),
        )
        if hotwords_path and files.bpe is not None:
            # Gipformer uses BPE tokens. Recent sherpa-onnx versions can use the
            # sentencepiece vocabulary when hotword biasing is enabled.
            kwargs["modeling_unit"] = "bpe"
            kwargs["bpe_vocab"] = str(files.bpe)

        self.sample_rate = int(sample_rate)
        self.simple_punctuation = bool(simple_punctuation)
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(**kwargs)

    def _display_text(self, text: str, final: bool) -> str:
        text = " ".join(str(text).split()).strip()
        if not text or not final or not self.simple_punctuation:
            return text
        text = text[:1].upper() + text[1:]
        if text[-1] not in ".!?…":
            text += "."
        return text

    def transcribe(self, audio: np.ndarray, final: bool = True) -> Transcription:
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        audio_s = x.size / float(self.sample_rate)
        if x.size == 0:
            return Transcription(text="", language="vi", elapsed_s=0.0, audio_s=0.0)

        stream = self.recognizer.create_stream()
        stream.accept_waveform(self.sample_rate, x)
        started = time.perf_counter()
        self.recognizer.decode_stream(stream)
        elapsed = time.perf_counter() - started

        result = getattr(stream, "result", None)
        text = str(getattr(result, "text", "") or "").strip()
        text = self._display_text(text, final=final)
        return Transcription(
            text=text,
            language="vi",
            elapsed_s=elapsed,
            audio_s=audio_s,
        )
