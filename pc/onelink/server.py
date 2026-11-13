"""Tiny local web server: serves the web UI and exposes the bridge as JSON.

This is what makes the tray-only mode cheap — there is no WebView2 process when
the window is closed. The UI opens in the user's normal browser, and every API
call is a POST to 127.0.0.1 on a random port. Bound to loopback only.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .webhost import INDEX, WEBUI

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}

API_METHODS = ("state", "refresh", "add_manual", "select", "connect", "disconnect",
               "set_cfg", "copy_to_clip")


class _Handler(BaseHTTPRequestHandler):
    server_version = "OneLink"
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- files
    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_file(INDEX)
            return
        name = os.path.basename(path)
        candidate = os.path.join(WEBUI, name)
        if os.path.isfile(candidate):
            self._send_file(candidate)
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def _send_file(self, path: str):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            self._json({"ok": False, "error": "unreadable"}, 500)
            return
        ext = os.path.splitext(path)[1].lower()
        ctype = CONTENT_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------ api
    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/api/"):
            self._json({"ok": False, "error": "no such endpoint"}, 404)
            return
        method = self.path[len("/api/"):].split("?", 1)[0]
        if method not in API_METHODS:
            self._json({"ok": False, "error": "unknown method"}, 400)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"[]"
        try:
            args = json.loads(raw.decode("utf-8") or "[]")
        except (ValueError, UnicodeDecodeError):
            args = []
        if not isinstance(args, list):
            args = [args]
        bridge = self.server.bridge  # type: ignore[attr-defined]
        fn = getattr(bridge, method, None)
        if fn is None:
            self._json({"ok": False, "error": "missing method"}, 500)
            return
        try:
            result = fn(*args)
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)}, 500)
            return
        if result is None:
            result = True
        self._json(result)

    def _json(self, payload, status: int = 200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):  # keep the console quiet
        pass


class UiServer:
    """Owns the HTTP thread; start() returns the URL to open."""

    def __init__(self, bridge, port: int = 0):
        self.bridge = bridge
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self._httpd is None:
            httpd = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
            httpd.daemon_threads = True
            httpd.bridge = self.bridge  # type: ignore[attr-defined]
            self._httpd = httpd
            self.port = httpd.server_address[1]
            self._thread = threading.Thread(target=httpd.serve_forever,
                                            name="onelink-http", daemon=True)
            self._thread.start()
        return self.url

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d/" % self.port

    def stop(self):
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
