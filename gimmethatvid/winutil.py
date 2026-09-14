"""Small Windows integrations: folders, clipboard, Explorer, title bar, one instance."""
from __future__ import annotations

import ctypes
import glob
import os
import shutil
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_ole32 = ctypes.WinDLL("ole32", use_last_error=True)

# --- clipboard ---------------------------------------------------------------
CF_UNICODETEXT = 13

_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL


def read_clipboard_text(limit: int = 4096) -> str:
    """Plain text on the clipboard, or "" if there is none or it's busy."""
    if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""
    for _ in range(10):                       # another app may hold it for a moment
        if _user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return ""
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)[:limit]
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


# --- known folders -----------------------------------------------------------
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


_shell32.SHGetKnownFolderPath.argtypes = [
    ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)
]
_shell32.SHGetKnownFolderPath.restype = ctypes.HRESULT
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole32.CoTaskMemFree.restype = None

FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


def downloads_dir() -> Path:
    """The user's real Downloads folder, even if it was moved to another drive."""
    guid = _GUID.from_buffer_copy(uuid.UUID(FOLDERID_DOWNLOADS).bytes_le)
    raw = ctypes.c_void_p()
    try:
        _shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(raw))
        return Path(ctypes.wstring_at(raw.value))
    except OSError:
        return Path.home() / "Downloads"
    finally:
        if raw.value:
            _ole32.CoTaskMemFree(raw)


def pretty_folder(path) -> str:
    """Short human label for a folder.

    Inside the profile: 'C:\\Users\\me\\Downloads\\GimmeThatVid' -> 'Downloads › GimmeThatVid'.
    Elsewhere the drive letter matters, so keep a real path: 'D:\\GimmeThatVid'.
    """
    path = Path(path)
    try:
        parts = path.relative_to(Path.home()).parts
        if parts:
            return " › ".join(parts[-2:])
    except ValueError:
        pass
    text = str(path)
    if len(text) <= 42:
        return text
    return "…\\" + "\\".join(path.parts[-2:])


# --- Explorer / shell --------------------------------------------------------
def reveal_in_explorer(path) -> None:
    path = Path(path)
    if path.exists():
        # A plain command string: Explorer mis-parses /select when Python quotes it.
        subprocess.Popen(f'explorer /select,"{path}"')
    elif path.parent.exists():
        os.startfile(path.parent)


def open_with_default_app(path) -> None:
    os.startfile(str(path))


_kernel32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
_kernel32.SetFileAttributesW.restype = wintypes.BOOL
FILE_ATTRIBUTE_HIDDEN = 0x2


def hide_path(path) -> None:
    _kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)


# --- ffmpeg ------------------------------------------------------------------
def find_ffmpeg() -> str | None:
    """ffmpeg merges YouTube's separate video and audio streams into one MP4."""
    if getattr(sys, "frozen", False):                 # the installed app ships its own
        bundled = Path(sys.executable).parent / "ffmpeg" / "ffmpeg.exe"
        if bundled.is_file():
            return str(bundled)
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [os.path.join(local, "Microsoft", "WinGet", "Links", "ffmpeg.exe")]
    candidates += sorted(
        glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin", "ffmpeg.exe")),
        reverse=True,
    )
    candidates += [r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"]
    return next((c for c in candidates if os.path.isfile(c)), None)


# --- window chrome -----------------------------------------------------------
_dwmapi = ctypes.WinDLL("dwmapi")
_dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_dwmapi.DwmSetWindowAttribute.restype = ctypes.HRESULT

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def _colorref(hex_rgb: str) -> int:
    value = int(hex_rgb.lstrip("#"), 16)
    r, g, b = value >> 16 & 0xFF, value >> 8 & 0xFF, value & 0xFF
    return r | g << 8 | b << 16                      # COLORREF is 0x00BBGGRR


def style_dark_titlebar(hwnd: int, caption="#000000", text="#F5F5F7", border="#1C1C1E") -> None:
    """Dark title bar that blends into the black app background (Windows 11)."""
    settings = (
        (DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
        (DWMWA_CAPTION_COLOR, _colorref(caption)),
        (DWMWA_TEXT_COLOR, _colorref(text)),
        (DWMWA_BORDER_COLOR, _colorref(border)),
    )
    for attribute, value in settings:
        data = wintypes.DWORD(value)
        try:
            _dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))
        except OSError:
            pass                                    # colour attributes need Windows 11


_shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
_shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.HRESULT


def set_app_user_model_id(app_id: str) -> None:
    """Own taskbar button and icon, instead of being grouped under python.exe."""
    try:
        _shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except OSError:
        pass


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


_user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
_user32.SystemParametersInfoW.restype = wintypes.BOOL
SPI_GETWORKAREA = 0x0030


def work_area_height_logical() -> int | None:
    """Usable screen height in DPI-independent pixels (what pywebview sizes use)."""
    rect = _RECT()
    if not _user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return None
    try:
        dpi = _user32.GetDpiForSystem() or 96
    except AttributeError:
        dpi = 96
    return int((rect.bottom - rect.top) * 96 / dpi)


# --- single instance ---------------------------------------------------------
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
_instance_mutex = None


def acquire_single_instance(name: str) -> bool:
    """False if another copy is already running (launching twice shouldn't open two windows)."""
    global _instance_mutex
    _instance_mutex = _kernel32.CreateMutexW(None, False, f"Local\\{name}.SingleInstance")
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def focus_window_titled(title: str) -> None:
    hwnd = _user32.FindWindowW(None, title)
    if hwnd:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.SetForegroundWindow(hwnd)


def error_box(title: str, message: str) -> None:
    _user32.MessageBoxW(None, message, title, 0x10)      # MB_ICONERROR
