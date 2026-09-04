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
from .wda_client import WDAClient, WDAError, activity_file, redact_actions

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
    w, h = _window_size()
    return {"width": w, "height": h, "units": "points"}


# window_size() is a measured 201ms round trip (WDA resolves the ACTIVE
# APPLICATION's frame to answer it), and half a dozen call sites paid it on
# every single scroll/page/read. Memoised here, NOT inside WDAClient: about a
# dozen tests in test_wda_client.py use window_size() as their one-request
# session-heal probe, and caching it at the client would silence exactly the
# round trip those tests assert on.
#
# The guard is session id AND orientation, and the orientation half is not
# optional. It is NOT a screen constant: width and height swap when the device
# rotates, and before this memo existed every call site handled that correctly
# and for free by always asking. Session id alone cannot see a rotation, and
# the memo lives as long as the process — which for the MCP server is the whole
# session. A stale value here is not a slow tap, it is a tap at coordinates off
# the side of the screen: unlock() swipes from x=w/2, so a cached landscape
# 844x390 makes it swipe at x=422 on a 390-point-wide portrait lock screen, the
# bottom-edge swipe never lands, the pad never appears and unlock() raises.
# "The Unlock button did nothing" is a symptom this project has already
# debugged three times (docs/ERRORS.md 2026-08-12, 2026-08-13 x2), and
# find_on_home_screen's page swipe and the screen_info() MCP tool read the same
# value. orientation() costs 7.7ms against 201ms, so the guard keeps ~193ms of
# the saving and gives correctness back.
_size_cache: dict = {"wh": None, "session_id": None, "orientation": None}


def _window_size(c: WDAClient | None = None) -> tuple[float, float]:
    c = c or client()
    # 7.7ms to prove the phone has not rotated under a 201ms memo. Read the
    # session id AFTER it, never before: orientation() is a session request, so
    # it heals an evicted session, and a sid sampled first would still be the
    # dead one — which is exactly what the stale entry is keyed on, so the
    # guard would match itself and serve the stale size.
    orient = c.orientation()
    sid = getattr(c, "session_id", None)
    if (
        _size_cache["wh"] is not None
        and _size_cache["session_id"] == sid
        and _size_cache["orientation"] == orient
    ):
        return _size_cache["wh"]
    wh = c.window_size()
    _size_cache["wh"] = wh
    _size_cache["session_id"] = sid
    _size_cache["orientation"] = orient
    return wh


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


def _cached_screen() -> list[dict] | None:  # noqa: vulture  (called by mcp_server.py)
    """The visible rows IF the tree cache is still warm, else None. Never reads.

    For the error paths: a lookup that failed read the tree on its way to
    failing, so the screen the agent is about to ask for ("Call ocr() to see
    what is visible") is already sitting here and costs nothing. A COLD cache
    returns None rather than paying for the read — every action calls
    _invalidate_tree() BEFORE it acts, so a tap blocked by .state/STOP or a
    wedged link would bill a Home Screen /source (3.0-5.7s, or no answer at
    all) purely to decorate an error. Free or nothing.

    Marks the session tainted exactly as ui_tree()'s cache hit does: these rows
    reach the model, so a send after them still needs a human. That includes
    the cache send_message filled under trust.internal() — the thread content
    really did reach the model this time.
    """
    if _tree_cache["tree"] is None or time.monotonic() - _tree_cache["ts"] >= _TREE_TTL:
        return None
    if _foreign_activity() != _tree_cache["act"]:
        return None
    trust.mark("screen", _tree_cache["flags"])
    return collect_texts(_tree_cache["tree"])


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
    w, h = _window_size()
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
    _, h = _window_size()
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
    w, h = _window_size()
    # "Is the icon on THIS page" is a yes/no, and find_text() answered it with
    # ocr() -> ui_tree() -> /source. The swipe invalidates the cache every
    # turn, so every page paid the Home Screen's worst case (3.0-5.7s, 554-610
    # nodes, 244 KB measured) against 0.37s for a bounded lookup. Probe first,
    # dump the tree only on a hit. `name` is in the predicate because
    # find_text matches label OR name OR value, and a label-only probe would
    # silently skip a page it would have found. One bounded query returning
    # one id, so the unbounded `**/*` incident does not apply here.
    chain = "**/XCUIElementTypeIcon"
    if _predicate_safe(text):
        chain += f'[`label CONTAINS[c] "{text}" OR name CONTAINS[c] "{text}"`]'
    # Start at page 1 or the scan silently begins wherever you happen to be and
    # misses everything behind you. This used to be two press_home() calls on
    # the belief that a second press pages back — it does not, /wda/homescreen
    # is a no-op once the springboard is up, and press_home() says so itself.
    # Caught on device 2026-08-12: from page 2 the scan started on page 2.
    goto_home_page(1)
    for _ in range(max_pages):
        if client().find_first(chain):
            for el in find_text(text):
                if el.get("type") == "Icon":
                    return el
        # No wait_stable() here (unlike scroll_until_found): WDA_IDLE_WAIT
        # already absorbs the swipe settle inside the /actions call itself
        # (same evidence that retired goto_home_page()'s old 0.55s
        # _PAGE_SETTLE), and the next find_text() re-reads to confirm what
        # landed. 299ms/page measured on a still screen before this removal.
        swipe(w * 0.9, h / 2, w * 0.1, h / 2, 0.3)
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


def set_clipboard(text: str) -> None:
    """Set the iPhone system clipboard content.

    Refuses text containing PHONE_PASSCODE to prevent accidental leakage.
    """
    if config.PHONE_PASSCODE and config.PHONE_PASSCODE in text:
        raise WDAError(
            "Refused: this text contains your phone passcode. "
            "If this was not you, an instruction on the phone screen may have tried to steal it."
        )
    client().set_clipboard(text)


def get_clipboard() -> str:
    """Read text from the iPhone system clipboard.

    Marks the retrieved content as untrusted input from the phone.
    """
    text = client().get_clipboard()
    trust.mark("clipboard", trust.scan(text))
    return text


# WebDriver key code for delete-backwards. WDA takes it in the /wda/keys list
# exactly like a printable character.
_BACKSPACE = chr(0xE003)


def _predicate_safe(text: str) -> bool:
    """Is `text` safe to interpolate into a class-chain predicate?

    An iOS class chain delimits its predicate with BACKTICKS and its string
    literals with double quotes, so a backtick, a double quote or a backslash
    in the interpolated text closes the predicate early, WDA rejects the whole
    malformed chain, and find_first raises straight out of an MCP tool. The
    guard used to be `'"' not in text` alone, which left the backtick — the
    actual delimiter — open. Every call site degrades to its bare type chain,
    which is the safe answer, so False here is slower and never wrong.
    """
    return not any(ch in text for ch in '"`\\')


def _field_element(field: dict) -> str | None:
    """Element id for the field the CALLER pointed at, or None.

    Never WDA's /element/active. On a Messages thread that answers with a
    message BUBBLE (an XCUIElementTypeTextView named CKBalloonTextView) even
    with the keyboard up and the caret blinking in the compose bar, so both
    things that used it worked on a message instead of the field: the clear ran
    WDA's clear routine on a bubble, which long-presses it — that is what
    opened the Tapback picker in the middle of a send — and the read-back
    returned the bubble's text, so send_message refused the message it had just
    typed correctly (device, 2026-08-12; docs/ERRORS.md).

    Bounded class chains only, one id back — see the section note in
    wda_client. The label pins the right field when a screen holds several
    (`ocr()` reports a field's label as its text); the bare type chain is the
    fallback for a field whose label iOS leaves empty.
    """
    kind = "**/XCUIElementType" + str(field.get("type", ""))
    label = str(field.get("text", "")).strip()
    chains = [kind]
    if label and _predicate_safe(label):
        chains.insert(0, f'{kind}[`label == "{label}"`]')
    for chain in chains:
        try:
            eid = client().find_first(chain)
        except WDAError:
            return None
        if eid:
            return eid
    return None


def _field_value(eid: str | None) -> str | None:
    """What the field really holds, or None if it cannot be read.

    NOT collect_texts(): that prefers `label`, which for a text field is the
    PLACEHOLDER ("Message" on the Messages compose bar), so it reads the same
    whether the field is empty or holds a draft. The typed content is `value` —
    though an EMPTY field reports its placeholder there too ("iMessage",
    measured on device), so an emptied field never reads back as "".
    """
    if eid is None:
        return None
    try:
        return client().element_value(eid)
    except WDAError:
        return None


def _clear_field(eid: str | None, field: dict | None = None) -> None:
    """Empty the field, in order of reliability.

    The explicit Clear-text button is a SearchField affordance, not a
    TextField one (confirmed on device — the Messages compose bar never
    grows one). set_field_text()'s only real caller, send_message(), always
    passes a TextField, so searching for the button there was a full ocr()
    -> ui_tree() -> /source fetch (3.5-7.4s, always cold: the tap that just
    happened invalidated the cache) that could structurally never find
    anything. Skip the search unless the caller is looking at a SearchField.
    """
    if field is None or field.get("type") == "SearchField":
        clear = [
            e
            for e in ocr()
            if e["type"] == "Button" and e["text"].strip() in ("Clear text", "Clear")
        ]
        if clear:  # search fields carry an explicit button
            tap(clear[0]["x"], clear[0]["y"])
            return
    if eid is not None:
        try:  # WebDriver's own clear: one call, any content length
            client().element_clear(eid)
            return
        except WDAError:
            pass
    current = _field_value(eid) or ""  # last resort: backspace over what is there
    if current:
        type_text(_BACKSPACE * (len(current) + 2))


def _duty_rest(started: float, interval: float, deadline: float) -> float:
    """Rest between two polls: never shorter than the read, never past `deadline`.

    The floor keeps a shorter interval from bursting into WDA's one-at-a-time
    queue on an expensive read; the deadline clamp keeps the rest from sleeping
    straight through the timeout it is gated by and buying another whole read.
    """
    rest = max(interval, time.monotonic() - started)
    return max(0.0, min(rest, deadline - time.monotonic()))


_KEYBOARD_CHAIN = "**/XCUIElementTypeKeyboard"
# needs device check: the throttle between probes, not a measured slide-up. The
# slide-up itself is never guessed — each caller passes its own flat sleep as
# the cap, so the worst case is byte-identical to the wait it replaces.
_KEYBOARD_POLL = 0.1


def _await_keyboard(cap: float) -> None:
    """Wait for the keyboard to be up, at most `cap` — the sleep this replaces.

    Typing before the keyboard has finished sliding up DROPS THE FIRST KEYS, so
    the two flat sleeps that stood here were paying for the animation blind: a
    fixed 0.4s/0.8s whether the keyboard was already up or still moving. The
    wait is conditional on the keyboard now, never removed — a bounded probe
    (one element id, not a /source) returns the moment it is there, the cap
    keeps the worst case at today's sleep (it bounds the probes as well as the
    rests: a probe is NOT free, and one started with less cap left than it
    costs would run past the sleep it replaces), and a probe WDA refuses pays
    out the rest of that sleep rather than typing into a keyboard that may not
    be up.
    """
    c = client()
    deadline = time.monotonic() + cap
    cost = 0.0  # what the last probe took on the wire
    while True:
        left = deadline - time.monotonic()
        # The cap bounds the PROBES too, not just the rests between them: a
        # probe is not free (the only class chain measured on this device costs
        # 328ms), so one started with less cap than that left runs past the
        # sleep it replaces. Sleep out what is left instead — identical to the
        # flat sleep, which is what the cap promises.
        if left <= 0 or left < cost:
            time.sleep(max(0.0, left))
            return
        started = time.monotonic()
        try:
            if c.find_first(_KEYBOARD_CHAIN):
                return
        except WDAError:  # no answer: pay out the old sleep, drop no keys
            time.sleep(max(0.0, deadline - time.monotonic()))
            return
        cost = time.monotonic() - started
        # Duty-cycle floor, like press_home's: a class-chain query resolves the
        # active application, so never rest less than the probe itself cost.
        time.sleep(_duty_rest(started, _KEYBOARD_POLL, deadline))


def set_field_text(field: dict, text: str, verify: bool = True) -> str:
    """Replace `field`'s contents with `text`. Returns what actually landed.

    type_text() is POST /wda/keys, which APPENDS at the cursor: it does not
    replace what is already there. iOS keeps an unsent draft per Messages
    thread and resumes a search field mid-query, so typing into a field that
    already holds something puts draft+text on the phone while the caller still
    believes it typed `text`. Clear first, then read the field back, so a caller
    can never report a message it did not send.

    Pass the field element you are about to type into (the one you would tap):
    that is an ocr() row, whose x/y/type are what this needs. It taps the field
    itself, so do not tap first. `verify=False` skips the read-back round trip.

    The clear and the read-back address `field` itself, resolved to an element
    id AFTER the tap: the keyboard slide-up moves the field (the Messages
    compose bar goes from y=908 to y=601, measured), so the id has to come from
    a class chain and not from the coordinates that were just tapped.
    """
    tap(field["x"], field["y"])
    _await_keyboard(0.4)  # keyboard slide-up, or the first keys are dropped
    eid = _field_element(field)
    _clear_field(eid, field)
    type_text(text)  # /wda/keys goes to the real first responder, which is right
    if not verify:
        return text
    landed = _field_value(eid)
    if landed is None:
        return text
    # A read-back is a screen read like any other. _field_element falls back to
    # the bare type chain when the label predicate misses, so on a screen with
    # more than one text field this can hand back a DIFFERENT field's value —
    # a surviving draft, or a string planted in a web form — and under
    # approval.mode() == "flagged" the taint is exactly what arms the send gate.
    # send_message calls this inside trust.internal(), so its own bookkeeping
    # read still does not arm the gate it is about to check.
    trust.mark("screen", trust.scan(landed))
    return landed


_SPRINGBOARD = "com.apple.springboard"
_HOME_POLL = 0.05
# Wall clock, not an attempt count: at a 0.25s interval on top of a ~102ms
# active_app() read the cycle was ~352ms against a recorded ~830ms arrival, so
# detection landed ~280ms late. Bounding the ceiling in seconds means a shorter
# interval buys more looks, not less patience — and the ceiling is the old
# loop's own effective one (8 reads at ~102ms plus 7 x 0.25s), so this is a
# detection win with no patience given up on a path whose recorded failure was
# returning EARLY.
_HOME_DEADLINE = 2.8


def press_home() -> None:
    """Go to the Home Screen, as if the physical Home gesture were used.

    Leaves whatever app was open. It does NOT change which Home Screen page you
    are on: /wda/homescreen is a no-op once you are already on the Home Screen
    (verified on device — two consecutive calls from page 4 both stayed on page
    4). Use goto_home_page() to reach a specific page.

    Waits for the springboard to actually come forward, because /wda/homescreen
    is NOT reliably synchronous: measured 2026-08-12, it returned in ~50ms with
    the app still frontmost on two tries out of three and the springboard
    arrived at ~830ms, while the third call took 1.4s and was done on return.
    Returning early is not a cosmetic problem — the viewer's second Home press
    then read a stale active app and pressed home again instead of walking to
    page 1, and goto_home_page() read no PageIndicator and raised. Bounded and
    silent on timeout: the physical gesture cannot fail, so neither may this;
    callers that need to know check the screen.
    """
    _invalidate_tree()
    c = client()
    c.home()
    deadline = time.monotonic() + _HOME_DEADLINE
    while True:
        started = time.monotonic()
        if c.active_app().get("bundleId") == _SPRINGBOARD:
            return
        if time.monotonic() >= deadline:
            return
        # Rest at least as long as the read took, so the loop can never spend
        # more than half its time inside WDA. active_app() resolves the active
        # application, one of the calls that can block with no upper bound in a
        # wedging app, and this loop is what the viewer's Home button drives —
        # a warm 10ms read would otherwise turn the interval into a 40-request
        # burst into WDA's one-at-a-time queue.
        time.sleep(max(_HOME_POLL, time.monotonic() - started))


_PAGE_VALUE = re.compile(r"Page (\d+) of (\d+)")


_PAGE_INDICATOR_CHAIN = "**/XCUIElementTypePageIndicator"


def current_page() -> dict | None:
    """Where the Home Screen is: {"index", "total", "zone"}, or None.

    Reads the PageIndicator's `value` ("Page 4 of 8"). Index 0 is Today View,
    1..total are real Home Screen pages, and total+1 is the App Library — iOS
    itself numbers Today View 0.

    Returns None when no PageIndicator is on screen (an app is open) or when the
    value does not parse: an iOS wording change must fail loudly, not guess.

    Deliberately NOT ui_tree(): this wants one attribute off one element, and a
    full /source dump of the Home Screen measured 3.0-5.7s (554-610 nodes,
    244 KB) against 0.37s for the targeted lookup. That gap is the whole reason
    goto_home_page() used to take ten seconds. ocr() still cannot see this
    element either way — collect_texts prefers `label`, which is null here, so
    it falls back to `name` ("Page control") and drops `value`.
    """
    c = client()
    eid = c.find_first(_PAGE_INDICATOR_CHAIN)
    # Still a screen read, so the send gate still arms. No flags to pass on: the
    # only thing reaching the caller is two integers parsed out of a string iOS
    # generates itself, so there is no untrusted text for the scanner to see.
    trust.mark("screen", [])
    if eid is None:
        return None
    match = _PAGE_VALUE.search(c.element_value(eid))
    if not match:
        return None
    index, total = int(match.group(1)), int(match.group(2))
    if index <= 0:
        zone = "today"
    elif index > total:
        zone = "app_library"
    else:
        zone = "home"
    return {"index": index, "total": total, "zone": zone}


def goto_home_page(n: int = 1) -> None:
    """Land on Home Screen page `n` from anywhere.

    Works from any page, from Today View, from the App Library, and from inside
    an open app. press_home() cannot do this: /wda/homescreen only exits an app
    to the springboard and is a no-op between Home Screen pages.

    Raises ValueError for a target that is not a Home Screen page, and
    RuntimeError when the walk cannot get there — a partial walk that silently
    leaves you two pages short is the failure this guards against.

    Nothing sleeps between the steps. WDA already waits for the springboard to
    go idle inside the /actions call (waitForIdleTimeout, 2s cap), so the 0.55s
    settle this used to pay after every swipe was counting the same wait twice.
    Measured on device 2026-08-12: the first read after swipe() returns is
    correct 6/6, and a six-page walk went from 10.7s to 7.0s. Leaving an app is
    the one step that does need waiting, and press_home() now does that itself.
    """
    page = current_page()
    if page is None:  # an app is open
        press_home()
        page = current_page()
        if page is None:
            raise RuntimeError(
                "no PageIndicator after press_home; not on the Home Screen"
            )
    if not 1 <= n <= page["total"]:
        raise ValueError(
            f"page {n} is not a Home Screen page (1..{page['total']}); "
            "Today View is 0 and the App Library is past the end"
        )
    if page["index"] == n:
        return  # zero swipes: the phone never moved, so there is nothing to
        # verify, and the re-read cost 0.37s of viewer busy overlay on the
        # commonest Home press of all. Every walk that DOES swipe keeps its
        # verify and its RuntimeError.
    # Derive the walk from the real screen. These were hardcoded x=40 <-> x=400
    # while every other gesture goes through _window_size(), and 400 is off the
    # right edge of a 390-393pt portrait iPhone — a gesture that starts
    # off-screen is swallowed in silence and costs a full corrective pass.
    w, h = _window_size()
    for _ in range(2):  # walk, verify, then one corrective pass
        delta = page["index"] - n
        for _ in range(abs(delta)):
            if delta > 0:
                swipe(40, h / 2, w - 40, h / 2, 0.25)  # left->right: toward page 1
            else:
                swipe(w - 40, h / 2, 40, h / 2, 0.25)
        # Verify in one round trip where it can: current_page() is find_first
        # plus element_value (0.37s), and find_first alone on a comparable
        # chain is 0.11s. `total` is already known from the entry read. A miss
        # falls through to the full read, so both RuntimeErrors below stay
        # exactly as reachable — an unparseable indicator must still fail loud.
        if client().find_first(
            f'{_PAGE_INDICATOR_CHAIN}[`value == "Page {n} of {page["total"]}"`]'
        ):
            trust.mark("screen", [])  # still a screen read; the send gate arms
            return
        page = current_page()
        if page is None:
            raise RuntimeError("lost the PageIndicator mid-walk")
        if page["index"] == n:
            return
    raise RuntimeError(f"wanted page {n}, still on page {page['index']}")


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


# needs device check: a floor on the poll gap, over a 100-156ms active_app()
# read (measured). 0.5s was three intervals of nothing per launch; the rest is
# the read's own duration, so the loop still cannot outrun WDA (see press_home).
_APP_POLL = 0.1


def wait_for_app(
    bundle_id: str, timeout: float = 10.0, interval: float = _APP_POLL
) -> bool:
    """Poll until `bundle_id` is frontmost. True on success, False on timeout.

    Lets open_app() flows fail fast and loud instead of inferring foreground
    state from wait_stable() timing.

    Rests at least as long as the last read took: active_app() resolves the
    active application, one of the calls that can block with no upper bound in
    a wedging app, so a shorter interval must buy looks and not a burst into
    WDA's one-at-a-time queue. The rest is clamped to the deadline, so `timeout`
    still bounds the call at one read of overshoot rather than two.
    """
    deadline = time.monotonic() + timeout
    while True:
        started = time.monotonic()
        if current_app().get("bundleId") == bundle_id:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_duty_rest(started, interval, deadline))


def _resolve_bundle(name: str) -> str:
    """Friendly name ('Settings'), bundle id, or installed-app name -> bundle id.

    Shared by open_app() and close_app(), so both accept exactly the same
    spellings and raise the same "Did you mean" hint.
    """
    key = name.lower().strip()
    # A dot alone does NOT make it a bundle id: `ios apps --list` reports names
    # with the version attached ("YouTube 21.32.4", "TikTok 46.4.0"), so a bare
    # dot test shipped every one of those straight to app_launch as a bundle id
    # and iOS answered "Application info provider returned nil" — including for
    # the exact name this function's own "Did you mean" hint suggests. Bundle
    # ids are reverse-DNS and never contain spaces; installed names here do.
    looks_like_bundle = "." in name and " " not in name.strip()
    bundle = BUNDLE_IDS.get(key) or (name if looks_like_bundle else None)
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
    return bundle


# `ios apps --list` names carry the version ("Hinge 10.2.0"); the process name
# `ios ps` reports does not ("Hinge"), and neither does a human.
_VERSION_SUFFIX = re.compile(r"\s+\d[\w.]*$")


def open_apps() -> list[dict]:
    """The apps that are open, as the iOS app switcher lists them, newest
    first: [{name, bundle_id, pid}].

    Read over USB (`ios ps`, ~0.7s), never from the screen: WebDriverAgent's
    touches cannot reach the home-indicator zone, so the switcher screen itself
    cannot be opened (device.running_apps has the measurement). Running
    processes are joined against the installed list and BUNDLE_IDS, which is
    what drops Siri, Spotlight, the keyboard and the WDA runner — the things a
    switcher never shows. A process nothing matches is left out rather than
    guessed at. `name` is the display name; `bundle_id` is what open_app() and
    close_app() take.
    """
    installed: dict[str, tuple[str, str]] = {}
    for app in device.list_apps():
        base = _VERSION_SUFFIX.sub("", app["name"]).strip()
        installed.setdefault(base.lower(), (base, app["bundle_id"]))
    system: dict[str, tuple[str, str]] = {}
    for key, bid in BUNDLE_IDS.items():
        # com.apple.MobileSMS runs as "MobileSMS", com.apple.mobiletimer as
        # "MobileTimer": the last segment, case aside, is the process name.
        system[bid.rsplit(".", 1)[-1].lower()] = (key.title(), bid)
    system["photos"] = ("Photos", BUNDLE_IDS["photos"])  # mobileslideshow
    out = []
    for proc in device.running_apps():
        hit = installed.get(proc["name"].lower()) or system.get(proc["name"].lower())
        if not hit or "xctrunner" in hit[1]:
            continue
        out.append({"name": hit[0], "bundle_id": hit[1], "pid": proc["pid"]})
    return out


def close_app(name: str) -> bool:
    """Force-quit an app: the switcher's swipe-up. Takes the same names as
    open_app(). False when it was not running."""
    _invalidate_tree()
    return device.kill_app(_resolve_bundle(name))


def open_app(name: str, wait_seconds: float = 0.0) -> None:
    """Open an app by friendly name ('Settings'), bundle id, or installed-app name.

    Pass `wait_seconds` to have the launch CONFIRMED before this returns, and
    raise if the app never reached the foreground. The default 0.0 launches and
    returns exactly as before, and must stay 0.0: viewer.py calls this inside
    _action_slot(), so a non-zero default would hold _ACTION_LOCK for the whole
    wait and 409-drop the human's next taps (_ACTION_WAIT is 2s).

    This is the only way to get the correct foreground wait without knowing the
    bundle id: wait_for_app() needs one, open_app resolves it privately, and
    inside act() a later step cannot read an earlier step's result — so a
    batched open-then-look otherwise settles for wait_stable()'s "the screen
    stopped moving", which a launch that bounced back to the Home Screen also
    satisfies.
    """
    bundle = _resolve_bundle(name)
    _invalidate_tree()
    client().app_launch(bundle)
    if wait_seconds > 0 and not wait_for_app(bundle, timeout=wait_seconds):
        # Loud, not a return value: keeping `-> None` leaves the MCP output
        # schema, the viewer's own call and four test stubs untouched, and a
        # raise stops an act() batch here instead of letting the next step tap
        # the previous screen's coordinates.
        raise WDAError(
            f"{name!r} did not reach the foreground within {wait_seconds}s. "
            "Call current_app() to see what did, or retry with a longer wait."
        )


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


# The thread header's own button, as _thread_title() identifies it. The list's
# row photos match this too, which is why a hit only earns a full read: the
# geometry test in _thread_title() is what actually separates them. `name` is
# in the predicate for the same reason it is in find_on_home_screen's:
# _thread_title reads collect_texts' `label or name or value`, so a label-only
# probe reads a name-only header as GONE and _go_back returns without waiting
# at all — which is the mid-animation second tap this loop exists to prevent.
_THREAD_HEADER_CHAIN = (
    "**/XCUIElementTypeButton["
    '`label BEGINSWITH "Contact photo for " OR name BEGINSWITH "Contact photo for "`]'
)
_THREAD_POLL = 0.25  # a probe is cheap; do not spin on it
# The search-result poll keeps the 0.5s it always had. Its turn can still cost
# a whole /source — the "Messages with: <name>" filter row is itself a Cell
# carrying the contact's name, so the probe answers yes while the real
# conversation row is still landing — and at 0.25s that path paid MORE /source
# dumps than the loop this replaced, on the just-woken phone the 20s deadline
# exists for.
_SEARCH_POLL = 0.5
# How long the probe is allowed to gate the tree read. Half the deadline, so a
# probe that structurally cannot match still leaves a full 10s of the patient
# poll that worked before it.
_SEARCH_PROBE_SECONDS = 10.0


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
    # "Is the header gone yet" is a yes/no, and the usual answer is yes on the
    # first look — which must not cost a whole-tree fetch (~3s on a busy
    # screen). A bounded probe for the header button answers it; the full read
    # runs only while the probe still sees one, so _thread_title() keeps the
    # last word and the 5s deadline is unchanged.
    while time.monotonic() < deadline and client().find_first(_THREAD_HEADER_CHAIN):
        if _thread_title(ui_tree()) is None:
            break
        time.sleep(_THREAD_POLL)
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
    _await_keyboard(0.8)  # keyboard slide-up
    # Messages can resume mid-search with a stale query; clear it before typing.
    stale = [e for e in ocr() if e["type"] == "Button" and e["text"] == "Clear text"]
    if stale:
        tap(stale[0]["x"], stale[0]["y"])
    type_text(contact)

    # Poll for result cells: the caret blink defeats wait_stable here. Be
    # patient — the 20s deadline is tuned against a just-woken phone that
    # missed a shorter window (bit live 2026-08-10). The turn is a bounded
    # probe first, not a whole-tree fetch: "has the search returned anything"
    # is a yes/no, and /source takes ~3s on a busy screen. The tree is read
    # only once the probe says yes, so _conversation_cells() still decides on
    # exactly the input it always saw.
    #
    # The probe can only ever be an OPTIMISATION, never the decision: it is not
    # a superset of _conversation_cells. That matches on `label or name or
    # value` and accepts containment BOTH ways (`_title_matches` verifies a
    # cell labelled "Wes" for the contact "Wes Sander"), and no one-directional
    # CONTAINS predicate expresses that. So the probe gates only the first half
    # of the deadline; past that the loop is the plain tree poll it always was,
    # and a predicate that can never match costs some cheap probes instead of
    # turning a patient 20s wait into a hard 20s failure.
    needle = contact.strip()  # _title_matches strips both sides; match it
    cells_chain = "**/XCUIElementTypeCell"
    if _predicate_safe(needle):
        cells_chain += (
            f'[`label CONTAINS[c] "{needle}" OR name CONTAINS[c] "{needle}"`]'
        )
    start = time.monotonic()
    deadline = start + 20
    probe_until = start + _SEARCH_PROBE_SECONDS
    while True:
        if time.monotonic() >= probe_until or client().find_first(cells_chain):
            if _conversation_cells(ui_tree(), contact):
                break
        if time.monotonic() >= deadline:
            raise WDAError(
                f"No conversation named {contact!r} in Messages search. "
                "Check the name, or open the conversation by hand."
            )
        time.sleep(_SEARCH_POLL)
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
        w, _h = _window_size()
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

# Send-button scan: one look straight away plus ONE retry. Bounded so a slow
# toolbar cannot become a spurious "Send button not found" on text that typed
# correctly, which is what the flat 0.5s sleep it replaced was insuring.
#
# One retry, not three: every re-scan drops the tree first, so each one is a
# guaranteed-cold whole /source on an open Messages thread. Four of them is a
# far worse miss path than the flat sleep, and the premise that the first look
# usually hits is UNMEASURED — _field_value is a ~0.1s attribute read, so the
# gap between typing and this scan is much smaller than the 0.5s it replaced.
# At these values the worst case is the old wait plus exactly one extra read.
_SEND_SCAN_TRIES = 2
_SEND_SCAN_INTERVAL = 0.5


def _send_gate(contact: str, text: str) -> None:
    """The prompt-injection gate every send passes: raises unless approved.

    Shared by send_message and send_image so the image path cannot drift into
    a bypass — for an image `text` is the card's label ("[image 56 KB: x.png]").
    """
    taint = trust.tainted()
    if not taint or trust.is_human_initiated():
        return
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


def _find_send_button(tries: int = _SEND_SCAN_TRIES) -> dict | None:
    """The Messages Send button, or None. One look plus one cold retry.

    `tries=1` is for the viewer's Enter key, where "no Send button" is the
    normal answer in every app but Messages and a retry would tax each Return.
    """
    for attempt in range(tries):
        sends = [
            e
            for e in ocr()
            if e["type"] == "Button" and e["text"].strip().lower() == "send"
        ]
        if sends:
            return sends[0]
        if attempt < tries - 1:
            time.sleep(_SEND_SCAN_INTERVAL)
            _invalidate_tree()
    return None


_IMAGE_MAX_BYTES = (
    8_000_000  # one WDA POST carries it base64; iMessage recompresses anyway
)
_IMAGE_MAGIC = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpeg"}
_PASTE_MENU_WAIT = 3.0  # needs device check: the edit menu after a long press


def send_image(contact: str, image_path: str, text: str = "") -> dict:
    """Send a PNG/JPEG file from this PC as a Messages attachment.

    Puts the image on the phone's clipboard, opens the thread, long-presses the
    compose bar and taps Paste, types `text` as the caption if given, then
    Send. Same approval gate as send_message: the card shows the file name,
    size and caption. Refuses a thread whose compose bar already holds a
    draft, because Paste would send draft+image.
    """
    path = Path(image_path)
    data = path.read_bytes()
    return _send_image_bytes(contact, data, path.name, text)


def _send_image_bytes(contact: str, data: bytes, name: str, text: str = "") -> dict:
    """send_image's body, on bytes: the viewer's pasted-image send shares it."""
    kind = next((k for m, k in _IMAGE_MAGIC.items() if data.startswith(m)), None)
    if kind is None:
        raise WDAError(f"{name} is not a PNG or JPEG")
    if len(data) > _IMAGE_MAX_BYTES:
        raise WDAError(f"{name} is {len(data) // 1_000_000} MB; downscale it first")
    if config.PHONE_PASSCODE and config.PHONE_PASSCODE in text:
        raise WDAError("Refused: the caption contains your phone passcode.")
    label = f"[image {max(1, len(data) // 1024)} KB: {name}]"
    if text.strip():
        label = f"{label} {text.strip()}"
    _send_gate(contact, label)

    with trust.internal():
        # Clipboard first: setting it flashes the WDA runner over whatever is
        # frontmost and hands the screen back, so do it before the thread walk.
        client().set_clipboard(data, "image")
        _invalidate_tree()
        title = _open_thread(contact)
        fields = [e for e in ocr() if e["type"] == "TextField"]
        if not fields:
            raise WDAError("No compose field on screen. Is the conversation open?")
        field = max(fields, key=lambda e: e["y"])
        draft = set_field_text(field, "")  # empties a surviving draft, reads back
        if draft.strip() and draft.strip().lower() not in _EMPTY_COMPOSE_VALUES:
            _log_action(contact, title, label, sent=False)
            raise WDAError(
                f"Refused: the compose field still reads {draft!r}. "
                "Clear the conversation's draft on the phone and try again."
            )
        # Re-resolve the bar: the keyboard slide-up moved it (y=908 -> 601 on
        # device), and a long press at the old coordinates lands on a key.
        fields = [e for e in ocr() if e["type"] == "TextField"]
        if fields:
            field = max(fields, key=lambda e: e["y"])
        long_press(field["x"], field["y"])
        paste = wait_for_text("Paste", timeout=_PASTE_MENU_WAIT, exact=True)
        if paste is None:
            raise WDAError(
                "No Paste option appeared after long-pressing the compose bar."
            )
        tap(paste["x"], paste["y"])
        if text.strip():
            # The caret sits after the pasted attachment. type_text APPENDS,
            # which is what we want here. iOS sends the two as separate bubbles
            # (image, then text) from one Send tap — verified on device.
            type_text(text.strip())
        send = _find_send_button()
        if send is None:
            raise WDAError("Send button not found — the image may not have pasted.")
        _log_action(contact, title, label, sent=False)  # attempt, before the tap
        tap(send["x"], send["y"])
        _log_action(contact, title, label, sent=True)
    return {"contact": contact, "resolved_title": title, "image": name, "sent": True}


def save_clipboard_image(path: str) -> dict:
    """Save the image on the iPhone's clipboard to `path` on this PC as PNG.

    Copy a photo or screenshot on the phone, then call this. Returns
    {"path", "bytes"}; raises when the clipboard holds no image. The bytes are
    phone content, so the read taints the session like any other.
    """
    png = client().get_clipboard_image()
    trust.mark("clipboard", [])
    if not png:
        raise WDAError(
            "The phone clipboard holds no image. Copy one on the phone first."
        )
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    return {"path": str(out), "bytes": len(png)}


# What an EMPTY Messages compose bar reads back as: element_value hands back
# the placeholder, not "" (see set_field_text).
_EMPTY_COMPOSE_VALUES = {"imessage", "text message", "message", "sms"}


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
    _send_gate(contact, text)

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
        # Look immediately, then retry in small steps: the toolbar is usually
        # up by the time the read-back returned, and a flat 0.5s paid for the
        # slow case on every send. The re-scan must drop the tree first —
        # ocr() is cached ~2s, so it would otherwise answer three times from
        # the one read that already missed the button.
        send = _find_send_button()
        if send is None:
            raise WDAError("Send button not found — text may not have been typed.")
        _log_action(contact, title, text, sent=False)  # attempt, before the tap
        tap(send["x"], send["y"])
        # Nothing after this reads the screen, and the tap already blocked on
        # waitForIdleTimeout inside /actions — the same argument that retired
        # goto_home_page()'s _PAGE_SETTLE. The 1.5s that sat here was dead.
        _log_action(contact, title, landed, sent=True)  # confirmed
    return {"contact": contact, "resolved_title": title, "text": landed, "sent": True}


# needs device check: a floor on the poll gap. Every turn here drops the cache
# and re-reads the WHOLE tree, so the interval is pure tail on top of a read
# that already costs 0.22s inside an app and 3.0-5.7s on the Home Screen — the
# rest is the read's own duration, which is what actually paces this loop.
_TEXT_POLL = 0.25


def wait_for_text(
    text: str, timeout: float = 10.0, interval: float = _TEXT_POLL, exact: bool = False
) -> dict | None:
    """Poll until `text` appears on screen; returns the element or None.

    The complement of wait_stable(): that says the screen stopped moving, this
    says the thing you were waiting for actually showed up. The returned
    element carries x/y, so the caller can tap it without re-searching.

    Rests at least as long as the last read took (press_home's duty cycle): a
    tree read is the most expensive perception call there is, so a shorter
    interval buys looks on a cheap screen and cannot burst on an expensive one.
    The rest is clamped to the deadline, so `timeout` still bounds the call at
    one read of overshoot rather than two.
    """
    deadline = time.monotonic() + timeout
    while True:
        started = time.monotonic()
        hits = find_text(text, exact=exact)
        if hits:
            return hits[0]
        if time.monotonic() >= deadline:
            return None
        time.sleep(_duty_rest(started, interval, deadline))
        _invalidate_tree()  # never poll the cached tree — read a fresh one


# Gap between two stability compares. One WDA round trip (~50-100ms) is how
# far apart two screenshots have to be to differ mid-animation, which is a
# LOWER bound on this, not proof that 0.15 is enough. needs device check: the
# interval is only paid while the screen is genuinely still moving, and
# scroll_until_found is the one caller where nothing else waits list momentum
# out (WDA_ANIM_COOLOFF=0), so this is the one constant to walk back to the
# 0.5 it shipped with if a scroll starts stopping short.
_STABLE_INTERVAL = 0.15


def wait_stable(timeout: float = 10.0, interval: float = _STABLE_INTERVAL) -> bool:
    """Wait until two consecutive screenshots are identical. True if stable.

    The first comparison happens IMMEDIATELY. Callers reach here straight after
    a gesture that WDA already settled server-side (~0.7s per swipe, measured —
    see config.WDA_ANIM_COOLOFF), so the screen is usually still by the time we
    are asked. Sleeping before the first compare made that common case cost a
    guaranteed extra `interval`, up to 9 times per scroll_until_found and 17
    times per find_on_home_screen. Two screenshots are a WDA round trip apart
    (~50-100ms), which is far enough to differ mid-animation — and that same
    number is why `interval` defaults to 0.15 and not the 0.5s it used to: the
    interval is only paid while the screen is genuinely still moving, so
    anything longer than one round trip is overshoot on the tail.
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
    # The real pad's digits are Key elements (device dump 2026-08-13); Button
    # stays accepted for older tree shapes. Counting digits matters beyond
    # belt-and-braces: a localized pad has no "passcode" text to match.
    digits = {
        e["text"]
        for e in texts
        if e["type"] in ("Button", "Key") and e["text"].isdigit()
    }
    if len(digits) >= 9:
        return True
    return any("passcode" in e["text"].lower() for e in texts)


def _on_lock_screen(tree: dict) -> bool:
    """True when this tree is the (still-locked) lock screen — CoverSheet.

    unlock()'s "lit and no pad, so it was just asleep and is now usable"
    shortcut assumes a lit screen means unlocked. A PRIORITY NOTIFICATION
    breaks that: it keeps the lock screen LIT while the phone stays locked,
    so when the wake swipe fails to raise the pad (the ~16s wake-transition
    hang, then the notification-lit screen never darkens to trigger the retry)
    the shortcut returned {ok: true} over a phone still on its lock screen
    ("runs ~20s then nothing happens"; Wes 2026-08-20). The lock screen's own
    markers — SBCoverSheetWindow, "Swipe up to unlock", "Locked" — say which
    lit screen this is (device dump 2026-08-20). Same lying-success class as
    the dark-screen silent return fixed 2026-08-13, pointed at the lit case.
    """
    for e in collect_texts(tree):
        t = e["text"]
        if e["type"] == "Window" and "CoverSheet" in t:
            return True
        low = t.lower()
        if "swipe up to unlock" in low or low == "locked":
            return True
    return False


def _pad_digit_probe(pad_tree: dict) -> str:
    """Class chain matching one digit of the pad we are about to tap.

    The post-type "is the pad still on screen" question does not need a tree:
    a bounded find_first answers it in 0.11s where the full /source of the
    freshly unlocked Home Screen — /source's worst case — costs 3.0-5.7s
    (both measured on device 2026-08-14). Probing a digit the pad actually
    showed, by its own element type, keeps the check honest for a
    Button-shaped pad too.
    """
    for e in collect_texts(pad_tree):
        if (
            e["type"] in ("Button", "Key")
            and len(e["text"]) == 1
            and e["text"].isdigit()
        ):
            return f'**/XCUIElementType{e["type"]}[`label == "{e["text"]}"`]'
    # Alphanumeric fallback: the passcode keyboard's keys. Best-effort — the
    # digit pad above is the real device's shape.
    return '**/XCUIElementTypeKey[`label == "5"`]'


def _scrub_secret(message: str, secret: str | None) -> str:
    """Blank a secret out of an error message before it reaches logs/output."""
    return message.replace(secret, "•••") if secret else message


_UNLOCK_TIMEOUT = 45.0  # first gesture after a deep sleep: 20.5s measured live

# Is the display on? PNG size is the cheap probe, and these are the real
# numbers off the device (2026-08-12): display OFF 50 KB, Calculator 245 KB
# (a mostly-black UI, so close to the worst case for a lit app), Home Screen
# 888 KB. 120 KB sits ~2.4x above the dark frame and ~2x below the darkest lit
# screen measured. It is a heuristic, not a lock-state oracle: an app painting
# a near-pure-black full screen could still read as dark. That is the accepted
# residual — the alternative probes either act on the phone (press_button wakes
# by EXITING the app, verified) or come from /wda/locked, which lies.
_LIT_SCREEN_BYTES = 120_000


def _pad_dismissed(c, probe: str) -> bool:
    """True once the pad's digit probe stops matching — the pad left the
    screen. Attempt-counted with a wall-clock cap, same shape as unlock()'s
    pad_appears: tests with a no-op sleep stay instant, and a slow probe
    cannot stretch the check much past ~3s."""
    start = time.monotonic()
    for i in range(8):
        if c.find_first(probe) is None:
            return True
        if i >= 1 and time.monotonic() - start > 3.0:
            break
        time.sleep(0.3)
    return False


def _enter_passcode(c, passcode: str, pad_tree: dict) -> None:
    """Put the passcode in: TYPE it in one request, fall back to pad taps.

    One /wda/keys request enters every digit at once — the near-instant
    entry unlock had before 2026-08-13 — against ~2.8s of visible
    one-finger taps. But /wda/keys goes to the FOCUSED element, and the pad
    being on screen does not mean the pad holds focus: a lock-screen
    priority notification held focus while the pad sat behind it and ate
    all six typed digits (live 2026-08-13). So the typed attempt is never
    trusted: the pad must LEAVE the screen (bounded digit probe, 0.11s —
    never a /source), and a pad still up falls back to TAPPING the digit
    buttons, which need no focus. Eaten digits consume no iOS lockout
    attempt, so the fallback is free in the exact case it exists for; a
    wrong PHONE_PASSCODE now burns two attempts (one typed, one tapped)
    before unlock()'s exit check raises — accepted: that is a persistent
    .env misconfiguration the error names out loud, not a live race.

    Alphanumeric passcodes get a full keyboard instead of the pad, so any
    character without a digit button keeps the plain typing path.
    """
    centers = {}
    for e in collect_texts(pad_tree):
        if (
            e["type"] in ("Button", "Key")
            and len(e["text"]) == 1
            and e["text"].isdigit()
        ):
            centers.setdefault(e["text"], (e["x"], e["y"]))
    if not (passcode and all(ch in centers for ch in passcode)):
        try:
            c.type_text(passcode)
        except WDAError as exc:
            raise WDAError(_scrub_secret(str(exc), passcode)) from None
        return
    # The tap coordinates ARE the digits — keep them out of the live feed.
    with redact_actions("passcode entry"):
        # The pad is static, so idle settling buys nothing: the whole entry
        # runs at waitForIdleTimeout 0 (six taps went 4.94s -> 2.8s measured
        # live 2026-08-14; the typed request rides the same setting). Restore
        # is a finally: the setting rides the SHARED session. Do NOT batch
        # the fallback taps into one /actions request instead: six down/up
        # cycles in one pointer source enter deterministically WRONG digits,
        # and six parallel pointer sources KILL WDA outright (both on device
        # 2026-08-14; docs/ERRORS.md).
        c.set_wait_for_idle(0)
        try:
            try:
                c.type_text(passcode)
            except WDAError as exc:
                # A typing ERROR is not "digits eaten": a timeout's keys may
                # still land, and tapping on top of them garbles the attempt.
                # Raise instead — unlock is one attempt per click by design.
                raise WDAError(_scrub_secret(str(exc), passcode)) from None
            if _pad_dismissed(c, _pad_digit_probe(pad_tree)):
                return  # typed digits landed: unlocked, no taps needed
            for ch in passcode:
                x, y = centers[ch]
                # Name the finger contact instead of riding the client's
                # default: a dropped pad tap burns an iOS lockout attempt,
                # so this path must not silently follow a shorter default
                # tuned for dense app screens.
                c.tap(x, y, hold_ms=80)
        finally:
            try:
                c.set_wait_for_idle(config.WDA_IDLE_WAIT)
            except WDAError:
                pass  # digits are in; a session on eager waits self-heals
                # at the next fresh session, failing here would be a lie


def unlock(c: WDAClient | None = None) -> None:
    """Make the phone usable: wake it and, if the passcode pad comes up, enter
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
    try:
        frontmost = c.active_app().get("bundleId")
    except WDAError as exc:
        # /wda/activeAppInfo CRASHES while the lock screen is LIT — "attempt
        # to insert nil object from objects[2]" (live 2026-08-13, reproduced:
        # lit lock screen -> crash, dark -> answers springboard). A priority
        # notification keeps the lock screen lit for as long as it shows, so
        # every unlock during one died right here, before the first gesture.
        # The crash only happens on the lock screen — a real frontmost app
        # answers fine — so it cannot mean "in use": carry on with the wake.
        # ONLY that crash, though: any other WDAError (timeout, dead session)
        # leaves the phone's state unknown, and carrying on would Home-press
        # and edge-swipe a phone that may be unlocked with an app open.
        if "insert nil object" not in str(exc):
            raise
        frontmost = None
    if frontmost is not None and frontmost != "com.apple.springboard":
        # ...but only when the phone is genuinely in use. active_app() goes
        # STALE behind a lock: a phone that locked with an app frontmost keeps
        # naming that app until the display wakes, so this return used to
        # refuse the exact state unlock() exists for, and every launch after it
        # failed "device was not, or could not be, unlocked" (bit live
        # 2026-08-12). A lit screen is what "in use" actually means.
        if len(c.screenshot()) >= _LIT_SCREEN_BYTES:
            return  # frontmost app on a lit screen — touch nothing
    # A session that crossed a screen lock is POISONED: it keeps answering
    # GETs but its first /actions hangs ~16s inside XCTest's snapshot timeout
    # before failing "point.x != INFINITY" (16.23s measured on device
    # 2026-08-14 — long enough for the woken lock screen to re-sleep, so the
    # wake swipe burned on a dark screen and unlock ran 30-50s; a priority
    # notification makes this the RELIABLE case by keeping the poisoned
    # session alive). A fresh session is 0.02s (same run), is born after the
    # lock, and cannot be poisoned — mint one instead of discovering the
    # poison mid-wake. Past the in-use return above, so a phone someone is
    # using never gets its session churned; a merely-asleep phone loses a
    # healthy session, which is fine: the new id lands in .state/wda_session,
    # every client adopts it, and the viewer retunes its stream on change.
    c.fresh_session()
    # Deliberately NOT the _window_size() memo: unlock reads this once, and a
    # lock almost always evicts the session, so the memo would miss anyway and
    # the orientation guard would just add a SECOND round trip to the most
    # timing-sensitive path here — the first gesture after a deep sleep blocked
    # WDA 20.5s (measured), and this function already has three ERRORS.md
    # entries. One call, before the wake, so it can't eat awake-time.
    w, h = c.window_size()
    _invalidate_tree()  # about to change the screen, like any other action

    def wake_and_swipe():
        # On a locked phone the bottom-edge swipe summons the passcode pad;
        # on a merely-asleep phone it lands on the home screen. Higher swipe
        # starts scroll the lock-screen notification list instead.
        # The flat sleeps in here stay flat ON PURPOSE — the one path in this
        # file whose waits are not tuned down. Three ERRORS.md entries live on
        # it (burned swipe on a re-slept screen, the ~16s poisoned-session
        # hang, the notification that ate six typed digits) and nothing here
        # can be re-measured without the phone in hand.
        c.press_button("home")  # wake the display
        time.sleep(0.5)
        c.swipe(w / 2, h * 0.98, w / 2, h * 0.30, 0.25)
        time.sleep(1.0)

    pad_tree: dict = {}

    def pad_appears(seconds: float) -> bool:
        # Poll, don't peek once: the pad animates in, and a slow swipe can
        # land well after the call returns. Attempt-counted (not wall-clock)
        # so tests with a no-op sleep stay instant. Keeps the tree that showed
        # the pad: _enter_passcode aims its digit taps with it.
        nonlocal pad_tree
        start = time.monotonic()
        attempts = max(1, int(seconds / 0.4))
        for i in range(attempts):
            pad_tree = c.source()
            if _passcode_pad_visible(pad_tree):
                return True
            if i < attempts - 1:
                # The attempt count assumes a 0.4s /source, but a lock-screen
                # /source can run ~3s, and 7 polls of a screen a burned swipe
                # never changed cost 21s (live 2026-08-14). Wall clock caps
                # the spend; two reads minimum so a pad that animates in
                # after a slow first read is still caught.
                if i >= 1 and time.monotonic() - start > seconds:
                    return False
                time.sleep(0.4)  # flat by design too — see wake_and_swipe above
        return False

    wake_and_swipe()
    if not pad_appears(3.0):
        if len(c.screenshot()) >= _LIT_SCREEN_BYTES and not _on_lock_screen(pad_tree):
            return  # lit and no pad, not the lock screen: was just asleep,
            # now awake+usable. A notification-lit lock screen fails this and
            # falls through to a second wake+swipe instead of a false success.
        # Dark again: a slow swipe landed after the lock screen re-slept and
        # burned on a black screen. One more charge, then stop — endless
        # gesturing at a phone that will not show a pad helps nobody. But
        # stopping is not success: a silent return here made the viewer answer
        # {"ok": true} and the MCP tool say "unlocked" over a still-dark phone.
        wake_and_swipe()
        if not pad_appears(3.0):
            if len(c.screenshot()) >= _LIT_SCREEN_BYTES and not _on_lock_screen(
                pad_tree
            ):
                return  # lit without a pad, not the lock screen: awake+usable
            if _on_lock_screen(pad_tree):
                raise WDAError(
                    "Woke the phone but the passcode pad never appeared — a "
                    "lock-screen notification can hold the swipe. Swipe up on "
                    "the phone to bring up the passcode, then try again."
                )
            raise WDAError(
                "Woke the phone twice but the screen stayed dark and no "
                "passcode pad appeared. Wake it by hand (side button), then "
                "try again."
            )
    if not config.PHONE_PASSCODE:
        raise WDAError(
            "Phone is locked. Set PHONE_PASSCODE in .env or unlock it by hand."
        )
    # The tree fetch above can take seconds when the viewer is streaming, and
    # the lock screen re-sleeps fast — taps on a dark screen go nowhere, so
    # re-probe and wake again if it slept.
    if len(c.screenshot()) < _LIT_SCREEN_BYTES:
        wake_and_swipe()
        pad_tree = c.source()  # the screen was redrawn: re-aim the digit taps
    _enter_passcode(c, config.PHONE_PASSCODE, pad_tree)
    # Success = the pad leaves the screen. This used to be sleep(0.7) plus a
    # full /source of the just-unlocked Home Screen — /source's worst case,
    # 3.0-5.7s measured — so the viewer sat ~5s behind its busy label over a
    # phone that was visibly unlocked (Wes, live 2026-08-14). A bounded probe
    # for one of the pad's own digits answers in 0.11s. Attempt-counted with
    # a wall-clock cap, same shape as pad_appears: tests with a no-op sleep
    # stay instant, and a slow probe cannot stretch the check past ~3s.
    if _pad_dismissed(c, _pad_digit_probe(pad_tree)):
        return  # pad gone: unlocked
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
    "set_clipboard",
    "get_clipboard",
    "compact",
    "press_home",
    "current_page",
    "goto_home_page",
    "open_app",
    "open_apps",
    "close_app",
    "current_app",
    "wait_for_app",
    "send_message",
    "send_image",
    "save_clipboard_image",
    "read_messages",
    "wait_stable",
    "wait_for_text",
    "unlock",
    "WDAError",
]
