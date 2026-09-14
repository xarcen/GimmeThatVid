"""Runs each download in its own process, so it can always be stopped.

A download stuck inside a network read can't be interrupted from another
thread: yt-dlp only looks at a cancel flag between chunks of data, and a
stalled YouTube connection never delivers the next chunk. A separate process
can simply be ended. That makes Cancel immediate, and lets a watchdog notice
a stall, retry once with fresh links, and otherwise say so.
"""
from __future__ import annotations

import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Callable

from .downloader import Cancelled, FriendlyError, cleanup_stale_work_dirs

STALL_SECONDS = 45        # downloading, but no new bytes arriving for this long
START_SECONDS = 120       # no sign of life at all since the process started
STALL_RETRIES = 1         # fresh links often fix a YouTube stall
CREATE_NO_WINDOW = 0x08000000

Emit = Callable[[dict], None]


class _Stalled(Exception):
    pass


def worker_main(url: str, quality: str, out_dir: str, ffmpeg: str | None, events) -> None:
    """Entry point inside the download process."""
    for name in ("stdout", "stderr"):                  # no console in the packaged app
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    try:
        from . import engine
        engine.activate()                              # same yt-dlp as the main process
        from . import downloader

        result = downloader.download(url, quality, out_dir, events.put, threading.Event(), ffmpeg)
        events.put({"type": "result", "result": result})
    except FriendlyError as exc:
        events.put({"type": "failure", "message": exc.message, "detail": exc.detail})
    except BaseException as exc:                       # noqa: BLE001 - report everything to the parent
        events.put({"type": "failure", "message": "Something went wrong while downloading.",
                    "detail": f"{type(exc).__name__}: {exc}"[:300]})


def _end(process) -> None:
    """End the download process and everything it started (ffmpeg included)."""
    if process.pid is not None and process.is_alive():
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    process.join(5)


def _attempt(url, quality, out_dir, emit, cancel, ffmpeg, target, stall_seconds, start_seconds) -> dict:
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    process = context.Process(target=target, args=(url, quality, str(out_dir), ffmpeg, events),
                              name="gimmethatvid-download", daemon=True)
    process.start()

    started = last_sign_of_life = time.monotonic()
    last_bytes = -1
    phase = "starting"                                 # starting -> downloading -> finishing
    try:
        while True:
            if cancel.is_set():
                _end(process)
                raise Cancelled()

            try:
                event = events.get(timeout=0.25)
            except queue.Empty:
                event = None
            now = time.monotonic()

            if event is not None:
                kind = event.get("type")
                if kind == "result":
                    return event["result"]
                if kind == "failure":
                    raise FriendlyError(event["message"], event.get("detail", ""))
                if kind == "progress":
                    phase = "downloading"
                    if event.get("downloaded", 0) != last_bytes:
                        last_bytes = event.get("downloaded", 0)
                        last_sign_of_life = now
                elif kind == "stage":
                    if event.get("stage") in ("finishing", "converting"):
                        phase = "finishing"            # ffmpeg can take a while; not a stall
                    last_sign_of_life = now
                emit(event)
                continue

            if not process.is_alive():
                # It may have sent its result just before exiting.
                try:
                    event = events.get(timeout=1.5)
                except queue.Empty:
                    event = None
                if event and event.get("type") == "result":
                    return event["result"]
                if event and event.get("type") == "failure":
                    raise FriendlyError(event["message"], event.get("detail", ""))
                raise FriendlyError("The download stopped unexpectedly.", f"exit code {process.exitcode}")

            if phase == "downloading" and now - last_sign_of_life > stall_seconds:
                _end(process)
                raise _Stalled()
            if phase == "starting" and now - started > start_seconds:
                _end(process)
                raise FriendlyError("YouTube didn't respond. Try again in a moment.")
    finally:
        if process.is_alive():
            _end(process)
        events.close()


def run_job(url: str, quality: str, out_dir, emit: Emit, cancel: threading.Event, ffmpeg: str | None,
            *, target=worker_main, stall_seconds: float = STALL_SECONDS,
            start_seconds: float = START_SECONDS) -> dict:
    """Download in a child process. Returns the downloader's result dict.

    Raises Cancelled or FriendlyError, like downloader.download does.
    """
    for attempt in range(STALL_RETRIES + 1):
        try:
            return _attempt(url, quality, out_dir, emit, cancel, ffmpeg, target, stall_seconds, start_seconds)
        except _Stalled:
            _tidy(out_dir)
            if attempt < STALL_RETRIES:
                emit({"type": "stage", "stage": "retrying"})
        except (Cancelled, FriendlyError):
            _tidy(out_dir)
            raise
    raise FriendlyError("YouTube stopped sending this video. Try again, or pick a lower quality.")


def _tidy(out_dir) -> None:
    """A killed download can't clean up after itself; do it here."""
    for _ in range(3):
        cleanup_stale_work_dirs(out_dir)
        time.sleep(0.3)                                # ffmpeg may still be letting go of a file
