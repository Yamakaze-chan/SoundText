"""Prepare official unquantized LFM2.5-1.2B-Instruct BF16 (safetensors).

All checkpoints and Hugging Face caches remain within project ./models.
SoundText now uses LFM by default; NLLB is kept for A/B testing.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys

from model_paths import configure_local_model_cache

REPO_ID = "LiquidAI/LFM2.5-1.2B-Instruct"
# Official checkpoint metadata from the Hugging Face model repository.
EXPECTED_SHA256 = "1ba63d9adb03ae43581db0e136e4416febe0441aff7296397bd455fb6017f73a"
FILES = (
    "config.json", "generation_config.json", "model.safetensors",
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "chat_template.jinja",
)
DEST_NAME = "lfm2.5-1.2b-instruct"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-hash", action="store_true", help="Skip verification of full BF16 model checksum")
    args = parser.parse_args()
    root = configure_local_model_cache()
    destination = root / DEST_NAME
    destination.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    print(f"Repo  : {REPO_ID}")
    print(f"Target: {destination}")
    print("Format: native BF16 safetensors (NOT GGUF, NOT quantized)")
    print("Size  : approx. 2.34 GB for model weights, plus tokenizer/config")
    print("Note  : LFM is the default translator; NLLB is kept for comparison.")
    print()

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("[MISSING] huggingface_hub. Install packages using menu option 11.")
        return 2

    try:
        # local_dir is a real model copy (not just a global cache pointer).
        # Hugging Face's local_dir metadata and temp downloads are inside ./models.
        snapshot_download(
            repo_id=REPO_ID,
            local_dir=str(destination),
            allow_patterns=list(FILES),
            max_workers=2,
        )
    except KeyboardInterrupt:
        print("\n[STOP] Download interrupted. Run the menu option again to resume.")
        return 130
    except Exception as exc:
        print(f"[ERROR] Download failed: {exc}")
        print("Run this option again to retry/resume. No external model path is used.")
        return 1

    missing = [name for name in FILES if not (destination / name).is_file()]
    if missing:
        print("[MISSING] Files: " + ", ".join(missing))
        return 1

    weight = destination / "model.safetensors"
    if weight.stat().st_size < 2_000_000_000:
        print(f"[ERROR] model.safetensors is too small ({weight.stat().st_size:,} bytes).")
        return 1
    if not args.skip_hash:
        print("[VERIFY] SHA256 of 2.34 GB checkpoint (may take a while)...")
        actual = sha256(weight)
        if actual != EXPECTED_SHA256:
            print("[ERROR] Unexpected SHA256. Check the official upstream revision before use.")
            print("Expected:", EXPECTED_SHA256)
            print("Actual  :", actual)
            return 1
    print("[OK] Official LFM2.5-1.2B-Instruct BF16 files ready in ./models.")
    print("To inspect readiness, choose the Diagnose option in SoundText.bat.")
    print("LFM is the default translation backend. Test with menu 16 first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
