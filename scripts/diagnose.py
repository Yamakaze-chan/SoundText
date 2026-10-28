"""Audit SoundText's model files and Python dependencies without downloading anything.

Quick mode checks NLLB/ASR/speaker model paths and Python dependencies.
--imports isolates Python imports to catch DLL/runtime problems.
"""
from __future__ import annotations

import argparse
from importlib import metadata, util
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

# Distribution name, import name. Missing imports can also be caused by DLL problems.
PACKAGES = [
    ("customtkinter", "customtkinter"),
    ("Pillow", "PIL"),
    ("numpy", "numpy"),
    ("onnxruntime", "onnxruntime"),
    ("PyAudioWPatch", "pyaudiowpatch"),
    ("sherpa-onnx", "sherpa_onnx"),
    ("sherpa-onnx-core", None),
    ("funasr", "funasr"),
    ("torch", "torch"),
    ("torchaudio", "torchaudio"),
    ("transformers", "transformers"),
    ("accelerate", "accelerate"),
    ("huggingface_hub", "huggingface_hub"),
    ("sentencepiece", "sentencepiece"),
    ("optimum-onnx", "optimum.onnxruntime"),
    ("requests", "requests"),
]


def get_config(root: Path) -> dict:
    with (root / "config_stt_vi_ja.json").open(encoding="utf-8") as stream:
        return json.load(stream)


def path_under_root(root: Path, value: str) -> Path:
    raw = Path(value)
    return raw if raw.is_absolute() else root / raw


def verify_file(path: Path, lower_bound: int = 1) -> bool:
    return path.is_file() and path.stat().st_size >= lower_bound


def model_section(root: Path, config: dict) -> tuple[int, int, int]:
    """Return counts of required failures, optional warnings, required ready groups."""
    print("\n=== MODEL FILES ===")
    required_bad, optional_bad, required_ok = 0, 0, 0

    checks: list[tuple[str, Path, list[tuple[str, int]], bool]] = []
    vad = path_under_root(root, config.get("vad", {}).get("model_path", "models/silero-vad/silero_vad.onnx"))
    checks.append(("Silero VAD", vad.parent, [(vad.name, 1024)], True))
    asr = config.get("asr", {})
    gip_path = path_under_root(root, asr.get("gipformer_model_path", "models/gipformer1.5-65m-rnnt"))
    suffix = ".int8.onnx" if asr.get("gipformer_precision", "int8").lower() == "int8" else ".onnx"
    gip_files = [(f"{part}{suffix}", 1024) for part in ("encoder", "decoder", "joiner")]
    gip_files += [("tokens.txt", 1)]
    if str(asr.get("gipformer_hotwords_file", "")).strip():
        hotwords = path_under_root(root, asr["gipformer_hotwords_file"])
        checks.append(("Gipformer hotwords", hotwords.parent, [(hotwords.name, 1)], True))
    checks.append(("Gipformer VI", gip_path, gip_files, True))
    sensevoice_path = path_under_root(root, asr.get("sensevoice_model_path", "models/sensevoice-small"))
    checks.append(("SenseVoice JA/EN", sensevoice_path, [
        ("config.yaml", 1), ("model.pt", 1024 * 1024),
        ("chn_jpn_yue_eng_ko_spectok.bpe.model", 1024), ("am.mvn", 1),
    ], True))

    trans = config.get("translation", {})
    backend = str(trans.get("backend", "nllb_onnx")).lower()
    trans_enabled = bool(trans.get("enabled", True))
    nllb_active = trans_enabled and backend in {"nllb_onnx", "nllb", "onnx"}
    nllb_path = path_under_root(root, trans.get("nllb_model_path", "models/nllb-trilingual-en-vi-ja-onnx"))
    nllb_files = [(name, 1) for name in (
        "config.json", "tokenizer.json", "tokenizer_config.json", "sentencepiece.bpe.model"
    )] + [(name, 1024) for name in (
        "encoder_model_quantized.onnx", "decoder_model_quantized.onnx", "decoder_with_past_model_quantized.onnx"
    )]
    checks.append(("NLLB translation", nllb_path, nllb_files, nllb_active))

    spk = config.get('speakers', {})
    if spk.get('enabled', True):
        paths = [
            ('Speaker segmentation', spk.get('segmentation_path', 'models/speaker-diarization/segmentation.onnx'), 400_000),
            ('Speaker embedding', spk.get('embedding_path', 'models/speaker-embedding/redimnet_b2_vox2.onnx'), 4_000_000),
        ]
        for title, filename, minimum in paths:
            f = path_under_root(root, filename)
            # Missing speaker models do not prevent basic ASR/translation, but
            # are required for the optional speaker feature when enabled.
            checks.append((title, f.parent, [(f.name, minimum)], False))

    for name, directory, files, required in checks:
        missing = [filename for filename, min_size in files
                   if not verify_file(directory / filename, min_size)]
        if missing:
            if required:
                required_bad += 1
                status = "MISSING"
            else:
                optional_bad += 1
                status = "OPTIONAL"
            print(f"[{status}] {name} : {directory}")
            for filename in missing:
                print(f"    - {filename}")
        else:
            if required:
                required_ok += 1
            print(f"[OK] {name} : {directory}")
        if required and not (directory.resolve() == (root / "models").resolve() or
                             (root / "models").resolve() in directory.resolve().parents):
            print(f"  [WARN] Model is outside ./models/: {directory}")
    return required_bad, optional_bad, required_ok


def try_import(module: str, seconds: float = 25.0, root: Path = ROOT) -> tuple[bool, str]:
    # External packages may crash process with missing DLLs: isolate them.
    if module == "optimum.onnxruntime":
        source = "from optimum.onnxruntime import ORTModelForSeq2SeqLM"
    else:
        source = "import " + module
    try:
        proc = subprocess.run([sys.executable, "-c", source], cwd=str(root),
                              capture_output=True, text=True, timeout=seconds,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, f"import timed out after {seconds:.0f}s"
    if proc.returncode:
        msg = (proc.stderr or proc.stdout or "process failed").strip().splitlines()
        return False, (msg[-1] if msg else "import failure")[:220]
    return True, ""


def package_section(config: dict, imports: bool, root: Path = ROOT) -> int:
    print("\n=== PYTHON LIBRARIES ===")
    missing_count = 0
    backend = config.get("translation", {}).get("backend", "nllb_onnx")
    translation_enabled = config.get("translation", {}).get("enabled", True)
    needed = list(PACKAGES)
    if config.get("audio", {}).get("resampler_backend", "soxr") == "soxr":
        needed.append(("soxr", "soxr"))
    # Python packages for former LFM backend are optional after removing summary.
    optional = {"huggingface_hub", "requests", "accelerate"}
    if not translation_enabled or backend not in {"nllb_onnx", "onnx", "nllb"}:
        optional.update({"optimum-onnx", "sentencepiece"})
    # Keep distribution metadata checks inexpensive in quick mode.
    for dist_name, module in needed:
        required = dist_name not in optional
        try:
            version = metadata.version(dist_name)
        except metadata.PackageNotFoundError:
            version = None
        ok = version is not None
        error = ""
        if ok and imports and module:
            ok, error = try_import(module, root=root)
        elif ok and module:
            # Lightweight presence check; no high-cost torch/FunASR imports in quick mode.
            try:
                if util.find_spec(module.split(".", 1)[0]) is None:
                    ok, error = False, "Python import name was not found"
            except Exception as exc:
                ok, error = False, str(exc)
        if ok:
            print(f"[OK] {dist_name} {version}" + (" (import OK)" if imports and module else ""))
        else:
            if required:
                missing_count += 1
            level = "MISSING" if required else "OPTIONAL"
            print(f"[{level}] {dist_name}" + (f" : {error}" if error else ""))
    try:
        import tkinter
        print(f"[OK] tkinter {tkinter.TkVersion} (Python built-in)")
    except Exception as exc:
        missing_count += 1
        print(f"[MISSING] tkinter: {exc} (install a Python build with Tk support)")
    return missing_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imports", action="store_true", help="Actually import packages in isolated processes (slower)")
    parser.add_argument("--root", type=Path, default=ROOT, help="Project root (for diagnostics/tests)")
    args = parser.parse_args()
    root = args.root.resolve()
    print("SoundText - Offline dependency/model diagnostic")
    print("Project:", root)
    print("Python :", sys.executable)
    print("Version:", platform.python_version(), "/", platform.system(), platform.machine(), "/", struct.calcsize("P") * 8, "bit")
    print("No downloads, installations or model loads are performed.")
    python_bad = sys.version_info < (3, 10) or struct.calcsize("P") * 8 != 64
    if python_bad:
        print("[MISSING] 64-bit Python 3.10+ is required for this SoundText package.")
    try:
        config = get_config(root)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print("[ERROR] Cannot load config_stt_vi_ja.json:", exc)
        return 2
    model_bad, optional_bad, model_ok = model_section(root, config)
    lib_bad = package_section(config, args.imports, root) + int(python_bad)
    print("\n=== RESULT ===")
    print(f"Required model groups ready: {model_ok}; missing/incomplete: {model_bad}")
    print(f"Missing/broken required libraries: {lib_bad}")
    print(f"Optional model groups not ready: {optional_bad} (speaker models when enabled)")
    if model_bad:
        print("Fix models using menu 8 (ASR), 9 (speakers), or 10 (NLLB), then check again.")
    if lib_bad:
        print("Fix packages using menu 11 (pip requirements), then check again.")
        if not args.imports:
            print("Use menu 14 for import/DLL compatibility checks as well.")
    if optional_bad:
        print("Optional speaker models absent: use menu 9 to enable speaker detection on both lanes.")
    if not model_bad and not lib_bad:
        print("[PASS] Required models/packages appear present.")
        if not args.imports:
            print("For DLL checks, run Deep diagnostics (menu 14).")
        print("Presence checks cannot guarantee real-time performance or successful inference.")
    return 1 if (model_bad or lib_bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
