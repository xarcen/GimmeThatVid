# GimmeThatVid

Paste a YouTube link, get an MP4 (or an MP3). Dark, simple, one window.

## Install it / send it to someone

**`installer\dist\GimmeThatVid-Setup.exe`** is the whole app in one file.
Send that file (Google Drive, WeTransfer, USB stick); the other PC needs nothing
else installed.

Running it installs GimmeThatVid like any normal app:

- shows up in **Windows Search** and the Start menu (desktop icon optional)
- installs just for that Windows user, so no admin password
- uninstall through *Settings → Apps*
- installing a newer setup over it updates it and keeps settings and history

The first time, Windows may show **"Windows protected your PC"** because the
setup isn't signed with a paid certificate: click **More info → Run anyway**.

The installed app keeps its download engine (yt-dlp) up to date by itself:
once a day it checks for a new official release, verifies it, and uses it from
the next start. Nobody has to run `pip` for it to keep working.

## Open it from source

Double-click **`GimmeThatVid.lnk`** in this folder. No console window appears,
and opening it again just brings the existing window to the front.

## Using it

1. **Copy a YouTube link** anywhere. When GimmeThatVid opens (or you switch back
   to it) the link is picked up from the clipboard and the video shows up
   straight away. You can also paste, type, or drag a link onto the window.
2. **Pick a quality** from the dropdown. It starts on the best the video has —
   4K, 1440p, 1080p … — with the file size next to each. **Audio only** at the
   bottom saves an MP3.
3. **Download.** The ring shows progress, speed and time left; Cancel stops it
   and cleans up.
4. **Done.** The file plays right in the app. **Play** opens it in your normal
   player, **Show in Folder** opens Explorer with it selected.

Keyboard: <kbd>Enter</kbd> loads the link / starts the download,
<kbd>Ctrl</kbd>+<kbd>V</kbd> pastes from anywhere, <kbd>Esc</kbd> clears or goes
back, arrow keys work in the quality menu.

## Files

- Saved to a **`GimmeThatVid`** folder inside your Windows Downloads location
  (wherever Windows has Downloads, even on another drive). Click "Saving to …"
  at the bottom to change it.
- Named **`YYYYMMDD-Title.mp4`** with the download date, e.g.
  `20260914-How Golems Spawn.mp4`. Characters Windows forbids in file names
  (`| ? : " / \ < > *`) are dropped; a duplicate name gets ` (2)`.
- MP3s are 192 kbps and include the title, channel name and the thumbnail as
  cover art.
- The last few downloads are listed under **Recent** on the start screen.

### About 4K

YouTube only offers 4K (and 1440p) as VP9 or AV1 video. GimmeThatVid still
saves an ordinary `.mp4`, and it plays in the app, in Windows Media Player on
Windows 11 and in VLC. Up to 1080p it prefers H.264, which plays everywhere.

## When downloads stop working

YouTube changes things now and then, and the fix is almost always a newer
yt-dlp. The installed app fetches it by itself (restart GimmeThatVid a day
later). When running from source, update it with:

```bash
python -m pip install -U yt-dlp
```

## Requirements

Python 3.13 with the packages in `requirements.txt`, plus **ffmpeg**
(`winget install Gyan.FFmpeg`) for joining video with audio and for MP3s.
Without ffmpeg only low-quality single-file downloads are possible. The window
uses Microsoft Edge WebView2, which Windows 11 already has.

Settings, history and a log live in `%APPDATA%\GimmeThatVid`.

## For development

| Path | What it is |
|---|---|
| `GimmeThatVid.pyw` | entry point (`python GimmeThatVid.pyw` shows errors in a console) |
| `gimmethatvid/main.py` | creates the window |
| `gimmethatvid/api.py` | functions the UI calls |
| `gimmethatvid/downloader.py` | yt-dlp: video info, qualities, download, MP3 |
| `gimmethatvid/media_server.py` | local server that lets the in-app player stream and seek |
| `gimmethatvid/winutil.py` | clipboard, Downloads folder, Explorer, dark title bar, single instance |
| `gimmethatvid/store.py` | settings and history |
| `gimmethatvid/engine.py` | keeps yt-dlp current in the installed app |
| `gimmethatvid/selftest.py` | `GimmeThatVid.exe --self-test report.json` for the packaged build |
| `gimmethatvid/ui/` | `index.html`, `style.css`, `app.js`, logo |
| `installer/GimmeThatVid.spec` | PyInstaller: freezes the app into `GimmeThatVid.exe` |
| `installer/GimmeThatVid.iss` | Inno Setup: turns that into `GimmeThatVid-Setup.exe` |
| `tools/build_installer.py` | runs both and adds ffmpeg |
| `tools/build_assets.py` | rebuilds the logo and `.ico` from `GimmeThatVid.png` |
| `tools/make_shortcut.ps1` | recreates `GimmeThatVid.lnk` |
| `tools/smoke_test.py` | drives the real window end to end and screenshots each step |

### Building a new setup

1. Raise `__version__` in `gimmethatvid/__init__.py`.
2. Download and unzip `ffmpeg-release-essentials.zip` from
   <https://www.gyan.dev/ffmpeg/builds/>.
3. Run (needs `pip install pyinstaller` and `winget install JRSoftware.InnoSetup`):

   ```bash
   python tools/build_installer.py --ffmpeg-dir PATH\TO\ffmpeg-x.y-essentials_build
   ```

4. Check the packaged build before sending it:

   ```bash
   installer\dist\GimmeThatVid\GimmeThatVid.exe --self-test report.json --update
   ```

   It downloads a short Creative Commons video as MP4 and MP3 without opening a
   window and writes what passed to `report.json`.

- `set GTV_DEBUG=1` before starting to get the WebView developer tools.
- Open `gimmethatvid/ui/index.html?demo=preview` (also `home`, `menu`, `loading`,
  `error`, `progress`, `finishing`, `done`) in Edge to look at a screen without Python.
- `python tools/smoke_test.py <empty folder>` runs the full check — video,
  quality menu, MP3, cancel, error — without touching your real settings or
  download folder.
