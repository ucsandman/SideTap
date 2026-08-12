# Viewer Long-Press + Home-to-Page-1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the human viewer a long-press gesture, and make the Home button
actually land on Home Screen page 1 from anywhere.

**Architecture:** Two new helpers read the `PageIndicator`'s `value` to learn
exact page position, then swipe an exact number of times. The viewer gains an
`/api/long_press` endpoint and rewires `/api/home` to the new helper. The
browser fires the long press on a local timer during the hold, so the iOS menu
appears while the user is still holding.

**Tech Stack:** Python 3.12, `requests` only, pytest, vanilla JS in a single
`viewer.html`.

## Global Constraints

- No new runtime dependencies beyond `requests` and `mcp`.
- Unit tests must pass with no phone attached.
- Coordinates are points, never pixels.
- New agent primitives go in `helpers.py`, added to `__all__`, and to
  `mcp_server._TOOLS` when MCP-safe.
- `wda_client.py` stays free of go-ios knowledge; `device.py` stays free of HTTP.
- Repo `skills/` copies carry no machine-specific paths and no personal device
  details; edit there first, then re-copy to `~/.claude/skills/`.
- Commit messages use the repo's `area: lowercase description` style and end with
  the `Co-Authored-By` and `Claude-Session` trailers.

**Deviation from the spec, already decided:** `current_page()` and
`goto_home_page()` take **no client parameter**. The spec proposed one, matching
`helpers.unlock(c)`. That precedent covers gesture-only helpers; these read the
screen, and every screen read must go through `ui_tree()` so `trust` marks the
session tainted. Passing a raw client would bypass that. Clients adopt the shared
session id from `.state/wda_session`, so `helpers.client()` inside the viewer
process does not evict the viewer's session.

## File Structure

- `src/phone_harness/helpers.py` — add `current_page()`, `goto_home_page()`; fix
  the `press_home` docstring. Both new names into `__all__`.
- `src/phone_harness/mcp_server.py` — both new names into `_TOOLS`.
- `src/phone_harness/viewer.py` — add `/api/long_press`; rewire `/api/home`.
- `src/phone_harness/viewer.html` — hoist the pointer helpers, add the hold
  timer, ring, and `pointermove`/`pointercancel` listeners; move Home to
  `withBusy`.
- `tests/test_helpers.py` — page parsing and walk arithmetic.
- `tests/test_viewer.py` — the two endpoints.
- Docs: `skills/phone-gotchas/SKILL.md`, `docs/ERRORS.md`, repo `CLAUDE.md`.

---

### Task 1: `current_page()`

**Files:**
- Modify: `src/phone_harness/helpers.py`
- Test: `tests/test_helpers.py`

**Interfaces:**
- Consumes: `helpers.ui_tree()`, `helpers._invalidate_tree()`.
- Produces: `current_page() -> dict | None` returning
  `{"index": int, "total": int, "zone": str}` with `zone` one of `"today"`,
  `"home"`, `"app_library"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_helpers.py`:

```python
def _tree_with_page(value):
    """Minimal tree carrying a PageIndicator, shaped like the real one."""
    node = {"type": "PageIndicator", "name": "Page control", "children": []}
    if value is not None:
        node["value"] = value
    return {"type": "Application", "children": [
        {"type": "Other", "children": [node]},
    ]}


class PageClient:
    """Serves one fixed tree; enough for read-only page tests."""

    def __init__(self, tree):
        self.tree = tree

    def source(self):
        return self.tree


def _use_tree(monkeypatch, tree):
    helpers._invalidate_tree()
    monkeypatch.setattr(helpers, "_client", PageClient(tree))


def test_current_page_reads_home_page(monkeypatch):
    _use_tree(monkeypatch, _tree_with_page("Page 4 of 8"))
    assert helpers.current_page() == {"index": 4, "total": 8, "zone": "home"}


def test_current_page_calls_today_view_page_zero(monkeypatch):
    _use_tree(monkeypatch, _tree_with_page("Page 0 of 8"))
    assert helpers.current_page()["zone"] == "today"


def test_current_page_calls_app_library_past_the_end(monkeypatch):
    _use_tree(monkeypatch, _tree_with_page("Page 9 of 8"))
    assert helpers.current_page()["zone"] == "app_library"


def test_current_page_none_when_an_app_is_open(monkeypatch):
    _use_tree(monkeypatch, {"type": "Application", "children": []})
    assert helpers.current_page() is None


def test_current_page_none_on_unparseable_value(monkeypatch):
    # An iOS update could change the string. Fail loudly, never guess.
    _use_tree(monkeypatch, _tree_with_page("Seite 4 von 8"))
    assert helpers.current_page() is None


def test_current_page_none_when_value_missing(monkeypatch):
    _use_tree(monkeypatch, _tree_with_page(None))
    assert helpers.current_page() is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_helpers.py -k current_page -q`
Expected: FAIL, `AttributeError: module 'phone_harness.helpers' has no attribute 'current_page'`

- [ ] **Step 3: Implement**

Add near `press_home` in `src/phone_harness/helpers.py`, and add `re` to the
imports if it is not already there:

```python
_PAGE_VALUE = re.compile(r"Page (\d+) of (\d+)")


def current_page() -> dict | None:
    """Where the Home Screen is: {"index", "total", "zone"} or None.

    Reads the PageIndicator's `value` ("Page 4 of 8"). index 0 is Today View,
    1..total are real Home Screen pages, and total+1 is the App Library. iOS
    itself numbers Today View 0.

    Returns None when no PageIndicator is on screen (an app is open) or when the
    value does not parse — an iOS wording change must fail loudly, not guess.

    NOTE: ocr() cannot see this. collect_texts prefers `label`, which is null on
    this element, so it falls back to `name` ("Page control") and drops `value`.
    """
    node = _find_page_indicator(ui_tree())
    if node is None:
        return None
    m = _PAGE_VALUE.search(str(node.get("value") or ""))
    if not m:
        return None
    index, total = int(m.group(1)), int(m.group(2))
    if index <= 0:
        zone = "today"
    elif index > total:
        zone = "app_library"
    else:
        zone = "home"
    return {"index": index, "total": total, "zone": zone}


def _find_page_indicator(node) -> dict | None:
    if isinstance(node, dict):
        if node.get("type") == "PageIndicator":
            return node
        for child in node.get("children") or []:
            hit = _find_page_indicator(child)
            if hit is not None:
                return hit
    elif isinstance(node, list):
        for child in node:
            hit = _find_page_indicator(child)
            if hit is not None:
                return hit
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_helpers.py -k current_page -q`
Expected: PASS, 6 tests.

- [ ] **Step 5: Export it**

In `helpers.py` add `"current_page"` to `__all__`. In `mcp_server.py` add
`"current_page"` to `_TOOLS`.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests -q`
Expected: PASS. `test_mcp_server.py` asserts `_TOOLS` names resolve, so a typo
fails here.

- [ ] **Step 7: Commit**

```bash
git add src/phone_harness/helpers.py src/phone_harness/mcp_server.py tests/test_helpers.py
git commit -F - <<'MSG'
helpers: read exact Home Screen position from the PageIndicator

Its value carries "Page 4 of 8", with Today View at 0 and App Library at
total+1. ocr() cannot see it: collect_texts prefers a label that is null on this
element, so it falls back to the name and drops the value.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0118uEKTq4hrQfSbweXedppT
MSG
```

---

### Task 2: `goto_home_page()`

**Files:**
- Modify: `src/phone_harness/helpers.py`
- Test: `tests/test_helpers.py`

**Interfaces:**
- Consumes: `current_page()`, `helpers.swipe()`, `helpers.press_home()`.
- Produces: `goto_home_page(n: int = 1) -> None`. Raises `ValueError` for `n`
  outside `1..total`, `RuntimeError` when the walk cannot reach `n`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_helpers.py`:

```python
class PagingClient:
    """Simulates paging: a left-to-right swipe moves toward page 1."""

    def __init__(self, index, total=8, stuck=False):
        self.index, self.total, self.stuck = index, total, stuck
        self.swipes = []

    def source(self):
        return _tree_with_page(f"Page {self.index} of {self.total}")

    def swipe(self, x1, y1, x2, y2, seconds=0.3):
        self.swipes.append("toward" if x2 > x1 else "away")
        if not self.stuck:
            self.index += -1 if x2 > x1 else 1

    def home(self):
        pass


def _paging(monkeypatch, index, total=8, stuck=False):
    helpers._invalidate_tree()
    stub = PagingClient(index, total, stuck)
    monkeypatch.setattr(helpers, "_client", stub)
    monkeypatch.setattr(helpers.time, "sleep", lambda _s: None)
    return stub


def test_goto_home_page_walks_from_page_four(monkeypatch):
    stub = _paging(monkeypatch, 4)
    helpers.goto_home_page(1)
    assert stub.swipes == ["toward"] * 3
    assert stub.index == 1


def test_goto_home_page_walks_from_the_last_page(monkeypatch):
    stub = _paging(monkeypatch, 8)
    helpers.goto_home_page(1)
    assert stub.swipes == ["toward"] * 7


def test_goto_home_page_swipes_away_from_today_view(monkeypatch):
    stub = _paging(monkeypatch, 0)
    helpers.goto_home_page(1)
    assert stub.swipes == ["away"]
    assert stub.index == 1


def test_goto_home_page_walks_back_from_app_library(monkeypatch):
    stub = _paging(monkeypatch, 9)
    helpers.goto_home_page(1)
    assert stub.swipes == ["toward"] * 8


def test_goto_home_page_is_a_noop_when_already_there(monkeypatch):
    stub = _paging(monkeypatch, 1)
    helpers.goto_home_page(1)
    assert stub.swipes == []


def test_goto_home_page_rejects_a_target_off_the_home_screen(monkeypatch):
    _paging(monkeypatch, 4)
    with pytest.raises(ValueError):
        helpers.goto_home_page(0)
    with pytest.raises(ValueError):
        helpers.goto_home_page(9)


def test_goto_home_page_raises_when_the_walk_never_lands(monkeypatch):
    # A silent partial walk is the failure class this harness keeps producing.
    stub = _paging(monkeypatch, 4, stuck=True)
    with pytest.raises(RuntimeError) as err:
        helpers.goto_home_page(1)
    assert "4" in str(err.value)
    assert stub.swipes  # it tried, including one corrective pass


def test_goto_home_page_leaves_an_open_app_first(monkeypatch):
    helpers._invalidate_tree()

    class AppThenHome:
        def __init__(self):
            self.homed = False
            self.swipes = []

        def source(self):
            if not self.homed:
                return {"type": "Application", "children": []}
            return _tree_with_page("Page 1 of 8")

        def home(self):
            self.homed = True

        def swipe(self, *_a, **_k):
            self.swipes.append("x")

    stub = AppThenHome()
    monkeypatch.setattr(helpers, "_client", stub)
    monkeypatch.setattr(helpers.time, "sleep", lambda _s: None)
    helpers.goto_home_page(1)
    assert stub.homed
    assert stub.swipes == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_helpers.py -k goto_home_page -q`
Expected: FAIL, no attribute `goto_home_page`.

- [ ] **Step 3: Implement**

Add below `current_page()` in `helpers.py`:

```python
_PAGE_SETTLE = 0.55


def goto_home_page(n: int = 1) -> None:
    """Land on Home Screen page `n`, from any page, Today View, App Library or
    an open app.

    press_home() cannot do this: /wda/homescreen only exits an app to the
    springboard, and from page 4 it is a no-op (verified on device).
    """
    page = current_page()
    if page is None:  # an app is open
        press_home()
        time.sleep(_PAGE_SETTLE)
        page = current_page()
        if page is None:
            raise RuntimeError("no PageIndicator after press_home; not on the Home Screen")
    if not 1 <= n <= page["total"]:
        raise ValueError(
            f"page {n} is not a Home Screen page (1..{page['total']}); "
            "Today View is 0 and App Library is past the end"
        )
    for _ in range(2):  # walk, verify, then one corrective pass
        delta = page["index"] - n
        for _ in range(abs(delta)):
            if delta > 0:
                swipe(40, 500, 400, 500, 0.25)  # left->right: toward page 1
            else:
                swipe(400, 500, 40, 500, 0.25)
            time.sleep(_PAGE_SETTLE)
        page = current_page()
        if page is None:
            raise RuntimeError("lost the PageIndicator mid-walk")
        if page["index"] == n:
            return
    raise RuntimeError(f"wanted page {n}, still on page {page['index']}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_helpers.py -k goto_home_page -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Export it**

Add `"goto_home_page"` to `__all__` in `helpers.py` and to `_TOOLS` in
`mcp_server.py`.

- [ ] **Step 6: Fix the false docstring**

In `helpers.py`, `press_home()`'s docstring currently claims it returns to the
first Home Screen page. Replace that paragraph with:

```python
    """Go to the Home Screen, as if the physical Home gesture were used.

    Leaves whatever app was open. It does NOT change which Home Screen page you
    are on: /wda/homescreen is a no-op once you are already on the Home Screen
    (verified on device — two calls from page 4 both stayed on page 4). Use
    goto_home_page() to reach a specific page.
    """
```

- [ ] **Step 7: Run the full suite and commit**

Run: `python -m pytest tests -q`

```bash
git add src/phone_harness/helpers.py src/phone_harness/mcp_server.py tests/test_helpers.py
git commit -F - <<'MSG'
helpers: goto_home_page walks to an exact page and verifies it landed

press_home cannot do this. /wda/homescreen only exits an app to the springboard;
from page 4 two consecutive calls both stayed on page 4, so the docstring
claiming it returns to the first page was wrong and is corrected here.

The walk reads position once, swipes exactly that many times, then re-reads. A
partial walk raises instead of silently leaving you two pages short.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0118uEKTq4hrQfSbweXedppT
MSG
```

---

### Task 3: Viewer endpoints

**Files:**
- Modify: `src/phone_harness/viewer.py:451-472`
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: `helpers.goto_home_page`, `client.long_press(x, y, seconds)`.
- Produces: `POST /api/long_press {x, y, seconds?}`; `/api/home` now walks.

- [ ] **Step 1: Write the failing tests**

In `tests/test_viewer.py`, add a `long_press` recorder to `StubClient`:

```python
    def long_press(self, x, y, seconds):
        self.calls.append(("long_press", x, y, seconds))
```

Then add:

```python
def test_long_press_passes_point_and_duration(base_url, stub):
    r = requests.post(base_url + "/api/long_press",
                      json={"x": 100, "y": 200, "seconds": 0.8}, timeout=5)
    assert r.status_code == 200
    assert ("long_press", 100.0, 200.0, 0.8) in stub.calls


def test_long_press_clamps_duration(base_url, stub):
    requests.post(base_url + "/api/long_press",
                  json={"x": 1, "y": 2, "seconds": 99}, timeout=5)
    assert stub.calls[-1][3] == 3.0


def test_home_walks_to_page_one(base_url, monkeypatch):
    seen = []
    monkeypatch.setattr(viewer.helpers, "goto_home_page",
                        lambda n=1: seen.append(n))
    r = requests.post(base_url + "/api/home", json={}, timeout=5)
    assert r.status_code == 200
    assert seen == [1]
```

Match the existing fixtures: if `test_viewer.py` exposes the stub through the
`base_url` fixture under a different name, reuse that name rather than adding a
`stub` fixture.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_viewer.py -k "long_press or home_walks" -q`
Expected: FAIL — 404 for `/api/long_press`, and `/api/home` still calls
`client.home()`.

- [ ] **Step 3: Implement**

In `viewer.py`, add `from . import helpers` at module scope if it is not already
imported there, then replace the `/api/home` branch and add the new one:

```python
            elif path == "/api/long_press":
                with _action_slot():
                    self.client.long_press(
                        float(payload["x"]),
                        float(payload["y"]),
                        min(max(float(payload.get("seconds", 0.8)), 0.2), 3.0),
                    )
                self._json({"ok": True})
            elif path == "/api/home":
                with _action_slot():
                    helpers.goto_home_page(1)
                self._json({"ok": True})
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_viewer.py -q`
Expected: PASS. Existing `/api/home` tests that assert `"home" in stub.calls`
will now fail — update them to assert the walk instead; that is the intended
behaviour change, not a regression to paper over.

- [ ] **Step 5: Commit**

```bash
git add src/phone_harness/viewer.py tests/test_viewer.py
git commit -F - <<'MSG'
viewer: long-press endpoint, and Home now reaches page 1

Home called /wda/homescreen, which does nothing once you are already on the
Home Screen, so the button left you wherever you were. It now walks.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0118uEKTq4hrQfSbweXedppT
MSG
```

---

### Task 4: Viewer gesture

**Files:**
- Modify: `src/phone_harness/viewer.html:817-856` (pointer handlers) and the
  `btn-home` binding at `:926`

**Interfaces:**
- Consumes: `POST /api/long_press`, `withBusy`, `showDot`, `showHint`.
- Produces: no new JS exports; behaviour only.

- [ ] **Step 1: Hoist the shared pointer helpers**

`fire()` runs while the pointer is still down, but the rect lookup, `toPt` and
the 409-aware `post` currently live inside the `pointerup` handler. Move all
three to the shared scope beside `let drag = null;` so both handlers use them.
`toPt` must take the rect as an argument or read it fresh, since the pane can be
resized between events.

- [ ] **Step 2: Add the constants and the timer**

```js
const LONG_PRESS_MS = 400;        // local hold before we send it
const LONG_PRESS_SECONDS = 0.8;   // how long the phone itself holds
```

In `pointerdown`, after the existing guards and `setPointerCapture`:

```js
  drag = { x: ev.clientX, y: ev.clientY, t: Date.now(), timer: null, fired: false };
  if (inputEnabled && !phoneBusy()) {
    showRing(ev.clientX - r.left, ev.clientY - r.top);
    drag.timer = setTimeout(() => fireLongPress(ev.clientX, ev.clientY), LONG_PRESS_MS);
  }
```

- [ ] **Step 3: Add move, cancel, and fire**

```js
function cancelHold() {
  if (drag && drag.timer) { clearTimeout(drag.timer); drag.timer = null; }
  killRing();
}

screen.addEventListener('pointermove', (ev) => {
  if (!drag || drag.fired) return;
  if (Math.hypot(ev.clientX - drag.x, ev.clientY - drag.y) >= 8) cancelHold();
});

screen.addEventListener('pointercancel', () => { cancelHold(); drag = null; });

async function fireLongPress(cx, cy) {
  if (!drag) return;
  drag.timer = null;
  drag.fired = true;
  completeRing();
  const r = screen.getBoundingClientRect();
  const p = toPt(cx, cy, r);
  await post('/api/long_press', { x: p.x, y: p.y, seconds: LONG_PRESS_SECONDS });
}
```

In `pointerup`, before anything else:

```js
  cancelHold();
  if (drag && drag.fired) { drag = null; return; }   // already sent; send nothing more
```

- [ ] **Step 4: Add the ring**

A circle drawn at the press point that fills over `LONG_PRESS_MS`, using the
same overlay layer `showDot` already uses. Under
`@media (prefers-reduced-motion: reduce)` do not animate — render a static ring
that switches to the completed colour in `completeRing()`.

- [ ] **Step 5: Move Home to withBusy**

Replace the binding at `viewer.html:926`:

```js
document.getElementById('btn-home').onclick = () =>
  withBusy('GOING HOME', () => gesturePost('/api/home'));
```

Required, not cosmetic: the walk holds `_ACTION_LOCK` for seconds while
`_ACTION_WAIT` is 2.0s, so without the busy label every click during it is
dropped with a 409 — the unlabelled-freeze condition behind the logged runaway
click burst.

- [ ] **Step 6: Verify in the real viewer**

Start it: `python launch.py`, open http://127.0.0.1:8770.

- Quick click still taps.
- Press and hold on an app icon: the ring fills, the iOS context menu opens.
- Press, then drag before the ring fills: the page swipes, no menu.
- Home from page 4: `GOING HOME` shows, phone lands on page 1.
- Clicks during the walk are visibly ignored, not queued.

- [ ] **Step 7: Commit**

```bash
git add src/phone_harness/viewer.html
git commit -F - <<'MSG'
viewer: hold to long-press, with a ring that shows it registered

A stationary hold used to fire a plain tap whatever its duration, so a human
could not open a context menu or reach Edit Home Screen. The press now fires on
a local timer while the finger is still down, so the menu appears during the
hold rather than after it.

Home moves behind a busy label because the page walk holds the phone for
seconds and unlabelled freezes are what make people click again.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0118uEKTq4hrQfSbweXedppT
MSG
```

---

### Task 5: Documentation

**Files:**
- Modify: `skills/phone-gotchas/SKILL.md`, `docs/ERRORS.md`, `CLAUDE.md`
- Copy: `skills/phone-gotchas/SKILL.md` → `~/.claude/skills/phone-gotchas/SKILL.md`

- [ ] **Step 1: Replace the marker guidance in phone-gotchas**

The `left-of-home-scroll-view` row works but is strictly worse. Replace it with
the `PageIndicator` method: one read gives index, total and zone. State plainly
that `ocr()` cannot see the value, and why. Keep the off-by-one row — it is still
the consequence that matters.

- [ ] **Step 2: Rewrite the page-survey snippet**

Replace the dedup walk with:

```python
p = current_page()                       # {"index", "total", "zone"} or None
goto_home_page(1)
for i in range(1, p["total"] + 1):
    body = [(e["text"], round(e["x"]), round(e["y"]))
            for e in R(lambda: ocr()) if e["type"] == "Icon" and e["y"] <= 820]
    ...
    if i < p["total"]:
        sw(400, 500, 40, 500, 0.25)      # away from page 1
```

No dedup, no end-stop confusion, and the page count is known up front.

- [ ] **Step 3: Correct ERRORS.md**

In the 2026-08-11 entry, replace the `left-of-home-scroll-view` discriminator
with the `PageIndicator` value, and record that `press_home` is a no-op between
pages. Keep the cross-page drag findings unchanged.

- [ ] **Step 4: Update repo CLAUDE.md**

Extend the `viewer.py` bullet: hold-to-long-press with the ring, and Home walking
to page 1 behind a busy label. Extend the `helpers.py` bullet with
`current_page()`/`goto_home_page()` and the reason `ocr()` cannot see the page
value.

- [ ] **Step 5: Re-copy and verify**

```bash
cp skills/phone-gotchas/SKILL.md /c/Users/sandm/.claude/skills/phone-gotchas/SKILL.md
diff -q skills/phone-gotchas/SKILL.md /c/Users/sandm/.claude/skills/phone-gotchas/SKILL.md
grep -rn "Grok\|DeepSeek" skills/ docs/    # must return nothing
```

- [ ] **Step 6: Commit**

```bash
git add skills/ docs/ERRORS.md CLAUDE.md
git commit -F - <<'MSG'
docs: the PageIndicator replaces the scroll-view marker

One read gives index, total and which of the three zones you are in, so the page
survey no longer has to dedup its way to an end stop and guess which end it hit.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0118uEKTq4hrQfSbweXedppT
MSG
```

---

### Task 6: On-device acceptance

**Files:** none — verification only.

- [ ] **Step 1: Confirm the helper against the real phone**

```bash
cd C:/Projects/phone-claude && ./phone-harness.cmd <<'PY'
import time
def R(fn, n=6, wait=5):
    for i in range(n):
        try: return fn()
        except Exception:
            if i == n-1: raise
            time.sleep(wait)
for start in (4, 8):
    R(lambda: goto_home_page(1))
    for _ in range(start - 1):
        R(lambda: swipe(400,500,40,500,0.25)); time.sleep(0.6)
    print("from", start, "->", R(lambda: goto_home_page(1)) or R(lambda: current_page()))
PY
```

Expected: `{"index": 1, "total": 8, "zone": "home"}` both times.

- [ ] **Step 2: Confirm from Today View and App Library**

Swipe to each, call `goto_home_page(1)`, assert `current_page()["index"] == 1`.

- [ ] **Step 3: Confirm from inside an app**

`open_app("settings")`, then `goto_home_page(1)`, assert index 1.

- [ ] **Step 4: Run the whole suite one more time**

Run: `python -m pytest tests -q`

- [ ] **Step 5: Push**

```bash
git push -u origin viewer-longpress-home
```

## Self-Review

**Spec coverage.** `current_page` → Task 1. `goto_home_page` → Task 2.
`/api/long_press` and `/api/home` → Task 3. Viewer gesture, ring, hoisting,
`withBusy` → Task 4. Docs including the `press_home` docstring (Task 2, Step 6)
→ Tasks 2 and 5. Testing → per-task plus Task 6. Every spec risk is either a
tunable constant (`LONG_PRESS_MS`, `LONG_PRESS_SECONDS`) or a loud failure
(unparseable value → `None`, partial walk → `RuntimeError`).

**Placeholders.** None. Every code step carries the code. Task 4's ring is
described rather than written because it depends on the existing overlay markup
`showDot` uses, which the implementer will have in front of them; the behaviour
it must have is fully specified.

**Type consistency.** `current_page()` returns the same three-key dict in Tasks
1, 2, 3 and 6. `goto_home_page(n=1)` keeps one signature throughout.
`LONG_PRESS_SECONDS` (0.8) matches the endpoint default in Task 3. The swipe
direction is `40,500 → 400,500` = toward page 1 in every task, matching the
device runs that produced it.
