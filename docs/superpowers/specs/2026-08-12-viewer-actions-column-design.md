# Viewer Actions column: whole-row chips, Go to page, Gestures, Thread

Date: 2026-08-12
Status: approved, not yet implemented

## Problem

Two things, one column (`#col-actions` in `viewer.html`).

1. **A chip row is cut in half.** `.chips` carries `max-height:150px;
   overflow:hidden`. The app chips wrap to five rows on a 20-app phone, and
   150px cuts row five through the middle. On the reporter's screen the
   `Music` chip shows its top half and nothing says an app was dropped. This
   breaks the column's own overflow policy, which is *hard caps plus
   degrade-to-fit, never a bare clip*.
2. **The column is ~780px of dead space.** `#col-actions` spans both grid rows
   (`grid-template-areas` line 209-210), so it is the full height of the
   dashboard, but it holds only two short blocks. It is the most valuable
   space on the page and it shows nothing.

## Findings that drove this design

Read from the source, not from docs. Do not re-derive.

- `helpers.read_messages(contact, limit=20)` exists, returns
  `[{'text', 'from_me'}, ...]` oldest first, and **no human surface calls it**.
  The viewer can send a message and cannot read one back.
- `helpers.current_page()` costs 0.37s and `goto_home_page(n)` costs ~2.5s from
  page 3 (both measured 2026-08-12, previous commit).
- `/api/phone` already polls every 10s, already reads `active_app()`, and
  already serves `_LAST_PHONE` while `_ACTION_LOCK` is held. A page read rides
  that call for free rather than adding a poller.
- The edge-gesture buttons (`btn-notifs`, `btn-control`) are thin
  `POST /api/swipe` wrappers over `edgeSwipe()` (viewer.html:1205-1212). New
  gestures need no Python at all.
- `withBusy` must NOT wrap `gesturePost`. `gesturePost` opens with a
  `phoneBusy()` guard, so the nested call refuses itself and sends nothing.
  This shipped once already (2026-08-12, Home button) and a test scans for it.

## Design

### 1. Whole-row chip clamp

Delete `max-height:150px` from `.chips`. Clamp at render instead:

- Measure the real row height from the second row's `offsetTop`.
- Budget = 30% of the column's client height, floor 62px (one row).
- Keep `floor(budget / rowHeight)` whole rows. Remove every chip at or below
  the first dropped row's `offsetTop`.
- If any were dropped, show `+N more apps ▸`, which opens the full list in the
  existing overlay shell.

Re-runs on resize and whenever the app list changes. A chip is never half
visible, and a dropped chip is always counted out loud.

### 2. Column order

`Text someone` · `Open app` · `Go to page` · `Gestures` · `Thread`.

The first four are `flex:0 0 auto` and stay small. `Thread` is the only
`flex:1 1 auto` block, so it absorbs all free height. It goes last because the
free height is at the bottom; putting it mid-column would leave a visible gap
between two dense blocks.

### 3. Go to page

- Chips: `Today · 1 … N · Library`, current highlighted.
- `GET /api/phone` gains `page` and `pages`, computed **only** when the front
  app is springboard, and `null` otherwise. Same `_LAST_PHONE` cache while the
  action lock is held.
- `POST /api/page {index}` calls `helpers.goto_home_page(index)` under
  `_ACTION_LOCK`, behind a busy label.
- Index follows iOS: `0` is Today View, `1..N` are real pages, `N+1` is App
  Library.

### 4. Gestures

Four buttons, all `edgeSwipe()` calls, no new endpoint. Every row measured on
device 2026-08-12:

| Button | Gesture | Result |
|---|---|---|
| ← Back | left edge → 91% width, 0.6s | works |
| Search | 26% → 73% height, 0.5s | works (Spotlight) |
| Scroll ↑ | 35% → 75% height, 0.3s | works |
| Scroll ↓ | 75% → 35% height, 0.3s | works |

**A system gesture that is too short or too fast is swallowed silently.** Back
at 45% width / 0.25s did nothing from Settings > General. The identical gesture
at 91% width / 0.6s popped the screen every time. The numbers are measurements,
not preferences.

**A gesture button hands focus back to the document.** Keys reach the phone
only while nothing focusable holds focus — the window `keydown` handler returns
early on `input,textarea,button,select,[tabindex]`. A clicked button keeps
focus, so Search opened Spotlight and then swallowed the search string, and the
obvious repair (click the screen) sends a TAP that closes Spotlight. The
handler calls `blur()` only when `ev.detail > 0`: that value is 0 for
Enter/Space on a focused button, and blurring there would throw a keyboard user
out of the tab order. Search also says so once, in the hint line.

**Switcher was cut.** Tried twice on device (bottom edge → 55% height over
0.9s, then → 45% over 1.4s): the phone did not move either time. The app
switcher needs a swipe that HOLDS before it lifts, and `swipe()` is
press-move-release with nothing in between, so duration cannot substitute for
the hold. It needs a press/pause/release action chain in `wda_client` first,
which is its own change.

### 5. Thread

- Header: `THREAD` plus a `Read <contact>` button. Contact comes from the
  existing `#text-to` field, so there is no second input.
- `POST /api/read-thread {contact, limit}` calls `helpers.read_messages` under
  `_ACTION_LOCK`, returns `{messages:[{text, from_me}]}`.
- Click to run, never a poll. It drives the phone: it opens Messages, searches
  for the contact and opens the thread. The card says so.
- Runs behind a `withBusy('reading')` label, because it holds the phone for
  about 10s and an unlabelled freeze is what makes a human click again.
- Bubbles degrade to fit, oldest dropped first, with `↑ N older not shown`.
  Same policy as the checks panel and the sends feed.
- Read only. Replies use the Send box above. A bubble is not a control.

## Non-goals

- No live thread polling. Every read costs the phone ~10s.
- No reply-from-bubble. Send stays in one place, behind the approval gate.
- No new dependency. Everything here is `requests` plus what already ships.
- The `Screen text, click to tap` panel was considered and cut. It duplicates
  what the phone pane already does with a real tap.

## Risks

- **Switcher gesture may not work** (see above). Tested before ship.
- **Page read adds WDA work.** +0.37s per 10s poll, but only on the Home
  Screen, and only when the action lock is free.
- **`read_messages` leaves the phone in the thread.** That is a real side
  effect of a read. The card states it rather than hiding it.
