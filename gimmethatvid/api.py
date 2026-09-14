"""The bridge the web UI calls as window.pywebview.api.*

pywebview exposes every public attribute of this object to JavaScript, so all
state lives in underscore names. File actions take a history id, never a path,
so the page can only ever touch files this app saved.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import webview

from . import downloader, jobs, winutil
from .downloader import Cancelled, FriendlyError
from .media_server import MediaServer
from .store import Store


class Api:
    def __init__(self, store: Store, media: MediaServer, ffmpeg: str | None):
        self._store = store
        self._media = media
        self._ffmpeg = ffmpeg
        self._window = None
        self._cancel = threading.Event()
        self._busy = threading.Lock()
        self._info_cache: dict[str, dict] = {}

    # --- wiring (not exposed) ------------------------------------------------
    def _attach(self, window) -> None:
        self._window = window

    def _shutdown(self) -> None:
        self._cancel.set()

    def _emit(self, event: dict) -> None:
        window = self._window
        if window is None:
            return
        try:
            window.evaluate_js(f"window.GTV && window.GTV.onEvent({json.dumps(event)})")
        except Exception:
            pass                                    # window already closing

    def _payload(self, entry: dict) -> dict:
        path = Path(entry["path"])
        return {
            "id": entry["id"],
            "name": path.name,
            "title": entry.get("title") or path.stem,
            "channel": entry.get("channel", ""),
            "size": entry.get("size", 0),
            "duration": entry.get("duration", 0),
            "saved_at": entry.get("saved_at", 0),
            "kind": entry.get("kind", "video"),
            "ext": path.suffix.lstrip(".").upper(),
            "folder_label": winutil.pretty_folder(path.parent),
            "media_url": self._media.url_for(path),
            **downloader.thumbnails(entry.get("video_id", "")),
        }

    def _existing(self, item_id: str) -> Path | None:
        entry = self._store.find(item_id)
        if entry and Path(entry["path"]).is_file():
            return Path(entry["path"])
        return None

    # --- called from JavaScript ----------------------------------------------
    def bootstrap(self) -> dict:
        return {
            "output_label": winutil.pretty_folder(self._store.get("output_dir")),
            "history": self.recent(),
            "ffmpeg": bool(self._ffmpeg),
        }

    def recent(self) -> list[dict]:
        return [self._payload(e) for e in self._store.history()[:5]]

    def clipboard_url(self) -> str:
        text = winutil.read_clipboard_text().strip()
        return text if downloader.is_youtube_url(text) else ""

    def fetch_info(self, url: str) -> dict:
        url = (url or "").strip()
        if not downloader.is_youtube_url(url):
            return {"ok": False, "error": "That doesn't look like a YouTube link."}
        try:
            info = self._info_cache.get(url)
            if info is None:
                info = downloader.fetch_info(url, self._ffmpeg)
                self._info_cache[url] = info
        except FriendlyError as exc:
            return {"ok": False, "error": exc.message, "detail": exc.detail}
        except Exception as exc:
            return {"ok": False, "error": "Couldn't read that video.", "detail": str(exc)[:300]}
        return {"ok": True, "info": info}               # default is always the best quality

    def start_download(self, url: str, quality: str) -> dict:
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "error": "A download is already running."}
        self._cancel.clear()
        threading.Thread(target=self._run_download, args=(url, str(quality)),
                         name="download", daemon=True).start()
        return {"ok": True}

    def cancel_download(self) -> bool:
        self._cancel.set()
        return True

    def history_item(self, item_id: str) -> dict | None:
        entry = self._store.find(item_id)
        if entry and Path(entry["path"]).is_file():
            return self._payload(entry)
        return None

    def play(self, item_id: str) -> bool:
        path = self._existing(item_id)
        if path:
            winutil.open_with_default_app(path)
        return bool(path)

    def reveal(self, item_id: str) -> bool:
        path = self._existing(item_id)
        if path:
            winutil.reveal_in_explorer(path)
        return bool(path)

    def open_output_folder(self) -> bool:
        folder = Path(self._store.get("output_dir"))
        folder.mkdir(parents=True, exist_ok=True)
        winutil.open_with_default_app(folder)
        return True

    def choose_folder(self) -> dict:
        if self._window is None:
            return {"ok": False}
        kind = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
        current = self._store.get("output_dir")
        result = self._window.create_file_dialog(kind, directory=current if Path(current).is_dir() else "")
        if not result:
            return {"ok": False}
        folder = result[0] if isinstance(result, (list, tuple)) else result
        self._store.set("output_dir", str(folder))
        return {"ok": True, "output_label": winutil.pretty_folder(folder)}

    # --- worker ---------------------------------------------------------------
    def _run_download(self, url: str, quality: str) -> None:
        try:
            result = jobs.run_job(url, quality, self._store.get("output_dir"),
                                  self._emit, self._cancel, self._ffmpeg)
            entry = {"id": uuid.uuid4().hex[:12], "saved_at": time.time(), "quality": quality, **result}
            self._store.add(entry)
            self._emit({"type": "done", "file": self._payload(entry)})
        except Cancelled:
            self._emit({"type": "cancelled"})
        except FriendlyError as exc:
            self._emit({"type": "error", "error": exc.message, "detail": exc.detail})
        except Exception as exc:
            self._emit({"type": "error", "error": "Something went wrong while downloading.",
                        "detail": f"{type(exc).__name__}: {exc}"[:300]})
        finally:
            self._busy.release()
