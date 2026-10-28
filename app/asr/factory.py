from __future__ import annotations

from app.config import AppConfig
from .gipformer_engine import LocalGipformerOfflineEngine
from .sensevoice_engine import LocalSenseVoiceEngine


def build_asr_engine(config: AppConfig, *, language: str, threads: int):
    """Build a local CPU STT engine.

    vi -> Gipformer 1.5 65M RNNT
    ja/en/auto -> SenseVoiceSmall (FunASR)
    """
    lang = (language or '').strip().lower()
    if lang == 'vi':
        return LocalGipformerOfflineEngine(
            model_dir=config.asr.gipformer_model_path,
            precision=config.asr.gipformer_precision,
            num_threads=max(1, int(threads)),
            decoding_method=config.asr.gipformer_decoding_method,
            max_active_paths=config.asr.gipformer_max_active_paths,
            sample_rate=config.audio.target_sample_rate,
            hotwords_file=config.asr.gipformer_hotwords_file,
            hotwords_score=config.asr.gipformer_hotwords_score,
            blank_penalty=config.asr.gipformer_blank_penalty,
            simple_punctuation=config.asr.gipformer_simple_punctuation,
        ), 'gipformer'
    if lang in {'ja', 'en', 'auto'}:
        return LocalSenseVoiceEngine(
            model_path=config.asr.sensevoice_model_path,
            language=lang,
            device=config.asr.sensevoice_device or 'cpu',
            num_threads=max(1, int(threads)),
            sample_rate=config.audio.target_sample_rate,
            partial_use_itn=config.asr.sensevoice_partial_use_itn,
            final_use_itn=config.asr.sensevoice_final_use_itn,
            ban_emo_unk=config.asr.sensevoice_ban_emo_unk,
            rich_transcription=config.asr.sensevoice_rich_transcription,
        ), 'sensevoice'
    raise ValueError(f'Unsupported STT language: {language!r}. Choose vi, ja, en, or auto.')
