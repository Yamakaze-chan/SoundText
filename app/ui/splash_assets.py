"""Resolve user-selected splash artwork; never silently replace it with stock art."""
from __future__ import annotations
from pathlib import Path

from app.ui.branding import resolve_branding_path


def choose_splash_art(settings: dict) -> tuple[Path | None, str | None]:
    """Return image and an actionable warning, or None if splash art is disabled."""
    if not settings.get("show_splash", True):
        return None, None
    raw = str(settings.get("splash_path") or "").strip()
    if not raw:
        return None, "Bạn chưa cấu hình splash_path trong branding.json"
    image = resolve_branding_path(raw)
    if image is None:
        return None, f"Không tìm thấy ảnh splash: {raw} (kiểm tra branding.json)"
    if image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return None, f"Định dạng splash không hỗ trợ: {image.suffix}"
    return image, None
