"""Agent-facing primitives, pre-imported by the phone-harness CLI.

Same surface as the original macOS phone-harness, plus tree-based extras.
All coordinates are in points (what the UI tree uses), origin top-left.
"""

from __future__ import annotations

import difflib
import json
import re
import time
from pathlib import Path

from . import approval, capture, config, device, trust
from .wda_client import WDAClient, WDAError, activity_file

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
    # Pixels cannot be scanned for injected text, but a vision model reads
    # them, so a screenshot taints exactly like a text read.
    trust.mark("screenshot", [])
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
_tree_cache: dict = {"tree": None, "ts": 0.0, "flags": [], "act": 0.0}


def _invalidate_tree() -> None:
    _tree_cache["tree"] = None


def _foreign_activity() -> float:
    """mtime of the shared action log, which EVERY process appends to.

    _invalidate_tree() only fires inside the process that acted, but the viewer
    and the MCP server are separate processes. A human tap in the viewer used to
    leave the agent serving a cached tree for up to the TTL, then tapping the
    coordinates of a screen that had already changed. Every action POST already
    records itself here (wda_client._request), so the mtime is a cross-process
    "something moved" signal for free. Same shared-state-file pattern as the
    WDA session id.
    """
    try:
        return activity_file().stat().st_mtime
    except OSError:
        return 0.0


def ui_tree() -> dict:
    """Raw UI element tree (nested dicts). The precise view of the screen.

    Every text read in this module reaches the screen through here, so this is
    the one place that has to mark the session tainted: whatever is on the
    phone is attacker-controlled, and a send after this point needs a human.
    """
    now = time.monotonic()
    act = _foreign_activity()
    if (
        _tree_cache["tree"] is not None
        and now - _tree_cache["ts"] < _TREE_TTL
        and act == _tree_cache["act"]
    ):
        trust.mark("screen", _tree_cache["flags"])
        return _tree_cache["tree"]
    tree = client().source()
    # Scan the visible text only: the raw tree is mostly geometry, and the
    # flags shown on the approval card should come from what a human would
    # have seen (or, for hidden characters, would not have seen).
    flags = trust.scan_items([e["text"] for e in collect_texts(tree)])
    _tree_cache.update(tree=tree, ts=time.monotonic(), flags=flags, act=act)
    trust.mark("screen", flags)
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
    """Press and hold at (x, y) in points for `seconds`, then release.

    Opens context menus, the app icon jiggle/rearrange mode, and text selection.
    """
    _invalidate_tree()
    client().long_press(x, y, seconds)


def swipe(x1: float, y1: float, x2: float, y2: float, seconds: float = 0.3) -> None:
    """Drag from (x1, y1) to (x2, y2) in points over `seconds`.

    A raw physical drag: the finger travels exactly the path given, with no
    direction abstraction. To scroll a list, prefer scroll(), which takes a
    named direction and inverts it for you. Use this for edge gestures (swipe
    down from the top edge for Notification Center) and for drag-and-drop.
    """
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
    """All elements whose text matches (case-insensitive), best match first.

    Substring match by default; `exact=True` requires the whole text to match.
    Results are ordered exact matches first, then shortest text, so index 0 is
    the least noisy match. That order is what `tap_text(index=N)` selects from.
    """
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
    """Find text on screen and tap it. Returns the element tapped.

    `index` picks from find_text()'s order: exact matches first, then shortest
    text. Use it to disambiguate two controls with the same label. `exact=True`
    narrows the match instead of matching any substring.
    """
    hits = find_text(text, exact=exact)
    if not hits:
        raise WDAError(
            f"Text not found on screen: {text!r}. Call ocr() to see what is visible."
        )
    if index >= len(hits) or index < -len(hits):
        # Name what we already have: a bare IndexError cost the agent another
        # find_text()/ocr() round trip just to learn the count.
        labels = ", ".join(repr(h["text"]) for h in hits[:6])
        raise WDAError(
            f"index {index} is out of range for {text!r}: {len(hits)} match"
            f"{'' if len(hits) == 1 else 'es'} on screen ({labels})."
        )
    el = hits[index]
    tap(el["x"], el["y"])
    return el


# Fractions of screen height where a nav bar or bottom toolbar can sit on top
# of a list row. A hit inside them is real but not reliably tappable — tapping
# one lands on the chrome instead of the row — so the scroll helpers keep going
# until a hit lands between them. Measured on a 956pt screen: the nav bar ends
# ~162pt and the search/tab bar starts ~822pt.
_REACH_TOP, _REACH_BOTTOM = 0.17, 0.86


def _in_reach(el: dict, height: float) -> bool:
    return _REACH_TOP * height < el["y"] < _REACH_BOTTOM * height


def scroll_until_found(
    text: str,
    max_scrolls: int = 8,
    direction: str = "down",
    amount: float = 0.35,
    exact: bool = False,
) -> dict:
    """Scroll until `text` sits in the tappable middle of the screen, return it.

    One call instead of scroll-a-guessed-amount-then-look-then-correct. A hit
    hiding under the nav bar does not count as found, because tapping it hits
    the bar instead of the row — that exact mis-tap is what this prevents.
    """
    _, h = client().window_size()
    for _ in range(max_scrolls + 1):
        for el in find_text(text, exact=exact):
            if _in_reach(el, h):
                return el
        scroll(direction, amount)
        wait_stable(timeout=3)
    raise WDAError(
        f"{text!r} never reached the tappable middle of the screen after "
        f"{max_scrolls} {direction} scrolls. Call ocr() to see what is visible."
    )


def find_on_home_screen(text: str, max_pages: int = 15) -> dict:
    """Find a Home Screen icon by name across pages, return the element.

    find_text only ever sees the current page, so an icon parked deep in the
    Home Screen reads as missing. "Add to Home Screen" drops a new icon in the
    first free slot, which is usually the last page, so this is the normal way
    to find one. Icons inside folders are not visible to this.
    """
    w, h = client().window_size()
    press_home()
    wait_stable(timeout=3)
    press_home()  # from any page, a second press lands on page 1
    wait_stable(timeout=3)
    for _ in range(max_pages):
        for el in find_text(text):
            if el.get("type") == "Icon":
                return el
        swipe(w * 0.9, h / 2, w * 0.1, h / 2, 0.3)
        wait_stable(timeout=3)
    raise WDAError(
        f"No Home Screen icon named {text!r} in the first {max_pages} pages. "
        "It may be inside a folder, where this cannot see it."
    )


def type_text(text: str) -> None:
    """Type into the currently focused text field (tap the field first).

    Refuses to type PHONE_PASSCODE. Nothing the agent legitimately types
    contains it, and an injected instruction must not be able to spend it into
    a note, a search box or a message. unlock() types it straight through the
    client, so unlocking is unaffected.
    """
    if config.PHONE_PASSCODE and config.PHONE_PASSCODE in text:
        raise WDAError(
            "Refused: this text contains your phone passcode. Only unlock() "
            "may type it. If this was not you, an instruction on the phone "
            "screen may have tried to steal it."
        )
    _invalidate_tree()
    client().type_text(text)


# WebDriver key code for delete-backwards. WDA takes it in the /wda/keys list
# exactly like a printable character.
_BACKSPACE = chr(0xE003)


def _focused_value() -> str | None:
    """What the focused field really holds, or None if it cannot be read.

    NOT collect_texts(): that prefers `label`, which for a text field is the
    PLACEHOLDER ("Message" on the Messages compose bar), so it reads the same
    whether the field is empty or holds a draft. The typed content is `value`.
    """
    try:
        return client().element_value(client().active_element())
    except WDAError:
        return None


def _clear_focused_field() -> None:
    """Empty the focused field, in order of reliability."""
    clear = [
        e
        for e in ocr()
        if e["type"] == "Button" and e["text"].strip() in ("Clear text", "Clear")
    ]
    if clear:  # search fields carry an explicit button
        tap(clear[0]["x"], clear[0]["y"])
        return
    try:  # WebDriver's own clear: one call, any content length
        client().element_clear(client().active_element())
        return
    except WDAError:
        pass
    current = _focused_value() or ""  # last resort: backspace over what is there
    if current:
        type_text(_BACKSPACE * (len(current) + 2))


def set_field_text(field: dict, text: str, verify: bool = True) -> str:
    """Replace `field`'s contents with `text`. Returns what actually landed.

    type_text() is POST /wda/keys, which APPENDS at the cursor: it does not
    replace what is already there. iOS keeps an unsent draft per Messages
    thread and resumes a search field mid-query, so typing into a field that
    already holds something puts draft+text on the phone while the caller still
    believes it typed `text`. Clear first, then read the field back, so a caller
    can never report a message it did not send.

    Pass the field element you are about to type into (the one you would tap).
    `verify=False` skips the read-back round trip.
    """
    tap(field["x"], field["y"])
    time.sleep(0.4)  # keyboard slide-up, or the first keys are dropped
    _clear_focused_field()
    type_text(text)
    if not verify:
        return text
    landed = _focused_value()
    return text if landed is None else landed


def press_home() -> None:
    """Go to the Home Screen, as if the physical Home gesture were used.

    Leaves whatever app was open. Returns to the first Home Screen page, so it
    is the reliable way back to a known state after a wrong tap.
    """
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
    installed: list[str] = []
    if not bundle:
        for app in device.list_apps():
            installed.append(app["name"])
            if key == app["name"].lower().strip():
                bundle = app["bundle_id"]
                break
    if not bundle:
        # We just walked every installed name; hand back the near misses instead
        # of making the agent spend another call to list them. BUNDLE_IDS is in
        # the pool too: `ios apps --list` omits system apps, so without it a typo
        # for Messages or Settings would get no suggestion at all.
        pool = list(dict.fromkeys(installed + list(BUNDLE_IDS)))
        near = difflib.get_close_matches(name, pool, n=3, cutoff=0.6)
        if not near:
            near = difflib.get_close_matches(key, pool, n=3, cutoff=0.6)
        if not near:
            near = [n for n in pool if key in n.lower()][:3]
        hint = (
            f" Did you mean: {', '.join(repr(n) for n in near)}?"
            if near
            else " Check installed names with `ios apps --list`."
        )
        raise WDAError(f"Unknown app {name!r}.{hint}")
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
    profile button and opens its menu (seen on device 2026-08-09). Same trap
    when no thread is open at all: the list's profile button passes the
    geometry test too, so back out only when a compose bar proves a thread is
    actually on screen (bit live 2026-08-10 — a missed row tap ended with the
    profile menu open).
    """
    tree = ui_tree()
    if not any(e["type"] == "TextField" for e in collect_texts(tree)):
        return False  # no compose bar: not in a thread — nothing to back out of
    back = _nav_back_button(tree)
    if back is None:
        return False
    tap(back["x"], back["y"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _thread_title(ui_tree()) is not None:
        time.sleep(0.5)
        _invalidate_tree()
    return True


def _conversation_cells(tree: dict, contact: str) -> list[dict]:
    """Conversation candidates in Messages search results. Pure (unit-tested).

    Search results name conversations with bare Cells ('Wes Sander' — none of
    the preview/date chrome list rows carry; verified on device 2026-08-10).
    The 'Messages with: <name>' filter row is chrome, and message-content hits
    are not Cells, so a Cell that clears _title_matches/_leads_with is a
    conversation. Ordered exact-name first, like find_text.
    """
    needle = contact.strip().lower()
    out = []
    for el in collect_texts(tree):
        label = el["text"].strip()
        if el["type"] != "Cell" or label.lower().startswith("messages with:"):
            continue
        if _title_matches(label, contact) or _leads_with(label, contact):
            out.append(el)
    out.sort(key=lambda e: e["text"].strip().lower() != needle)
    return out


def _open_thread(contact: str) -> str | None:
    """Open the Messages conversation for `contact`; return the thread title.

    Finds the thread by TYPING the name into the list's search field — the
    old scroll-the-list hunt tapped rows mid-settle and missed (bit live
    2026-08-10). Candidates are still verified by OPENING them: the thread
    header names 1:1 threads exactly; a wrong thread is backed out of (back
    lands on the still-filtered results) and the next candidate tried.
    Returns None when an unnameable (group) thread was opened via a cell that
    leads with `contact`. Handles Messages resuming mid-thread or mid-search.
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

    fields = [e for e in ocr() if e["type"] == "SearchField"]
    if not fields:
        raise WDAError(
            "Messages search field not found — is the conversation list on "
            "screen? Call ocr() to check."
        )
    tap(fields[0]["x"], fields[0]["y"])
    time.sleep(0.8)  # keyboard slide-up
    # Messages can resume mid-search with a stale query; clear it before typing.
    stale = [e for e in ocr() if e["type"] == "Button" and e["text"] == "Clear text"]
    if stale:
        tap(stale[0]["x"], stale[0]["y"])
    type_text(contact)

    # Poll for result cells: the caret blink defeats wait_stable here. Be
    # patient — /source takes ~3s on a busy screen, so a short window only
    # fits 2 reads and a just-woken phone missed it (bit live 2026-08-10).
    deadline = time.monotonic() + 20
    while not _conversation_cells(ui_tree(), contact):
        if time.monotonic() >= deadline:
            raise WDAError(
                f"No conversation named {contact!r} in Messages search. "
                "Check the name, or open the conversation by hand."
            )
        time.sleep(0.5)
        _invalidate_tree()

    tried: set[str] = set()
    for _ in range(3):
        cells = [
            c
            for c in _dedup_rows(_conversation_cells(ui_tree(), contact))
            if c["text"].strip().lower() not in tried
        ]
        if not cells:
            raise WDAError(
                f"Every search match for {contact!r} opened the wrong thread. "
                "Open the conversation by hand, then retry."
            )
        cell = cells[0]
        sole = len(cells) == 1
        tried.add(cell["text"].strip().lower())
        tap(cell["x"], cell["y"])
        wait_stable(timeout=8)
        title = _thread_title(ui_tree())
        if title is not None and _title_matches(title, contact):
            return title
        if title is None and sole and _leads_with(cell["text"], contact):
            return None  # a group thread opened via its own leading cell
        if not _go_back():
            raise WDAError(
                f"Opened thread is {title!r}, expected {contact!r} — aborting."
            )
    raise WDAError(
        f"Contact {contact!r} is ambiguous in Messages search — no candidate "
        "opened a matching thread. Open the conversation by hand."
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
    with trust.internal():  # navigating to the thread is bookkeeping, not content
        _open_thread(contact)
        w, _h = client().window_size()
        bubbles = _message_bubbles(ui_tree(), w)[-limit:]
    # Incoming messages are the most direct injection route into this agent,
    # so name this source specifically rather than leaving it as "screen".
    trust.mark("read_messages", trust.scan_items([b["text"] for b in bubbles]))
    return bubbles


_GATE_REFUSALS = {
    "deny": "Send denied in the viewer. The user rejected this message.",
    "timeout": (
        "Nobody approved this send in the viewer in time, so it was refused. "
        "Ask the user to click Approve and try again."
    ),
    "busy": (
        "Another send is already waiting for approval in the viewer. "
        "Answer that card first."
    ),
}


def send_message(contact: str, text: str) -> dict:
    """Send a Message to a conversation: open Messages, open the thread, type, send.

    `contact` must match the conversation name in the Messages list (e.g. "Mom").
    Refuses to send if the contact name is ambiguous or the thread that opens does
    not match `contact`, and records every send to .state/actions.log.

    Prompt-injection gate: once anything has been read off the phone in this
    process, the send waits for the user to click Approve in the viewer. A send
    the user typed into the viewer themselves is not gated. There is deliberately
    no argument to skip this — every parameter of an MCP tool is reachable by an
    injected instruction, so a bypass argument would hand over the key.

    The compose field is labeled "Message", not "iMessage" — message bubbles carry
    "iMessage" in their labels, so never search for that.
    """
    taint = trust.tainted()
    if taint and not trust.is_human_initiated():
        flags = list(taint["flags"])
        for flag in trust.scan(text):
            if flag not in flags:
                flags.append(flag)
        gate = approval.mode()  # read now: the human can flip it mid-session
        if gate == "off" or (gate == "flagged" and not flags):
            verdict = "approve"
        else:
            verdict = approval.request(contact, text, flags, taint["source"])
        if verdict != "approve":
            _log_action(contact, None, text, sent=False)
            raise WDAError(_GATE_REFUSALS.get(verdict, _GATE_REFUSALS["deny"]))

    with trust.internal():  # the send's own reads are not agent-facing content
        title = _open_thread(contact)

        fields = [e for e in ocr() if e["type"] == "TextField"]
        if not fields:
            raise WDAError(
                "No compose field on screen. Is the conversation open? "
                "Call ocr() to check."
            )
        field = max(fields, key=lambda e: e["y"])  # compose bar sits at the bottom
        # Clear first: /wda/keys APPENDS, and iOS keeps an unsent draft per
        # thread. Without this the phone sends draft+text while the human has
        # already approved a card showing only `text`.
        landed = set_field_text(field, text)
        if landed.strip() != text.strip():
            # The human approved `text`. Anything else in the bar is unapproved
            # content, so it must not reach the wire.
            _log_action(contact, title, text, sent=False)
            raise WDAError(
                "Refused: the compose field holds text that was not approved. "
                f"Asked to send {text!r} but the field reads {landed!r}. "
                "Clear the conversation's draft on the phone and try again."
            )
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
        _log_action(contact, title, landed, sent=True)  # confirmed
    return {"contact": contact, "resolved_title": title, "text": landed, "sent": True}


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
    """Wait until two consecutive screenshots are identical. True if stable.

    The first comparison happens IMMEDIATELY. Callers reach here straight after
    a gesture that WDA already settled server-side (~0.7s per swipe, measured —
    see config.WDA_ANIM_COOLOFF), so the screen is usually still by the time we
    are asked. Sleeping before the first compare made that common case cost a
    guaranteed extra `interval`, up to 9 times per scroll_until_found and 17
    times per find_on_home_screen. Two screenshots are a WDA round trip apart
    (~50-100ms), which is far enough to differ mid-animation.
    """
    prev = capture.screenshot_png()
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = capture.screenshot_png()
        if cur == prev:
            return True
        prev = cur
        time.sleep(interval)
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


_UNLOCK_TIMEOUT = 45.0  # first gesture after a deep sleep: 20.5s measured live


def unlock(c: WDAClient | None = None) -> None:
    """Make the phone usable: wake it and, if the passcode pad comes up, type
    PHONE_PASSCODE from .env (opt-in). Scrubs the passcode from any error.

    Decides from what is actually on screen — NEVER from /wda/locked, which
    can report unlocked while the pad is on screen (seen live 2026-08-09).
    Pass `c` to reuse an existing client; with the shared session model a
    patient clone adopts the same session instead of stealing it.
    """
    c = c or client()
    # The first gesture after the phone has slept a while can block WDA for
    # 10-20s (measured 20.5s live 2026-08-09). A short-timeout client (the
    # viewer's is 10s) aborts a swipe that is still going to land, so the
    # sequence runs on a patient clone sharing the same session.
    if isinstance(c, WDAClient) and c.timeout < _UNLOCK_TIMEOUT:
        patient = WDAClient(base_url=c.base_url, timeout=_UNLOCK_TIMEOUT)
        patient.session_id = c.session_id
        c = patient
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

    def pad_appears(seconds: float) -> bool:
        # Poll, don't peek once: the pad animates in, and a slow swipe can
        # land well after the call returns. Attempt-counted (not wall-clock)
        # so tests with a no-op sleep stay instant.
        attempts = max(1, int(seconds / 0.4))
        for i in range(attempts):
            if _passcode_pad_visible(c.source()):
                return True
            if i < attempts - 1:
                time.sleep(0.4)
        return False

    wake_and_swipe()
    if not pad_appears(3.0):
        if len(c.screenshot()) >= 150_000:
            return  # lit and no pad: was just asleep, now awake+usable
        # Dark again: a slow swipe landed after the lock screen re-slept and
        # burned on a black screen. One more charge, then give up — endless
        # gesturing at an already-unlocked phone helps nobody.
        wake_and_swipe()
        if not pad_appears(3.0):
            return
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


# ---- screen compaction -------------------------------------------------
# Lives here, not at the MCP boundary, so a CLI-piped script gets the same
# ~64% smaller read the MCP tools get. ocr()/find_text() still return the
# raw list: viewer.py, send_message and the tests need the full tree.
# Wrappers around the whole screen; never a target, never state. `Other` is
# deliberately NOT here: the Home Screen search affordance is an `Other`, so
# dropping the type loses a real tap target. Redundant `Other` containers are
# handled by enclosure and duplicate collapsing below instead.
_NOISE_TYPES = frozenset({"Application", "Window"})
# Types worth keeping when the same text lands twice in the same place.
_ACTIONABLE = frozenset(
    {"Button", "Cell", "Switch", "SearchField", "TextField", "Icon"}
)
# Types that are only ever labels. Anything else may be independently tappable
# — a Switch inside its row is the case that makes a blanket rule unsafe — so
# only these are eligible to be dropped as duplicates of an enclosing element.
_LABEL_TYPES = frozenset({"StaticText", "Image"})


def _encloses(outer: dict, inner: dict) -> bool:
    """True when outer's rect covers inner's and outer is the larger of the two."""
    o, i = outer.get("rect"), inner.get("rect")
    if not o or not i:
        return False
    return (
        o["x"] <= i["x"]
        and o["y"] <= i["y"]
        and o["x"] + o["width"] >= i["x"] + i["width"]
        and o["y"] + o["height"] >= i["y"] + i["height"]
        and o["width"] * o["height"] > i["width"] * i["height"]
    )


def _overlaps(a: dict, b: dict) -> bool:
    """True when the two rects intersect. Rows without geometry never match."""
    ra, rb = a.get("rect"), b.get("rect")
    if not ra or not rb:
        return False
    return not (
        ra["x"] + ra["width"] <= rb["x"]
        or rb["x"] + rb["width"] <= ra["x"]
        or ra["y"] + ra["height"] <= rb["y"]
        or rb["y"] + rb["height"] <= ra["y"]
    )


def _rank(el: dict) -> tuple[bool, float]:
    """Tap-worthiness: an actionable type first, then the larger target."""
    r = el.get("rect") or {}
    return (el.get("type") in _ACTIONABLE, r.get("width", 0) * r.get("height", 0))


def compact(rows: list[dict], limit: int | None = 60) -> list[dict]:
    """Strip a screen read down to what the model can act on.

    Two thirds of a raw read is noise: containers, and a label repeating the
    text of the control that encloses it. Dropping the label is also the safer
    target — tapping the inner StaticText instead of its Button is the classic
    mis-tap. rect goes too; x/y is what a tap needs.
    """
    keep = [r for r in rows if r.get("type") not in _NOISE_TYPES]
    survivors = [
        r
        for r in keep
        if not (
            r.get("type") in _LABEL_TYPES
            and any(
                o is not r and r["text"] in o["text"] and _encloses(o, r) for o in keep
            )
        )
    ]
    # Same text, same place, twice over (a row and its identical twin, repeated
    # scroll-bar chrome): keep whichever is worth tapping, in original order.
    chosen: list[dict] = []
    for r in survivors:
        twin = next(
            (c for c in chosen if c["text"] == r["text"] and _overlaps(c, r)), None
        )
        if twin is None:
            chosen.append(r)
        elif _rank(r) > _rank(twin):
            chosen[chosen.index(twin)] = r
    order = {id(r): i for i, r in enumerate(survivors)}
    chosen.sort(key=lambda r: order[id(r)])
    out = [{k: v for k, v in r.items() if k != "rect"} for r in chosen]
    if limit is not None and len(out) > limit:
        dropped = len(out) - limit
        out = out[:limit]
        # Say so out loud: a silently truncated screen reads as a complete one.
        out.append(
            {
                "text": f"[{dropped} more rows not shown; narrow with find_text()]",
                "type": "Truncation",
                "x": 0,
                "y": 0,
            }
        )
    return out


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
    "scroll_until_found",
    "find_on_home_screen",
    "type_text",
    "set_field_text",
    "compact",
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
