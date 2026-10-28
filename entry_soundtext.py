"""Frozen entry point: locate external files before loading inference libraries."""
from __future__ import annotations

import sys
import traceback

from app.runtime_paths import configure_runtime

root = configure_runtime()
from app.user_config import ensure_user_configs
ensure_user_configs(root)

if getattr(sys, "frozen", False):
    # --windowed on Windows has no console. Preserve startup diagnostics in a
    # readable file rather than losing import/load exceptions silently.
    log_dir = root / "output" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "startup.log"
        if log_path.exists() and log_path.stat().st_size > 5_000_000:
            log_path.replace(log_dir / "startup.previous.log")
        log = log_path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = log
        sys.stderr = log
    except OSError:
        pass

# Install the Windows process-commit ceiling before Torch, FunASR or Optimum
# are imported. This affects the 8 GB frozen EXE ONLY, not Python source runs.
if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    from app.runtime_memory import install_windows_memory_cap, log_memory, is_low_memory_frozen
    if is_low_memory_frozen():
        import os
        # Keep native CPU thread pools from reserving unnecessary stacks.
        os.environ["OMP_NUM_THREADS"] = "2"
        os.environ["MKL_NUM_THREADS"] = "2"
        os.environ["OPENBLAS_NUM_THREADS"] = "2"
    install_windows_memory_cap()
    log_memory("EXE entry")

try:
    from main import main
    # Lightweight smoke-check invoked by the Windows build script. This
    # verifies that the frozen bundle can actually import the application
    # without opening the UI or loading model weights.
    if __name__ == "__main__":
        if "--check-bundle" in sys.argv[1:]:
            print("[OK] Frozen SoundText can import the main module")
            raise SystemExit(0)
        if "--check-translation-runtime" in sys.argv[1:]:
            # No local model weights needed: this is an import/source test.
            # Import the same classes used by LocalNllbOnnxTranslator.
            import inspect
            from transformers import AutoTokenizer
            from optimum.onnxruntime import ORTModelForSeq2SeqLM
            import onnxruntime  # noqa: F401
            for cls in (AutoTokenizer, ORTModelForSeq2SeqLM):
                lines, _ = inspect.getsourcelines(cls)
                if not lines:
                    raise RuntimeError(f"Python source unavailable for {cls.__module__}.{cls.__name__}")
            print("[OK] Frozen NLLB translation dependencies and sources are readable")
            raise SystemExit(0)
        if "--check-translation-model" in sys.argv[1:]:
            # Manual diagnostic AFTER copying models/ next to SoundText.exe.
            # No microphone, UI, or recording session is started.
            from app.config import AppConfig
            from app.translation.factory import build_translation_engine
            config = AppConfig.load(root / "config_stt_vi_ja.json")
            translator = build_translation_engine(config)
            translator._load()  # load actual ONNX weights and tokenizer
            result, _elapsed = translator.translate("Hello.", "en", "vi")
            if not result.strip():
                raise RuntimeError("NLLB returned an empty translation")
            print(f"[OK] Frozen NLLB loaded and translated: {result}")
            raise SystemExit(0)
        if "--check-funasr-source" in sys.argv[1:]:
            # Exercise the same registry imports as SenseVoice initialization,
            # without loading large model weights or accessing an audio device.
            import inspect
            from funasr import AutoModel
            from funasr.models.sense_voice.model import SenseVoiceSmall
            lines, _ = inspect.getsourcelines(SenseVoiceSmall)
            if not lines:
                raise RuntimeError("Bundled FunASR source file is not readable")
            print("[OK] Frozen FunASR/SenseVoice source is readable")
            raise SystemExit(0)
        raise SystemExit(main())
except SystemExit:
    raise
except BaseException:
    report = traceback.format_exc()
    try:
        print(report, file=sys.stderr)
    except Exception:
        pass
    if sys.platform == "win32" and not any(
        flag in sys.argv[1:] for flag in ("--check-bundle", "--check-funasr-source",
                                           "--check-translation-runtime", "--check-translation-model")
    ):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "Không thể khởi động SoundText.\n"
                "Xem output\\logs\\startup.log để biết chi tiết.",
                "SoundText — Lỗi khởi động", 0x10,
            )
        except Exception:
            pass
    raise
