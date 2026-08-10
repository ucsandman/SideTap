# Viewer right-panel redesign — design

Date: 2026-08-10. Approved approach: big bang (one branch, all sections land together).

## Problem

The viewer's right panel (`#side` in `viewer.html`) spends almost all of its
space on 10 doctor check cards that usually all say PASS. The Activity feed and
Recent sends are pushed below the fold, and the panel does nothing active for
the user.

## Goals

- Healthy state costs one line, not ten cards.
- The freed space becomes a dashboard: quick actions, agent monitoring,
  passive phone info, and a safe console.
- No new runtime dependencies. All new POST endpoints origin-guarded, action
  POSTs serialized through `_ACTION_LOCK`, STOP kill switch applies.

## Layout

```
┌───────────────────────────────┐
│ ● All checks pass  🔋78 🔓 Safari │  status strip (always visible)
├───────────────────────────────┤
│ [Actions] [Agent] [Phone] [>_]│  tab bar (Actions = default)
├───────────────────────────────┤
│  active tab content           │
└───────────────────────────────┘
```

## Components

### 1. Status strip

- One line: green dot + "All checks pass", or red dot + "N checks failing".
  Right side: battery %, lock icon, current app name.
- Click toggles the full check-card list (today's cards, unchanged markup).
  Any FAIL auto-expands the list. The fix-input panel keeps its current
  show/hide logic inside this expansion.
- Data sources are passive GETs only: doctor results from the existing
  `/api/doctor` poll; battery/lock/current-app via a new passive read in the
  viewer backend (WDA status endpoints; no gestures, no session creation).
- `/wda/locked` is DISPLAY-ONLY. It can report unlocked with the passcode pad
  on screen; nothing acts on it (the `unlock()` rule in CLAUDE.md is untouched).

### 2. Actions tab (default)

- "Text someone": contact chips + message box + Send.
  - Chips are learned from the Recent sends history; a pin icon keeps
    favorites. Pins live in browser localStorage — nothing new on disk.
  - Send calls new `POST /api/text {to, message}` → `helpers.send_message`.
- "Open app": chips learned from `open_app` entries in the activity log,
  pinnable the same way. Tap calls new `POST /api/open-app {name}` →
  `helpers.open_app`.
- When touch input is down (`inputEnabled` false), action buttons disable
  with a tooltip, same pattern as existing buttons.

### 3. Agent tab

- The existing Activity feed and Recent sends sections move here unchanged
  and get the full column height. No backend changes.

### 4. Phone tab

- Passive detail card: battery, lock state, current app, screen size in
  points, short WDA session id, input-signature days left.
- Refreshes with the existing 10s status poll. Zero phone interaction —
  no message reading, no notification pulls (explicit non-goal).

### 5. Console tab (`>_`)

- One input line accepting a single whitelisted helper call, e.g.
  `tap_text("General")` or `ocr()`.
- New `POST /api/console {line}`: parse with `ast` (call node, literal args
  only), dispatch only names on a whitelist (same spirit as
  `mcp_server._ACT_TOOLS`; `screenshot` excluded — bytes don't render).
  Anything else returns a clear error and executes nothing.
- Result pretty-printed below the input; `ocr()` output scrolls in its own
  box. Up-arrow history kept in localStorage.
- Runs through `_ACTION_LOCK`; STOP blocks it at the `_request` chokepoint
  like every other action.

## Error handling

- Console: parse errors and helper exceptions render inline under the input,
  never as alert dialogs.
- `/api/text` validates both fields non-empty; returns structured JSON errors
  like existing endpoints.
- Status strip degrades: if the passive WDA read fails, show the doctor state
  alone (no battery/app segment), never block the strip.

## Testing

- `tests/test_viewer.py` style (no phone): console endpoint rejects unknown
  names, attribute access, and non-literal args; accepts whitelisted calls
  with literal args (helpers mocked). `/api/text` field validation. Status
  aggregation with a mocked client. Origin guard covers the new POSTs.
- Rendered proof before done: open the viewer, click through all four tabs,
  send a real text, run a console line, watch the strip expand on a forced
  FAIL.

## Non-goals

- No message/notification reading in the Phone tab (chosen: passive only).
- No arbitrary Python in the console (chosen: whitelisted helper calls only).
- No server-side storage for pins/history (localStorage only).
- No changes to gesture handling, MJPEG stream, unlock logic, or kill switch.
