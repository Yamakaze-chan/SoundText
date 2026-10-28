"""Single source of truth for writable SoundText runtime files.

In an onedir PyInstaller build the executable lives alongside branding.json,
config_stt_vi_ja.json, models/ and output/.  PyInstaller's _internal / _MEIPASS
is used for bundled Python resources only, never for editable user data.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundled_root() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle).resolve() if bundle else app_root()


def configure_runtime(*, change_directory: bool = True) -> Path:
    """Run before heavyweight ML imports and before reading relative model paths."""
    root = app_root()
    models = root / "models"
    hf_home = models / "_hf_cache"
    values = {
        "SOUNDTEXT_ROOT": str(root),
        "MODELS_DIR": str(models),
        "HF_HOME": str(hf_home),
        "HF_HUB_CACHE": str(hf_home / "hub"),
        "HUGGINGFACE_HUB_CACHE": str(hf_home / "hub"),
        "HF_XET_CACHE": str(hf_home / "xet"),
        "HF_ASSETS_CACHE": str(hf_home / "assets"),
        "TORCH_HOME": str(models / "_torch_cache"),
        "XDG_CACHE_HOME": str(models / "_cache"),
        "TOKENIZERS_PARALLELISM": "false",
    }
    for key, value in values.items():
        if key == "TOKENIZERS_PARALLELISM":
            os.environ.setdefault(key, value)
        else:
            os.environ[key] = value
    if change_directory:
        os.chdir(root)
    return root
