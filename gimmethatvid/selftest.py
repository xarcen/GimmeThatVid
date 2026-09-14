"""GimmeThatVid.exe --self-test REPORT.json [--update]

Checks, without opening a window, the things that most often break in a
packaged build: the download engine loads (and from where), the bundled ffmpeg
is found, HTTPS works, and a real MP4 download (video + audio merged) and a
real MP3 conversion both succeed. With --update it also runs the engine
updater. Results go to REPORT.json; the exit code is 0 only if everything passed.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

from . import __version__, engine, winutil

# Short and Creative Commons licensed.
TEST_URL = "https://www.youtube.com/watch?v=ytoGJiYH8ms"


def run_self_test(report_path: Path, update: bool = False) -> int:
    report = {"app_version": __version__, "frozen": engine.FROZEN, "executable": sys.executable, "checks": []}

    def check(name, fn):
        started = time.time()
        entry = {"name": name}
        try:
            entry.update(ok=True, detail=fn())
        except Exception as exc:
            entry.update(ok=False, detail=f"{type(exc).__name__}: {getattr(exc, 'message', exc)}",
                         trace=traceback.format_exc()[-2000:])
        entry["seconds"] = round(time.time() - started, 1)
        report["checks"].append(entry)
        return entry["ok"]

    active = engine.activate()

    def engine_loads():
        import yt_dlp
        import yt_dlp.version
        return {"version": yt_dlp.version.__version__,
                "from": "downloaded update" if active else "built into the app",
                "module_file": getattr(yt_dlp, "__file__", "")}

    ffmpeg = winutil.find_ffmpeg()

    def ffmpeg_found():
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found")
        return ffmpeg

    if check("download engine loads", engine_loads):
        from . import downloader, jobs

        def video_info():
            info = downloader.fetch_info(TEST_URL, ffmpeg)
            return {"title": info["title"], "qualities": [q["label"] for q in info["qualities"]]}

        def save(quality):
            folder = Path(tempfile.mkdtemp(prefix="gimmethatvid-selftest-"))
            try:
                # through the separate download process, exactly like the app does
                result = jobs.run_job(TEST_URL, quality, folder, lambda event: None,
                                      threading.Event(), ffmpeg)
                path = Path(result["path"])
                return {"file": path.name, "megabytes": round(path.stat().st_size / 1e6, 1)}
            finally:
                shutil.rmtree(folder, ignore_errors=True)

        check("ffmpeg is bundled", ffmpeg_found)
        check("reads video details over HTTPS", video_info)
        check("downloads a 720p MP4 (merge)", lambda: save("720"))
        check("downloads an MP3 (conversion + cover art)", lambda: save(downloader.AUDIO))

    if update:
        def engine_update():
            import yt_dlp.version
            stored = engine.update_now(yt_dlp.version.__version__, force=True)
            return {"stored": stored, "zip": str(engine.engine_dir() / "yt-dlp.zip")}
        check("engine updater downloads and verifies a release", engine_update)

    report["passed"] = all(c["ok"] for c in report["checks"])
    Path(report_path).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0 if report["passed"] else 1
