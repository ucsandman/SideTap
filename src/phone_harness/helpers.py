"""Agent-facing primitives, pre-imported by the phone-harness CLI.

Same surface as the original macOS phone-harness, plus tree-based extras.
All coordinates are in points (what the UI tree uses), origin top-left.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import capture, config, device
from .wda_client import WDAClient, WDAError

_client: WDAClient | None = None


def client() -> WDAClient:
    global _client
    if _client is None:
        _client = WDAClient()
    return _client


# ---- perception ------------------------------------------------------------


def screenshot(path: str | None = None) -> bytes:
    """PNG of the current screen. Saves to `path` if given, returns the bytes.

    Uses go-ios (no WebDriverAgent needed), so viewing works even before the
    input driver is signed.
    """
    png = capture.screenshot_png()
    if path:
        Path(path).write_bytes(png)
    return png


def screen_info() -> dict:
    """Window size in points; tap coordinates must stay inside this."""
    w, h = client().window_size()
    return {"width": w, "height": h, "units": "points"}


def collect_texts(node: dict, out: list | None = None) -> list[dict]:
    """Walk a WDA source tree; return visible elements that carry text.

    Pure function (unit-tested). Each hit: {text, x, y, rect, type} where
    x,y is the element center in points.
    """
    if out is None:
        out = []
    if not isinstance(node, dict):
        return out
    visible = str(node.get("isVisible", "1")) in ("1", "true", "True")
    text = node.get("label") or node.get("name") or node.get("value") or ""
    rect = node.get("rect") or {}
    if visible and text and rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
        out.append(
            {
                "text": str(text),
                "x": rect["x"] + rect["width"] / 2,
                "y": rect["y"] + rect["height"] / 2,
                "rect": rect,
                "type": node.get("type", ""),
            }
        )
    for child in node.get("children") or []:
        collect_texts(child, out)
    return out


def ui_tree() -> dict:
    """Raw UI element tree (nested dicts). The precise view of the screen."""
    return client().source()


def ocr() -> list[dict]:
    """All visible on-screen text with center coordinates.

    Name kept from the original harness; here it reads the real UI element
    tree, so results are exact, not OCR guesses.
    """
    return collect_texts(ui_tree())


# ---- action ----------------------------------------------------------------


def tap(x: float, y: float) -> None:
    """Tap at (x, y) in points."""
    client().tap(x, y)


def long_press(x: float, y: float, seconds: float = 1.0) -> None:
    client().long_press(x, y, seconds)


def swipe(x1: float, y1: float, x2: float, y2: float, seconds: float = 0.3) -> None:
    client().swipe(x1, y1, x2, y2, seconds)


def scroll(direction: str = "down", amount: float = 0.4) -> None:
    """Scroll the screen content. direction: up/down/left/right.

    'down' means see content further down (content moves up).
    """
    w, h = client().window_size()
    cx, cy = w / 2, h / 2
    dx = dy = 0.0
    if direction == "down":
        dy = -h * amount
    elif direction == "up":
        dy = h * amount
    elif direction == "left":
        dx = -w * amount
    elif direction == "right":
        dx = w * amount
    else:
        raise ValueError("direction must be up, down, left, or right")
    client().swipe(cx, cy, cx + dx, cy + dy, 0.3)


def find_text(text: str, exact: bool = False) -> list[dict]:
    """All elements whose text matches (case-insensitive)."""
    needle = text.lower().strip()
    hits = []
    for el in ocr():
        hay = el["text"].lower().strip()
        if (hay == needle) if exact else (needle in hay):
            hits.append(el)
    # exact matches first, then shortest text (least noisy match)
    hits.sort(key=lambda e: (e["text"].lower().strip() != needle, len(e["text"])))
    return hits


def tap_text(text: str, index: int = 0, exact: bool = False) -> dict:
    """Find text on screen and tap it. Returns the element tapped."""
    hits = find_text(text, exact=exact)
    if not hits:
        raise WDAError(
            f"Text not found on screen: {text!r}. Call ocr() to see what is visible."
        )
    el = hits[index]
    tap(el["x"], el["y"])
    return el


def type_text(text: str) -> None:
    """Type into the currently focused text field (tap the field first)."""
    client().type_text(text)


def press_home() -> None:
    client().home()


BUNDLE_IDS = {
    "settings": "com.apple.Preferences",
    "safari": "com.apple.mobilesafari",
    "messages": "com.apple.MobileSMS",
    "mail": "com.apple.mobilemail",
    "photos": "com.apple.mobileslideshow",
    "camera": "com.apple.camera",
    "notes": "com.apple.mobilenotes",
    "music": "com.apple.Music",
    "app store": "com.apple.AppStore",
    "maps": "com.apple.Maps",
    "calendar": "com.apple.mobilecal",
    "clock": "com.apple.mobiletimer",
    "phone": "com.apple.mobilephone",
    "facetime": "com.apple.facetime",
    "reminders": "com.apple.reminders",
    "files": "com.apple.DocumentsApp",
    "shortcuts": "com.apple.shortcuts",
    "health": "com.apple.Health",
    "wallet": "com.apple.Passbook",
    "calculator": "com.apple.calculator",
    "weather": "com.apple.weather",
}


def open_app(name: str) -> None:
    """Open an app by friendly name ('Settings'), bundle id, or installed-app name."""
    key = name.lower().strip()
    bundle = BUNDLE_IDS.get(key) or (name if "." in name else None)
    if not bundle:
        for app in device.list_apps():
            if key == app["name"].lower().strip():
                bundle = app["bundle_id"]
                break
    if not bundle:
        raise WDAError(
            f"Unknown app {name!r}. Use a bundle id, or check installed names with "
            "`ios apps --list`."
        )
    client().app_launch(bundle)


def wait_stable(timeout: float = 10.0, interval: float = 0.5) -> bool:
    """Wait until two consecutive screenshots are identical. True if stable."""
    prev = capture.screenshot_png()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        cur = capture.screenshot_png()
        if cur == prev:
            return True
        prev = cur
    return False


def unlock() -> None:
    """Wake and unlock the phone. Types PHONE_PASSCODE from .env if set (opt-in)."""
    c = client()
    c.unlock()
    time.sleep(0.5)
    if c.is_locked() and config.PHONE_PASSCODE:
        w, h = c.window_size()
        c.swipe(w / 2, h * 0.85, w / 2, h * 0.25, 0.2)  # swipe up to passcode pad
        time.sleep(1.0)
        c.type_text(config.PHONE_PASSCODE)
        time.sleep(0.5)
    if c.is_locked():
        raise WDAError(
            "Phone is still locked. Unlock it by hand or set PHONE_PASSCODE in .env."
        )


__all__ = [
    "client",
    "screenshot",
    "screen_info",
    "ocr",
    "ui_tree",
    "collect_texts",
    "tap",
    "long_press",
    "swipe",
    "scroll",
    "find_text",
    "tap_text",
    "type_text",
    "press_home",
    "open_app",
    "wait_stable",
    "unlock",
    "WDAError",
]
