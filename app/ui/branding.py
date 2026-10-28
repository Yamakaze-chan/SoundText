"""Presentation-only local app branding and file-opening helpers.

No inference, audio, translation or diarization state is read or modified here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.runtime_paths import app_root, bundled_root
import shutil
import subprocess
import sys

PROJECT_ROOT = app_root()
BRANDING_FILE = PROJECT_ROOT / "branding.json"
ASSET_DIR = PROJECT_ROOT / "assets" / "branding"
DEFAULTS = {
    "app_name": "SoundText",
    "app_version": "1.0.0",
    "publisher": "SoundText",
    "tagline": "Phụ đề và biên bản hội thoại trực tiếp",
    "icon_path": "assets/default-branding/soundtext-icon.png",
    "splash_path": "assets/default-branding/soundtext-splash.png",
    "show_splash": True,
    "splash_duration_ms": 1400,
}


def branding_file_error() -> str | None:
    """Return a visible diagnostic if the user's editable metadata is invalid."""
    if not BRANDING_FILE.is_file():
        return f"Không thấy {BRANDING_FILE.name} bên cạnh ứng dụng"
    try:
        data = json.loads(BRANDING_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return "branding.json phải chứa một JSON object"
        if "splash_path" in data and not isinstance(data["splash_path"], str):
            return "splash_path phải là đường dẫn dạng chuỗi trong branding.json"
    except (OSError, ValueError) as exc:
        return f"branding.json không đọc được: {exc}"
    return None


def load_branding() -> dict[str, object]:
    result = dict(DEFAULTS)
    try:
        payload = json.loads(BRANDING_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for key in DEFAULTS:
                if key in payload and type(payload[key]) is type(DEFAULTS[key]):
                    result[key] = payload[key]
    except (OSError, ValueError) as exc:
        print(f"[BRANDING WARN] Cannot load {BRANDING_FILE}: {exc}")
    return result


def resolve_branding_path(value: str) -> Path | None:
    if not value:
        return None
    expanded = os.path.expandvars(str(value).strip().strip('"'))
    path = Path(expanded).expanduser()
    if path.is_absolute():
        path = path.resolve()
        return path if path.is_file() else None
    # User-editable files take precedence over any bundled stock artwork.
    external = (PROJECT_ROOT / path).resolve()
    if external.is_file():
        return external
    bundled = (bundled_root() / path).resolve()
    return bundled if bundled.is_file() else None


def store_brand_asset(source: str, kind: str) -> str:
    """Copy a user-selected image into the app, avoiding external absolute paths."""
    if kind not in {"icon", "splash"}:
        raise ValueError("Unsupported asset type")
    path = Path(source).expanduser().resolve(strict=True)
    allowed = {".ico", ".png", ".gif"} if kind == "icon" else {".png", ".jpg", ".jpeg", ".gif"}
    if not path.is_file() or path.suffix.lower() not in allowed:
        raise ValueError(f"Unsupported {kind} image: {path.suffix}")
    if path.stat().st_size > 24 * 1024 * 1024:
        raise ValueError("Image too large (24 MB limit)")
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    dest = ASSET_DIR / (f"custom-{kind}" + path.suffix.lower())
    if path != dest.resolve():
        shutil.copy2(path, dest)
    return dest.relative_to(PROJECT_ROOT).as_posix()


def save_branding(config: dict[str, object]) -> None:
    clean = dict(DEFAULTS)
    name = str(config.get("app_name", "")).strip()
    tagline = str(config.get("tagline", "")).strip()
    clean["app_name"] = (name or DEFAULTS["app_name"])[:60]
    clean["tagline"] = tagline[:150]
    clean["app_version"] = str(config.get("app_version", "1.0.0"))[:24]
    clean["publisher"] = str(config.get("publisher", "SoundText"))[:100]
    for key in ("icon_path", "splash_path"):
        path = str(config.get(key, "")).strip()
        clean[key] = path
    clean["show_splash"] = bool(config.get("show_splash", True))
    clean["splash_duration_ms"] = max(300, min(5000, int(config.get("splash_duration_ms", 1400))))
    tmp = BRANDING_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(BRANDING_FILE)


def open_session_directory(session_dir: str) -> Path:
    """Open this session's actual directory using the OS file manager."""
    base = Path(session_dir).expanduser()
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    folder = base.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(folder))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
    return folder
