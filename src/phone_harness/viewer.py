"""Local web viewer: live phone screen, click-to-tap, doctor panel.

Stdlib http.server only. Serves on http://127.0.0.1:8770 (config.VIEWER_PORT,
override with VIEWER_PORT in .env).
The page streams frames from WDA's MJPEG server (:9100) and falls back to
polling /api/screenshot when the stream is down.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import admin, capture, config
from .wda_client import WDAClient, WDAError

_HTML = Path(__file__).with_name("viewer.html")

# Shared state for the "Fix input" job so a GET can poll a POST-started run.
_FIX_LOCK = threading.Lock()
_FIX_JOB = {"running": False, "step": "idle", "message": "", "ok": None}


def _fix_input_worker():
    from . import signing

    def progress(step, message):
        with _FIX_LOCK:
            _FIX_JOB["step"] = step
            _FIX_JOB["message"] = message

    result = signing.fix_input(progress=progress)
    with _FIX_LOCK:
        _FIX_JOB.update(
            running=False,
            step=result["step"],
            message=result["message"],
            ok=result["ok"],
        )


def _start_fix_input() -> dict:
    with _FIX_LOCK:
        if _FIX_JOB["running"]:
            return dict(_FIX_JOB)
        _FIX_JOB.update(running=True, step="p12", message="starting…", ok=None)
    threading.Thread(target=_fix_input_worker, daemon=True).start()
    with _FIX_LOCK:
        return dict(_FIX_JOB)


def _png_size(png: bytes) -> tuple[int, int]:
    """Read width/height from a PNG's IHDR chunk (bytes 16..24)."""
    if len(png) >= 24 and png[12:16] == b"IHDR":
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return 0, 0


# Tune WDA's MJPEG stream once per viewer run (defaults are 10fps/quality 25).
_MJPEG_TUNED = False


def _tune_mjpeg(client: WDAClient) -> None:
    global _MJPEG_TUNED
    if _MJPEG_TUNED:
        return
    try:
        client.configure_mjpeg(framerate=15, quality=60)
        _MJPEG_TUNED = True
    except WDAError:
        pass  # stream still works on defaults


# 1x1 grey PNG shown when the phone is unreachable
_PLACEHOLDER = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763a8a9a90100029d0116f27ba7c60000000049"
    "454e44ae426082"
)


def _recent_actions(limit: int = 10) -> list[dict]:
    """Last `limit` send records from .state/actions.log (newest last)."""
    log = config.STATE_DIR / "actions.log"
    if not log.exists():
        return []
    recs = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            recs.append(json.loads(line))
        except ValueError:
            pass
    return recs


def _lock_ports() -> dict:
    """Launch the self-elevating firewall script (UAC prompt on the desktop)."""
    script = config.REPO_ROOT / "scripts" / "lock_ports.ps1"
    if not script.exists():
        return {"ok": False, "message": "scripts/lock_ports.ps1 not found"}
    try:
        subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            creationflags=(
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            ),
        )
    except OSError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "Approve the admin prompt, then re-run checks."}


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

    def _allowed(self) -> bool:
        """Reject cross-origin / DNS-rebinding requests.

        Loopback binding is not a boundary against the user's own browser: any
        page in any tab can POST to 127.0.0.1. We validate against the port we
        are actually serving on (not config, so tests on an ephemeral port work).
        """
        port = self.server.server_port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        ):
            return False
        sfs = self.headers.get("Sec-Fetch-Site")
        if sfs and sfs not in ("same-origin", "none"):
            return False
        return True

    def do_GET(self):  # noqa: vulture
        if not self._allowed():
            self._json({"error": "forbidden"}, 403)
            return
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
                    _tune_mjpeg(self.client)
                    self._json(
                        {
                            "window": {"width": w, "height": h},
                            "input": True,
                            "mjpeg": config.MJPEG_PORT,
                        }
                    )
                except WDAError:
                    pw, ph = _png_size(capture.screenshot_png(max_age=0.4))
                    self._json(
                        {
                            "window": {"width": pw, "height": ph},
                            "input": False,
                            "mjpeg": None,
                        }
                    )
            elif path == "/api/doctor":
                self._json(admin.doctor_results())
            elif path == "/api/fix-input":
                with _FIX_LOCK:
                    self._json(dict(_FIX_JOB))
            elif path == "/api/actions":
                self._json(_recent_actions())
            else:
                self._json({"error": "not found"}, 404)
        except WDAError as exc:
            self._json({"error": str(exc)}, 502)
        except (ConnectionAbortedError, BrokenPipeError):
            pass

    def do_POST(self):  # noqa: vulture
        if not self._allowed():
            self._json({"error": "forbidden"}, 403)
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json({"error": "forbidden"}, 403)
            return
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
        try:
            if path == "/api/tap":
                self.client.tap(float(payload["x"]), float(payload["y"]))
                self._json({"ok": True})
            elif path == "/api/swipe":
                self.client.swipe(
                    float(payload["x1"]),
                    float(payload["y1"]),
                    float(payload["x2"]),
                    float(payload["y2"]),
                    min(max(float(payload.get("seconds", 0.3)), 0.05), 3.0),
                )
                self._json({"ok": True})
            elif path == "/api/type":
                self.client.type_text(str(payload.get("text", "")))
                self._json({"ok": True})
            elif path == "/api/home":
                self.client.home()
                self._json({"ok": True})
            elif path == "/api/fix-input":
                self._json(_start_fix_input())
            elif path == "/api/lock-ports":
                self._json(_lock_ports())
            else:
                self._json({"error": "not found"}, 404)
        except WDAError as exc:
            self._json({"error": str(exc)}, 502)
        except (ConnectionAbortedError, BrokenPipeError):
            pass


class _Server(ThreadingHTTPServer):
    # _kill_stale_viewer() kills the previous instance before we bind, so the old
    # double-bind risk is gone. Keep reuse on so a restart can rebind immediately
    # instead of failing while the old socket drains from TIME_WAIT.
    allow_reuse_address = True  # noqa: vulture  (read by ThreadingHTTPServer)


def _kill_stale_viewer() -> None:
    """Kill a previous viewer (recorded in .state/viewer.pid) so we can bind."""
    pid_file = config.STATE_DIR / "viewer.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
        except ValueError:
            pid = 0
        if pid and pid != os.getpid():
            from .device import _pid_alive

            if _pid_alive(pid):
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    import signal

                    os.kill(pid, signal.SIGTERM)
    config.STATE_DIR.mkdir(exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def serve(open_browser: bool = True) -> int:  # noqa: vulture
    _kill_stale_viewer()
    server = _Server(("127.0.0.1", config.VIEWER_PORT), Handler)
    url = f"http://127.0.0.1:{config.VIEWER_PORT}"
    print(f"Viewer: {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nViewer stopped.")
    return 0
