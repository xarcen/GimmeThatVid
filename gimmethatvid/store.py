"""Settings and recent downloads, kept as JSON under %APPDATA%\\GimmeThatVid."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import APP_NAME

MAX_HISTORY = 30


def data_dir() -> Path:
    override = os.environ.get("GTV_DATA_DIR")          # lets tests run in isolation
    base = Path(override) if override else Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)                               # never leave a half-written file


class Store:
    def __init__(self, default_output_dir: Path):
        self._lock = threading.Lock()
        self._dir = data_dir()
        self._settings_path = self._dir / "settings.json"
        self._history_path = self._dir / "history.json"

        saved = _read_json(self._settings_path, {})
        self._settings = {"output_dir": str(default_output_dir)}
        if isinstance(saved, dict):
            self._settings.update({k: v for k, v in saved.items() if k in self._settings and v})

        history = _read_json(self._history_path, [])
        self._history = [e for e in history if isinstance(e, dict) and e.get("id") and e.get("path")]

    @property
    def log_path(self) -> Path:
        return self._dir / "log.txt"

    def get(self, key: str):
        with self._lock:
            return self._settings.get(key)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._settings[key] = value
            _write_json(self._settings_path, self._settings)

    def history(self) -> list[dict]:
        """Newest first, skipping files that were moved or deleted since."""
        with self._lock:
            entries = [dict(e) for e in self._history]
        return [e for e in entries if Path(e["path"]).is_file()]

    def find(self, item_id: str) -> dict | None:
        with self._lock:
            return next((dict(e) for e in self._history if e["id"] == item_id), None)

    def add(self, entry: dict) -> None:
        with self._lock:
            rest = [e for e in self._history if e["path"] != entry["path"]]
            self._history = [entry, *rest][:MAX_HISTORY]
            _write_json(self._history_path, self._history)
