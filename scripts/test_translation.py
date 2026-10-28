"""Translate a single Japanese/English sentence with the selected LOCAL engine.

Run from menu 16 or: python scripts/test_translation.py --backend nllb_onnx
This is an isolated CPU smoke test, not a live audio benchmark.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import AppConfig
from app.translation.factory import build_translation_engine


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", choices=["nllb_onnx"], default=None)
    p.add_argument("--language", choices=["ja", "en"], default="ja")
    p.add_argument("--text", default=None)
    args = p.parse_args()
    config = AppConfig.load(ROOT / "config_stt_vi_ja.json")
    if args.backend:
        config.translation.backend = args.backend
    path = ROOT / config.translation.nllb_model_path
    if not path.is_dir():
        print(f"[MISSING] Model directory: {path}")
        return 2
    text = args.text if args.text is not None else input("Japanese/English sentence: ")
    if not text.strip():
        print("[SKIP] Empty input.")
        return 2
    print(f"Engine: {config.translation.backend}\nModel : {path}\nLoading and running on CPU...")
    t0 = time.perf_counter()
    try:
        engine = build_translation_engine(config)
        result, inference_s = engine.translate(text, args.language, "vi")
    except Exception as exc:
        print(f"[ERROR] Translation failed: {type(exc).__name__}: {exc}")
        return 1
    print(f"\n[VI] {result or '(empty result)'}")
    print(f"[MT] {inference_s:.2f}s (excludes first model load, if any)")
    print(f"[TOTAL FIRST CALL] {time.perf_counter() - t0:.2f}s")
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
