from __future__ import annotations

import argparse
import gc
from pathlib import Path
import time
import wave

import numpy as np

from app.asr.gipformer_engine import LocalGipformerOfflineEngine, resolve_gipformer_files
from app.asr.sensevoice_engine import LocalSenseVoiceEngine
from app.audio.pyaudio_capture import PyAudioCapture, list_devices
from app.config import AppConfig
from app.pipeline_meeting import MeetingSTTPipeline
from app.presets import PRESETS, apply_preset
from app.storage.session_store import SessionStore
from app.runtime_paths import configure_runtime
from app.user_config import ensure_user_configs


def cmd_devices() -> int:
    devices = list_devices()
    print(f"{'IDX':>4}  {'IN':>2} {'OUT':>3} {'RATE':>6} {'LOOP':>4}  NAME")
    for d in devices:
        print(f"{d.index:>4}  {d.max_input_channels:>2} {d.max_output_channels:>3} "
              f"{d.default_sample_rate:>6} {('yes' if d.is_loopback else ''):>4}  {d.name}")
    return 0


def cmd_check(config: AppConfig) -> int:
    ok = True
    print("[V15.2 STT] Checking local Vietnamese + Japanese ASR...")
    try:
        files = resolve_gipformer_files(config.asr.gipformer_model_path, config.asr.gipformer_precision)
        vi_ok = all(p.is_file() for p in (files.encoder, files.decoder, files.joiner, files.tokens))
    except Exception as exc:
        vi_ok = False
        print(f"[MISSING] Gipformer: {exc}")
    print(f"[{'OK' if vi_ok else 'MISSING'}] VI / Gipformer: {config.asr.gipformer_model_path}")
    ok &= vi_ok

    ja_ok = Path(config.asr.sensevoice_model_path).is_dir()
    print(f"[{'OK' if ja_ok else 'MISSING'}] JA / SenseVoice: {config.asr.sensevoice_model_path}")
    ok &= ja_ok

    vad_ok = Path(config.vad.model_path).is_file()
    print(f"[{'OK' if vad_ok else 'MISSING'}] Silero VAD: {config.vad.model_path}")
    ok &= vad_ok

    if ok:
        try:
            vi = LocalGipformerOfflineEngine(
                model_dir=config.asr.gipformer_model_path,
                precision=config.asr.gipformer_precision,
                num_threads=1,
                decoding_method=config.asr.gipformer_decoding_method,
                max_active_paths=config.asr.gipformer_max_active_paths,
                sample_rate=config.audio.target_sample_rate,
                hotwords_file=config.asr.gipformer_hotwords_file,
                hotwords_score=config.asr.gipformer_hotwords_score,
                blank_penalty=config.asr.gipformer_blank_penalty,
                simple_punctuation=config.asr.gipformer_simple_punctuation,
            )
            print("[OK] Gipformer runtime loaded")
            del vi
            gc.collect()
        except Exception as exc:
            print(f"[ERROR] Gipformer runtime: {exc}")
            ok = False
        try:
            ja = LocalSenseVoiceEngine(
                model_path=config.asr.sensevoice_model_path,
                language="ja",
                device=config.asr.sensevoice_device,
                num_threads=1,
                sample_rate=config.audio.target_sample_rate,
                partial_use_itn=config.asr.sensevoice_partial_use_itn,
                final_use_itn=config.asr.sensevoice_final_use_itn,
                ban_emo_unk=config.asr.sensevoice_ban_emo_unk,
                rich_transcription=config.asr.sensevoice_rich_transcription,
            )
            print("[OK] SenseVoice runtime loaded")
            del ja
            gc.collect()
        except Exception as exc:
            print(f"[ERROR] SenseVoice runtime: {exc}")
            ok = False
    print(f"[INFO] Translation configured: {config.translation.backend if config.translation.enabled else 'disabled'}")
    if config.translation.enabled:
        model_path = (
            config.translation.nllb_model_path
            if config.translation.backend == "nllb_onnx"
            else config.translation.model_path
        )
        print(f"[INFO] Translation model: {model_path} (not verified by ASR check)")
    return 0 if ok else 1


def cmd_probe(config: AppConfig, source_name: str, seconds: float) -> int:
    if source_name not in {"mic", "system"}:
        return 2
    device_index = config.audio.mic_device_index if source_name == "mic" else config.audio.system_device_index
    channel_mode = config.audio.mic_channel_mode if source_name == "mic" else config.audio.system_channel_mode
    source = PyAudioCapture(
        source=source_name,
        target_sample_rate=config.audio.target_sample_rate,
        device_index=device_index,
        frames_per_buffer=config.audio.frames_per_buffer,
        queue_seconds=config.audio.queue_seconds,
        resampler_backend=config.audio.resampler_backend,
        resampler_quality=config.audio.resampler_quality,
        channel_mode=channel_mode,
    )
    seconds = max(1.0, float(seconds))
    print(f"Recording {seconds:.1f}s from {source_name}...")
    source.start()
    chunks: list[np.ndarray] = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            x = source.read(timeout=0.25)
            if x is not None and x.size:
                chunks.append(x)
    finally:
        source.stop()
    if not chunks:
        print("No audio captured.")
        return 1
    audio = np.concatenate(chunks).astype(np.float32, copy=False)
    out_dir = Path(config.storage.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"probe_{source_name}.wav"
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(config.audio.target_sample_rate)
        wf.writeframes(pcm16.tobytes())
    rms = float(np.sqrt(np.mean(audio * audio) + 1e-12))
    print(f"Saved: {out}\nRMS: {rms:.5f}  Peak: {float(np.max(np.abs(audio))):.5f}")
    return 0


def cmd_recover(config: AppConfig, session_path: str) -> int:
    path = Path(session_path)
    if not path.is_dir():
        print(f"Session directory not found: {path}")
        return 2
    store = SessionStore(config.storage.output_dir, postprocess=config.postprocess,
                         reliability=config.reliability, session_dir=path)
    state = store.recover()
    store.close(status="recovered")
    print(f"Recovered: {path}")
    print(f"Segments : {state.get('transcript_entries', 0)}")
    print(f"Timeline : {state.get('timeline_end_s', 0.0):.2f}s")
    print("Rebuilt  : transcript.txt/.srt + YOU/REMOTE transcript files")
    return 0


def main() -> int:
    configure_runtime()  # Supports launching from a shortcut/other working directory.
    ensure_user_configs()  # Never replace user-edited branding/ASR settings.
    p = argparse.ArgumentParser(description="SoundText V15.2: local realtime VI/JA speech-to-text on both YOU and REMOTE")
    p.add_argument("command", choices=["run", "devices", "check", "probe", "recover"], nargs="?", default="run")
    p.add_argument("--config", default="config_stt_vi_ja.json")
    p.add_argument("--preset", choices=list(PRESETS), default=None)
    p.add_argument("--audio-mode", choices=["both", "remote", "you"], default=None)
    p.add_argument("--mic-device-index", type=int, default=None)
    p.add_argument("--system-device-index", type=int, default=None)
    p.add_argument("--mic-channel-mode", choices=["auto", "mic_speech", "mean", "loudest", "left", "right"], default=None)
    p.add_argument("--system-channel-mode", choices=["auto", "stereo_speech", "mean", "loudest", "left", "right"], default=None)
    p.add_argument("--no-ui", action="store_true")
    p.add_argument("--translation-backend", choices=["nllb_onnx"], default=None)
    p.add_argument("--no-speakers", action="store_true", help="Disable speaker models to prioritize latency")
    p.add_argument("--no-early-speaker-split", action="store_true", help="Only diarize completed utterances; no extra streaming probes")
    p.add_argument("--resume-session", default=None)
    p.add_argument("--session", default=None)
    p.add_argument("--probe-source", choices=["mic", "system"], default="system")
    p.add_argument("--seconds", type=float, default=8.0)
    args = p.parse_args()

    config = AppConfig.load(args.config)
    config.reliability.preset = apply_preset(config, args.preset or config.reliability.preset)
    # Runtime-only resource budget for 8 GiB frozen EXEs. Never rewrite the
    # user's configuration file or change normal Python launches.
    from app.runtime_memory import is_low_memory_frozen
    if is_low_memory_frozen():
        config.asr.cpu_threads = min(int(config.asr.cpu_threads), 4)
        config.translation.cpu_threads = min(int(config.translation.cpu_threads), 1)
        print("[RAM SAFE] 8 GB EXE: ASR threads <= 4, translation threads <= 1")
    if args.translation_backend:
        config.translation.backend = args.translation_backend
    if args.audio_mode:
        config.meeting.audio_mode = args.audio_mode
    if args.mic_device_index is not None:
        config.audio.mic_device_index = args.mic_device_index
    if args.system_device_index is not None:
        config.audio.system_device_index = args.system_device_index
    if args.mic_channel_mode:
        config.audio.mic_channel_mode = args.mic_channel_mode
    if args.system_channel_mode:
        config.audio.system_channel_mode = args.system_channel_mode
    if args.no_speakers:
        config.speakers.enabled = False
    if args.no_early_speaker_split:
        config.speakers.early_split_enabled = False
    if args.no_ui:
        config.meeting.ui_enabled = False
    if args.resume_session:
        config.reliability.resume_session = args.resume_session

    if args.command == "devices":
        return cmd_devices()
    if args.command == "check":
        return cmd_check(config)
    if args.command == "probe":
        return cmd_probe(config, args.probe_source, args.seconds)
    if args.command == "recover":
        if not args.session:
            p.error("recover requires --session PATH")
        return cmd_recover(config, args.session)

    if config.meeting.ui_enabled:
        try:
            from app.ui.ctk_window import run_ctk_application
        except ImportError as exc:
            print("[UI ERROR] CustomTkinter is required for the desktop interface.")
            print("Install via SoundText.bat option 11 (pip install -r requirements.txt).")
            print(f"Details: {exc}")
            return 2
        return run_ctk_application(config)
    MeetingSTTPipeline(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
