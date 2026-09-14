"""Build installer/dist/GimmeThatVid-Setup.exe.

    python tools/build_installer.py --ffmpeg-dir PATH\\TO\\ffmpeg-release-essentials

--ffmpeg-dir is the unzipped ffmpeg "essentials" build from gyan.dev (the
folder that contains bin\\ffmpeg.exe and LICENSE). Needs PyInstaller
(pip install pyinstaller) and Inno Setup 6 (winget install JRSoftware.InnoSetup).

Steps: freeze the app with PyInstaller, add ffmpeg.exe plus its licence, then
compile the Inno Setup script.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gimmethatvid import APP_NAME, __version__  # noqa: E402

INSTALLER = ROOT / "installer"
BUILD = INSTALLER / "build"
DIST = INSTALLER / "dist"


def find_iscc() -> Path:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    ]
    on_path = shutil.which("iscc")
    if on_path:
        candidates.insert(0, Path(on_path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit("Inno Setup 6 not found. Install it with:  winget install JRSoftware.InnoSetup")


def write_version_resource(path: Path) -> None:
    """Version details shown in the exe's Properties and in Task Manager."""
    numbers = tuple(int(p) for p in (__version__.split(".") + ["0", "0", "0"])[:4])
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{APP_NAME}'),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


def step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ffmpeg-dir", required=True, type=Path)
    args = parser.parse_args()

    ffmpeg_exe = args.ffmpeg_dir / "bin" / "ffmpeg.exe"
    if not ffmpeg_exe.is_file():
        raise SystemExit(f"{ffmpeg_exe} not found")
    iscc = find_iscc()

    BUILD.mkdir(parents=True, exist_ok=True)
    version_file = BUILD / "version_info.txt"
    write_version_resource(version_file)

    step(f"Freezing {APP_NAME} {__version__} with PyInstaller")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
         str(INSTALLER / "GimmeThatVid.spec")],
        check=True, env={**os.environ, "GTV_VERSION_FILE": str(version_file)},
    )
    app_dir = DIST / APP_NAME

    step("Adding ffmpeg")
    target = app_dir / "ffmpeg"
    target.mkdir(exist_ok=True)
    shutil.copy2(ffmpeg_exe, target / "ffmpeg.exe")
    for name in ("LICENSE", "README.txt"):        # GPL: ship the licence with the binary
        if (args.ffmpeg_dir / name).is_file():
            shutil.copy2(args.ffmpeg_dir / name, target / f"{name.split('.')[0]}.txt")

    step("Compiling the installer with Inno Setup")
    subprocess.run(
        [str(iscc), "/Q",
         f"/DAppVersion={__version__}",
         f"/DSourceDir={app_dir}",
         f"/DOutputDir={DIST}",
         f"/DIconFile={ROOT / 'gimmethatvid' / 'assets' / 'icon.ico'}",
         str(INSTALLER / "GimmeThatVid.iss")],
        check=True,
    )

    setup = DIST / f"{APP_NAME}-Setup.exe"
    app_size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    print(f"\nDone.\n  installer : {setup}  ({setup.stat().st_size / 1e6:.0f} MB)"
          f"\n  installed : about {app_size / 1e6:.0f} MB on disk")


if __name__ == "__main__":
    main()
