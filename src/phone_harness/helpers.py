"""Agent-facing primitives, pre-imported by the phone-harness CLI.

Same surface as the original macOS phone-harness, plus tree-based extras.
All coordinates are in points (what the UI tree uses), origin top-left.
"""

from __future__ import annotations

import json
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


# /source serializes the whole tree (~3s/200KB on a busy screen), so back-to-back
# reads reuse one fetch. Any action invalidates; the short TTL bounds staleness
# when the screen changes on its own (animations, notifications).
_TREE_TTL = 2.0
_tree_cache: dict = {"tree": None, "ts": 0.0}


def _invalidate_tree() -> None:
    _tree_cache["tree"] = None


def ui_tree() -> dict:
    """Raw UI element tree (nested dicts). The precise view of the screen."""
    now = time.monotonic()
    if _tree_cache["tree"] is not None and now - _tree_cache["ts"] < _TREE_TTL:
        return _tree_cache["tree"]
    tree = client().source()
    _tree_cache.update(tree=tree, ts=time.monotonic())
    return tree


def ocr() -> list[dict]:
    """All visible on-screen text with center coordinates.

    Name kept from the original harness; here it reads the real UI element
    tree, so results are exact, not OCR guesses.
    """
    return collect_texts(ui_tree())


# ---- action ----------------------------------------------------------------


def tap(x: float, y: float) -> None:
    """Tap at (x, y) in points."""
    _invalidate_tree()
    client().tap(x, y)


def long_press(x: float, y: float, seconds: float = 1.0) -> None:
    _invalidate_tree()
    client().long_press(x, y, seconds)


def swipe(x1: float, y1: float, x2: float, y2: float, seconds: float = 0.3) -> None:
    _invalidate_tree()
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
    _invalidate_tree()
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
    _invalidate_tree()
    client().type_text(text)


def press_home() -> None:
    _invalidate_tree()
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
    _invalidate_tree()
    client().app_launch(bundle)


def _ambiguous_hits(hits: list[dict], contact: str) -> list[dict]:
    """Competing rows when a contact name cannot be resolved confidently.

    Pure function (unit-tested). Empty result = safe to tap hits[0]. Non-empty =
    several rows match and NONE is an exact label, so tapping would be a guess.
    An exact label match wins (tap_text/find_text sort exact first), so it is
    never ambiguous.
    """
    needle = contact.strip().lower()
    if any(h["text"].strip().lower() == needle for h in hits):
        return []
    return hits if len(hits) > 1 else []


def _thread_title(tree: dict) -> str | None:
    """Recipient name of the open one-to-one Messages thread, or None.

    Pure function (unit-tested). This iOS build puts the app word "Messages" in
    the NavigationBar (verified on device), so we read the thread header instead:
    the "Contact photo for <name>" button uniquely names a 1:1 thread's
    recipient. Group threads have no such button, so this returns None for them —
    the caller treats None as "cannot verify" and leans on the ambiguity check.
    """
    prefix = "Contact photo for "
    for el in collect_texts(tree):
        if el["type"] == "Button" and el["text"].startswith(prefix):
            return el["text"][len(prefix) :].strip()
    return None


def _log_action(
    contact: str, resolved_title: str | None, text: str, sent: bool
) -> None:
    """Append one JSONL line to .state/actions.log (gitignored). Never raises."""
    rec = {
        "ts": time.time(),
        "contact": contact,
        "resolved_title": resolved_title,
        "text": text,
        "sent": sent,
    }
    try:
        config.STATE_DIR.mkdir(exist_ok=True)
        with open(config.STATE_DIR / "actions.log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass  # a failed audit line must never block a send


def send_message(contact: str, text: str) -> dict:
    """Send a Message to a conversation: open Messages, open the thread, type, send.

    `contact` must match the conversation name in the Messages list (e.g. "Mom").
    Refuses to send if the contact name is ambiguous or the thread that opens does
    not match `contact`, and records every send to .state/actions.log.

    The compose field is labeled "Message", not "iMessage" — message bubbles carry
    "iMessage" in their labels, so never search for that.
    """
    open_app("messages")
    wait_stable(timeout=8)

    hits = find_text(contact)
    if not hits:
        raise WDAError(
            f"Contact {contact!r} not in the Messages list. It may be below the "
            "fold — scroll the list, or open the thread by hand first."
        )
    ambiguous = _ambiguous_hits(hits, contact)
    if ambiguous:
        names = ", ".join(repr(h["text"][:40]) for h in ambiguous[:5])
        raise WDAError(
            f"Contact {contact!r} is ambiguous — matches {names}. Use a more exact "
            "name or open the thread by hand."
        )

    tap_text(contact)
    wait_stable(timeout=8)

    title = _thread_title(ui_tree())
    if title is not None:
        t, c = title.strip().lower(), contact.strip().lower()
        if c not in t and t not in c:
            raise WDAError(
                f"Opened thread is {title!r}, expected {contact!r} — aborting "
                "before typing."
            )

    fields = [e for e in ocr() if e["type"] == "TextField"]
    if not fields:
        raise WDAError(
            "No compose field on screen. Is the conversation open? Call ocr() to check."
        )
    field = max(fields, key=lambda e: e["y"])  # compose bar sits at the bottom
    tap(field["x"], field["y"])
    time.sleep(0.8)
    type_text(text)
    time.sleep(0.5)
    sends = [
        e
        for e in ocr()
        if e["type"] == "Button" and e["text"].strip().lower() == "send"
    ]
    if not sends:
        raise WDAError("Send button not found — text may not have been typed.")
    _log_action(contact, title, text, sent=False)  # attempt, before the tap
    tap(sends[0]["x"], sends[0]["y"])
    time.sleep(1.5)
    _log_action(contact, title, text, sent=True)  # confirmed
    return {"contact": contact, "resolved_title": title, "text": text, "sent": True}


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


def _passcode_pad_visible(tree: dict) -> bool:
    """Is the lock-screen passcode pad on screen?

    Pure function (unit-tested). True when the digit pad (buttons 0-9) or a
    "... Passcode" prompt is visible. unlock() must never type the passcode
    without this — on any other screen the digits would land in whatever field
    happens to be focused (a search box, a message...).
    """
    texts = collect_texts(tree)
    digits = {e["text"] for e in texts if e["type"] == "Button" and e["text"].isdigit()}
    if len(digits) >= 9:
        return True
    return any("passcode" in e["text"].lower() for e in texts)


def _scrub_secret(message: str, secret: str | None) -> str:
    """Blank a secret out of an error message before it reaches logs/output."""
    return message.replace(secret, "•••") if secret else message


def unlock(c: WDAClient | None = None) -> None:
    """Make the phone usable: wake it and, if the passcode pad comes up, type
    PHONE_PASSCODE from .env (opt-in). Scrubs the passcode from any error.

    Decides from what is actually on screen — NEVER from /wda/locked, which
    can report unlocked while the pad is on screen (seen live 2026-08-09).
    Pass `c` to reuse an existing client: WDA holds ONE session, so a second
    client would steal it mid-sequence (the viewer passes its own).
    """
    c = c or client()
    if c.active_app().get("bundleId") != "com.apple.springboard":
        return  # an app is frontmost: unlocked and in use — touch nothing
    w, h = c.window_size()  # before the wake, so it can't eat awake-time

    def wake_and_swipe():
        # On a locked phone the bottom-edge swipe summons the passcode pad;
        # on a merely-asleep phone it lands on the home screen. Higher swipe
        # starts scroll the lock-screen notification list instead.
        c.press_button("home")  # wake the display
        time.sleep(0.5)
        c.swipe(w / 2, h * 0.98, w / 2, h * 0.30, 0.25)
        time.sleep(1.0)

    wake_and_swipe()
    if not _passcode_pad_visible(c.source()):
        return  # no pad appeared: the phone was just asleep, now awake+usable
    if not config.PHONE_PASSCODE:
        raise WDAError(
            "Phone is locked. Set PHONE_PASSCODE in .env or unlock it by hand."
        )
    # The tree fetch above can take seconds when the viewer is streaming, and
    # the lock screen re-sleeps fast — typed keys on a dark screen go nowhere.
    # A black frame compresses to almost nothing, so screenshot size is a
    # cheap screen-still-lit probe; wake and swipe again if it slept.
    if len(c.screenshot()) < 150_000:
        wake_and_swipe()
    try:
        c.type_text(config.PHONE_PASSCODE)
    except WDAError as exc:
        raise WDAError(_scrub_secret(str(exc), config.PHONE_PASSCODE)) from None
    time.sleep(0.7)
    if _passcode_pad_visible(c.source()):
        raise WDAError(
            "Typed the passcode but the pad is still on screen — wrong "
            "PHONE_PASSCODE, or the screen slept mid-type. Not retrying "
            "automatically (repeated wrong attempts lock the phone out)."
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
    "send_message",
    "wait_stable",
    "unlock",
    "WDAError",
]
