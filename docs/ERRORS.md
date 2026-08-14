# ERRORS.md

Recurring failures, their root causes, and the fix that actually worked. Short
entries only. Newest first.

---

- **2026-08-14 — Passcode digits typed in visibly slowly (~5s for six)** (Wes: "still typing in the numbers really slowly, that used to be instantly fast"). Root cause: each pad tap paid the session's `waitForIdleTimeout` (2s ceiling) plus a 0.15s sleep — the pre-2026-08-13 "instant" was `/wda/keys`, the exact path a lock-screen notification eats digits through, so a revert was off the table. Fix: `_enter_passcode` runs the tap burst at `waitForIdleTimeout` 0 (the pad is static; nothing to settle) and restores `WDA_IDLE_WAIT` in a finally — 4.94s → 2.6-3.1s live, every digit in order. **Two traps found proving alternatives on Calculator first, NEVER retry them on the pad:** (1) six down/up cycles batched in ONE pointer source enter deterministically WRONG digits (`246810` came out `426861010`, identical across three timings) — on the pad that is wrong passcode attempts toward a lockout; (2) six parallel pointer sources in one `/actions` KILL WDA outright, same class as the unbounded `**/*` query. Prevention: mechanism-test tap experiments in Calculator (its digits are `Key` elements like the pad), never on the lock screen.

- **2026-08-14 — Unlock held the viewer busy ~5s over a visibly unlocked phone** (Wes: "I have to sit here and look at an unlocked phone for like 5 seconds"). Root cause: the wrong-passcode guard after the last digit tap was `sleep(0.7)` + one full `/source` of the freshly unlocked Home Screen — `/source`'s worst case, 3.0-5.7s measured — while `_ACTION_LOCK` kept the busy overlay up. Prevention: ask single-element questions with a bounded `find_first`, never a tree read — the same probe answered in 0.11s (measured), and the live tail is now 0.23s. The guard itself stays: a straight revert would have resurrected the lying-success bug fixed 2026-08-13.

## 2026-08-14 — Unlock took 30-50s "because of priority notifications": the real 16s was a lock-poisoned session, and the notification only made it reliable

**Symptom.** The viewer's Unlock button took ~30s whenever a priority
notification sat on the lock screen; without one it felt fast.

**Where the time actually went** (activity-log trace of the live incident):
the first wake swipe blocked 17.5s, then 7 pad polls at ~3s/`/source` burned
21s on a screen the swipe never changed, then a second wake+swipe attempt
succeeded in ~9s.

**Root cause** (split on device, no notification involved): a session that
crosses a screen lock keeps answering GETs but its first `/actions` HANGS
16.23s inside XCTest's ~15s snapshot timeout before failing
`point.x != INFINITY` — measured raw, next to a 0.02s fresh `POST /session`
on the same lit lock screen and a 0.95s swipe on that fresh session. During
the hang the woken lock screen re-slept, so the swipe burned dark, and the
attempt-counted pad poll (assumes 0.4s/`/source`, lock screen runs ~3s)
multiplied a 3s budget into 21s. The priority notification never caused the
hang; it correlates because it keeps the poisoned session alive-and-lit, so
the slow path fires every time.

**Fix.** `unlock()` now mints a fresh session (`WDAClient.fresh_session()`)
after its in-use early return and before any gesture — the fresh id is born
after the lock and cannot be poisoned — and `pad_appears` is wall-clock
capped (min two reads). Measured after: 15.7s end-to-end from a locked dark
phone, no hang, first swipe lands. Lesson: when a symptom pattern-matches to
a visible trigger (the notification), split the timeline with raw timed
calls before believing the trigger is the cause.

---

## 2026-08-14 — Seven perf wins shipped green and carried four defects, two of them CRITICAL

**Symptom.** A parallel build of the top seven latency wins finished with the
full suite green (365 passed, 16 new tests, every lane having proved its own
tests could fail). An adversarial review pass then found four real defects.

**What the green suite did not catch.**
- `/api/status` memoised `window_size()` — which was the ONLY WDA request that
  endpoint made. On a cache hit it made no network call at all, so it reported
  `"input": True` over a dead link indefinitely. Deep sleep kills WDA ~15min
  after the screen darkens, so that is the normal case, and viewer.html HIDES
  the Restart-link and fix-input buttons while input is true. The optimisation
  removed a round trip that was also doing a second job.
- `device._run_cache` was a module global mutated by a save/restore
  contextmanager in a `ThreadingHTTPServer` process. Two overlapping doctor
  passes leave a non-None dict frozen for the life of the process.
- The `window_size` memo had no rotation guard, so a landscape app could poison
  `unlock()`'s swipe geometry.
- The viewer's hidden-tab guard closed `screen.src` but left `setInterval(
  loadStatus, 5000)` running, and `loadStatus` re-opened the stream on the next
  tick. **The optimisation did nothing and its test passed**, because the test
  only grepped the handler's own body — the bug lived one function away.

**Root cause of the pattern.** Every one of these is a change that is correct in
isolation and wrong in context: a removed call that had a second job, a cache
with no invalidation for the thing that actually changes, a guard placed one
scope away from what it guards. Unit tests written by the same reasoning that
produced the change inherit its blind spot.

**Prevention.** A green suite is evidence the code does what its author thought,
never that the author was right. For any change that REMOVES a call, ask what
else that call was doing. For any cache, name what invalidates it and whether a
second thread or process can change the thing behind its back. For any guard,
prove it is in the code path that actually does the work — and write the test
against that path, not against the guard's own source text.

---

## 2026-08-14 — Every "impossibly fast" WDA reading was an error body. 11,804 bytes is the tell.

**Symptom.** While benchmarking `/source`, three separate "huge win" results
appeared and all three were false: `?format=xml` looked 400x faster than
`?format=json`; `/session/{id}/screen` looked like a free 3.7 ms replacement for
the 211 ms `/window/size`; and the same 655 KB tree looked like it could be
served in 9 ms instead of 7 s.

**Root cause.** All of them were error responses, timed without reading the body.
- `?format=xml` at 8 ms / 11,804 B was `{"error": "invalid session id"}` — the
  shared session had rotated mid-benchmark.
- `/session/{id}/screen` at 3.7 ms was `{"error": "unknown command"}`. The
  endpoint does not exist. The real one is `/session/{id}/wda/screen`, and it
  costs **291-300 ms** — *more* than `/window/size`, so it cannot replace it.
- The 9 ms `/source` runs were the same `invalid session id` error, because the
  benchmark had captured `SID` into a shell variable before the session rotated.

**The tell.** An `invalid session id` body is **11,804 bytes** and answers in
about 9 ms. Any WDA timing near those two numbers is an error, not a win.

**Prevention.** Never time a WDA endpoint without asserting on the body. Re-read
`.state/wda_session` per request rather than caching the id in a variable across
a long run — the session rotated four times during ~30 minutes of read-only
probing. And when a benchmark shows a 100x+ win, treat it as a measurement bug
until the payload is proved identical: here the honest xml-vs-json number was
~15% and 7.2x fewer bytes, not 400x.

**Also settled by the same session.** `waitForIdleTimeout` 2 vs 0 makes no
measurable difference to `/source` (interleaved, order-alternated A/B: +3%, noise).
An A-then-B run had suggested 0 was 2.3x *slower*; that was time drift, not the
setting. Interleave before believing any A/B on this device.

---

## 2026-08-14 — A passing test suite that fails at random: the clock spelled out the passcode

**Symptom.** `python -m pytest tests -q` failed on
`test_redact_actions_hides_gesture_coordinates`. The identical command re-run
seconds later gave 349 passed. Nothing had changed.

**Root cause.** The test typed `wda.tap(123, 456)` inside `redact_actions` and
then asserted `"123" not in text and "456" not in text` over the WHOLE activity
log file. Each line is `{"ts": time.time(), "action": ...}` — about ten digits of
epoch. The clock passed through 1786123456 and the substring scan matched the
timestamp, not a leak. A time bomb: the test fails whenever the current epoch
happens to contain the two coordinates.

**Fix.** Scan every field EXCEPT `ts`
(`tests/test_wda_client.py`). Dropping only the clock keeps a future new field
covered, so the test still fails if redaction genuinely leaks.

**Prevention.** Never substring-scan a log line that carries a timestamp for a
short numeric literal. Assert against parsed fields. Verified both directions:
with `redact_actions` neutered the assertion still fires, and with the clock
pinned to 1786123456 the old form trips while the new one does not.

---

## 2026-08-14 — Renewing early is IMPOSSIBLE on a free ID, and chasing it broke a working install twice

**Symptom.** Following up the entry below: with everything green, we tried to
clear the 46h countdown "properly" (Fix input armed, then Sideloadly Start).
Sideloadly finished ("Done.") — and WDA died with error 103, twice, and the
countdown never moved.

**Root cause, two layers.** (1) Every Sideloadly Start does a bare install
(nested .xctest unsigned), so mid-flow it REPLACES the working WDA and kills
input until a local `fix-input .state/profile.mobileprovision` re-sign — the
already-documented 103. The wizard is built to recover by capturing the fresh
profile and re-signing, but the capture missed (180s window vs the multi-minute
human+Sideloadly loop; widened to 600s). (2) The prize was fake anyway:
`%LOCALAPPDATA%\Sideloadly\account-appids.json` showed `NearestTtl:
2026-08-16T04:05:51Z` AFTER tonight's fresh sign — Apple pins every re-sign to
the App ID's original 7-day window. The countdown mathematically cannot be
cleared before it expires; only the first sign AFTER expiry starts a new week.

**Fix.** Input repaired both times with the local re-sign (needs the phone
unlocked). The countdown check and the <36h toast no longer prescribe
fix-input while counting down — they say no click can extend it early and
what to do once it dies; the expired branch keeps the real instructions.
Tests pin both messages.

**Lesson.** When a repeated "fix" keeps not fixing, check whether the goal is
reachable at all before improving the aim. Apple's own TTL record
(`account-appids.json`) was the ground truth all along, one file away from
the profile everyone was staring at. And a doctor `fix:` line is an
instruction someone WILL follow at midnight — a prescribed action that cannot
work is a bug in the doctor, not in the person following it.

---

## 2026-08-13 — "Fix input isn't working": the 46h countdown was TRUE, and renewal never renewed

**Symptom.** Viewer showed FAIL "input signature expires in 46h" after two
fix-input runs the same day. Read as a broken check; the check was right.

**Root cause.** Two renewals that renew nothing: (1) the mid-week
`fix-input .state/profile.mobileprovision` re-signs with the SAME captured
profile, so the expiry mathematically cannot move — it is a repair tool
(nested-.xctest signing), not a renewal; (2) the full run built the p12, then
timed out waiting for Sideloadly, which never signed (its daemon log was
untouched all day — Fix input's watcher arms, but only a human's Start click
in Sideloadly makes Apple mint a profile). Only that click buys a new week.

**Fix.** None needed in the check. An adversarial review of the pipeline it
guards found and fixed 6 real defects the same night (see the entry's commit):
profile committed to `.state` BEFORE `ios sign app` succeeded (failed re-sign
⇒ false 7-day PASS); `subprocess.TimeoutExpired` uncaught through
`fix_input` + a worker thread with no try/except (wizard stuck at "running"
forever); `unlock()` swallowing EVERY `WDAError` from `active_app()` instead
of just the lit-lock-screen crash; `unlock()`'s give-up returning silently
(viewer said ok:true over a dark phone); the watcher capture path accepting a
stale `captured.mobileprovision` the PS script failed to delete; zero tests on
the watcher branch and on the 48h/0h check boundaries. All six now pinned by
tests.

**Lesson.** "The check is red" has two readings — broken check or true bad
news — and the countdown check was the messenger. Also: a first diagnosis of
"everything works" (the black viewer pane WAS just a sleeping display) can be
right about the symptom in front of you and still miss the bugs behind the
next one; the review the user insisted on paid for itself.

---

- **2026-08-13** — WDA runner died around a viewer restart (`Start-Process python launch.py` at 23:01) and would not come back: every `up()` after it failed in `testmanagerd` dtx timeouts ("cannot initiate a IDE session" / "Timed out while enabling automation mode") with tunnel, DDI, lockdown, and signature all green, phone locked the whole time. Root cause of the death not pinned (runwda.log is rewritten per start, so the original crash evidence is gone); the wedge needed a hand unlock of the phone before WDA would start again. Prevention: treat "dtx timeouts with everything green + phone locked" as "unlock the phone by hand", not as a broken tunnel — and don't chase it with more restarts.

---

## 2026-08-13 — Unlock still died with a priority notification up: /wda/activeAppInfo crashes while the lock screen is lit

**Symptom.** Same report as the entry below, hours after that fix shipped: the
Unlock button "did nothing" with a priority notification on the lock screen.
This time the activity log shows the difference — ZERO gestures. No wake, no
swipe, no passcode entry. The button worked fine when the screen was dark.

**Root cause.** `unlock()`'s first phone call is `active_app()` (the "is the
phone genuinely in use" guard). WDA's `GET /wda/activeAppInfo` CRASHES while
the lock screen is LIT — `unknown error: attempt to insert nil object from
objects[2]` — and answers normally the moment the screen goes dark (reproduced
on device: lit frame 2.06 MB → crash, dark frame 50 KB → springboard). A
priority notification keeps the lock screen lit for as long as it shows, so
every press of Unlock during one raised out of `unlock()` before a single
gesture reached the phone. Without a notification the screen is dark at press
time, the call succeeds, and unlock works — which is why the earlier fix
looked complete.

**Fix.** `unlock()` catches `WDAError` from that one call and treats it as
"nothing frontmost": the crash only happens on the lock screen (a real
frontmost app answers fine), so it can never mean "in use". Verified live by
waking the display, polling `active_app()` until it entered the crash state,
then running `unlock()` inside it — the phone unlocked.
`test_unlock_survives_active_app_crash_on_lit_lock_screen` pins it.

**Lesson.** The guard that protects a feature can be the thing that kills it:
the failure was in the pre-flight check, not the unlock path anyone was
staring at. When a fix ships and the same symptom returns, diff the EVIDENCE,
not the theory — "typed digits went nowhere" and "zero gestures logged" are
different bugs wearing the same report.

---

## 2026-08-13 — Unlock typed all six digits and the phone stayed locked: a priority notification held keyboard focus

**Symptom.** The viewer's Unlock button "did nothing" while an Apple priority
notification sat on the lock screen. The activity log shows a complete run:
wake, swipe, second wake+swipe, pad detected, `type (6 chars)` — then the
pad-still-visible check raised. The same button unlocked in 4-6s as soon as
the notification was gone.

**Root cause.** `unlock()` entered the passcode with `/wda/keys`, which sends
keystrokes to whatever element holds FOCUS. Pad visible ≠ pad focused: the
notification overlay kept focus while the pad sat behind it, so all six typed
digits went into the void.

**Fix.** `helpers._enter_passcode` now TAPS the pad's digits at their tree
coordinates — a tap needs no focus. `/wda/keys` survives only as the
alphanumeric-passcode fallback (full keyboard, no digit buttons). The digit
taps run inside `wda_client.redact_actions("passcode entry")`: a pad tap's
coordinates spell out the passcode digit by digit, and the activity feed
never records typed text, so it must not record these taps either.

**Second finding, same day.** The first live run of the tap path silently
fell back to typing: the real pad's digits are `Key` elements, not `Button`s
(`Key '1'`..`Key '0'`, device dump in the entry's session). That also means
`_passcode_pad_visible`'s digit-count branch had NEVER matched on device —
detection was riding entirely on the English "Enter Passcode" text. Both now
accept `Key`. Lessons: `/wda/keys` entry silently depends on focus — when a
stateless gesture can do the job, prefer the gesture; and a fallback that
engages silently looks exactly like the fix working (L1: watch the new path
actually fire — the redacted log lines are the tell).

## 2026-08-13 — Error 103 while the signature check was green: a bare Sideloadly install replaced the fixed one

**Symptom.** WDA died mid-week with `Failed to load the test bundle (Error
code: 103, XCTestErrorDomain)` on every launch, while the doctor showed ten
green checks — including "input signature good for 2 more day(s)". Working at
16:44, dead by 19:01, same day.

**Root cause.** Sideloadly ran on the desktop that afternoon (sessions.json
17:12, installations.db 19:00 — one minute before the first 103) and its Start
click did a bare install of WDA, which leaves the nested `.xctest` unsigned.
The green signature check was telling the truth about the wrong thing: it
parses the LOCAL `.state/profile.mobileprovision`, not what is installed on
the phone, so a mid-week Sideloadly click reintroduces 103 with every local
check green. A 17:12 `fix-input` attempt had also stalled before its signing
step (wda.p12 rebuilt, signed IPA untouched) — likely a capture window that
timed out before the 19:00 Start click.

**Fix.** `phone-harness fix-input .state/profile.mobileprovision` run to
completion with the phone unlocked. Re-signed the nested `.xctest` with the
still-valid captured profile, installed, WDA answered.

**Lesson.** Error 103 + green signature check = the phone-side install is
bare, not the profile expired. The timestamps that told the story:
`.state/wda_session` (last time WDA answered), Sideloadly's
`installations.db` (when a bare install landed), `wda/WebDriverAgent-signed.ipa`
(whether fix-input ever reached its signing step).

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

---

## 2026-08-12 — Stuck in Spotlight: five gestures failed because the keyboard owned the bottom of the screen

**Symptom.** After using Spotlight to prove a hidden app was still searchable,
the phone would not return to the Home Screen. `current_app()` reported
`com.apple.Spotlight` and `current_page()` returned `None` through five
consecutive attempts: `press_home()`, a bottom-edge swipe at 47%/0.45s, a swipe
down, then edge swipes at 79%/0.8s and 91%/0.6s.

**Root cause.** Two independent things, and fixing only one never helps.

1. `press_home()` is `POST /wda/homescreen`, which only exits a real app to the
   springboard. Spotlight is an overlay, so it is a no-op there.
2. Every "stronger" edge swipe started at y≈950 on a 956pt screen — **inside the
   keyboard**, which was up and owned the bottom ~40%. The gesture never
   reached the screen edge, so making it longer and slower could not work. The
   start point was wrong, not the shape.

The escalation was the bug: three of the five attempts were the same hypothesis
("the swipe is too weak") retried with bigger numbers. One screenshot showed the
keyboard immediately and the next attempt worked first try.

**Fix.** Tap the empty blurred area between the results and the search bar
(mid-screen, ~y=477). One tap → `com.apple.springboard`, page 1 of 1.

**Lesson.** When a system edge gesture does nothing, screenshot before resizing
it — ask what is *covering* the edge, not whether the swipe was big enough. A
tree read shows the keyboard's buttons but not that they occupy the region you
are aiming at; only the picture makes the occlusion obvious. Codified as two
rows in `skills/phone-gotchas/SKILL.md` (and the `~/.claude/skills/` copy).

## 2026-08-13 — bash double quotes ate the `$env:` safety overrides and launched a second SideTap for real

Testing `site/install.ps1` (the sidetap.io one-line installer) from the Bash
tool: the command wrapped `powershell -Command "..."` in DOUBLE quotes, so bash
interpolated `$env:SIDETAP_INSTALL_ROOT` and `$env:SIDETAP_NO_LAUNCH` to empty
strings before PowerShell ever saw them. The script then did exactly what it
ships to do — installed to the real `%LOCALAPPDATA%\SideTap` and launched it.
The rogue viewer double-bound :8770 next to the live one (Windows allows the
second bind; Python's http.server sets SO_REUSEADDR), so clicks could land on
either instance.

**Recovery that worked.** Its own `.state` named everything it owned (only
`viewer.pid` — its `up()` never got a tunnel), so: kill that pid (PID-based,
never by name), delete the directory it created, re-run the real repo's
`scripts/install_shortcut.ps1` to point the shortcuts back.

**Lessons.**
1. Bash→PowerShell env vars: single-quote the whole `-Command` string, or `$env:`
   is silently gone. An eaten override does not error — the run just proceeds
   without its safety rails.
2. Before running anything whose failure mode is "does the real thing", verify
   the override reached the child: `powershell -Command '...; Write-Output
   "override=$env:X"'` costs one second. (L1 again: a guard never observed
   working has been written, not verified.)
3. A second viewer on :8770 does not fail loudly — SO_REUSEADDR means both
   listen and traffic splits between them. `netstat -ano | findstr :8770`
   showing TWO pids is the tell.

## 2026-08-13 — the first clean-machine test found two shipped defects in one run

An OpenClaw agent ran the sidetap.io installer on a laptop with no phone and
no prior install (the two branches this machine could never exercise). Both
findings were real:

1. **`/api/status` returned 500 with no phone**, because the WDA-less fallback
   takes a go-ios screenshot, and with no device that raises uncaught. The
   wizard rides on that JSON, so the entire first-run experience silently
   never appeared — on exactly the machine it was built for. Fix: the
   screenshot fallback is wrapped; `window` is null and the page treats it
   like its own fetch-failed state. A test pins the phoneless 200.
2. **Updating while SideTap ran destroyed the install.** `Remove-Item` on the
   locked app dir deleted everything deletable (.env and launch.py included)
   before hitting the locked `.state` logs and throwing. Fix: rename-first —
   `Move-Item app app.old` fails on an open file handle BEFORE anything is
   touched, so a running SideTap aborts the update with instructions and zero
   changes. Keeps restore from `app.old` afterwards; a `_keep` stash stranded
   by the old installer is healed on the next run. Note: a directory that is
   only a process's CWD renames FINE on Windows — the lock that matters is an
   open file handle, which the real product always holds (`.state` logs), and
   the test holds one for real.

**Lesson.** The untested branches were where both bugs lived. "Works on the
dev machine" tested the wrong half: the dev machine has a phone, so the
phoneless path never ran, and it was never updated while running. A clean
machine plus "report, don't fix" found both in under two minutes of runtime.
