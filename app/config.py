from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class AudioConfig:
    source: str = "meeting"  # fixed for V15 STT-only
    device_index: int | None = None
    mic_device_index: int | None = None
    system_device_index: int | None = None
    target_sample_rate: int = 16000
    frames_per_buffer: int = 1024
    queue_seconds: float = 6.0
    resampler_backend: str = "soxr"
    resampler_quality: str = "HQ"
    channel_mode: str = "auto"
    mic_channel_mode: str = "mic_speech"
    system_channel_mode: str = "stereo_speech"


@dataclass(slots=True)
class VadConfig:
    model_path: str = "models/silero-vad/silero_vad.onnx"
    threshold: float = 0.45
    negative_threshold: float = 0.30
    min_silence_ms: int = 550
    pre_roll_ms: int = 400
    post_roll_ms: int = 220
    min_speech_ms: int = 280
    partial_interval_ms: int = 900
    partial_window_s: float = 4.5
    max_utterance_s: float = 12.0
    hard_split_overlap_ms: int = 1000

    # Microphone speech needs longer context than clean system loopback. These
    # values apply only to the YOU lane; REMOTE keeps the base VAD profile.
    mic_threshold: float = 0.40
    mic_negative_threshold: float = 0.22
    mic_min_silence_ms: int = 950
    mic_pre_roll_ms: int = 550
    mic_post_roll_ms: int = 320
    mic_min_speech_ms: int = 350
    mic_partial_interval_ms: int = 700
    mic_partial_window_s: float = 6.5
    mic_max_utterance_s: float = 18.0
    mic_hard_split_overlap_ms: int = 1400


@dataclass(slots=True)
class AsrConfig:
    gipformer_model_path: str = "models/gipformer1.5-65m-rnnt"
    sensevoice_model_path: str = "models/sensevoice-small"
    cpu_threads: int = 6
    show_metrics: bool = True
    show_live: bool = True
    show_language: bool = True
    live_max_chars: int = 180
    live_show_metrics: bool = False

    sensevoice_device: str = "cpu"
    sensevoice_partial_use_itn: bool = False
    sensevoice_final_use_itn: bool = True
    sensevoice_ban_emo_unk: bool = False
    sensevoice_rich_transcription: bool = False

    gipformer_precision: str = "int8"
    gipformer_decoding_method: str = "modified_beam_search"
    gipformer_max_active_paths: int = 4
    gipformer_hotwords_file: str = ""
    gipformer_hotwords_score: float = 1.5
    gipformer_blank_penalty: float = 0.0
    gipformer_simple_punctuation: bool = True



@dataclass(slots=True)
class TranslationConfig:
    enabled: bool = True
    backend: str = "nllb_onnx"
    model_path: str = "models/nllb-trilingual-en-vi-ja-onnx"
    target_language: str = "vi"  # fixed UI/runtime target
    cpu_threads: int = 2
    max_input_tokens: int = 192
    max_new_tokens: int = 112
    num_beams: int = 1
    nllb_model_path: str = "models/nllb-trilingual-en-vi-ja-onnx"


@dataclass(slots=True)
class SpeakerConfig:
    # Enabled for both MIC and REMOTE when the two local ONNX files exist.
    enabled: bool = True
    segmentation_path: str = "models/speaker-diarization/segmentation.onnx"
    embedding_path: str = "models/speaker-embedding/redimnet_b2_vox2.onnx"
    cpu_threads: int = 1
    min_turn_s: float = 0.50
    early_split_enabled: bool = True
    probe_interval_s: float = 2.2
    similarity_threshold: float = 0.60
    max_backlog: int = 2


@dataclass(slots=True)
class MeetingConfig:
    enabled: bool = True
    mic_language: str = "vi"
    system_language: str = "ja"
    ui_enabled: bool = True
    ui_title: str = "SoundText - Realtime STT + Vietnamese Translation"
    ui_refresh_ms: int = 60
    ui_history_items: int = 120
    audio_mode: str = "both"  # both | remote | you



@dataclass(slots=True)
class NoiseReductionConfig:
    enabled: bool = True
    highpass_hz: float = 80.0
    strength: float = 0.65
    spectral_floor: float = 0.28
    learn_rate: float = 0.025
    speech_protect_ratio: float = 2.4
    min_learn_dbfs: float = -58.0
    max_learn_dbfs: float = -28.0


@dataclass(slots=True)
class MicGuardConfig:
    enabled: bool = True
    min_dbfs: float = -52.0
    noise_margin_db: float = 8.0
    echo_enabled: bool = True
    echo_correlation: float = 0.32
    echo_window_ms: int = 160
    echo_max_delay_ms: int = 220
    echo_lag_step_ms: int = 20
    downsample: int = 4


@dataclass(slots=True)
class PostprocessConfig:
    enabled: bool = True
    preserve_raw: bool = True
    case_mode: str = "sentence"
    add_terminal_punctuation: bool = True
    collapse_repetitions: bool = True
    merge_gap_s: float = 1.25
    max_turn_s: float = 45.0
    max_turn_chars: int = 900


@dataclass(slots=True)
class ReliabilityConfig:
    preset: str = "balanced"
    enabled: bool = True
    resume_session: str = ""
    resume_gap_s: float = 0.5
    artifact_refresh_interval_s: float = 600.0
    artifact_refresh_events: int = 128
    checkpoint_interval_s: float = 10.0
    journal_fsync_interval_s: float = 2.0
    health_interval_s: float = 60.0
    atomic_artifacts: bool = True


@dataclass(slots=True)
class StorageConfig:
    output_dir: str = "output"


@dataclass(slots=True)
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    speakers: SpeakerConfig = field(default_factory=SpeakerConfig)
    meeting: MeetingConfig = field(default_factory=MeetingConfig)
    mic_guard: MicGuardConfig = field(default_factory=MicGuardConfig)
    noise_reduction: NoiseReductionConfig = field(default_factory=NoiseReductionConfig)
    postprocess: PostprocessConfig = field(default_factory=PostprocessConfig)
    reliability: ReliabilityConfig = field(default_factory=ReliabilityConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            audio=AudioConfig(**raw.get("audio", {})),
            vad=VadConfig(**raw.get("vad", {})),
            asr=AsrConfig(**raw.get("asr", {})),
            translation=TranslationConfig(**raw.get("translation", {})),
            speakers=SpeakerConfig(**raw.get("speakers", {})),
            meeting=MeetingConfig(**raw.get("meeting", {})),
            mic_guard=MicGuardConfig(**raw.get("mic_guard", {})),
            noise_reduction=NoiseReductionConfig(**raw.get("noise_reduction", {})),
            postprocess=PostprocessConfig(**raw.get("postprocess", {})),
            reliability=ReliabilityConfig(**raw.get("reliability", {})),
            storage=StorageConfig(**raw.get("storage", {})),
        )
