from __future__ import annotations

from app.config import AppConfig

PRESETS = ("fast", "balanced", "stable")


def apply_preset(config: AppConfig, name: str | None) -> str:
    """Tune only VAD/live-caption cadence; models never change."""
    preset = (name or "balanced").strip().lower()
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset: {name}")

    if preset == "fast":
        config.vad.partial_interval_ms = min(config.vad.partial_interval_ms, 650)
        config.vad.partial_window_s = min(config.vad.partial_window_s, 3.6)
        config.reliability.artifact_refresh_interval_s = 900.0
        config.reliability.artifact_refresh_events = 192
    elif preset == "stable":
        config.vad.partial_interval_ms = max(config.vad.partial_interval_ms, 1300)
        config.vad.partial_window_s = max(config.vad.partial_window_s, 5.5)
        config.vad.min_silence_ms = max(config.vad.min_silence_ms, 650)
        config.reliability.artifact_refresh_interval_s = 300.0
        config.reliability.artifact_refresh_events = 64
    else:
        config.reliability.artifact_refresh_interval_s = max(300.0, min(900.0, config.reliability.artifact_refresh_interval_s))
        config.reliability.artifact_refresh_events = max(64, min(192, config.reliability.artifact_refresh_events))
    return preset
