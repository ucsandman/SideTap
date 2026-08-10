"""Local web viewer: live phone screen, click-to-tap, doctor panel.

Stdlib http.server only. Serves on http://127.0.0.1:8770 (config.VIEWER_PORT,
override with VIEWER_PORT in .env).
The page streams frames from WDA's MJPEG server (:9100) and falls back to
polling /api/screenshot when the stream is down.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import admin, capture, config
from .wda_client import WDAClient, WDAError, stop_engaged, stop_file

_HTML = Path(__file__).with_name("viewer.html")

# Identifies this viewer process in /api/status; the page reloads itself when
# it changes, so a tab from before a restart never runs stale JS against new
# endpoints (a stale tab cost three debugging rounds on 2026-08-09).
_BOOT_ID = str(os.getpid()) + "-" + str(int(time.time()))

# Shared state for the "Fix input" job so a GET can poll a POST-started run.
_FIX_LOCK = threading.Lock()
_FIX_JOB = {"running": False, "step": "idle", "message": "", "ok": None}

# One phone gesture at a time: unlock() is a timed wake→swipe→type sequence,
# and a tap/keystroke landing in the middle of it garbles both.
_ACTION_LOCK = threading.Lock()

# Last good /api/status payload. Served while a gesture holds _ACTION_LOCK so
# the browser's poll doesn't queue requests inside WDA mid-sequence (unlock is
# timing-sensitive: added latency lets the lock screen fall back asleep).
_LAST_STATUS: dict | None = None

# Last good /api/phone payload, served while a gesture holds _ACTION_LOCK
# (same reasoning as _LAST_STATUS).
_LAST_PHONE: dict | None = None

# LAN exposure, probed in the background (the port probe can take a second) and
# surfaced as a persistent banner on the phone pane — not only inside the
# doctor tab. WDA has no auth, so an exposed port must be loud by default.
_LAN_STATE = {"exposed": None}  # None = not checked yet


def _refresh_lan_state(delay: float = 0.0) -> None:
    def probe():
        if delay:
            time.sleep(delay)
        try:
            ok, _detail, _fix = admin._check_ports_local()
            _LAN_STATE["exposed"] = not ok
        except Exception:
            _LAN_STATE["exposed"] = None  # unknown; never crash the viewer

    threading.Thread(target=probe, daemon=True).start()


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


# Tune WDA's MJPEG stream once per session (WDA's own defaults are choppy).
# Settings ride with the session, so a replaced session (WDA restart) needs a
# retune — key on the session id. Gesture idle-waits are NOT tuned here: the
# session creator applies those (wda_client._create_session, config-driven).
_TUNED_SESSION: str | None = None


def _tune_mjpeg(client: WDAClient) -> None:
    global _TUNED_SESSION
    if client.session_id and client.session_id == _TUNED_SESSION:
        return
    try:
        client.configure_mjpeg()  # config.MJPEG_FPS/_QUALITY/_SCALE
        _TUNED_SESSION = client.session_id
    except WDAError:
        pass  # stream still works on defaults


# 1x1 grey PNG shown when the phone is unreachable
_PLACEHOLDER = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763a8a9a90100029d0116f27ba7c60000000049"
    "454e44ae426082"
)


def _jsonl_tail(name: str, limit: int) -> list[dict]:
    """Last `limit` records of a JSONL file in .state/ (newest last)."""
    log = config.STATE_DIR / name
    if not log.exists():
        return []
    recs = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            recs.append(json.loads(line))
        except ValueError:
            pass
    return recs


def _recent_actions(limit: int = 10) -> list[dict]:
    """Last `limit` send records from .state/actions.log (newest last)."""
    return _jsonl_tail("actions.log", limit)


def _recent_activity(limit: int = 30) -> list[dict]:
    """Last `limit` phone actions from .state/agent_activity.log (newest last)."""
    return _jsonl_tail("agent_activity.log", limit)


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


# Console whitelist: helper names the viewer's console may dispatch. Mirrors
# mcp_server._TOOLS (not imported: that module needs the mcp package).
# screenshot excluded — bytes don't render in a JSON console.
_CONSOLE_TOOLS = (
    "ocr",
    "screen_info",
    "tap",
    "tap_text",
    "long_press",
    "swipe",
    "scroll",
    "type_text",
    "press_home",
    "open_app",
    "current_app",
    "wait_for_app",
    "find_text",
    "wait_for_text",
    "wait_stable",
    "read_messages",
    "send_message",
    "unlock",
)


def _console_literal(node: ast.AST):
    """A literal AST node's value. Raises ValueError on anything non-literal."""
    if isinstance(node, ast.Constant):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        return -node.operand.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_console_literal(e) for e in node.elts]
    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):  # {**x}
            raise ValueError("arguments must be literals")
        try:
            return {
                _console_literal(k): _console_literal(v)
                for k, v in zip(node.keys, node.values)
            }
        except TypeError as exc:  # e.g. {[1, 2]: 3} - unhashable key
            raise ValueError("arguments must be literals") from exc
    raise ValueError("arguments must be literals")


def _parse_console(line: str) -> tuple[str, list, dict]:
    """Parse one whitelisted helper call. -> (name, args, kwargs).

    Accepts exactly `name(literal, key=literal, ...)` where name is in
    _CONSOLE_TOOLS. Everything else raises ValueError — this is the whole
    security story of /api/console, so nothing here may call eval/exec.
    """
    try:
        expr = ast.parse(line.strip(), mode="eval").body
    except SyntaxError as exc:
        raise ValueError(f"syntax error: {exc.msg}") from exc
    if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Name):
        raise ValueError('one helper call, e.g. tap_text("General")')
    name = expr.func.id
    if name not in _CONSOLE_TOOLS:
        raise ValueError(f"unknown helper: {name}")
    args = [_console_literal(a) for a in expr.args]
    if any(k.arg is None for k in expr.keywords):  # **kwargs
        raise ValueError("**kwargs not allowed")
    kwargs = {k.arg: _console_literal(k.value) for k in expr.keywords}
    return name, args, kwargs


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
                global _LAST_STATUS
                if _ACTION_LOCK.locked() and _LAST_STATUS is not None:
                    self._json(_LAST_STATUS)
                    return
                try:
                    w, h = self.client.window_size()
                    _tune_mjpeg(self.client)
                    _LAST_STATUS = {
                        "window": {"width": w, "height": h},
                        "input": True,
                        "mjpeg": config.MJPEG_PORT,
                        "lan_exposed": _LAN_STATE["exposed"],
                        "boot": _BOOT_ID,
                    }
                    self._json(_LAST_STATUS)
                except WDAError:
                    pw, ph = _png_size(capture.screenshot_png(max_age=0.4))
                    self._json(
                        {
                            "window": {"width": pw, "height": ph},
                            "input": False,
                            "mjpeg": None,
                            "lan_exposed": _LAN_STATE["exposed"],
                            "boot": _BOOT_ID,
                        }
                    )
            elif path == "/api/phone":
                global _LAST_PHONE
                if _ACTION_LOCK.locked() and _LAST_PHONE is not None:
                    self._json(_LAST_PHONE)
                    return
                info: dict = {"battery": None, "locked": None, "app": None}
                try:
                    info["battery"] = self.client.battery()
                except WDAError:
                    pass
                try:
                    info["locked"] = self.client.is_locked()
                except WDAError:
                    pass
                try:
                    info["app"] = self.client.active_app()
                except WDAError:
                    pass
                info["session"] = self.client.session_id
                _LAST_PHONE = info
                self._json(info)
            elif path == "/api/doctor":
                self._json(admin.doctor_results())
            elif path == "/api/fix-input":
                with _FIX_LOCK:
                    self._json(dict(_FIX_JOB))
            elif path == "/api/actions":
                self._json(_recent_actions())
            elif path == "/api/activity":
                self._json(_recent_activity())
            elif path == "/api/stop":
                self._json({"stopped": stop_engaged()})
            elif path == "/api/apps":
                from . import helpers

                self._json({"known": sorted(helpers.BUNDLE_IDS)})
            else:
                self._json({"error": "not found"}, 404)
        except WDAError as exc:
            self._json({"error": str(exc)}, 502)
        except (ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

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
                with _ACTION_LOCK:
                    self.client.tap(float(payload["x"]), float(payload["y"]))
                self._json({"ok": True})
            elif path == "/api/swipe":
                with _ACTION_LOCK:
                    self.client.swipe(
                        float(payload["x1"]),
                        float(payload["y1"]),
                        float(payload["x2"]),
                        float(payload["y2"]),
                        min(max(float(payload.get("seconds", 0.3)), 0.05), 3.0),
                    )
                self._json({"ok": True})
            elif path == "/api/type":
                with _ACTION_LOCK:
                    self.client.type_text(str(payload.get("text", "")))
                self._json({"ok": True})
            elif path == "/api/home":
                with _ACTION_LOCK:
                    self.client.home()
                self._json({"ok": True})
            elif path == "/api/lock":
                with _ACTION_LOCK:
                    self.client.lock()
                self._json({"ok": True})
            elif path == "/api/unlock":
                from . import helpers

                with _ACTION_LOCK:
                    # Reuse the viewer's client: unlock is timing-sensitive,
                    # and a fresh client would redo session discovery mid-wake.
                    helpers.unlock(self.client)
                self._json({"ok": True})
            elif path == "/api/up":
                # Restart tunnel + WDA (the fix after a replug). Slow (up to
                # ~60s) but synchronous: the button disables while it runs.
                self._json({"ok": admin.up() == 0})
            elif path == "/api/fix-input":
                self._json(_start_fix_input())
            elif path == "/api/lock-ports":
                result = _lock_ports()
                # Re-probe once the UAC prompt has had time to be approved, so
                # the banner clears without a manual refresh.
                _refresh_lan_state(delay=8.0)
                self._json(result)
            elif path == "/api/stop":
                config.STATE_DIR.mkdir(exist_ok=True)
                if payload.get("stop"):
                    stop_file().touch()
                else:
                    stop_file().unlink(missing_ok=True)
                self._json({"stopped": stop_engaged()})
            elif path == "/api/text":
                from . import helpers

                to = str(payload.get("to", "")).strip()
                message = str(payload.get("message", "")).strip()
                if not to or not message:
                    self._json(
                        {"ok": False, "error": "to and message are required"}, 400
                    )
                    return
                try:
                    with _ACTION_LOCK:
                        result = helpers.send_message(to, message)
                except WDAError:
                    raise  # existing 502 handler
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                body = json.dumps(
                    {
                        "ok": bool(result.get("sent"))
                        if isinstance(result, dict)
                        else False,
                        "result": result,
                    },
                    default=repr,
                )
                self._send(200, body.encode(), "application/json")
            elif path == "/api/open-app":
                from . import helpers

                name = str(payload.get("name", "")).strip()
                if not name:
                    self._json({"ok": False, "error": "name is required"}, 400)
                    return
                try:
                    with _ACTION_LOCK:
                        helpers.open_app(name)
                except WDAError:
                    raise
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                self._json({"ok": True})
            elif path == "/api/console":
                from . import helpers

                try:
                    name, args, kwargs = _parse_console(str(payload.get("line", "")))
                except ValueError as exc:
                    self._json({"ok": False, "error": str(exc)}, 400)
                    return
                try:
                    with _ACTION_LOCK:
                        result = getattr(helpers, name)(*args, **kwargs)
                except WDAError:
                    raise
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                body = json.dumps({"ok": True, "result": result}, default=repr)
                self._send(200, body.encode(), "application/json")
            else:
                self._json({"error": "not found"}, 404)
        except WDAError as exc:
            self._json({"error": str(exc)}, 502)
        except (ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as exc:
            self._json({"error": str(exc)}, 500)


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
            from .device import _safe_kill

            # Only kill a process that is still a python (viewer) one: the
            # pid file survives crashes and Windows reuses pids.
            _safe_kill(pid, "python")
    config.STATE_DIR.mkdir(exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def serve(open_browser: bool = True) -> int:  # noqa: vulture
    _kill_stale_viewer()
    _refresh_lan_state()
    server = _Server(("127.0.0.1", config.VIEWER_PORT), Handler)
    url = f"http://127.0.0.1:{config.VIEWER_PORT}"
    print(f"Viewer: {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nViewer stopped.")
    finally:
        # Drop our pid file so a later launch can't kill a reused pid.
        pid_file = config.STATE_DIR / "viewer.pid"
        try:
            if pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
    return 0
