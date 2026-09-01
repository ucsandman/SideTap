---
name: phone
description: Use when the user asks to text or message someone from their iPhone, read or check something on their phone, or control their iPhone — e.g. "text my dad ...", "send this to Mom", "summarize this and text it to X", "what's on my phone", "open <app> on my phone", "organize my home screen", "what apps do I have installed".
---

# Drive the user's iPhone (SideTap)

The user's real iPhone is connected to this PC over USB and fully controllable.
Replace `/path/to/sidetap` below with the SideTap folder — a git clone, or
`%LOCALAPPDATA%\SideTap\app` when it was installed with the sidetap.io
one-liner. Driving the phone works from any project directory.

## Run

```bash
cd /path/to/sidetap && ./phone-harness.cmd <<'PY'
send_message("Mom", "hi")
PY
```

Bare `phone-harness` is NOT on the bash PATH — always use `./phone-harness.cmd`
from the repo root. Helpers are pre-imported; print() output comes back to you.

**REQUIRED SUB-SKILL:** read `phone-gotchas` before your first tap. It holds the
coordinate traps, the batching tool, and what the harness genuinely cannot do —
none of which you can learn by looking at the screen.

## Helpers

| Helper | Use |
|---|---|
| `send_message(contact, text)` | full Messages send; `contact` = conversation name in the Messages list |
| `read_messages(contact, limit=20)` | read a thread back: `[{text, from_me}, ...]`, oldest first |
| `ocr()` | all visible text with center coords (real UI tree, exact). Elements are `{"text","x","y","type","rect"}` — the key is **`text`**, and `full=` is MCP-only |
| `find_text(t)` / `tap_text(t)` | locate / tap by visible text |
| `wait_for_text(t, timeout=10)` | poll until text appears; returns the element (with x/y) |
| `tap(x, y)` / `swipe(x1,y1,x2,y2,secs)` / `scroll("down")` | raw gestures, units = points |
| `long_press(x, y, seconds=1.0)` | context menus and other hidden affordances |
| `act([{"tool":n,"args":{...}}, ...])` | several tools in ONE round trip; screenshot excluded |
| `type_text(t)` | type into the focused field (tap the field first) — it **appends** at the cursor |
| `set_field_text(field, text)` | replace a field's contents: pass the `ocr()` row for the field (it taps it for you), reads the value back. The only correct way to fill a field that may already hold text or an iOS draft |
| `get_clipboard()` / `set_clipboard(t)` | read or write the iPhone system clipboard |
| `open_app("messages", wait_seconds=0)` | friendly name or bundle id; `wait_seconds>0` confirms it reached the foreground and raises if it never did (the only foreground-confirmed launch that does not need the bundle id) |
| `current_app()` / `wait_for_app(bundle_id)` | frontmost app / wait until one is |
| `screenshot("out.png")` / `press_home()` / `wait_stable()` / `unlock()` | utilities |

**Not in the table, and you will want them:**

- **There is no `drag()`.** Moving Home Screen icons means jiggle mode plus a
  hand-built `client()._pointer_actions` gesture, and it fails *silently* if you
  skip jiggle mode. Recipe and the page-hiding flow are in `phone-gotchas`.
  Same-page drags and folder-creation (drop icon A on icon B) are verified;
  **cross-page drags are not** and degrade into plain swipes that look like
  progress.
- **"Organise my Home Screen" is not a quick job.** ~160 icons is hours of drags
  and can strand half-sorted. Hiding pages via the `PageIndicator` editor is
  ~10 taps, reversible, and does most of the work — read the costing section in
  `phone-gotchas` before you promise anything.
- **Full installed-app inventory:** `ios apps --list` (go-ios, on PATH as
  `ios.EXE`). Instant, and far cheaper than sweeping Home Screen pages.

## Rules

- **Sending to real people:** send exactly what the user asked. If you composed the
  content yourself (a summary, a draft), include the sent text verbatim in your reply.
  Ask first only when the recipient or content is genuinely ambiguous.
- Messages compose field is labeled **"Message"**, NOT "iMessage" — old message
  bubbles carry "iMessage" in their labels; searching for it taps the wrong thing.
- `send_message`/`read_messages` handle Messages resuming mid-thread, find the
  thread via Messages SEARCH (type the name, tap the Conversations result), and
  verify the opened thread's header before acting — a wrong or unverifiable
  match raises instead of guessing.
- MCP alternative: once the `sidetap` MCP server is registered (see the SideTap
  README), sessions get all helpers as native `mcp__sidetap__*` tools — no
  Python piping. Prefer those tools when they are available.
- Anything fails to connect → `./phone-harness.cmd doctor` from the repo root.
  Never guess at connection problems. Common: free-Apple-ID signing expires every
  7 days → `phone-harness fix-input`, then the USER clicks Start in Sideloadly.
- Phone must be unlocked for bring-up; `unlock()` enters PHONE_PASSCODE from .env if set (digit passcodes are typed in one fast request and verified by the pad leaving the screen; if a lock-screen notification holding keyboard focus eats the typed digits, they go in by tapping the pad's buttons instead). It raises if the screen stays dark after two wake attempts — that means the phone needs a hand wake (side button), not a retry loop.
- Human-watchable live viewer: http://127.0.0.1:8770 (start with `python launch.py`).
