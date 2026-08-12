# ERRORS.md

Recurring failures, their root causes, and the fix that actually worked. Short
entries only. Newest first.

---

## 2026-08-12 — WDA calls a message bubble "the focused element", so sends refused themselves

**Symptom.** The viewer's Text someone walked the whole flow correctly — opened
Messages, searched, opened the right thread, typed `test` into the compose bar —
and then did not send. Instead the phone popped the Tapback (reaction) picker
open on an unrelated message. `.state/actions.log` recorded the send as
attempted and nothing went out.

**Root cause.** ONE call, two symptoms: `GET /element/active`. On a Messages
thread WDA answers it with a message BUBBLE — an `XCUIElementTypeTextView`
named `CKBalloonTextView` — not the compose bar, even with the keyboard up and
the caret blinking in the compose bar. `set_field_text` used it twice:

- `element_clear(active_element())` ran WDA's clear routine on a *message*,
  which long-presses it → that is the Tapback picker, and the POST still
  returned 200 so the activity feed logged a normal `clear`.
- `element_value(active_element())` read that same bubble back, so `landed` was
  the bubble's text, not `test`. `send_message`'s read-back guard did its job
  and refused to tap Send — on garbage input.

The draft was never cleared either, which is the entire reason
`set_field_text` exists.

**Cheapest discriminating test.** With the thread open and the field focused:
`/element/active` → `CKBalloonTextView`, value = a message; class chain
``**/XCUIElementTypeTextField[`label == "Message"`]`` → `messageBodyField`,
value = `test`, in 0.25s.

**Fix.** `helpers._field_element()` resolves the field the *caller* named with a
bounded class chain (label predicate first, bare type as fallback) and
`_clear_field`/`_field_value` take that id. `active_element()` is gone from
`wda_client` — it cannot be made right, and an unused method that lies about
the product's most important screen is a landmine. Resolve AFTER the tap: the
keyboard moves the compose bar from y=908 to y=601, so the tapped coordinates
no longer describe the element.

**Also worth knowing.** An empty iOS text field reports its PLACEHOLDER as
`value` (`iMessage` on the compose bar), so an emptied field never reads back
as `""`.

---

## 2026-08-12 — `unlock()` refused to wake a phone that locked with an app open

**Symptom.** Mid-session the phone auto-locked. `helpers.unlock()` returned
instantly and did nothing, every time. Meanwhile `ios`/WDA launches failed with
`Unable to launch ... because the device was not, or could not be, unlocked`,
and `active_app()` cheerfully reported `com.apple.calculator`.

**Root cause.** `unlock()` opens with "an app is frontmost -> the phone is
unlocked and in use, touch nothing". But `active_app()` goes STALE behind a
lock: a phone that locked while Calculator was frontmost keeps naming
Calculator until the display wakes. So the guard fired on exactly the state
`unlock()` exists to fix. One manual `press_button("home")` + swipe flipped
`active_app()` to `com.apple.springboard`, after which `unlock()` worked first
try — which is what confirmed the staleness rather than a broken gesture.

**Fix.** Keep the early return, but qualify it with the lit-screen probe
`unlock()` already used elsewhere, now `_LIT_SCREEN_BYTES`. An app in use is on
a LIT screen; an app "frontmost" on a dark one is a locked phone. The threshold
was an inherited 150_000 guess; measuring it made the margin obvious, so it is
now 120_000 — display OFF is 50 KB, Calculator (mostly-black UI, near the worst
case for a lit app) is 245 KB, Home Screen 888 KB. Two probes were rejected:
`/wda/locked` lies (display-only, and a test pins that `unlock()` never
consults it), and "wake first, then re-read `active_app`" is not passive —
`press_button("home")` on a lit unlocked phone EXITS the app, verified. The
residual is an app painting near-pure black, which reads as dark.

**Lesson.** "Which app is frontmost" is not a lock-state probe. Any WDA read
taken through a dark screen may be describing the phone as it was when the
screen went off.

---

## 2026-08-12 — `/elements` with an unbounded class chain kills WDA

**Symptom.** Profiling why a Home Screen `/source` costs 3.0-5.7s, I tested
whether `/elements` with inline attributes could beat it. The first query,
class chain `**/*`, never returned: WDA stopped answering on :8100 and every
following call failed `invalid session id: Session does not exist`.

**Root cause.** `**/*` matches every element in the hierarchy. On the Home
Screen that is 554-610 nodes, and WDA resolves each match into a full element
reference. It died mid-query rather than returning slowly.

**Fix.** Don't. Perception stays on `/source`. A BOUNDED type query is safe and
fast — `**/XCUIElementTypePageIndicator` plus one attribute read is 0.37s, and
`current_page()` now uses exactly that — so `WDAClient.find_first` takes a class
chain and returns ONE id, never a list, so it cannot grow into a screen sweep.
`phone-harness up` brought WDA back with no lasting damage.

**Lesson.** CLAUDE.md already said `/elements` measured slower than `/source`.
That was true, and understated: the failure mode is not slowness, it is taking
the session down. Note the measurement it came from was almost certainly taken
inside an app, where `/source` is 0.22s — the Home Screen is 25x worse, which
is what made the idea look attractive in the first place.

---

## 2026-08-12 — three "settles" that were each wrong in a different direction

**Symptom.** `goto_home_page(1)` took 10.75s from page 3. Separately, the
viewer's Home button sometimes pressed home twice and never walked.

**Root cause.** Three different waits, none matching what the device does.

1. `_PAGE_SETTLE`, 0.55s after every swipe: pure waste. WDA already waits for
   the springboard to go idle INSIDE the `/actions` call (`waitForIdleTimeout`),
   so this counted the same wait twice. The first read after `swipe()` returns
   is correct 6/6 on device.
2. `current_page()` on `ui_tree()`: not a wait at all, but it read the whole
   544-610 node Home Screen tree (3.0-5.7s) to get two integers off one element.
3. `press_home()` trusting `/wda/homescreen` to be synchronous. It is not: it
   returned in ~50ms with the app still frontmost on two tries of three, with
   the springboard arriving ~830ms later, and took 1.4s on the third. The 0.55s
   sleep that used to follow it was never enough either — the 5.5s `/source`
   right after was accidentally covering for it, so removing the tree read is
   what exposed this.

**Fix.** Delete the settle; read the PageIndicator through `find_first`; make
`press_home()` poll `active_app()` until the springboard is up (bounded, never
raises — the physical gesture cannot fail). 10.75s -> ~2.5s.

**Lesson.** A fixed sleep next to an async call hides how async it really is,
and a slow call downstream hides it again. When removing one wait exposes a
failure, the failure was already there. Poll for the state you need; don't
sleep a number that looked big enough.

---

## 2026-08-11 — Home Screen bulk reorganisation: the load-bearing gesture was never proved

**Failure.** A request to "organise the whole Home Screen" (8 Home Screen pages,
~160 loose icons, 132 installed apps) was surveyed, mocked, costed and *approved*
before the one gesture the whole plan depended on was tested. Two assumptions
then failed on contact with the device.

**1. The page I numbered 1 was not a Home Screen page at all — it was Today
View.** The dedup sweep walks until the icon signature repeats, and its leftmost
end stop is Today View ("page 0"): always present, not hideable, and **absent
from the page editor**. I counted it as page 1, which shifted every later page
number by one and made the real folder page "page 2". Had the page-hiding step
run on that numbering it would have unchecked the wrong thumbnails, including the
user's only organised page. The user caught it, not the tooling.

**The fix, found the next day:** the `PageIndicator`'s `value` states the
position outright — `Page 4 of 8` on a Home Screen page, `Page 0 of 8` on Today
View, `Page 9 of 8` in the App Library. One read gives index, total and zone, so
there is nothing to detect and no end stop to miscount. It went unnoticed because
`ocr()` cannot see it: `collect_texts` prefers `label`, which is null on that
element, so it falls back to `name` (`"Page control"`) and drops `value`. Shipped
as `helpers.current_page()`.

`press_home()` is **not** an anchor either: `/wda/homescreen` only exits an app
to the springboard. From page 4 two consecutive calls both stayed on page 4, and
from the App Library it does not even leave. Use `helpers.goto_home_page(1)`.

Secondary, and still true: **`ocr()` reports widgets with `type == "Icon"`,
identical to apps.** The tell is geometry — widget centres sit *between* the four
icon columns (x≈120 and 320, versus 69/170/270/371) — and because Today View is a
`ScrollView`, a large widget reported y=1124 on a 956pt screen and coordinates
shifted between reads.

**2. Cross-page drag does not work with the obvious gesture**, and nearly every
planned move needed it. Two failure modes, neither of which raises:

- A static `{"type":"pause"}` at the left edge (x=14–16) flips **no page at all**.
  The icon simply stays put.
- Gliding to the edge fast (5 segments × 130ms across ~260pt) then jittering
  *does* flip the page — but the icon was never picked up, so the gesture was
  only ever a swipe. The post-gesture read shows a different page, which looks
  exactly like success. A later full sweep found the icon still at its origin.

**Root cause of 2 (hypothesis, untested).** Pickup depends on the speed of the
first movement after `pointerDown`. The verified same-page drag uses ~180ms per
segment over short hops; the failing one used 130ms per segment across ~260pt.
Fast initial motion gets classified as a swipe, not a drag. Same-page drags never
hit this because they are short by nature.

**Verified working.** Same-page drag (icon moved, confirmed by coordinates).
Folder creation by dropping icon A onto icon B — and iOS auto-names the folder
from its *own* App Library category guess, not from the apps inside — two AI apps
landed in a folder called `Productivity`.

**Also cost a run.** `retry()` wrapped `ocr()` but not `swipe()`. One
`RemoteDisconnected` inside a mid-script swipe killed the script and lost every
buffered print, leaving the phone a step ahead of the last reported state.

**Result / what to do instead.** Bulk reorganisation is not economical over WDA:
~160 icons × ~10–15s per verified drag, against WDA dropping roughly 5× per 25
minutes of Home Screen work, with a real chance of stranding half-sorted (worse
than the starting state). Lead with page-hiding — `PageIndicator` → uncheck,
~10 taps, instant, reversible, apps stay installed and stay in App Library +
Spotlight — and hand-drag only the few icons that must live on page 1. App
Library already categorises everything for free.

**Alternatives rejected.** Dragging out of App Library (the drop slot is not
controllable; iOS drops it in the first free slot, usually the last page).
Multi-touch icon stacking (needs a second simultaneous pointer; unproven on WDA).

**Captured in** `skills/phone-gotchas/SKILL.md` (traps table, cross-page drag
section, and the "price a bulk reorganisation" costing section) and
`skills/phone/SKILL.md`. Both re-copied to `~/.claude/skills/`.

---

## 2026-08-12 — The viewer's checks were a snapshot of the worst second of the boot

**Symptom.** Plug in the phone, SideTap opens, header says "3 checks failing"
and the Checks overlay pops itself open. Click **Refresh checks** and all 11
pass. Every single time.

**Root cause.** `loadDoctor()` ran EXACTLY ONCE, on `window load`. `launch.py`
opens the browser immediately and runs `admin.up()` in a background thread, so
that one run landed mid bring-up, when the tunnel and WDA genuinely were not up
yet. Nothing ever re-ran it. Everything else on the page polls (status 5s,
activity 3s, phone 10s); the checks alone were frozen. The overlay auto-opened
because `prevFails` starts at 0, so the very first render always looked like an
ok→fail transition.

**Fix.** The checks re-run themselves while any fails (3s, 3s, 5s, 8s, 15s, then
every 30s) and stop once green — a full run is ~2s of go-ios subprocesses plus a
screenshot, so polling it forever would fight the live stream for the phone.
`admin.bringing_up()` (the `_UP_LOCK` state) reaches the page on `/api/status`
as `starting`, and while it is true the header reads "Starting link…" with grey
dots rather than a red count. `/api/doctor` now serves its last result while
`_ACTION_LOCK` is held, same as `/api/status` and `/api/phone`.

**Lesson.** A one-shot read rendered next to live-polling neighbours reads as
live. If a panel can be wrong the moment it is drawn, it has to re-draw itself
or say why it cannot.

---

## 2026-08-12 — Restarting the UI killed the phone link (`taskkill /T`)

**Symptom.** Found while verifying the fix above. `phone-harness doctor`: all 11
green. Start SideTap again (double-click, or `python launch.py` while one is
already open). Seconds later: tunnel not running, WDA not answering.

**Root cause.** `_kill_stale_viewer()` killed the previous viewer with
`taskkill /PID <pid> /T /F`. The `/T` kills the process TREE, and the tunnel and
the forwards are children of the launch.py that started them. Worse, the order
hides it: `launch.py` starts the `up()` thread FIRST, `up()` sees WDA answering
and returns "Already up" in ~0.2s, and only then does `serve()` reach
`_kill_stale_viewer()` and kill the link it had just approved. Nothing re-runs
`up()` after that, so the viewer comes back with a dead phone.

**Fix.** `_safe_kill(pid, prefix, tree=False)` for the viewer. Freeing the port
is the viewer's job; the go-ios processes are the phone link and are meant to
outlive a UI restart. `device.stop_all()` (i.e. `phone-harness down`) is what
stops those, by their own pid files, and keeps the tree kill.

**Lesson.** `/T` is not a stronger `taskkill`, it is a different one. Before
using it, ask what is parented to the process — detached service processes are
frequently children of whatever launched them.
