from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Any


class SessionJournal:
    """Append-only crash journal + atomic checkpoint for long meetings."""

    def __init__(self, session_dir: str | Path, *, fsync_interval_s: float = 2.0) -> None:
        self.session_dir = Path(session_dir)
        self.path = self.session_dir / "session.journal.jsonl"
        self.state_path = self.session_dir / "session_state.json"
        self.fsync_interval_s = max(0.0, float(fsync_interval_s))
        self._lock = threading.RLock()
        self._last_fsync = 0.0

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def append(self, event: str, payload: dict[str, Any] | None = None, *, force: bool = False) -> None:
        row = {"ts": self._now_iso(), "event": str(event), "payload": payload or {}}
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(encoded)
                f.flush()
                now = time.monotonic()
                if force or self.fsync_interval_s == 0 or now - self._last_fsync >= self.fsync_interval_s:
                    os.fsync(f.fileno())
                    self._last_fsync = now

    def checkpoint(self, state: dict[str, Any]) -> None:
        payload = {"updated_at": self._now_iso(), **state}
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with self._lock:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_path)

    def read_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
