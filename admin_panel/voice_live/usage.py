"""admin_panel.voice_live.usage — how many minutes the live voice talked today, kept across restarts.

The providers bill by the audio minute, so a day has a hard cap (``VOICE_LIVE_DAILY_MINUTES``): no new conversation
starts once it is used up, and one that reaches it ends. A conversation counts from the moment it connects to the
provider until it ends. The count lives in a small JSON file (``VOICE_LIVE_USAGE_FILE``, outside ``~/.cache``, which is
cleared every night) keyed by local date, with the last 31 days kept. A file that cannot be read starts the count
fresh, and one that cannot be written is only logged: the minutes are still counted in memory and a conversation never
fails because of this file.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Callable

from admin_panel import voice_config
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["LiveUsage", "usage_store", "KEEP_DAYS"]

KEEP_DAYS = 31


class LiveUsage:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path, self._clock = Path(path), clock
        self._lock = threading.Lock()
        self._days = self._load()

    def add(self, seconds: float, *, session: bool = False) -> None:
        """Count ``seconds`` of talking today (and one more conversation when ``session``)."""
        with self._lock:
            day = self._days.setdefault(self._today(), {"seconds": 0.0, "sessions": 0})
            day["seconds"] = round(float(day.get("seconds", 0.0)) + max(0.0, float(seconds)), 3)
            day["sessions"] = int(day.get("sessions", 0)) + (1 if session else 0)
            for old in sorted(self._days)[:-KEEP_DAYS]:
                del self._days[old]
            self._save()

    def today(self) -> tuple[float, int]:
        """(seconds talked today, conversations started today)."""
        with self._lock:
            day = self._days.get(self._today(), {})
        return float(day.get("seconds", 0.0)), int(day.get("sessions", 0))

    def left_seconds(self, limit_minutes: int) -> float:
        return limit_minutes * 60 - self.today()[0]

    def _today(self) -> str:
        return date.fromtimestamp(self._clock()).isoformat()

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            days = data.get("days") if isinstance(data, dict) else None
            return {k: v for k, v in days.items() if isinstance(v, dict)} if isinstance(days, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, AttributeError):
            log.warning("voice_live usage: %s could not be read, counting from zero", self._path)
            return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"days": self._days}, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("voice_live usage: not saved (%s)", type(exc).__name__)


_stores: dict[str, LiveUsage] = {}


def usage_store() -> LiveUsage:
    """The usage of the file configured now (one object per file, so every conversation adds to the same count)."""
    path = str(voice_config.live_usage_file())
    if path not in _stores:
        _stores[path] = LiveUsage(Path(path))
    return _stores[path]
