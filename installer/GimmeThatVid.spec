# PyInstaller spec: freezes GimmeThatVid into a folder with GimmeThatVid.exe.
# Run through tools/build_installer.py, which also adds ffmpeg and builds the setup.
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "GimmeThatVid.pyw")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "gimmethatvid" / "ui"), "gimmethatvid/ui"),
        (str(ROOT / "gimmethatvid" / "assets"), "gimmethatvid/assets"),
    ],
    hiddenimports=[
        # imported inside functions, listed so they're never missed
        "gimmethatvid.api",
        "gimmethatvid.downloader",
        "gimmethatvid.jobs",
        "gimmethatvid.media_server",
        "gimmethatvid.selftest",
        # Standard-library modules a future yt-dlp might start using. The engine
        # updater loads new yt-dlp versions into this frozen Python, so it should
        # find what it asks for.
        "asyncio", "bz2", "hmac", "html.parser", "http.cookiejar", "importlib.metadata",
        "lzma", "netrc", "optparse", "secrets", "shlex", "sqlite3", "ssl", "unicodedata",
        "uuid", "xml.etree.ElementTree", "zipimport",
    ],
    excludes=["tkinter", "numpy", "PIL", "faster_whisper", "ctranslate2", "av",
              "onnxruntime", "matplotlib", "pytest", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GimmeThatVid",
    icon=str(ROOT / "gimmethatvid" / "assets" / "icon.ico"),
    version=os.environ.get("GTV_VERSION_FILE"),
    console=False,        # a window app: no black console box
    upx=False,            # UPX-packed exes trip antivirus false positives
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GimmeThatVid")
