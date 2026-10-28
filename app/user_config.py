"""Create editable user configs only on first run, never overwrite their edits.

Release ZIPs ship *.default.json instead of branding.json/config_stt_vi_ja.json.
This prevents an update from replacing a user's custom splash or ASR options.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from app.runtime_paths import app_root

DEFAULT_CONFIGS = {
    "branding.json": "branding.default.json",
    "config_stt_vi_ja.json": "config_stt_vi_ja.default.json",
}


def ensure_user_configs(root: Path | None = None) -> list[Path]:
    root = Path(root) if root is not None else app_root()
    created: list[Path] = []
    for current_name, default_name in DEFAULT_CONFIGS.items():
        current = root / current_name
        template = root / default_name
        if not current.exists() and template.is_file():
            try:
                # Exclusive create rather than check-then-copy to avoid two
                # instances writing the same preferences simultaneously.
                with template.open('rb') as src, current.open('xb') as dest:
                    shutil.copyfileobj(src, dest)
                created.append(current)
            except FileExistsError:
                continue
    return created


if __name__ == '__main__':
    for created in ensure_user_configs():
        print(f'[CONFIG] Created {created.name} from template')
