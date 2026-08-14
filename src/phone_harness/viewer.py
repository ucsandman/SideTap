"""Local web viewer: live phone screen, click-to-tap, doctor panel.

Stdlib http.server only. Serves on http://127.0.0.1:8770 (config.VIEWER_PORT,
override with VIEWER_PORT in .env).
The page streams frames from WDA's MJPEG server (:9100) and falls back to
polling /api/screenshot when the stream is down.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import admin, approval, capture, config, device, trust
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

# ...but a gesture that cannot START promptly is no longer aimed at the screen
# the human was looking at, so it must be DROPPED rather than stored and
# replayed. unlock() holds the lock 11s on a shallow lock and 20-30s after a
# deep sleep, and a dark unresponsive screen is exactly what makes someone
# click it again. Measured live 2026-08-11: four taps made during one 11s
# unlock all executed AFTER it finished, 4.8-9.5s stale, landing on the
# unlocked home screen — a burst of gestures nobody aimed at anything.
# Generous enough that two deliberate gestures in a row still serialize (a tap
# round-trips in ~0.4s, a swipe in ~0.9s).
_ACTION_WAIT = 2.0


class PhoneBusy(Exception):
    """A gesture could not get the phone within _ACTION_WAIT, so it was dropped."""


@contextlib.contextmanager
def _action_slot():
    """Hold the phone for one gesture, or drop the gesture if it waited too long."""
    if not _ACTION_LOCK.acquire(timeout=_ACTION_WAIT):
        raise PhoneBusy()
    try:
        yield
    finally:
        _ACTION_LOCK.release()


# Last good /api/status payload. Served while a gesture holds _ACTION_LOCK so
# the browser's poll doesn't queue requests inside WDA mid-sequence (unlock is
# timing-sensitive: added latency lets the lock screen fall back asleep).
_LAST_STATUS: dict | None = None

# Last good /api/phone payload, served while a gesture holds _ACTION_LOCK
# (same reasoning as _LAST_STATUS).
_LAST_PHONE: dict | None = None

# Last good /api/doctor payload, served while a gesture holds _ACTION_LOCK.
# Same reasoning again, and it matters more here: the page re-runs the checks
# by itself while any of them fails, and a full run is ~2s of subprocesses AND
# a screenshot — exactly the latency that lets a waking lock screen fall back
# asleep mid-unlock.
_LAST_DOCTOR: list | None = None

# Self-healing link. Deep sleep kills WDA (iOS kills the test runner ~15min
# after the screen goes dark; watched it die live 2026-08-10) and NOTHING over
# USB can wake the phone — lockdown itself is gated while it sleeps (ReadPair
# errors) even though tunnel services keep working. So the watchdog waits for
# the human to wake the phone (lockdown answers again) and then reruns up()
# with zero clicks. The cool-down keeps a genuinely broken link (e.g. expired
# signature) from being up()'ed in a loop.
_HEAL = {"cooldown_until": 0.0}
_HEAL_POLL = 20.0  # seconds between watchdog looks
_HEAL_COOLDOWN = 300.0  # after a failed up(): don't thrash a broken link


def _should_heal(now: float, *, wda_up: bool, lockdown_ok: bool, stopped: bool) -> bool:
    if stopped or wda_up or not lockdown_ok:
        return False
    return now >= _HEAL["cooldown_until"]


def _heal_loop() -> None:
    probe = WDAClient(timeout=3)
    while True:
        time.sleep(_HEAL_POLL)
        try:
            if _should_heal(
                time.monotonic(),
                wda_up=probe.is_up(),
                lockdown_ok=device.lockdown_ready(),
                stopped=stop_engaged(),
            ):
                ok = admin.up() == 0
                _HEAL["cooldown_until"] = time.monotonic() + (
                    60.0 if ok else _HEAL_COOLDOWN
                )
        except Exception:
            _HEAL["cooldown_until"] = time.monotonic() + _HEAL_COOLDOWN


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

    try:
        result = signing.fix_input(progress=progress)
    except Exception as exc:
        # fix_input reports its own known failures as ok:False results. If
        # anything else escapes, this thread must still flip running=False —
        # dying here left the wizard reading "running" forever, with no error.
        result = {"ok": False, "step": "error", "message": str(exc)}
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

# window_size() is a device constant (201ms, never changes for a given screen)
# but its HTTP round trip also heals client.session_id on eviction, which is
# exactly what _tune_mjpeg checks right after — so this is keyed on session id,
# same as _TUNED_SESSION above, not cached forever. A genuine session change
# still pays the round trip once.
_WINDOW_SESSION: str | None = None
_WINDOW_SIZE: tuple[float, float] | None = None
_WINDOW_ORIENT: str | None = None


def _tune_mjpeg(client: WDAClient) -> None:
    global _TUNED_SESSION
    if client.session_id and client.session_id == _TUNED_SESSION:
        return
    try:
        client.configure_mjpeg()  # config.MJPEG_FPS/_QUALITY/_SCALE
        _TUNED_SESSION = client.session_id
    except WDAError:
        pass  # stream still works on defaults


# The Home Screen is an app like any other as far as WDA is concerned.
_SPRINGBOARD = "com.apple.springboard"


def _app_is_open(client: WDAClient) -> bool:
    """True when something other than the Home Screen is frontmost.

    Unknown counts as False, so the Home button falls back to the full walk
    rather than becoming a dead button: press_home() does nothing at all once
    you are already on the Home Screen.
    """
    try:
        info = client.active_app() or {}
    except WDAError:
        return False
    bundle = str(info.get("bundleId") or "")
    return bool(bundle) and bundle != _SPRINGBOARD


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
                # Never cached: the page reads it to tell "the link is still
                # coming up" apart from "the link is broken".
                starting = admin.bringing_up()
                # First run = WDA has never published a session on this
                # machine; the page swaps its checks auto-open for the guided
                # setup wizard until it has, and shows the Setup guide button.
                fresh = {
                    "starting": starting,
                    "setup_done": (config.STATE_DIR / "wda_session").exists(),
                    "app_dir": str(config.REPO_ROOT),
                }
                if _ACTION_LOCK.locked() and _LAST_STATUS is not None:
                    self._json({**_LAST_STATUS, **fresh})
                    return
                try:
                    global _WINDOW_SESSION, _WINDOW_SIZE, _WINDOW_ORIENT
                    # window_size() is 201ms and answers a value that only a
                    # rotation changes, so it is memoised — but it was also the
                    # ONLY WDA request this endpoint made, and "input": True
                    # below is the claim that the link is alive. Serving the
                    # memo without asking the phone anything reports a healthy
                    # link over a dead one for as long as the outage lasts, and
                    # deep sleep kills WDA ~15min after the screen darkens, so
                    # that is the normal case, not an edge one. viewer.html
                    # HIDES btn-up ("Restart link") and btn-fix while input is
                    # true, so the lie also removes the two buttons that fix
                    # it. orientation() keeps the endpoint honest for 7.7ms: it
                    # raises WDAError when WDA is gone (falling through to the
                    # go-ios pixel-size branch below), it heals an evicted
                    # session the way the window_size() round trip used to so
                    # _tune_mjpeg still sees the change, and it doubles as the
                    # rotation guard the memo needs.
                    orient = self.client.orientation()
                    sid = self.client.session_id
                    if (
                        sid
                        and sid == _WINDOW_SESSION
                        and _WINDOW_SIZE is not None
                        and orient == _WINDOW_ORIENT
                    ):
                        w, h = _WINDOW_SIZE
                    else:
                        w, h = self.client.window_size()
                        _WINDOW_SESSION, _WINDOW_SIZE = sid, (w, h)
                        _WINDOW_ORIENT = orient
                    _tune_mjpeg(self.client)
                    _LAST_STATUS = {
                        "window": {"width": w, "height": h},
                        "input": True,
                        "mjpeg": config.MJPEG_PORT,
                        "lan_exposed": _LAN_STATE["exposed"],
                        "boot": _BOOT_ID,
                    }
                    self._json({**_LAST_STATUS, **fresh})
                except WDAError:
                    # No WDA. The go-ios screenshot gives pixel size — but on a
                    # phoneless machine (fresh install, nothing plugged in) it
                    # raises too, and this endpoint must STILL answer: the
                    # first-run wizard rides on setup_done, and the 500 this
                    # used to raise is what hid the wizard on the first
                    # clean-machine test (2026-08-13).
                    try:
                        pw, ph = _png_size(capture.screenshot_png(max_age=0.4))
                        window = {"width": pw, "height": ph}
                    except Exception:
                        window = None
                    self._json(
                        {
                            "window": window,
                            "input": False,
                            "mjpeg": None,
                            "lan_exposed": _LAN_STATE["exposed"],
                            "boot": _BOOT_ID,
                            **fresh,
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
                # Home Screen position, for the "Go to page" chips. Only on the
                # springboard: current_page() is one targeted element lookup
                # (0.37s measured), but inside an app there is no PageIndicator
                # to find and the call would be pure cost. trust.internal()
                # because this is the viewer's own 10s bookkeeping poll, not
                # content reaching an agent — without it the send gate would arm
                # itself every ten seconds for as long as the Home Screen is up.
                info["page"] = None
                if str((info["app"] or {}).get("bundleId") or "") == _SPRINGBOARD:
                    from . import helpers

                    try:
                        with trust.internal():
                            info["page"] = helpers.current_page()
                    except WDAError:
                        pass
                _LAST_PHONE = info
                self._json(info)
            elif path == "/api/doctor":
                global _LAST_DOCTOR
                if _ACTION_LOCK.locked() and _LAST_DOCTOR is not None:
                    self._json(_LAST_DOCTOR)
                    return
                _LAST_DOCTOR = admin.doctor_results()
                self._json(_LAST_DOCTOR)
            elif path == "/api/fix-input":
                with _FIX_LOCK:
                    self._json(dict(_FIX_JOB))
            elif path == "/api/actions":
                self._json(_recent_actions())
            elif path == "/api/activity":
                self._json(_recent_activity())
            elif path == "/api/stop":
                self._json({"stopped": stop_engaged()})
            elif path == "/api/send_approval":
                self._json({"mode": approval.mode(), "modes": list(approval.MODES)})
            elif path == "/api/pending_send":
                # A send the agent asked for after reading the phone. It is
                # blocked in another process until this is answered.
                self._json({"pending": approval.pending()})
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
                with _action_slot():
                    self.client.tap(float(payload["x"]), float(payload["y"]))
                self._json({"ok": True})
            elif path == "/api/swipe":
                with _action_slot():
                    self.client.swipe(
                        float(payload["x1"]),
                        float(payload["y1"]),
                        float(payload["x2"]),
                        float(payload["y2"]),
                        min(max(float(payload.get("seconds", 0.3)), 0.05), 3.0),
                    )
                self._json({"ok": True})
            elif path == "/api/type":
                with _action_slot():
                    self.client.type_text(str(payload.get("text", "")))
                self._json({"ok": True})
            elif path == "/api/long_press":
                with _action_slot():
                    self.client.long_press(
                        float(payload["x"]),
                        float(payload["y"]),
                        min(max(float(payload.get("seconds", 0.8)), 0.2), 3.0),
                    )
                self._json({"ok": True})
            elif path == "/api/home":
                from . import helpers

                # Two jobs, one button, exactly like the physical Home gesture:
                # from an app it only LEAVES the app (~0.4s, one call), and only
                # from the Home Screen itself does a second press walk to page 1.
                # Walking unconditionally froze the phone for 6-8s behind a busy
                # label just to minimise an app, which is most of what this
                # button gets used for. The walk is still the only way to reach
                # page 1: /wda/homescreen is a no-op between Home Screen pages.
                with _action_slot():
                    if _app_is_open(self.client):
                        helpers.press_home()
                    else:
                        helpers.goto_home_page(1)
                self._json({"ok": True})
            elif path == "/api/page":
                from . import helpers

                # Chips are 1..total only. goto_home_page() raises for anything
                # else on purpose, but it still WALKS correctly out of Today
                # View and the App Library, so those two need no chip of their
                # own — the header names them and any page chip escapes them.
                try:
                    index = int(payload["index"])
                except (KeyError, TypeError, ValueError):
                    self._json({"ok": False, "error": "index is required"}, 400)
                    return
                try:
                    with _action_slot():
                        helpers.goto_home_page(index)
                except WDAError:
                    raise
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                self._json({"ok": True})
            elif path == "/api/read-thread":
                from . import helpers

                contact = str(payload.get("contact", "")).strip()
                if not contact:
                    self._json({"ok": False, "error": "contact is required"}, 400)
                    return
                limit = min(max(int(payload.get("limit", 20)), 1), 50)
                try:
                    # NOT trust.internal(): message text is the most direct
                    # injection route into anything sharing this process, and
                    # read_messages marks it as such. The viewer's own Send is
                    # human_initiated and stays ungated; an agent send from the
                    # debug console does not, which is the point.
                    with _action_slot():
                        messages = helpers.read_messages(contact, limit)
                except WDAError:
                    raise
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                self._json({"ok": True, "messages": messages})
            elif path == "/api/lock":
                with _action_slot():
                    self.client.lock()
                self._json({"ok": True})
            elif path == "/api/unlock":
                from . import helpers

                # Unlock's wake/swipe goes THROUGH WDA. When deep sleep killed
                # WDA the button used to time out silently — say what to do.
                if not self.client.is_up():
                    self._json(
                        {
                            "ok": False,
                            "error": (
                                "Phone link is down (deep sleep kills it). Wake "
                                "the phone with its side button — the link "
                                "reconnects automatically, then Unlock works."
                            ),
                        }
                    )
                    return
                with _action_slot():
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
            elif path == "/api/send_approval":
                # The only writable path to the gate setting, and it is
                # origin-guarded like every other POST. No agent tool sets this.
                try:
                    self._json({"mode": approval.set_mode(payload.get("mode", ""))})
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
            elif path == "/api/send_decision":
                ok = approval.decide(
                    str(payload.get("id", "")), str(payload.get("decision", ""))
                )
                self._json({"ok": ok})
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
                    # The human typed this recipient and this text into the
                    # form and clicked, so there is nothing to approve. Gating
                    # here would also block inside _ACTION_LOCK for the whole
                    # approval timeout and freeze every other viewer gesture.
                    with _action_slot(), trust.human_initiated():
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
                    with _action_slot():
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
                    with _action_slot():
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
        except PhoneBusy:
            # Dropped on purpose. Say so plainly: silence here reads as the
            # freeze that caused the extra clicks in the first place.
            self._json(
                {
                    "ok": False,
                    "error": (
                        "Phone is busy with another action — that one was "
                        "dropped, not queued. Try again in a moment."
                    ),
                },
                409,
            )
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
            # NOT the process tree: the tunnel and the forwards are CHILDREN of
            # the launch.py that started them, so a tree kill here takes the
            # phone link down as a side effect of restarting the UI. Starting
            # SideTap while one was already open did exactly that (measured
            # 2026-08-12: all 11 checks green, then tunnel + WDA dead seconds
            # later, with up() already past its "Already up" early return).
            # down() is what stops those, by their own pid files.
            _safe_kill(pid, "python", tree=False)
    config.STATE_DIR.mkdir(exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def serve(open_browser: bool = True) -> int:  # noqa: vulture
    _kill_stale_viewer()
    _refresh_lan_state()
    threading.Thread(target=_heal_loop, daemon=True).start()
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
