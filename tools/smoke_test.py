"""Drive the real app window end to end and screenshot each step.

    python tools/smoke_test.py OUTPUT_DIR [YOUTUBE_URL]

Runs against an isolated data folder inside OUTPUT_DIR and saves downloads
there too, so your real settings, history and download folder stay untouched.
A window will appear on screen (kept on top) for the length of the test.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = Path(sys.argv[1]).resolve()
URL = sys.argv[2] if len(sys.argv) > 2 else "https://www.youtube.com/watch?v=ytoGJiYH8ms"
CANCEL_URL = "https://www.youtube.com/watch?v=82SwK1qv3nQ"      # longer, so there's time to cancel
VIDEOS = OUT / "videos"
DATA = OUT / "data"
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "history.json").unlink(missing_ok=True)
(DATA / "settings.json").write_text(json.dumps({"output_dir": str(VIDEOS)}), encoding="utf-8")
os.environ["GTV_DATA_DIR"] = str(DATA)

import webview  # noqa: E402
from PIL import ImageGrab  # noqa: E402

from gimmethatvid import winutil  # noqa: E402
from gimmethatvid.main import create_app  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok, info="") -> bool:
    results.append((name, bool(ok), str(info)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {info}", flush=True)
    return bool(ok)


# --- helpers -----------------------------------------------------------------
def js(window, code: str):
    return window.evaluate_js(code)


def wait(window, expression: str, timeout: float, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if js(window, f"!!({expression})"):
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def text(window, element_id: str) -> str:
    return js(window, f"document.getElementById({json.dumps(element_id)}).textContent")


def enter_link(window, url: str) -> None:
    """Type a link and press Enter. Enter loads immediately (no typing debounce),
    so the Download button is disabled before this returns and can't be stale."""
    js(window, f"""(() => {{
        const input = document.getElementById('linkInput');
        input.value = {json.dumps(url)};
        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
        document.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }}));
    }})()""")


def menu_labels(window) -> list[str]:
    return js(window, "[...document.querySelectorAll('#qualityMenu .menu-item .menu-label')].map(n => n.textContent)")


def choose_quality(window, label: str) -> bool:
    js(window, "document.getElementById('qualityBtn').click()")
    if not wait(window, "document.getElementById('qualityMenu').classList.contains('is-open')", 3):
        return False
    js(window, f"""([...document.querySelectorAll('#qualityMenu .menu-item')]
        .find(i => i.querySelector('.menu-label').textContent === {json.dumps(label)}) || {{ click() {{}} }}).click()""")
    return wait(window, "!document.getElementById('qualityMenu').classList.contains('is-open')", 3)


def download_and_wait(window, shot_prefix: str) -> bool:
    js(window, "document.getElementById('downloadBtn').click()")
    if not check(f"{shot_prefix}: progress view", wait(window, "document.body.dataset.view === 'progress'", 5)):
        return False
    if wait(window, "Number(document.getElementById('ringPct').textContent) >= 25"
                    " || document.getElementById('ring').classList.contains('is-finishing')", 90, 0.1):
        screenshot(window, f"{shot_prefix}_progress")
    if wait(window, "document.getElementById('ring').classList.contains('is-complete')", 300, 0.1):
        time.sleep(0.45)
        screenshot(window, f"{shot_prefix}_complete")
    return check(f"{shot_prefix}: done view", wait(window, "document.body.dataset.view === 'done'", 20))


def back_home(window) -> bool:
    js(window, "document.getElementById('againBtn').click()")
    return wait(window, "document.body.dataset.view === 'home'", 5)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def screenshot(window, name: str) -> None:
    hwnd = window.native.Handle.ToInt64()
    rect = RECT()
    # Extended frame bounds are in physical pixels whatever this thread's DPI mode is.
    ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect))
    ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True).save(OUT / f"{name}.png")


def probe(path: Path) -> dict:
    ffprobe = Path(winutil.find_ffmpeg()).with_name("ffprobe.exe")
    out = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries",
         "stream=codec_type,codec_name:stream_disposition=attached_pic:format_tags=title,artist",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8",
    )
    return json.loads(out.stdout or "{}")


# --- the run -----------------------------------------------------------------
def driver(window) -> None:
    today = time.strftime("%Y%m%d")
    try:
        check("ui booted", wait(window, "window.GTV && document.getElementById('folderLabel').textContent !== 'Downloads'", 20))
        time.sleep(1.6)
        screenshot(window, "01_home")

        # ---- video ---------------------------------------------------------
        enter_link(window, URL)
        check("preview loaded", wait(window, "!document.getElementById('downloadBtn').disabled", 40), text(window, "previewTitle"))
        wait(window, "document.getElementById('thumbImg').classList.contains('is-loaded')", 8)

        js(window, "document.getElementById('qualityBtn').click()")
        opened = wait(window, "document.getElementById('qualityMenu').classList.contains('is-open')", 3)
        labels = menu_labels(window)
        check("dropdown opens with every quality + audio", opened and labels and labels[-1] == "Audio only", ", ".join(labels or []))
        check("best quality is the default",
              js(window, "document.querySelector('#qualityMenu .menu-item').getAttribute('aria-selected') === 'true'")
              and text(window, "qualityLabel") == labels[0], text(window, "qualityLabel"))
        check("selected item has keyboard focus",
              js(window, "document.activeElement && document.activeElement.classList.contains('menu-item')"))
        time.sleep(0.7)
        screenshot(window, "02_menu")
        js(window, "document.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))")
        check("clicking outside closes it", wait(window, "!document.getElementById('qualityMenu').classList.contains('is-open')", 3))

        check("picked 720p", choose_quality(window, "720p") and text(window, "qualityLabel") == "720p", text(window, "sizeLabel"))
        time.sleep(0.4)
        screenshot(window, "03_preview_720")

        if download_and_wait(window, "04_video"):
            name = text(window, "fileName")
            check("video named YYYYMMDD-Title.mp4", name.startswith(f"{today}-") and name.endswith(".mp4"), name)
            check("player loads the mp4", wait(window, "document.getElementById('player').readyState >= 2", 20),
                  js(window, "(p => `${p.videoWidth}x${p.videoHeight}`)(document.getElementById('player'))"))
            js(window, "document.getElementById('player').currentTime = 90")
            check("seeking works (HTTP Range)",
                  wait(window, "(p => Math.abs(p.currentTime - 90) < 1.5 && p.readyState >= 2)(document.getElementById('player'))", 15))
            time.sleep(1.0)
            screenshot(window, "05_video_done")
        check("back home", back_home(window))

        # ---- audio ---------------------------------------------------------
        enter_link(window, URL)
        check("preview loaded again", wait(window, "!document.getElementById('downloadBtn').disabled", 40))
        check("picked Audio only", choose_quality(window, "Audio only") and text(window, "qualityLabel") == "MP3", text(window, "sizeLabel"))
        if download_and_wait(window, "06_audio"):
            name = text(window, "fileName")
            check("audio named YYYYMMDD-Title.mp3", name.startswith(f"{today}-") and name.endswith(".mp3"), name)
            check("done screen in audio mode", js(window, "document.getElementById('viewDone').classList.contains('is-audio')"),
                  text(window, "fileExt"))
            check("player loads the mp3", wait(window, "document.getElementById('player').readyState >= 1", 15),
                  js(window, "Math.round(document.getElementById('player').duration) + 's'"))
            time.sleep(1.2)
            screenshot(window, "07_audio_done")

            mp3 = next(VIDEOS.glob("*.mp3"), None)
            info = probe(mp3) if mp3 else {}
            streams = info.get("streams", [])
            tags = info.get("format", {}).get("tags", {})
            check("mp3 has cover art", any(s.get("disposition", {}).get("attached_pic") for s in streams))
            check("mp3 has title + artist tags", tags.get("title") and tags.get("artist"), f"{tags.get('artist')} - {tags.get('title')}")
        check("back home after audio", back_home(window))
        check("recent list shows both", wait(window, "document.querySelectorAll('#recentList li').length === 2", 6),
              js(window, "[...document.querySelectorAll('#recentList .recent-sub')].map(n => n.textContent).join(' | ')"))
        time.sleep(1.2)
        screenshot(window, "08_home_recent")

        # ---- cancel --------------------------------------------------------
        enter_link(window, CANCEL_URL)
        if check("cancel test: preview", wait(window, "!document.getElementById('downloadBtn').disabled", 40)):
            js(window, "document.getElementById('downloadBtn').click()")
            started = wait(window, "document.body.dataset.view === 'progress'"
                                   " && Number(document.getElementById('ringPct').textContent) >= 3"
                                   " && !document.getElementById('ring').classList.contains('is-busy')", 60, 0.1)
            check("cancel test: download really started", started, text(window, "progressDetail"))
            js(window, "document.getElementById('cancelBtn').click()")
            check("cancel returns home", wait(window, "document.body.dataset.view === 'home'", 30))
            check("cancel toast", wait(window, "document.getElementById('toast').textContent === 'Download cancelled'", 3))
            time.sleep(1.5)
            check("cancel leaves no partial files", not list(VIDEOS.glob(".gimmethatvid-*")))
            check("cancel saves nothing", len(list(VIDEOS.glob("*.mp4"))) == 1 and len(list(VIDEOS.glob("*.mp3"))) == 1)

        # ---- error ---------------------------------------------------------
        enter_link(window, "https://www.youtube.com/watch?v=aaaaaaaaaaa")
        shown = wait(window, "!document.getElementById('notice').hidden", 40)
        check("unavailable video: friendly error", shown and text(window, "noticeTitle") == "This video isn't available.",
              text(window, "noticeTitle"))
        time.sleep(0.8)
        screenshot(window, "09_error")
    except Exception as exc:  # noqa: BLE001
        check("driver crashed", False, repr(exc))
    finally:
        passed = sum(ok for _, ok, _ in results)
        print(f"\n{passed}/{len(results)} checks passed", flush=True)
        (OUT / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
        window.destroy()


if __name__ == "__main__":
    window, _api, options = create_app(on_top=True)
    webview.start(driver, (window,), **options)
