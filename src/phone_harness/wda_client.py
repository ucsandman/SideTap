"""Thin HTTP client for WebDriverAgent. No Appium, just requests.

WDA speaks a W3C-WebDriver-ish protocol on http://127.0.0.1:8100 once
`ios runwda` + `ios forward 8100 8100` are running (see device.py).
Coordinates are in points (the same units the UI element tree uses).
"""

from __future__ import annotations

import base64
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
        return value

    def _session_request(
        self, method: str, path: str, payload: dict | None = None
    ) -> Any:
        """Request under /session/{id}/..., recreating the session once if it died."""
        sid = self.ensure_session()
        try:
            return self._request(method, f"/session/{sid}{path}", payload)
        except WDAError as exc:
            if "invalid session" not in str(exc).lower():
                raise
            self.session_id = None
            sid = self.ensure_session()
            return self._request(method, f"/session/{sid}{path}", payload)

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
        value = self._request("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
        sid = value.get("sessionId") if isinstance(value, dict) else None
        if not sid:
            raise WDAError(f"Could not create a WDA session: {value!r}")
        self.session_id = sid
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

    def active_app(self) -> dict:  # noqa: vulture
        return self._session_request("GET", "/wda/activeAppInfo")

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
