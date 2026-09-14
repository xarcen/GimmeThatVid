"""Loopback HTTP server so the in-app player can stream a saved MP4.

The page is served over http://, and WebView2 won't play file:// media from an
http page. Seeking also needs Range requests, which http.server lacks. Only
files registered through url_for() are reachable, each under a random token.
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")
_DISCONNECTS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # The player drops its connection on every seek; that's normal, not an error.
        if isinstance(sys.exc_info()[1], _DISCONNECTS):
            return
        super().handle_error(request, client_address)
_CHUNK = 256 * 1024
_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
          ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}


class MediaServer:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_token: dict[str, str] = {}
        self._by_path: dict[str, str] = {}
        registry = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self._serve(send_body=False)

            def do_GET(self):
                self._serve(send_body=True)

            def _serve(self, send_body: bool):
                token = self.path.split("?", 1)[0].rsplit("/", 1)[-1].split(".", 1)[0]
                path = registry._lookup(token)
                if not path or not os.path.isfile(path):
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

                size = os.path.getsize(path)
                start, end, partial = 0, size - 1, False
                header = self.headers.get("Range")
                if header:
                    match = _RANGE.match(header.strip())
                    if match:
                        first, last = match.groups()
                        if first:
                            start = int(first)
                            end = min(int(last), size - 1) if last else size - 1
                        elif last:                       # "bytes=-500": the final 500 bytes
                            start = max(0, size - int(last))
                        partial = True
                    if not match or start > end:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return

                length = end - start + 1
                self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
                self.send_header("Content-Type", _TYPES.get(os.path.splitext(path)[1].lower(), "video/mp4"))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if not send_body:
                    return

                try:
                    with open(path, "rb") as fh:
                        fh.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = fh.read(min(_CHUNK, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except OSError:
                    pass                                 # the player seeked away or closed

        self._httpd = _QuietServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._httpd.serve_forever, name="media-server", daemon=True).start()

    def url_for(self, path) -> str:
        path = os.path.abspath(str(path))
        with self._lock:
            token = self._by_path.get(path)
            if token is None:
                token = secrets.token_urlsafe(16)
                self._by_path[path] = token
                self._by_token[token] = path
        port = self._httpd.server_address[1]
        return f"http://127.0.0.1:{port}/media/{token}{os.path.splitext(path)[1].lower()}"

    def _lookup(self, token: str) -> str | None:
        with self._lock:
            return self._by_token.get(token)
