"""Keep all SoundText model downloads and associated caches inside ./models."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_ROOT = PROJECT_ROOT / "models"


def configure_local_model_cache() -> Path:
    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    hf_home = MODELS_ROOT / "_hf_cache"
    locations = {
        "HF_HOME": hf_home,
        "HF_HUB_CACHE": hf_home / "hub",
        "HUGGINGFACE_HUB_CACHE": hf_home / "hub",
        "HF_XET_CACHE": hf_home / "xet",
        "HF_ASSETS_CACHE": hf_home / "assets",
        "TORCH_HOME": MODELS_ROOT / "_torch_cache",
        "XDG_CACHE_HOME": MODELS_ROOT / "_cache",
    }
    # Intentionally override machine defaults for the model preparation scripts.
    for key, value in locations.items():
        os.environ[key] = str(value)
    return MODELS_ROOT
