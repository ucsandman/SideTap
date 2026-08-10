"""Thin HTTP client for WebDriverAgent. No Appium, just requests.

WDA speaks a W3C-WebDriver-ish protocol on http://127.0.0.1:8100 once
`ios runwda` + `ios forward 8100 8100` are running (see device.py).
Coordinates are in points (the same units the UI element tree uses).
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import requests

from . import config


class WDAError(RuntimeError):
    """A WebDriverAgent call failed. The message says what and why."""


def stop_file():
    """Path of the kill-switch file. Read dynamically so tests can relocate it."""
    return config.STATE_DIR / "STOP"


def stop_engaged() -> bool:
    return stop_file().exists()


def activity_file():
    """Path of the live activity feed. Read dynamically so tests can relocate it."""
    return config.STATE_DIR / "agent_activity.log"


def session_file():
    """Path of the shared WDA session id. Read dynamically for tests.

    WDA holds exactly ONE session: a client creating its own evicts whoever
    had it (a viewer click would garble an agent mid-send_message). Every
    client therefore adopts the published id and only mints a fresh session
    when the published one is dead.
    """
    return config.STATE_DIR / "wda_session"


def _read_shared_session() -> str | None:
    try:
        sid = session_file().read_text(encoding="utf-8").strip()
        return sid or None
    except OSError:
        return None


def _write_shared_session(sid: str) -> None:
    try:
        config.STATE_DIR.mkdir(exist_ok=True)
        session_file().write_text(sid, encoding="utf-8")
    except OSError:
        pass  # sharing is best-effort; the session itself still works


_ACTIVITY_MAX_BYTES = 64_000
_ACTIVITY_KEEP_LINES = 200


def _activity_summary(path: str, payload: dict | None) -> str | None:
    """One feed line for a phone-changing POST, or None to skip.

    Pure function (unit-tested). NEVER includes typed text — /wda/keys carries
    passwords and passcodes, so only the character count is recorded.
    """
    payload = payload or {}
    if path == "/wda/homescreen":
        return "home"
    if path == "/wda/unlock":
        return "wake"
    if path.endswith("/wda/keys"):
        n = len(payload.get("value") or [])
        return f"type ({n} char{'' if n == 1 else 's'})"
    if path.endswith("/wda/pressButton"):
        return f"button: {payload.get('name', '?')}"
    if path.endswith("/wda/apps/launch"):
        return f"open app: {payload.get('bundleId', '?')}"
    if path.endswith("/actions"):
        try:
            steps = payload["actions"][0]["actions"]
            moves = [s for s in steps if s.get("type") == "pointerMove"]
            pauses = [s.get("duration", 0) for s in steps if s.get("type") == "pause"]
            if len(moves) >= 2:
                a, b = moves[0], moves[-1]
                return (
                    f"swipe ({a['x']:.0f}, {a['y']:.0f}) → ({b['x']:.0f}, {b['y']:.0f})"
                )
            if moves:
                kind = "long-press" if max(pauses or [0]) > 300 else "tap"
                return f"{kind} ({moves[0]['x']:.0f}, {moves[0]['y']:.0f})"
        except (KeyError, IndexError, TypeError):
            pass
        return "touch gesture"
    if path.endswith("/appium/settings"):
        return None  # stream tuning, not a phone action
    return path.rsplit("/", 1)[-1]  # unknown action: still visible in the feed


def _log_activity(path: str, payload: dict | None) -> None:
    """Append one line to .state/agent_activity.log. Never raises.

    Every process driving the phone (agent scripts, the viewer) funnels through
    _request, so this one append gives the human a complete live feed. The
    trim keeps the file bounded; losing lines to a rare concurrent trim is
    acceptable, breaking a phone action is not.
    """
    try:
        summary = _activity_summary(path, payload)
        if not summary:
            return
        feed = activity_file()
        config.STATE_DIR.mkdir(exist_ok=True)
        with open(feed, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "action": summary}) + "\n")
        if feed.stat().st_size > _ACTIVITY_MAX_BYTES:
            lines = feed.read_text(encoding="utf-8", errors="replace").splitlines(
                keepends=True
            )
            feed.write_text("".join(lines[-_ACTIVITY_KEEP_LINES:]), encoding="utf-8")
    except Exception:
        pass  # the feed must never break a phone action


class WDAClient:
    def __init__(self, base_url: str = config.WDA_URL, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_id: str | None = None

    # ---- plumbing ----------------------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        # Kill switch: every phone-changing call funnels through here as a POST.
        # GETs (perception) and session creation stay allowed so the viewer can
        # still show the screen while stopped.
        if method == "POST" and path != "/session" and stop_engaged():
            raise WDAError(
                "STOP is engaged (.state/STOP exists). Click RESUME in the "
                "viewer, or delete the file, to allow actions again."
            )
        url = self.base_url + path
        try:
            resp = requests.request(method, url, json=payload, timeout=self.timeout)
        except requests.Timeout as exc:
            raise WDAError(
                f"{method} {path}: WebDriverAgent did not answer within "
                f"{self.timeout:g}s. It may be busy or wedged; try again or "
                "run `phone-harness up`."
            ) from exc
        except requests.RequestException as exc:
            raise WDAError(
                f"Cannot reach WebDriverAgent at {self.base_url}. "
                "Run `phone-harness up` (and check `phone-harness doctor`)."
            ) from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise WDAError(
                f"{method} {path}: non-JSON reply (HTTP {resp.status_code})"
            ) from exc
        value = body.get("value")
        if isinstance(value, dict) and value.get("error"):
            raise WDAError(
                f"{method} {path}: {value.get('error')}: {value.get('message', '')}"
            )
        if resp.status_code >= 400:
            raise WDAError(f"{method} {path}: HTTP {resp.status_code}: {body}")
        if method == "POST" and path != "/session":
            _log_activity(path, payload)  # only actions that actually happened
        return value

    @staticmethod
    def _session_unusable(exc: WDAError) -> bool:
        """Errors a fresh session fixes. "invalid session" is the dead one;
        "point.x != INFINITY" is a session that crossed a screen lock — it
        still answers perception GETs but every /actions fails forever
        (seen live 2026-08-09; the unlock button only woke the phone)."""
        msg = str(exc).lower()
        return "invalid session" in msg or "point.x != infinity" in msg

    def _session_request(
        self, method: str, path: str, payload: dict | None = None
    ) -> Any:
        """Request under /session/{id}/..., recovering once if the session died.

        Recovery prefers the published shared id: if another process already
        replaced the session, adopt theirs instead of evicting it right back.
        """
        sid = self.ensure_session()
        try:
            return self._request(method, f"/session/{sid}{path}", payload)
        except WDAError as exc:
            if not self._session_unusable(exc):
                raise
        shared = _read_shared_session()
        if shared and shared != sid:
            self.session_id = shared  # another process already replaced it
            try:
                return self._request(method, f"/session/{shared}{path}", payload)
            except WDAError as exc:
                if not self._session_unusable(exc):
                    raise
        self._create_session()
        return self._request(method, f"/session/{self.session_id}{path}", payload)

    # ---- session -----------------------------------------------------------

    def status(self) -> dict:
        return self._request("GET", "/status")

    def is_up(self) -> bool:
        try:
            self.status()
            return True
        except WDAError:
            return False

    def ensure_session(self) -> str:
        if self.session_id:
            return self.session_id
        shared = _read_shared_session()
        if shared:
            self.session_id = shared  # dead ids heal in _session_request
            return shared
        return self._create_session()

    def _create_session(self) -> str:
        value = self._request("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
        sid = value.get("sessionId") if isinstance(value, dict) else None
        if not sid:
            raise WDAError(f"Could not create a WDA session: {value!r}")
        self.session_id = sid
        _write_shared_session(sid)
        try:
            # Settings ride with the session, so only the creator applies them.
            self._request(
                "POST",
                f"/session/{sid}/appium/settings",
                {
                    "settings": {
                        "waitForIdleTimeout": config.WDA_IDLE_WAIT,
                        "animationCoolOffTimeout": config.WDA_ANIM_COOLOFF,
                    }
                },
            )
        except WDAError:
            pass  # a session on default waits is slow, not broken
        return sid

    # ---- perception --------------------------------------------------------

    def screenshot(self) -> bytes:
        """Current screen as PNG bytes (pixel resolution = points * scale)."""
        value = self._request("GET", "/screenshot")
        return base64.b64decode(value)

    def window_size(self) -> tuple[float, float]:
        value = self._session_request("GET", "/window/size")
        return float(value["width"]), float(value["height"])

    def source(self) -> dict:
        """Full UI element tree as nested dicts (type, label, name, value, rect, children)."""
        return self._session_request("GET", "/source?format=json")

    def active_app(self) -> dict:
        return self._session_request("GET", "/wda/activeAppInfo")

    def battery(self) -> dict:
        """Raw WDA battery info: {level: 0..1, state: int} (state 2 = charging)."""
        return self._session_request("GET", "/wda/batteryInfo")

    # DISPLAY-ONLY: /wda/locked can report unlocked with the passcode pad on
    # screen (a test pins that unlock() never consults it). The viewer's status
    # strip shows it; nothing may act on it.
    def is_locked(self) -> bool:
        return bool(self._request("GET", "/wda/locked"))

    # ---- action ------------------------------------------------------------

    def _pointer_actions(self, steps: list[dict]) -> None:
        self._session_request(
            "POST",
            "/actions",
            {
                "actions": [
                    {
                        "type": "pointer",
                        "id": "finger1",
                        "parameters": {"pointerType": "touch"},
                        "actions": steps,
                    }
                ]
            },
        )

    def tap(self, x: float, y: float) -> None:
        self._pointer_actions(
            [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 80},
                {"type": "pointerUp", "button": 0},
            ]
        )

    def long_press(self, x: float, y: float, seconds: float = 1.0) -> None:
        self._pointer_actions(
            [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": int(seconds * 1000)},
                {"type": "pointerUp", "button": 0},
            ]
        )

    def swipe(
        self, x1: float, y1: float, x2: float, y2: float, seconds: float = 0.3
    ) -> None:
        # Short pre-move hold: long holds read as drag-to-rearrange, and the
        # home screen only pages on a quick flick.
        self._pointer_actions(
            [
                {"type": "pointerMove", "duration": 0, "x": x1, "y": y1},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 40},
                {
                    "type": "pointerMove",
                    "duration": int(seconds * 1000),
                    "x": x2,
                    "y": y2,
                },
                {"type": "pointerUp", "button": 0},
            ]
        )

    def type_text(self, text: str) -> None:
        """Type into the focused field (tap a field first)."""
        self._session_request("POST", "/wda/keys", {"value": list(text)})

    def press_button(self, name: str) -> None:  # noqa: vulture
        """Hardware buttons: home, volumeUp, volumeDown."""
        self._session_request("POST", "/wda/pressButton", {"name": name})

    def home(self) -> None:
        self._request("POST", "/wda/homescreen")

    def unlock(self) -> None:
        """Wake + swipe up. Only unlocks fully if the phone has no passcode."""
        self._request("POST", "/wda/unlock")

    def lock(self) -> None:
        """Lock the screen. Right after an unlock, iOS's require-passcode
        grace period can make this time out while the screen still turns off —
        treat that error as cosmetic, not a failure to lock."""
        self._request("POST", "/wda/lock")

    def app_launch(self, bundle_id: str) -> None:
        self._session_request("POST", "/wda/apps/launch", {"bundleId": bundle_id})

    def configure_mjpeg(
        self,
        framerate: int = config.MJPEG_FPS,
        quality: int = config.MJPEG_QUALITY,
        scale: int = config.MJPEG_SCALE,
    ) -> None:
        self._session_request(
            "POST",
            "/appium/settings",
            {
                "settings": {
                    "mjpegServerFramerate": framerate,
                    "mjpegServerScreenshotQuality": quality,
                    "mjpegScalingFactor": scale,
                }
            },
        )
