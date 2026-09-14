"""Keeps the download engine (yt-dlp) current in the installed app.

YouTube changes often and yt-dlp ships fixes within days, but the copy frozen
into GimmeThatVid.exe never changes. So once a day the installed app fetches
the latest official yt-dlp release -- a zip of the pure-Python package --
checks it against the release's SHA-256 list, and keeps it in
%LOCALAPPDATA%\\GimmeThatVid\\engine. On the next start, activate() makes Python
import yt_dlp from that zip instead of the frozen copy.

Only active in the installed (frozen) app. When running from source, pip owns
yt-dlp and nothing here interferes.
"""
from __future__ import annotations

import hashlib
import importlib.abc
import io
import json
import os
import sys
import threading
import time
import urllib.request
import zipfile
import zipimport
from pathlib import Path

from . import APP_NAME, __version__

RELEASE_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
ZIPAPP_ASSET = "yt-dlp"          # the platform-independent build: a zip with a shebang line
CHECKSUM_ASSET = "SHA2-256SUMS"
CHECK_EVERY = 24 * 3600
TIMEOUT = 30

FROZEN = bool(getattr(sys, "frozen", False))


def engine_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME / "engine"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _zip_path() -> Path:
    return engine_dir() / "yt-dlp.zip"


def _state_path() -> Path:
    return engine_dir() / "state.json"


def _read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(**changes) -> None:
    state = _read_state() | changes
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, _state_path())


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for piece in str(text).strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


# --- loading the downloaded copy ---------------------------------------------
class _ZipFinder(importlib.abc.MetaPathFinder):
    """Serves every yt_dlp.* module from the zip.

    It has to sit in front of PyInstaller's own importer and answer for all
    submodules too; otherwise a new yt_dlp package could quietly pull old
    frozen submodules into itself.
    """

    def __init__(self, archive: str):
        self.archive = archive

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "yt_dlp" and not fullname.startswith("yt_dlp."):
            return None
        parent = fullname.rpartition(".")[0]
        location = os.path.join(self.archive, *parent.split(".")) if parent else self.archive
        try:
            return zipimport.zipimporter(location).find_spec(fullname)
        except zipimport.ZipImportError:
            return None


def activate() -> str | None:
    """Use the downloaded engine if a healthy one exists. Call before anything imports yt_dlp.

    Returns the version now in use, or None when the frozen copy is used.
    """
    if not FROZEN or "yt_dlp" in sys.modules:
        return None
    archive = _zip_path()
    if not archive.is_file():
        return None

    finder = _ZipFinder(str(archive))
    sys.meta_path.insert(0, finder)
    try:
        import yt_dlp.version
        return yt_dlp.version.__version__
    except Exception:
        # A broken download must never break the app: fall back to the frozen copy.
        sys.meta_path.remove(finder)
        for name in [m for m in sys.modules if m == "yt_dlp" or m.startswith("yt_dlp.")]:
            del sys.modules[name]
        try:
            archive.unlink()
        except OSError:
            pass
        return None


# --- fetching updates ----------------------------------------------------------
def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{__version__}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def update_now(current_version: str, force: bool = False) -> str | None:
    """Download and store a newer engine if there is one. Returns the new version, or None.

    force=True fetches the latest release even if it isn't newer (used by --self-test).
    """
    release = json.loads(_get(RELEASE_API))
    latest = release["tag_name"]
    _write_state(checked_at=time.time(), latest=latest)

    if not force:
        if _version_tuple(latest) <= _version_tuple(current_version):
            return None
        if _read_state().get("stored") == latest and _zip_path().is_file():
            return None                               # already waiting for the next start

    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    if ZIPAPP_ASSET not in assets or CHECKSUM_ASSET not in assets:
        return None

    expected = None
    for line in _get(assets[CHECKSUM_ASSET]).decode("utf-8", "replace").splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip("*") == ZIPAPP_ASSET:
            expected = fields[0].lower()
    data = _get(assets[ZIPAPP_ASSET])
    if not expected or hashlib.sha256(data).hexdigest() != expected:
        return None

    # It must really be the package, at the version the release claims.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        version_source = archive.read("yt_dlp/version.py").decode("utf-8", "replace")
    if f"'{latest}'" not in version_source and f'"{latest}"' not in version_source:
        return None

    tmp = _zip_path().with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, _zip_path())
    _write_state(stored=latest, stored_at=time.time())
    return latest


def start_background_update(current_version: str) -> None:
    """Check at most once a day, quietly, without slowing the app down."""
    if not FROZEN:
        return
    if time.time() - _read_state().get("checked_at", 0) < CHECK_EVERY:
        return

    def worker():
        try:
            update_now(current_version)
        except Exception as exc:                      # offline, GitHub down, ...: try again tomorrow
            print(f"engine update check failed: {exc!r}", file=sys.stderr)

    threading.Thread(target=worker, name="engine-update", daemon=True).start()
