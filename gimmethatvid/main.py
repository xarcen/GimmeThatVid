"""Start GimmeThatVid: one window, a web UI, and the download engine behind it."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# Nothing imported at module level may import yt_dlp: engine.activate() has to
# run first so the installed app can load an updated copy.
from . import APP_ID, APP_NAME, engine, winutil
from .store import data_dir

HERE = Path(__file__).resolve().parent
UI_INDEX = HERE / "ui" / "index.html"
ICON = HERE / "assets" / "icon.ico"

WIDTH, HEIGHT = 460, 760
MIN_WIDTH, MIN_HEIGHT = 400, 600


def _silence_missing_console() -> None:
    """Without a console (pythonw or the packaged exe), a stray print() would crash."""
    log = open(data_dir() / "log.txt", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def create_app(on_top: bool = False, clean_leftovers: bool = False):
    """Build the window and everything behind it. Returns (window, api, start_options).

    clean_leftovers deletes partial downloads from an earlier crash. Only pass
    True while holding the single-instance lock: otherwise it could delete the
    temp folder of a download another running copy is doing right now.
    """
    engine.activate()

    import webview                                  # slow imports, so only once we're sure to run
    import yt_dlp.version

    from . import downloader
    from .api import Api
    from .media_server import MediaServer
    from .store import Store

    winutil.set_app_user_model_id(APP_ID)
    store = Store(default_output_dir=winutil.downloads_dir() / APP_NAME)
    if clean_leftovers:
        downloader.cleanup_stale_work_dirs(store.get("output_dir"))

    api = Api(store=store, media=MediaServer(), ffmpeg=winutil.find_ffmpeg())
    engine.start_background_update(yt_dlp.version.__version__)

    screen = winutil.work_area_height_logical()
    height = min(HEIGHT, int(screen * 0.94)) if screen else HEIGHT

    window = webview.create_window(
        APP_NAME,
        url=str(UI_INDEX),
        js_api=api,
        width=WIDTH,
        height=max(MIN_HEIGHT, height),
        min_size=(MIN_WIDTH, MIN_HEIGHT),
        background_color="#000000",
        on_top=on_top,
    )
    api._attach(window)

    def on_shown():
        try:
            winutil.style_dark_titlebar(window.native.Handle.ToInt64())
        except Exception:
            pass

    window.events.shown += on_shown
    window.events.closing += api._shutdown

    start_options = {
        "http_server": True,
        "private_mode": True,
        "icon": str(ICON) if ICON.exists() else None,
        "debug": os.environ.get("GTV_DEBUG") == "1",
    }
    return window, api, start_options


def main() -> None:
    if not winutil.acquire_single_instance(APP_NAME):
        winutil.focus_window_titled(APP_NAME)
        return

    import webview

    _window, _api, start_options = create_app(clean_leftovers=True)
    webview.start(**start_options)


def run() -> None:
    _silence_missing_console()

    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
        from .selftest import run_self_test
        sys.exit(run_self_test(Path(sys.argv[2]), update="--update" in sys.argv[3:]))

    try:
        main()
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        winutil.error_box(
            f"{APP_NAME} couldn't start",
            f"Something went wrong while starting {APP_NAME}.\n\n"
            f"Details were written to:\n{data_dir() / 'log.txt'}",
        )
