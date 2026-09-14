"""yt-dlp wrapper: metadata for the preview card, and the download itself."""
from __future__ import annotations

import copy
import datetime
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.utils import DownloadError

from . import winutil

Emit = Callable[[dict], None]

_YOUTUBE = re.compile(
    r"^(?:https?://)?(?:(?:www|m|music)\.)?"
    r"(?:youtube\.com/(?:watch\?|shorts/|live/|embed/)|youtu\.be/)\S+$",
    re.IGNORECASE,
)
_WORK_PREFIX = ".gimmethatvid-"
_NOTE_HEIGHT = re.compile(r"(\d{3,4})p")
AUDIO = "audio"                 # the quality value for "Audio only"
MP3_KBPS = 192
DEFAULT_MAX_TIER = 2160         # pre-selected quality never goes above 4K


def is_youtube_url(text: str) -> bool:
    text = (text or "").strip()
    return len(text) < 2048 and bool(_YOUTUBE.match(text))


class Cancelled(Exception):
    pass


class FriendlyError(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


class _SilentLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def thumbnails(video_id: str) -> dict:
    base = f"https://i.ytimg.com/vi/{video_id}"
    return {
        "thumb": f"{base}/maxresdefault.jpg",       # 1280x720, missing on some older videos
        "thumb_fallback": f"{base}/hqdefault.jpg",  # always there
        "thumb_small": f"{base}/mqdefault.jpg",     # 320x180, always there, no letterbox
    }


# --- errors ------------------------------------------------------------------
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_ERROR_TABLE = [
    (("private video",), "This video is private."),
    (("sign in to confirm your age", "age-restricted", "inappropriate for some users"),
     "This video is age-restricted, so it can't be downloaded without signing in."),
    (("not a bot",), "YouTube wants a sign-in check right now. Try again in a few minutes."),
    (("members-only", "join this channel"), "This video is only for channel members."),
    (("premieres in", "will begin in", "upcoming"), "This video hasn't premiered yet."),
    # Before the generic "not available" rule, which would otherwise claim the video is gone.
    (("requested format is not available", "no video formats found"),
     "This video doesn't offer a format that can be saved."),
    (("video unavailable", "is unavailable", "not available", "has been removed",
      "copyright claim", "account associated with this video has been terminated"),
     "This video isn't available."),
    (("unsupported url", "is not a valid url"), "That link doesn't lead to a video."),
    (("getaddrinfo", "unable to download webpage", "timed out", "network is unreachable",
      "connection aborted", "connection reset", "failed to resolve"),
     "Can't reach YouTube. Check your internet connection."),
    (("no space left", "errno 28"), "Your disk is full."),
    (("permission denied", "errno 13"), "GimmeThatVid can't write to the download folder."),
    (("ffmpeg",), "ffmpeg is missing, so video and audio can't be combined."),
]


def _friendly(exc: Exception) -> FriendlyError:
    raw = _ANSI.sub("", str(exc)).replace("ERROR: ", "").strip()
    low = raw.lower()
    for needles, message in _ERROR_TABLE:
        if any(n in low for n in needles):
            return FriendlyError(message)
    return FriendlyError("Something went wrong with that video.", raw[:300])


def _reject_unsupported(info: dict) -> None:
    if info.get("_type") in ("playlist", "multi_video"):
        raise FriendlyError("That's a playlist. Paste a link to a single video.")
    status = info.get("live_status")
    if info.get("is_live") or status == "is_live":
        raise FriendlyError("Live streams can't be saved while they're live.")
    if status == "is_upcoming":
        raise FriendlyError("This video hasn't premiered yet.")


def _base_options(ffmpeg: str | None) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "logger": _SilentLogger(),
        "socket_timeout": 20,
    }
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    return options


# --- preview metadata --------------------------------------------------------
def _short_side(fmt: dict) -> int:
    """'1080p' means the short side, which matters for vertical Shorts."""
    width, height = fmt.get("width"), fmt.get("height")
    if width and height:
        return min(width, height)
    return height or 0


def _tier(fmt: dict) -> int:
    """The '2160p' YouTube itself shows for a stream.

    Measuring pixels would call a cinematic 3840x1608 video '1440p'; YouTube's
    own label stays right for any aspect ratio.
    """
    match = _NOTE_HEIGHT.search(fmt.get("format_note") or "")
    return int(match.group(1)) if match else _short_side(fmt)


def _tier_label(tier: int) -> str:
    return {4320: "8K", 2160: "4K"}.get(tier, f"{tier}p")


def _is_video(fmt: dict) -> bool:
    return (fmt.get("vcodec") or "none") != "none" and _short_side(fmt) > 0


def _is_audio_only(fmt: dict) -> bool:
    return (fmt.get("vcodec") or "none") == "none" and (fmt.get("acodec") or "none") != "none"


def _size(fmt: dict, duration: float) -> int:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if size:
        return int(size)
    if fmt.get("tbr") and duration:
        return int(fmt["tbr"] * 1000 / 8 * duration)
    return 0


def _selection_options(ffmpeg: str | None, cap: int) -> dict:
    """Format choice shared by the size estimate and the real download, so they agree."""
    return {
        # Prefer H.264 + AAC: plays everywhere on Windows, including the Photos app.
        "format": "bv*+ba/b" if ffmpeg else "b[ext=mp4]/b",
        "format_sort": [f"res:{cap}", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"],
        "merge_output_format": "mp4",
    }


def _estimate_size(info: dict, cap: int, ffmpeg: str | None) -> int:
    """Run yt-dlp's own format selection offline and add up what it would fetch.

    Guessing the stream ourselves overshot ~3x, because YouTube lists high-bitrate
    HLS variants that the real selection never picks.
    """
    duration = info.get("duration") or 0
    options = _base_options(ffmpeg) | _selection_options(ffmpeg, cap) | {"skip_download": True}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            picked = ydl.process_ie_result(copy.deepcopy(info), download=False)
    except Exception:
        return 0
    chosen = picked.get("requested_formats") or [picked]
    return sum(_size(fmt, duration) for fmt in chosen)


def fetch_info(url: str, ffmpeg: str | None) -> dict:
    options = _base_options(ffmpeg) | {"skip_download": True}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
    except DownloadError as exc:
        raise _friendly(exc) from None
    _reject_unsupported(info)

    usable = [f for f in info.get("formats") or [] if _is_video(f)]
    if not ffmpeg:
        # Without ffmpeg only streams that already carry audio can be saved.
        usable = [f for f in usable if (f.get("acodec") or "none") != "none"]

    # "1080p" -> the largest short side among its streams. That number is the cap
    # handed to yt-dlp's res: sort, which also measures the short side.
    caps: dict[int, int] = {}
    for fmt in usable:
        tier = _tier(fmt)
        caps[tier] = max(caps.get(tier, 0), _short_side(fmt))
    tiers = sorted((t for t in caps if t >= 360), reverse=True) or sorted(caps, reverse=True)[:1]

    qualities = [
        {"value": str(caps[t]), "label": _tier_label(t), "short": _tier_label(t), "kind": "video",
         "size": _estimate_size(info, caps[t], ffmpeg)}
        for t in tiers
    ]

    # Pre-select the best quality up to 4K. 8K stays in the list, but an 8K
    # stream alone can be 7 GB and YouTube often stalls on it.
    default_tier = next((t for t in tiers if t <= DEFAULT_MAX_TIER), tiers[0] if tiers else None)
    default_quality = str(caps[default_tier]) if default_tier else None

    duration = info.get("duration") or 0
    has_audio = any((f.get("acodec") or "none") != "none" for f in info.get("formats") or [])
    if ffmpeg and has_audio:                        # converting to MP3 needs ffmpeg
        qualities.append({"value": AUDIO, "label": "Audio only", "short": "MP3", "kind": "audio",
                          "size": int(MP3_KBPS * 1000 / 8 * duration)})

    video_id = info.get("id") or ""
    return {
        "id": video_id,
        "url": info.get("webpage_url") or url,
        "title": info.get("title") or "Untitled video",
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": info.get("duration") or 0,
        "qualities": qualities,                                   # best first
        "default_quality": default_quality,
        **thumbnails(video_id),
    }


# --- download ----------------------------------------------------------------
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def clean_title(title: str | None) -> str:
    text = re.sub(r"\s+", " ", _ILLEGAL.sub(" ", title or "")).strip().strip(". ")
    text = text[:120].rstrip(". ") or "video"
    return f"{text}_" if text.upper() in _RESERVED else text


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 10_000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise FriendlyError("There are too many files with that name already.")


def _produced_file(info: dict, work: Path) -> Path:
    for item in info.get("requested_downloads") or []:
        filepath = item.get("filepath")
        if filepath and os.path.isfile(filepath):
            return Path(filepath)
    media = [p for p in work.iterdir()
             if p.is_file() and p.suffix.lower() in (".mp4", ".m4v", ".mkv", ".webm", ".mov", ".mp3", ".m4a")]
    if media:
        return max(media, key=lambda p: p.stat().st_size)
    raise FriendlyError("The download finished, but the video file is missing.")


def cleanup_stale_work_dirs(folder) -> None:
    """Partial downloads left behind if the app was closed mid-download."""
    try:
        for entry in Path(folder).glob(f"{_WORK_PREFIX}*"):
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass


def download(url: str, quality: str, out_dir, emit: Emit, cancel: threading.Event,
             ffmpeg: str | None) -> dict:
    out_dir = Path(out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=_WORK_PREFIX, dir=out_dir))
    except OSError as exc:
        raise _friendly(exc) from None
    winutil.hide_path(work)

    audio_only = quality == AUDIO
    cap = int(quality) if str(quality).isdigit() else 1080
    expected: dict[str, int] = {}      # format id -> bytes we expect it to be
    finished: dict[str, int] = {}      # format id -> bytes actually downloaded
    several = [False]
    last_emit = [0.0]

    def on_progress(d: dict) -> None:
        if cancel.is_set():
            raise Cancelled()
        fmt = d.get("info_dict") or {}
        fid = str(fmt.get("format_id") or "file")
        status = d.get("status")

        if status == "finished":
            size = d.get("total_bytes") or d.get("downloaded_bytes") or expected.get(fid, 0)
            finished[fid] = expected[fid] = size
            return
        if status != "downloading":
            return

        done = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if total:
            expected[fid] = total

        now = time.monotonic()
        if now - last_emit[0] < 0.12:
            return
        last_emit[0] = now

        all_total = sum(expected.values()) or total
        all_done = sum(v for k, v in finished.items() if k != fid) + done
        speed = d.get("speed")
        if audio_only:
            stage = "audio"
        elif several[0]:
            stage = "audio" if _is_audio_only(fmt) else "video"
        else:
            stage = "file"
        emit({
            "type": "progress",
            "stage": stage,
            "percent": min(99.0, all_done * 100.0 / all_total) if all_total else 0.0,
            "downloaded": all_done,
            "total": all_total,
            "speed": speed,
            "eta": (all_total - all_done) / speed if speed and all_total else None,
        })

    finishing = [False]

    def on_postprocess(d: dict) -> None:
        if d.get("status") != "started":
            return
        if cancel.is_set():
            raise Cancelled()
        if not finishing[0]:                 # merge, remux and move each report in
            finishing[0] = True
            emit({"type": "stage", "stage": "converting" if audio_only else "finishing"})

    options = _base_options(ffmpeg) | {
        "outtmpl": {"default": str(work / "media.%(ext)s")},
        "progress_hooks": [on_progress],
        "postprocessor_hooks": [on_postprocess],
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "overwrites": True,
    }
    if audio_only:
        if not ffmpeg:
            raise FriendlyError("ffmpeg is missing, so audio can't be converted to MP3.")
        options |= {
            "format": "ba/b",
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(MP3_KBPS)},
                {"key": "FFmpegMetadata", "add_metadata": True},       # title, artist, date
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},  # cover art
            ],
        }
    else:
        options |= _selection_options(ffmpeg, cap)
        if ffmpeg:
            options["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            emit({"type": "stage", "stage": "preparing"})
            info = ydl.extract_info(url, download=False)
            if cancel.is_set():
                raise Cancelled()
            _reject_unsupported(info)

            duration = info.get("duration") or 0
            planned = info.get("requested_formats") or [info]
            several[0] = len(planned) > 1
            for fmt in planned:
                expected[str(fmt.get("format_id") or "file")] = _size(fmt, duration)

            info = ydl.process_ie_result(info, download=True)

        if cancel.is_set():
            raise Cancelled()
        produced = _produced_file(info, work)
        stamp = datetime.date.today().strftime("%Y%m%d")
        target = _unique(out_dir / f"{stamp}-{clean_title(info.get('title'))}{produced.suffix.lower()}")
        os.replace(produced, target)
        return {
            "path": str(target),
            "size": target.stat().st_size,
            "title": info.get("title") or target.stem,
            "channel": info.get("channel") or info.get("uploader") or "",
            "duration": info.get("duration") or 0,
            "video_id": info.get("id") or "",
            "url": info.get("webpage_url") or url,
            "height": None if audio_only else info.get("height"),
            "kind": "audio" if audio_only else "video",
        }
    except Cancelled:
        raise
    except FriendlyError:
        if cancel.is_set():
            raise Cancelled() from None
        raise
    except Exception as exc:
        # yt-dlp can wrap our Cancelled in its own error type, so trust the flag.
        if cancel.is_set():
            raise Cancelled() from None
        raise _friendly(exc) from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
