"""Local web viewer: live phone screen, click-to-tap, doctor panel.

Stdlib http.server only. Serves on http://127.0.0.1:8765 (config.VIEWER_PORT).
The page streams frames from WDA's MJPEG server (:9100) and falls back to
polling /api/screenshot when the stream is down.
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import admin, capture, config
from .wda_client import WDAClient, WDAError

_HTML = Path(__file__).with_name("viewer.html")


def _png_size(png: bytes) -> tuple[int, int]:
    """Read width/height from a PNG's IHDR chunk (bytes 16..24)."""
    if len(png) >= 24 and png[12:16] == b"IHDR":
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return 0, 0


# 1x1 grey PNG shown when the phone is unreachable
_PLACEHOLDER = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763a8a9a90100029d0116f27ba7c60000000049"
    "454e44ae426082"
)


class Handler(BaseHTTPRequestHandler):
    client = WDAClient(timeout=10)

    def log_message(self, *args):  # keep the terminal quiet  # noqa: vulture
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):  # noqa: vulture
        path = self.path.split("?")[0]
        try:
            if path == "/":
                self._send(200, _HTML.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/screenshot":
                try:
                    self._send(200, capture.screenshot_png(max_age=0.4), "image/png")
                except Exception:
                    self._send(200, _PLACEHOLDER, "image/png")
            elif path == "/api/status":
                # Screen size in points comes from WDA when the input driver is up.
                # Without it we still stream go-ios screenshots and use pixel size.
                try:
                    w, h = self.client.window_size()
                    self._json({"window": {"width": w, "height": h}, "input": True})
                except WDAError:
                    pw, ph = _png_size(capture.screenshot_png(max_age=0.4))
                    self._json({"window": {"width": pw, "height": ph}, "input": False})
            elif path == "/api/doctor":
                self._json(admin.doctor_results())
            else:
                self._json({"error": "not found"}, 404)
        except WDAError as exc:
            self._json({"error": str(exc)}, 502)
        except (ConnectionAbortedError, BrokenPipeError):
            pass

    def do_POST(self):  # noqa: vulture
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
        try:
            if path == "/api/tap":
                self.client.tap(float(payload["x"]), float(payload["y"]))
                self._json({"ok": True})
            elif path == "/api/home":
                self.client.home()
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except WDAError as exc:
            self._json({"error": str(exc)}, 502)
        except (ConnectionAbortedError, BrokenPipeError):
            pass


def serve(open_browser: bool = True) -> int:  # noqa: vulture
    server = ThreadingHTTPServer(("127.0.0.1", config.VIEWER_PORT), Handler)
    url = f"http://127.0.0.1:{config.VIEWER_PORT}"
    print(f"Viewer: {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nViewer stopped.")
    return 0
