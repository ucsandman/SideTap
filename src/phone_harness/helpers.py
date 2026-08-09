"""Agent-facing primitives, pre-imported by the phone-harness CLI.

Same surface as the original macOS phone-harness, plus tree-based extras.
All coordinates are in points (what the UI tree uses), origin top-left.
"""

from __future__ import annotations

import json
import re
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


def current_app() -> dict:
    """Frontmost app info from WDA (bundleId, name, pid)."""
    return client().active_app()


def wait_for_app(bundle_id: str, timeout: float = 10.0, interval: float = 0.5) -> bool:
    """Poll until `bundle_id` is frontmost. True on success, False on timeout.

    Lets open_app() flows fail fast and loud instead of inferring foreground
    state from wait_stable() timing.
    """
    deadline = time.monotonic() + timeout
    while True:
        if current_app().get("bundleId") == bundle_id:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


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


def _leads_with(text: str, contact: str) -> bool:
    """Does a list-row label lead with the contact name? Pure (unit-tested).

    'Elissa' or 'Elissa, <preview>' leads; 'Mom & Elissa, …' and
    'Dad, Mom, …' do not. This is the bar for accepting a thread whose header
    cannot name it (groups): tapping a row where the name only appears
    mid-label is how a send lands in the wrong group chat.
    """
    t, c = text.strip().lower(), contact.strip().lower()
    return t == c or t.startswith(c + ",")


def _dedup_rows(hits: list[dict]) -> list[dict]:
    """Collapse duplicate labels, keeping order. Pure (unit-tested). Every
    Messages list row renders twice — nested elements repeat the label
    (verified on device 2026-08-09) — and a duplicate is not a competitor."""
    seen: set[str] = set()
    out = []
    for h in hits:
        key = h["text"].strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def _thread_title(tree: dict) -> str | None:
    """Recipient name of the open one-to-one Messages thread, or None.

    Pure function (unit-tested). This iOS build puts the app word "Messages" in
    the NavigationBar (verified on device), so we read the thread header instead:
    the "Contact photo for <name>" button names a 1:1 thread's recipient. The
    conversation LIST also shows contact photos (verified on device
    2026-08-09), but those hug the left edge of their rows — only a photo
    centered in the nav area (top of the screen, clear of the left edge) is
    the header. None = the list, or a thread the layout cannot name (groups) —
    the caller treats None as "cannot verify".
    """
    prefix = "Contact photo for "
    for el in collect_texts(tree):
        if (
            el["type"] == "Button"
            and el["text"].startswith(prefix)
            and el["rect"]["y"] < 140
            and el["rect"]["x"] > 130
        ):
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


def _title_matches(title: str, contact: str) -> bool:
    """Does an open thread's header title verify `contact`? Pure (unit-tested).

    Containment keeps 'Elissa' matching a header that shows a fuller name,
    but a multi-person title ('Mom & Elissa') must never verify a
    single-person contact — that is how a read or send lands in a group chat.
    """
    t, c = title.strip().lower(), contact.strip().lower()
    if t == c:
        return True
    if (" & " in t or "," in t) and not (" & " in c or "," in c):
        return False
    return c in t or t in c


def _nav_back_button(tree: dict) -> dict | None:
    """The nav-stack back control: the leftmost button hugging the top-left.

    Pure function (unit-tested). Its label varies ('Back', '33 unread'), so
    geometry is the signal, not text. WDA's synthetic edge-swipe does NOT
    trigger iOS's edge-pan back gesture (verified on device 2026-08-09), so
    tapping this button is the only reliable way back.
    """
    candidates = [
        el
        for el in collect_texts(tree)
        if el["type"] == "Button" and el["rect"]["y"] < 130 and el["rect"]["x"] < 130
    ]
    return min(candidates, key=lambda e: e["rect"]["x"]) if candidates else None


def _go_back() -> bool:
    """Tap the nav back button; wait until the thread header is gone.

    Polling matters: a second tap issued mid-animation lands on the list's
    profile button and opens its menu (seen on device 2026-08-09).
    """
    back = _nav_back_button(ui_tree())
    if back is None:
        return False
    tap(back["x"], back["y"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _thread_title(ui_tree()) is not None:
        time.sleep(0.5)
        _invalidate_tree()
    return True


def _open_thread(contact: str) -> str | None:
    """Open the Messages conversation for `contact`; return the thread title.

    Group labels make list rows unparseable ('Mom & Elissa, …' vs
    'Elissa, <preview>'), so candidates are verified by OPENING them: the
    thread header names 1:1 threads exactly; a wrong thread is backed out of
    and the next candidate tried (near misses get marked read on the phone —
    the price of verifying by opening). Returns None when an unnameable
    (group) thread was opened via a row that leads with `contact`. Handles
    Messages resuming mid-thread and rows scrolled off screen (bounded).
    """
    open_app("messages")
    wait_stable(timeout=8)

    # Messages resumes wherever it last was. Already in the right thread: done.
    # In some other 1:1 thread: back out toward the list (bounded).
    for _ in range(3):
        title = _thread_title(ui_tree())
        if title is None:
            break  # no 1:1 thread header: the list (or a group thread)
        if _title_matches(title, contact):
            return title
        if not _go_back():
            break  # cannot navigate out; the loud failures below take over

    if not find_text(contact):  # the row may be above or below the fold
        for direction in ("up", "up", "down", "down", "down"):
            scroll(direction, 0.6)
            if find_text(contact):
                break

    tried: set[str] = set()
    for _ in range(3):
        rows = [
            r
            for r in _dedup_rows(find_text(contact))
            if r["text"].strip().lower() not in tried
        ]
        if not rows:
            raise WDAError(
                f"Contact {contact!r} not found in the Messages list (or every "
                "match opened the wrong thread). Scroll to the conversation or "
                "open it by hand, then retry."
            )
        # find_text orders exact-first; prefer rows that lead with the name.
        rows.sort(key=lambda r: not _leads_with(r["text"], contact))
        row = rows[0]
        sole = len(rows) == 1
        tried.add(row["text"].strip().lower())
        tap(row["x"], row["y"])
        wait_stable(timeout=8)
        title = _thread_title(ui_tree())
        if title is not None and _title_matches(title, contact):
            return title
        if title is None and sole and _leads_with(row["text"], contact):
            return None  # a group thread opened via its own leading row
        if not _go_back():
            raise WDAError(
                f"Opened thread is {title!r}, expected {contact!r} — aborting."
            )
    raise WDAError(
        f"Contact {contact!r} is ambiguous in the Messages list — no candidate "
        "row opened a matching thread. Open the conversation by hand."
    )


# Bubble labels end with a time and sometimes a tapback note; both are chrome.
_BUBBLE_TIME = re.compile(r",\s*\d{1,2}:\d{2}(\s*[AP]M)?\s*$", re.IGNORECASE)
_BUBBLE_TAPBACK = re.compile(
    r",\s*\S+\s+(liked|loved|laughed at|emphasized|disliked|questioned)\s+this\s*$",
    re.IGNORECASE,
)


def _message_bubbles(tree: dict, width: float) -> list[dict]:
    """Message bubbles of the open thread, ordered top-to-bottom.

    Pure function (unit-tested). Verified on device (iOS 18, 2026-08-09): a
    bubble is a full-width Cell labeled '<sender>, <text>, <time>' — sender is
    'Your iMessage' when sent (why send_message never matches on 'iMessage').
    The Cell's inner Other repeats the label with the bubble's real geometry:
    which half of the screen it hugs gives from_me; the 'Your' prefix is the
    fallback when no inner element is found.
    """
    els = collect_texts(tree)
    bubbles = []
    for i, el in enumerate(els):
        if el["type"] != "Cell" or ", " not in el["text"]:
            continue
        sender, text = el["text"].split(", ", 1)
        text = _BUBBLE_TIME.sub("", text.replace("\u202f", " ")).strip()
        text = _BUBBLE_TAPBACK.sub("", text).strip()
        inner = next(
            (
                e
                for e in els[i + 1 : i + 4]
                if e["type"] == "Other" and e["text"] == el["text"]
            ),
            None,
        )
        from_me = (
            inner["x"] > width / 2 if inner is not None else sender.startswith("Your")
        )
        bubbles.append({"text": text, "from_me": from_me, "y": el["rect"]["y"]})
    bubbles.sort(key=lambda b: b["y"])
    return [{"text": b["text"], "from_me": b["from_me"]} for b in bubbles]


def read_messages(contact: str, limit: int = 20) -> list[dict]:
    """Read the last messages of a conversation, oldest first.

    Returns [{'text', 'from_me'}, ...]. Closes the loop send_message opened:
    the agent can now see the reply, not just write.
    """
    _open_thread(contact)
    w, _h = client().window_size()
    return _message_bubbles(ui_tree(), w)[-limit:]


def send_message(contact: str, text: str) -> dict:
    """Send a Message to a conversation: open Messages, open the thread, type, send.

    `contact` must match the conversation name in the Messages list (e.g. "Mom").
    Refuses to send if the contact name is ambiguous or the thread that opens does
    not match `contact`, and records every send to .state/actions.log.

    The compose field is labeled "Message", not "iMessage" — message bubbles carry
    "iMessage" in their labels, so never search for that.
    """
    title = _open_thread(contact)

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


def wait_for_text(
    text: str, timeout: float = 10.0, interval: float = 0.5, exact: bool = False
) -> dict | None:
    """Poll until `text` appears on screen; returns the element or None.

    The complement of wait_stable(): that says the screen stopped moving, this
    says the thing you were waiting for actually showed up. The returned
    element carries x/y, so the caller can tap it without re-searching.
    """
    deadline = time.monotonic() + timeout
    while True:
        hits = find_text(text, exact=exact)
        if hits:
            return hits[0]
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)
        _invalidate_tree()  # never poll the cached tree — read a fresh one


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
    _invalidate_tree()  # about to change the screen, like any other action

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
    "current_app",
    "wait_for_app",
    "send_message",
    "read_messages",
    "wait_stable",
    "wait_for_text",
    "unlock",
    "WDAError",
]
