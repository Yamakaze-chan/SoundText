from __future__ import annotations

from pathlib import Path
import numpy as np


class SileroOnnxVad:
    """Minimal local-only Silero VAD ONNX runtime wrapper.

    This mirrors the recurrent state/context behavior used by Silero's official
    ONNX wrapper, but uses NumPy directly so PyTorch is not required.
    """

    WINDOW_SAMPLES_16K = 512
    CONTEXT_SAMPLES_16K = 64

    def __init__(self, model_path: str | Path, sample_rate: int = 16000) -> None:
        if sample_rate != 16000:
            raise ValueError("This MVP VAD wrapper currently supports 16 kHz only")
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"Silero VAD model not found: {path}. Run scripts/prepare_models.py first."
            )
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("onnxruntime is required for local Silero VAD") from exc

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(path), providers=["CPUExecutionProvider"], sess_options=opts
        )
        self.sample_rate = sample_rate
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.CONTEXT_SAMPLES_16K), dtype=np.float32)

    def probability(self, chunk: np.ndarray) -> float:
        x = np.asarray(chunk, dtype=np.float32).reshape(1, -1)
        if x.shape[1] != self.WINDOW_SAMPLES_16K:
            raise ValueError(
                f"Silero VAD expects 512 samples at 16 kHz, got {x.shape[1]}"
            )
        model_input = np.concatenate((self._context, x), axis=1)
        outputs = self.session.run(
            None,
            {
                "input": model_input,
                "state": self._state,
                "sr": np.array(self.sample_rate, dtype=np.int64),
            },
        )
        probability, self._state = outputs[0], outputs[1]
        self._context = model_input[:, -self.CONTEXT_SAMPLES_16K :]
        return float(np.asarray(probability).reshape(-1)[0])
