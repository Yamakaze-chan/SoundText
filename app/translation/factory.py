from __future__ import annotations

from app.config import AppConfig
from .nllb_onnx_engine import LocalNllbOnnxTranslator


def build_translation_engine(config: AppConfig):
    backend = str(config.translation.backend or 'nllb_onnx').lower().strip()
    if backend not in {'nllb_onnx', 'onnx', 'nllb'}:
        raise ValueError(f'Only NLLB ONNX is supported: {backend}')
    return LocalNllbOnnxTranslator(
        config.translation.nllb_model_path,
        cpu_threads=config.translation.cpu_threads,
        max_input_tokens=config.translation.max_input_tokens,
        max_new_tokens=config.translation.max_new_tokens,
        num_beams=1,
    )
