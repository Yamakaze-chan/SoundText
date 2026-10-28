"""Remove disposable Python caches without touching models or session outputs."""
from __future__ import annotations

from pathlib import Path
import shutil

PROJECT = Path(__file__).resolve().parents[1]
EXCLUDED = {"models", "output", ".git", ".venv", "venv"}


def main() -> None:
    removed = 0
    for entry in PROJECT.rglob("*"):
        try:
            relative = entry.relative_to(PROJECT)
            if any(p in EXCLUDED for p in relative.parts):
                continue
            if entry.is_dir() and entry.name in {"__pycache__", ".pytest_cache"}:
                shutil.rmtree(entry)
                removed += 1
            elif entry.is_file() and entry.suffix in {".pyc", ".pyo"}:
                entry.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    print(f"Removed {removed} cache entries. Models and output sessions were preserved.")


if __name__ == "__main__":
    main()
