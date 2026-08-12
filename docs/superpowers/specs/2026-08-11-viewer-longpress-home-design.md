# Viewer long-press gesture + Home button that lands on page 1

Date: 2026-08-11
Status: approved, not yet implemented

## Problem

Two gaps in the human surface (`viewer.py` + `viewer.html`):

1. **No long press.** The viewer supports click-to-tap and drag-to-swipe. A
   stationary hold of any duration fires a plain tap, so a human driving the
   phone cannot open an app's context menu or reach jiggle / Edit Home Screen
   mode. Agents have `long_press()`; the human does not.
2. **Home does not go home.** The Home button calls `client.home()`
   (`POST /wda/homescreen`). From Home Screen page 4 that is a **no-op** — the
   phone stays on page 4.

## Findings that drove this design

All verified on device on 2026-08-11. These replace guesswork; do not re-derive.

- **`press_home()` does not change Home Screen pages.** From page 4, two
  consecutive `press_home()` calls both left the phone on page 4.
  `/wda/homescreen` only exits an app to the springboard. The docstring at
  `helpers.py:383` claims it "Returns to the first Home Screen page" — that claim
  is false and is corrected as part of this work.
- **The `PageIndicator` element carries exact position in its `value`.** Read
  from the raw tree:

  | Position | `PageIndicator.value` |
  |---|---|
  | Today View | `Page 0 of 8` |
  | Home Screen page 4 | `Page 4 of 8` |
  | App Library | `Page 9 of 8` |

  So index `0` is Today View, `1..total` are real Home Screen pages, and
  `total+1` is App Library. iOS itself calls Today View page 0.
- **`ocr()` hides that value.** `collect_texts` prefers `label`, which is `null`
  on this element, so it falls back to `name` (`"Page control"`) and `value`
  never reaches the flat list. Any consumer must read `ui_tree()`.
- **Hold-in-place is unclaimed.** `viewer.html:846` branches at `pointerup` on
  `moved < 8` → tap, else swipe. Hold duration currently only sets swipe speed,
  so binding a stationary hold to long-press conflicts with nothing.

## Scope

**In:** the two features above, plus the `current_page()` primitive Home needs,
plus correcting the docs written earlier the same day.

**Out:** a live "Page 4 of 8" readout in the viewer; a one-click Jiggle button
(long-press then clicking the menu row already works in two clicks). Both were
considered and explicitly declined.

## Design

### 1. `helpers.current_page(c=None) -> dict | None`

Returns `{"index": int, "total": int, "zone": str}` where `zone` is `"today"`
(index 0), `"home"` (1..total) or `"app_library"` (total+1). Returns `None` when
no `PageIndicator` is on screen, which means an app is open.

Walks `ui_tree()` for the first node with `type == "PageIndicator"` and parses
its `value` with `Page (\d+) of (\d+)`. A present indicator whose value does not
match that pattern returns `None` rather than guessing.

Takes an optional client so the viewer can pass its own, matching how
`/api/unlock` passes its client into `helpers.unlock(c)`. Added to `__all__` and
to `mcp_server._TOOLS` (it is MCP-safe: a pure read).

### 2. `helpers.goto_home_page(n=1, c=None)`

```
p = current_page(c)
if p is None:                      # an app is open
    press_home()
    p = current_page(c)
    if p is None: raise
delta = p["index"] - n
swipe toward page 1 |delta| times  (or away from it when delta < 0)
p2 = current_page(c)               # verify
if p2["index"] != n: one corrective pass, then raise naming the real page
```

Direction reference, so nobody re-derives it: `swipe(40, 500, 400, 500)` moves the
finger left→right and goes **toward page 1**; `swipe(400, 500, 40, 500)` goes away
from it.

Cost: page 4 → 3 swipes ≈ 2.4s. Page 8 → 7 ≈ 5.6s. Today View → 1 swipe away
from page 1. App Library → 8 swipes. Index already `n` → zero swipes.

`n` outside `1..total` raises `ValueError` — Today View and App Library are
reachable positions but are not Home Screen pages, so they are not valid targets.

No internal retry, consistent with the other helpers — but the post-walk verify
is mandatory, because a partial walk that silently leaves you on page 3 is
exactly the failure class this harness keeps producing. Agents wrap the call in
their own `R()`.

Added to `__all__` and to `mcp_server._TOOLS`.

### 3. `/api/long_press` (viewer.py)

Mirrors `/api/tap`, inside `_action_slot()`:

```python
elif path == "/api/long_press":
    with _action_slot():
        self.client.long_press(
            float(payload["x"]), float(payload["y"]),
            min(max(float(payload.get("seconds", 0.8)), 0.2), 3.0),
        )
    self._json({"ok": True})
```

The clamp mirrors the one `/api/swipe` already applies to its `seconds`.

### 4. `/api/home` (viewer.py)

Changes from `self.client.home()` to
`helpers.goto_home_page(1, c=self.client)`, still inside `_action_slot()`.

### 5. Viewer gesture (viewer.html)

Constants: `LONG_PRESS_MS = 400`, `LONG_PRESS_SECONDS = 0.8`.

- `pointerdown` — existing guards (`points`, `inputEnabled`, `phoneBusy()`);
  capture the pointer; start the ring animation; `setTimeout(fire, LONG_PRESS_MS)`.
- `pointermove` — new listener. Once movement exceeds the existing 8px threshold,
  clear the timer and kill the ring: this is a drag.
- `fire()` — set `drag.fired`, complete the ring, POST `/api/long_press` through
  the same helper that already handles a 409 with `showHint`.
- `pointerup` — if `drag.fired`, consume it and send nothing further; otherwise
  run today's tap/swipe branch unchanged.
- `pointercancel` — clear timer and ring.

Implementation note that will otherwise bite: the rect lookup, the `toPt`
converter and the 409-aware `post` helper currently live **inside** the
`pointerup` handler. `fire()` runs while the pointer is still down, so all three
must be hoisted to the shared scope before this works.

The ring is drawn at the press point and animates over `LONG_PRESS_MS`. Under
`prefers-reduced-motion` it is a static dot that changes colour at the threshold
instead of animating.

The Home button moves from `gesturePost('/api/home')` to
`withBusy('GOING HOME', …)`. This is required, not cosmetic: the walk holds
`_ACTION_LOCK` for seconds while `_ACTION_WAIT` is 2.0s, so without a busy label
every click during it is dropped with a 409 — the unlabelled-freeze condition
that produced the logged runaway click burst.

## Error handling

- `/api/long_press` 409 → existing `showHint` path, unchanged.
- `current_page()` returning `None` twice → raise rather than swipe blind.
- Wrong page after the walk → one corrective pass, then raise naming the actual
  page, surfaced by `withBusy`'s catch.
- WDA dropping mid-walk → propagates to the viewer's existing error path.

## Testing

Unit tests, must pass with no phone attached (repo rule):

- `current_page()` parses `Page 4 of 8` → `(4, 8, "home")`, `Page 0 of 8` →
  `zone == "today"`, `Page 9 of 8` → `zone == "app_library"`; returns `None` when
  the tree has no `PageIndicator`, and when its `value` does not match the
  pattern.
- `goto_home_page()` against a fake client asserts swipe counts and directions
  from index 4 (3 toward), 8 (7 toward), 0 (1 away), 9 (8 toward) and 1 (zero).
- `goto_home_page()` raises when the verifying read reports the wrong page.

On device:

- Long-press in the viewer opens an icon's context menu; the ring completes at
  the threshold; a drag started from a hold still swipes and sends no long press.
- Home lands on page 1 from page 4, page 8, Today View, App Library, and from
  inside an open app.

Rendered proof per the Definition of Done: drive the real viewer and confirm the
ring draws and the menu opens. Tests proving the endpoint responds are not
sufficient.

## Docs changed in the same change

- `skills/phone-gotchas/SKILL.md` — replace the `left-of-home-scroll-view`
  guidance with the `PageIndicator` method; rewrite the page-survey snippet to
  read position directly instead of the dedup walk; state that `ocr()` hides
  `value`. Re-copy to `~/.claude/skills/`.
- `docs/ERRORS.md` — correct the 2026-08-11 entry to the page-indicator method.
- `helpers.py:383` — fix the `press_home` docstring that claims it returns to the
  first page.
- Repo `CLAUDE.md` — extend the `viewer.py` bullet with the long-press gesture
  and the new Home behaviour.

No new environment variables, so `.env.example` is untouched.

## Risks

- **`LONG_PRESS_MS = 400` is a guess.** iOS uses ~500ms; a deliberate click is
  under 200ms; the viewer adds round-trip latency. It is a constant, tuned after
  the first real use.
- **Total lag to the menu is ~1.2s** (400ms local + 0.8s device press). The ring
  completing at 400ms is the signal that it registered. If it feels sluggish, cut
  `LONG_PRESS_SECONDS` before touching the threshold.
- **`goto_home_page` from page 8 holds the phone ~6s.** Mitigated by the busy
  label, not eliminated.
- **The page-indicator format is an iOS accessibility string.** An iOS update
  could change it. The parse returns `None` on a non-matching value rather than
  guessing, so the failure is loud.
