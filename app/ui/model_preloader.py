"""Prepare local inference sessions behind the splash, without opening audio or a session.

Each prepared engine belongs to one future recording session.  Never call
inference on these objects from the Tk event thread.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import sys
import traceback
from typing import Any


@dataclass
class PreparedModels:
    asr: dict[str, tuple[str, int, Any, str]] = field(default_factory=dict)
    vad: dict[str, Any] = field(default_factory=dict)
    speaker: Any = None
    translator: Any = None
    speaker_enabled: bool = False
    translation_enabled: bool = False
    warnings: list[str] = field(default_factory=list)

    def take_asr(self, source: str, language: str, threads: int):
        item = self.asr.pop(source, None)
        if item is not None and item[0] == language and item[1] == threads:
            return item[2], item[3]
        return None

    def take_vad(self, source: str):
        vad = self.vad.pop(source, None)
        if vad is not None:
            vad.reset()
        return vad

    def take_speaker(self, enabled: bool):
        if not enabled or not self.speaker_enabled:
            return None
        speaker, self.speaker = self.speaker, None
        return speaker

    def take_translator(self, enabled: bool):
        if not enabled or not self.translation_enabled:
            return None
        translator, self.translator = self.translator, None
        return translator


def prepare_models(config, progress=None) -> PreparedModels:
    """Construct real inference sessions once, not a throwaway warmup instance.

    If a critical ASR/VAD dependency fails, raise to let the GUI explain why
    recording cannot start. Optional diarization/MT failures match pipeline's
    existing graceful-degradation policy.
    """
    from app.runtime_memory import ensure_available_memory, is_low_memory_frozen, log_memory
    from app.asr.factory import build_asr_engine
    from app.vad.silero_onnx import SileroOnnxVad

    ready = PreparedModels()
    low_ram = is_low_memory_frozen()
    log_memory("splash begin")
    total = max(2, int(config.asr.cpu_threads))
    threads = {"mic": max(1, total // 2), "system": max(1, total - max(1, total // 2))}
    languages = {"mic": config.meeting.mic_language, "system": config.meeting.system_language}

    # 8 GB EXE: preloading BOTH models would hold two ASR backends in memory
    # before recording starts. The second lane is deferred to its first speech.
    mode = str(getattr(config.meeting, "audio_mode", "both")).lower()
    if low_ram:
        asr_sources = ("mic",) if mode in ("you", "mic") else ("system",)
        ready.warnings.append(
            "Máy 8 GB: chỉ tải trước nhận diện của nguồn ưu tiên; nguồn còn lại "
            "sẽ tải khi có tiếng nói."
        )
    else:
        asr_sources = ("mic", "system")

    for source in asr_sources:
        ensure_available_memory("khởi tạo mô hình nhận diện", minimum_gib=2.0 if low_ram else 4.0)
        log_memory(f"before splash ASR {source}")
        if progress:
            progress(f"Đang chuẩn bị nhận diện {'microphone' if source == 'mic' else 'âm thanh máy'}…")
        engine, backend = build_asr_engine(config, language=languages[source], threads=threads[source])
        ready.asr[source] = (languages[source], threads[source], engine, backend)
        log_memory(f"after splash ASR {source}")

    if progress:
        progress("Đang chuẩn bị nhận diện giọng nói…")
    for source in ("mic", "system"):
        ready.vad[source] = SileroOnnxVad(config.vad.model_path,
                                         sample_rate=config.audio.target_sample_rate)

    if config.speakers.enabled and mode not in ("you", "mic"):
        if progress:
            progress("Đang chuẩn bị phân biệt người nói…")
        try:
            ensure_available_memory("khởi tạo phân biệt người nói", minimum_gib=1.8 if low_ram else 3.0)
            log_memory("before speaker model")
            from app.speakers.diarizer import SpeakerDiarizer
            ready.speaker = SpeakerDiarizer(
                config.speakers.segmentation_path, config.speakers.embedding_path,
                sample_rate=config.audio.target_sample_rate,
                threads=config.speakers.cpu_threads,
                min_turn_s=config.speakers.min_turn_s,
                similarity_threshold=config.speakers.similarity_threshold,
            )
            ready.speaker_enabled = True
            log_memory("after speaker model")
        except Exception as exc:
            ready.warnings.append(f"Phân biệt người nói không khả dụng: {exc}")

    if config.translation.enabled and getattr(sys, "frozen", False):
        # Loading all ASR and 3 large NLLB ONNX graphs under splash can make
        # Windows unresponsive. In the EXE, keep the original on-demand NLLB
        # behavior. The translation worker checks free RAM before actual load.
        ready.warnings.append(
            "Chế độ tiết kiệm RAM: mô hình dịch sẽ chỉ tải khi cần dịch câu đầu tiên."
        )
    elif config.translation.enabled:
        if progress:
            progress("Đang tải mô hình dịch tiếng Việt…")
        try:
            from app.translation.factory import build_translation_engine
            ready.translator = build_translation_engine(config)
            # NLLB's constructor validates files but does not load ONNX weights.
            # Loading now removes the expensive first-translation pause.
            ready.translator._load()
            ready.translation_enabled = True
        except Exception as exc:
            # Important for frozen builds: the short UI warning alone hides
            # which package raises `could not get source code`.
            print("[TRANSLATION MODEL ERROR]\n" + traceback.format_exc(),
                  file=sys.stderr, flush=True)
            ready.warnings.append(f"Không thể tải mô hình dịch: {exc}")
            ready.translator = None
    log_memory("splash end")
    return ready
