# ERRORS.md

Recurring failures, their root causes, and the fix that actually worked. Short
entries only. Newest first.

---

## 2026-09-03 — one-liners: viewer Enter-is-Send

- Removing the `if (ev.key === 'Enter') ch = '\n';` line left the `else if`
  chain behind it dangling; the JS parse test caught it before the viewer
  shipped a page that would not load. When a chain's head goes, the next
  branch becomes the head.

## 2026-09-03 — set_clipboard answered 200 and set nothing: iOS only lets the frontmost app touch the pasteboard

**Symptom.** `POST /wda/setPasteboard` returned 200 with Messages frontmost,
`getPasteboard` came back `""`, and Paste in Notes inserted nothing. Text
had been broken the same way since iOS 16; nobody had read the clipboard
back, so it never showed.

**Root cause.** iOS 16+ restricts UIPasteboard to the foreground app. The WDA
runner is a background XCTest host, so both calls were no-ops. Activating the
runner first (`/wda/apps/activate`) made both work on the spot, image included.

**Second trap.** That activation ran 17-18s every time and the hand-back to
Messages did not land: the runner's "Automation Running" screen never goes
idle, so the shared session's `waitForIdleTimeout` (WDA_IDLE_WAIT=2) ran to
XCTest's own cap. At 0 it is 0.09s. Same shape as `_enter_passcode`: drop the
wait for the dance, restore it in a finally.

**Third trap.** `send_image` long-pressed the compose bar at the coordinates
read BEFORE it tapped the field; the keyboard slide-up moves the bar from
y=908 to y=601 and the press landed on a key. Re-read the field after the tap.

**Fourth trap.** The first hand-back polled `activeAppInfo` (up to 20 calls
per clipboard op) to see the previous app return. That call resolves the
active application, the class that can block WDA with no upper bound during
an app transition: 8 of the 10 wedge recoveries the watchdog has ever logged
landed that afternoon. Replaced with a fixed 1.0s settle. Separately, a
"hang" in `send_image` right after `save_clipboard_image` was the approval
card waiting in the viewer (the read taints the session), not WDA.

**Fix.** `WDAClient._runner_foreground()` wraps both pasteboard calls: idle
wait 0, activate runner, call, re-activate the previous app (Home when it was
springboard), bounded wait for it to be frontmost again, restore the idle
wait. Whole write 0.98s (was 21.75s). Runner bundle from `.state/wda_bundle`
or `WDA_BUNDLE_ID`; without either the call runs bare as before.

---

## 2026-09-01 — The fleet dashboard's iframe rendered {"error": "forbidden"}: same-site is not same-origin

**Symptom.** The SideTap Pro fleet dashboard (:8769) iframed the viewer
(:8770) and the frame showed the viewer's own 403 JSON instead of the phone.
Reported live by Wes on the first real render.

**Root cause.** A loopback port-crossing navigation carries
`Sec-Fetch-Site: same-site` (ports are ignored by the site definition), and
`viewer._allowed()` accepted only `same-origin`/`none`. Local HTTP-level
checks (curl, tests) never send Sec-Fetch headers, so nothing failed until a
real browser framed the page — the rendered-proof step is what caught it.

**Fix.** Accept `same-site` too: a page served from 127.0.0.1 is by
definition local software that could reach the unauthenticated API directly,
remote pages stay `cross-site` (DNS rebinding included — the header is
computed from the registrable domain, not the resolved IP), and cross-port
POSTs/fetches still die on the Origin check because fetch() sends Origin and
navigations do not. Tests pin both directions.

**Also this session (one-liner).** A sabotage-then-revert check used
`git checkout --` on a file whose changes were UNCOMMITTED and wiped the work
being verified; sabotage checks on uncommitted code must restore by file copy
or inverse edit, never by git.

---

## 2026-08-20 — A latency pass: the time is on the phone, and most of what we could remove was reads and sleeps nobody needed

**Symptom.** Three complaints, no error: swiping in the viewer is slow, the Home
button is slow, and the whole thing does not feel real time. The phone was
unreachable for the entire pass (WDA bring-up dies with XCTest error 103), so
nothing below was measured live — every number is a previously recorded device
figure or derived from one, and everything gesture-shaped ships behind a named
constant with the safe value as its default.

**What it was not: Python.** One viewer tap or swipe spends 3-5ms in Python plus
loopback plus the go-ios usbmux forward, bounded above by the recorded 3.8ms
`/status` round trip. That is ~1% of a tap and ~0.3% of a Home walk. A rewrite in
Rust or Go replaces that stage and touches nothing else, and there is no other
injection path on Windows: idb's companion is macOS-only, pymobiledevice3 has no
touch or HID service, and go-ios's own DeviceKit runner still goes through
testmanagerd. The rest is WDA HTTP dispatch, active-app resolution (72-102ms),
XCTest event synthesis and the scripted finger path — none of it host-language
work. Do not re-open the rewrite question without a new measurement.

**What it actually was, in three groups.** (1) Reads that only ever answered a
yes/no question pulled a whole `/source` — the Home Screen's worst case, 3.0-5.7s,
554-610 nodes, 244 KB — once per page in `find_on_home_screen`, and once per poll
turn in `_open_thread` and `_go_back`. (2) Dead wall clock: a 1.5s sleep after
`send_message`'s Send tap that nothing reads past, a flat 0.5s before its Send
scan, a 0.37s `goto_home_page` verify read after a walk that issued zero swipes,
and a `press_home` detection cycle of ~352ms against a recorded ~830ms springboard
arrival. (3) The viewer synthesising slow drags: a wheel notch was a fixed 0.25s
drag, which carries no iOS inertia, so sustained wheeling bought distance the slow
way and topped out near 0.64 screens/s, and the drag handler timed its gesture
from pointerdown, so a press-hesitate-flick went out as a 0.4s or clamped 0.5s
drag.

**What changed.** Bounded `find_first` probes now gate the tree reads in
`find_on_home_screen`, `_open_thread` and `_go_back`, and the full `/source` runs
only on a hit; `goto_home_page`'s verify is one value predicate with the old
`current_page()` as its fallback, and it returns before the walk loop when it is
already on page n. `press_home` polls at `_HOME_POLL` 0.05 inside a
`_HOME_DEADLINE` 2.8s wall clock, resting at least as long as the last
`active_app()` read took so the shorter interval cannot burst 40 requests into
WDA's one-at-a-time queue. `send_message` lost the trailing 1.5s and the flat
0.5s. `wait_stable`'s interval is `_STABLE_INTERVAL` 0.15. `_create_session` now
applies `maxTypingFrequency` and the MJPEG keys, `WDAClient.tap` takes a
`hold_ms`, the viewer's four human gesture endpoints run at
`waitForIdleTimeout=0` through `_human_gesture()`, the viewer's window-size memo
dropped its vestigial session key, and `Handler` speaks HTTP/1.1 with keep-alive.
Two safety notes that came out of it and are load-bearing: keep-alive makes an
unread request body the next request on the wire, so every guard that answers
without reading a body now closes the connection and a `Transfer-Encoding`
request is refused outright (a cross-origin page could otherwise smuggle a
Host-correct `POST /api/tap` past the origin guard, and a test asserts nothing but
EOF follows the 403); and every interpolated class-chain predicate goes through
`_predicate_safe()`, because a chain delimits its predicate with BACKTICKS and the
old `'"' not in text` guard left the actual delimiter open to agent-supplied text
in two registered MCP tools.

**A probe is an optimisation, never the decision.** `_open_thread`'s probe is not
a superset of the Python matcher it gates — `_conversation_cells` reads
`label or name or value` and `_title_matches` accepts containment BOTH ways (a
cell labelled "Wes" verifies the contact "Wes Sander"), which no one-directional
CONTAINS predicate expresses. So it gates only the first `_SEARCH_PROBE_SECONDS`
(10s) of the 20s deadline; past that the loop is the plain tree poll it always
was, and a predicate that can never match costs cheap probes instead of a hard
failure. `_SEARCH_POLL` also stayed at 0.5s: the "Messages with: <name>" filter
row is itself a Cell carrying the contact's name, so the probe answers yes while
the real row is still landing, and at 0.25s that path paid 6 `/source` dumps
against the old loop's 4.

**Pass 2 (same day): the watchdog was pressing Home on a phone that was merely
busy, and the sleeps pass 1 left behind.** The live find first. With the phone
back, `.state/agent_activity.log` showed "recovered a wedged link (pressed Home)"
every ~70s for over ten minutes, while `/status` answered in 49ms and
`/screenshot` in 218ms whenever they were probed by hand in between. Nothing was
wedged: another process was driving the phone hard (the old
`find_on_home_screen`, a 3.0-5.7s `/source` per page, back to back), and the
watchdog's evidence is three timeouts of a `WDAClient(timeout=3)` probe over 45s,
which a deep serial queue produces just as reliably as a wedge does. The
difference is that a wedge means NO request lands for ANYONE, and every landed
action POST already appends to the shared feed from every process
(`wda_client._append_activity`), so `_actions_landing()` now stats that file and
an mtime inside one poll resets `down_since` exactly like an answered probe. It
is a stat and not a probe on purpose: a WDA call aimed at a link suspected of
being wedged queues behind the very hang it is trying to detect. The window is
ONE POLL (`_HEAL_POLL`, 20s) and not the 45s silence floor — stacking the two
windows doubled time-to-recovery on a real wedge (~120s against ~80s) while the
viewer is dark — and `_should_heal` is untouched, so staleness alone still heals
nothing. Tests pin `viewer.activity_file` to a path of their own; against the
repo's real feed, which any local phone session touches, every healing test
passes by doing nothing.

The rest of pass 2 is the sleeps and reads pass 1 listed and did not take.
`set_field_text`'s 0.4s and `_open_thread`'s 0.8s "keyboard slide-up" are now
`_await_keyboard(cap)`: one bounded `**/XCUIElementTypeKeyboard` probe on a
`_KEYBOARD_POLL` 0.1 throttle, capped at that caller's own old sleep, so the
worst case is byte-identical to the sleep it replaces. The cap bounds the PROBES,
not just the rests between them — the only class chain timed on this device costs
328ms, so a probe started with less cap left than that runs past the sleep it
replaced; the loop sleeps out the remainder instead, and a probe that raises
`WDAError` pays out the REST of the cap rather than typing into a keyboard that
may not be up (dropped first keys is the recorded failure, and it is not being
re-run). `wait_for_text` and `wait_for_app` keep their `interval` parameter (both
are registered MCP tools) but default to `_TEXT_POLL` 0.25 and `_APP_POLL` 0.1
and rest `max(interval, last read)` clamped to the deadline, so `timeout` bounds
them at one read of overshoot instead of two. `/api/type` moved into
`_human_gesture` — `flushKeys` and the paste box are the viewer's real keystroke
path, nothing reads the tree after them, and every batch of typed characters was
paying WDA_IDLE_WAIT=2. `Handler.timeout = 60` reaps the connection a keep-alive
tab leaves parked in `rfile.readline()` (socket read timeout only; a test proves
a 2s handler survives a 1s timeout, rather than reasoning about it). The wheel's
flick threshold became `WHEEL_FLICK_MIN_PT` (150). `_create_session`'s settings
POST prints its swallowed `WDAError` to stderr — still best effort, but seven
keys the live view and every gesture wait ride on should not miss in silence, and
it is the line that will name an older WDA rejecting `accessibilityDeadline`.
`VIEWER_PHONE_POLL_SECONDS` and `WDA_ACCESSIBILITY_DEADLINE` are in
`.env.example` at last.

One bug found by review inside pass 2 and worth its own line: the hand-written
MCP wrapper `mcp_server.wait_for_text` passed `interval=0.5` positionally, so
tuning the helper's default moved nothing an agent could see. A wrapper must
never re-declare a helper's default; the guard is a test that walks every
`mcp_server` function shadowing a helper name and compares the defaults of every
shared parameter, so the next tuned default cannot be shadowed either.

**What still needs a device measurement.** Nothing here was timed on hardware.
The knobs, and the exact check each one wants (measurement plan, section 6 of the
review: 10 samples per number, median and p95, and re-run the control after every
config change — this codebase has killed three "obvious" theories that survived
only until their control was re-run):

- `viewer.html WHEEL_FLICK_SECONDS` (0.15, unmeasured) and `WHEEL_MAX_TRAVEL`
  (0.45, the measured value, restored after 0.6 was found to put the swipe
  endpoint at y=-84 off a 390x844 screen). On a long list, wheel one notch and one
  hard spin at `seconds` 0.25, 0.18, 0.15, 0.12, 0.10, five times each. Record two
  things: did the gesture land at all (screenshots before and after must differ),
  and how far the content moved. Repeat on Safari and on the Home Screen — paging
  is a different recogniser. Stop at the shortest duration that lands 5/5 on all
  three surfaces, then back off one step. Same pass re-checks the drag handler's
  `drag.moveT` change.
- The three predicates, BEFORE benchmarking anything that rides on them.
  `c.find_first('**/XCUIElementTypeIcon[`label CONTAINS[c] "Settings" OR name CONTAINS[c] "Settings"`]')`
  must return an id on the page holding Settings and None on a page that does not;
  same for the Cell predicate with a real contact name, the
  `label BEGINSWITH "Contact photo for " OR name BEGINSWITH ...` button predicate
  inside a thread, and the PageIndicator `value == "Page N of T"` verify. If any
  returns None where it should match, only `_open_thread`'s probe gate keeps that
  path alive and the rest are silently slower, not broken. Then
  `bench("find_icon", lambda: helpers.find_on_home_screen("<icon on page 3>"))`
  from page 1, before and after.
- `WDA_TYPING_FREQ` (60, WDA's own default, a deliberate no-op today). Run
  `helpers.set_field_text(field, "x" * 100)` five times each at 60, 80, 100, 120
  in a scratch field, diff the read-back character for character, record the wall
  clock. Stop at the highest rate with 5/5 exact read-backs — it fails quietly, as
  refused sends, not as an error.
- `WDA_TAP_HOLD_MS` (80, the shipped literal). 20 taps at 80 and 20 at 40 on a
  dense screen (keyboard keys, small Settings rows), counting misses. Never on the
  passcode pad — `_enter_passcode` pins its own 80 for that reason.
- `helpers._STABLE_INTERVAL` (0.15, was 0.5). Time `scroll_until_found(...)` on a
  long list at 0.5 and 0.15, five runs each, and confirm the same result is found.
  Walk it back to 0.5 if a scroll starts stopping short.
- The viewer idle wait (`_human_gesture`, `waitForIdleTimeout=0` on
  `/api/tap`, `/api/swipe`, `/api/long_press`, `/api/key`). Interleaved A/B on one
  screen, ten swipes at 2 and ten at 0, alternating, restoring in a finally. Then,
  with 0 set, run `goto_home_page(4)` and `find_on_home_screen(...)` five times
  each and count how often they need the corrective pass. That count is the whole
  decision, and it is also the measure of the accepted cross-process exposure: an
  MCP agent in another process is not held by `_ACTION_LOCK`.
- `_HOME_DEADLINE` (2.8) and the duty-cycle floor: instrument the loop to print
  the iteration and elapsed on return, from three apps, five times each. Confirm
  the total stays inside the ceiling and that detection lands closer to 830ms.
- `_SEND_SCAN_TRIES` / `_SEND_SCAN_INTERVAL` (2 / 0.5): after the Send tap, poll
  `ocr()` for the Send button and record how long the toolbar takes to be ready.
- `_SEARCH_POLL` / `_SEARCH_PROBE_SECONDS`, on a just-woken phone, which is what
  the 20s deadline exists for.
- `_tune_mjpeg` (T2-6): mint a fresh session with the viewer open and watch the
  stream. Note whether the picture degrades in place, hiccups, or drops to the PNG
  poll, and how long until `/api/status` retunes it. No user-facing claim about
  this should be made before that.
- Free, no code: turn on Reduce Motion (iOS Settings, Accessibility, Motion) and
  re-time a page walk — the Home Screen page transition is part of what a swipe
  round trip waits out.
- Pass 2's knobs, all unmeasured. `**/XCUIElementTypeKeyboard` FIRST, like the
  other three predicates: `c.find_first("**/XCUIElementTypeKeyboard")` must
  return an id while the keyboard is up (Messages compose, Messages search) and
  None with it down. A predicate that never matches is not a breakage here — the
  cap makes `_await_keyboard` exactly the sleep it replaced — but it makes the
  whole change a no-op, so confirm it rather than assume it. Then time
  `set_field_text(field, "hi")` and `_open_thread` five times each with the
  keyboard already up and with it down; `_KEYBOARD_POLL` (0.1) only matters if
  the probe turns out cheaper than the 328ms class chain measured today.
- `_TEXT_POLL` (0.25) and `_APP_POLL` (0.1): with the duty-cycle floor these
  should not raise the request COUNT against a slow read, which is the thing to
  check. Instrument `wait_for_text` on the Home Screen (a 3.0-5.7s `/source`)
  and count polls per 10s timeout at 0.5 and at 0.25 — the two numbers should
  match, because the floor, not the constant, is what bounds them there. Then
  the same in-app, where the read is 0.22s and the shorter interval is the point.
- `WHEEL_FLICK_MIN_PT` (150): same wheel pass as `WHEEL_FLICK_SECONDS` above,
  one extra question — does a one-notch (sub-threshold) wheel still nudge a list
  without overshooting, and is 150pt the right place for the boundary on a long
  Settings list and in Safari.
- `Handler.timeout` (60) needs no phone: it is a socket read timeout and the test
  already proves it does not bound handler execution.

**Left on the table** (noticed during the pass, deliberately not implemented):

Pass 2 landed 1, 2, 5, 6, 7, 8, 10, 11 and 12; each item below is the pass-1
record of why it was left, and the Pass 2 section above says what actually
shipped. 9 (`unlock()`'s flat sleeps) stays open BY DESIGN — three entries in
this file live on that path, a burned swipe costs a whole extra wake cycle, and
the pad gets one attempt before an iOS lockout, so it is not tuned without the
phone in hand. 3, 4, 13 and 14 are still open.

1. src/phone_harness/config.py:75 and :80 — VIEWER_PHONE_POLL_SECONDS and
   WDA_ACCESSIBILITY_DEADLINE are both operator-facing knobs with no entry in
   .env.example (which stops at MJPEG_SCALE). WDA_ACCESSIBILITY_DEADLINE in
   particular is the one bound on the TikTok wedge and the one an operator would
   want to disable if a heavy app starts erroring instead of just being slow. Two
   comment blocks and two blank keys. I did not add them: only WDA_TYPING_FREQ
   traces to a finding in my brief.
2. src/phone_harness/wda_client.py:_create_session — the settings POST is still
   `except WDAError: pass`, and it now carries seven keys instead of three,
   including the MJPEG values the viewer's live view depends on. A silent miss
   there is now invisible in one more way than before (the report flags this under
   T3-2 for animationCoolOffTimeout; it applies to the stream keys too). The cheap
   version is not a read-back round trip but logging the swallowed exception, since
   the whole point of the `pass` is that a mis-tuned session is slow, not broken.
   Not implemented — it changes failure behaviour on the session path and is
   outside my findings.
3. src/phone_harness/wda_client.py:_append_activity — mkdir(exist_ok=True) runs on
   every single action append even though _write_shared_session already made the
   directory at session create. Microseconds, correctly listed as "already fine" in
   the report's section 5. Listing it only so the next auditor does not re-derive
   it.
4. Not on the hot path but worth a line: tests/test_wda_client.py's `wda` fixture
   resets six FakeWDA class attributes but not `last_settings` (pre-existing) and
   now not `last_actions` (mine). Both are always written before they are read in
   every test that uses them, but a future test that asserts "nothing was sent"
   would inherit the previous test's value. The fixture is not in my findings so I
   left the existing pattern alone rather than half-fixing it.
5. helpers.py:946 — `_open_thread`'s `time.sleep(0.8)  # keyboard slide-up` after
   tapping the Messages search field is the exact flat-sleep pattern T1-2 just
   retired on the send path, and it runs on every viewer "Text someone", every
   "Thread" click, every MCP send_message and every read_messages. A bounded probe
   for the keyboard (e.g. `**/XCUIElementTypeKeyboard`) with a 0.1s throttle would
   pay the real slide-up instead of the worst case. Needs the predicate verified on
   device first, same caveat as T1-3/T1-4.
6. helpers.py:510 — `set_field_text`'s
   `time.sleep(0.4)  # keyboard slide-up, or the first keys are dropped` is the
   same thing one layer down, and it runs on every send. Same fix shape, same
   probe. The comment records a real failure (dropped first keys), so this one must
   not be deleted, only made conditional on the keyboard actually being up.
7. helpers.py:1165-1166 — `wait_for_text`'s `interval: float = 0.5` is the same
   overshoot T2-3 just fixed in `wait_stable`, and it is worse: each poll calls
   `_invalidate_tree()` and re-reads the WHOLE tree, so on a busy screen the
   interval is dwarfed by the read and the 0.5s is pure tail. Cutting it needs a
   judgement call about how hard to hammer /source, which is why I left it — but it
   is a registered MCP tool, so an agent pays it directly.
8. helpers.py:699 — `wait_for_app`'s `interval: float = 0.5` sits on top of a
   ~76-102ms `active_app()` read, the same ratio T1-7 just fixed in `press_home`
   (~352ms cycle against an ~830ms event). The same wall-clock-deadline shape
   applies cleanly.
9. helpers.py:1422 — another bare `time.sleep(0.4)` inside `unlock()`'s wake/swipe
   sequence. Not touched because unlock's timing is the most incident-scarred path
   in the file and the report did not cover it; worth a look with the phone
   present, not without.
10. src/phone_harness/viewer.py:718 (`/api/type`) — this is the viewer's real
    keystroke path: viewer.html:1172 (flushKeys) and :1204/:1478 (paste box) all
    POST /api/type, while /api/key only carries arrows and numpad specials. Nothing
    reads the tree after it, so by T3-1's own criterion it belongs in
    `_human_gesture` and is probably the single biggest remaining win of that
    finding (every batch of typed characters pays WDA_IDLE_WAIT=2 today). Left
    alone because the brief enumerated tap/swipe/long_press/key; one-word change if
    the coordinator wants it, and test_human_gestures_run_with_the_idle_wait_off
    extends by one tuple.
11. src/phone_harness/viewer.html:1136 — the 150pt flick threshold is inline next
    to WHEEL_FLICK_SECONDS. If the device check turns into real tuning it wants to
    be a constant too; not done to keep the brief's "single named constant".
12. src/phone_harness/viewer.py:401 (Handler) — with keep-alive, an idle connection
    now holds its thread parked in rfile.readline forever. Browsers close their own
    idle sockets so this is not a leak in practice, but `Handler.timeout = 60` would
    reap pathological ones (BaseHTTPRequestHandler already turns a read timeout into
    close_connection). Not added: no evidence it is needed, and it is a behaviour
    change nobody asked for.
13. src/phone_harness/viewer.py:479-500 (/api/status) — `_tune_mjpeg(self.client)`
    still runs on every 5s poll and is the only thing that reapplies the MJPEG
    settings after a remint, which is T2-6's territory (wda_client.py, not my file).
    Nothing I changed makes it worse — orientation() still heals the session before
    it — but T2-5's comment block and T2-6 describe the same coupling, so whoever
    lands T2-6 should re-read that comment.
14. Report correction worth recording: the report's T1-1 fix text says the drag
    handler already ships `dist > 150 ? 0.12 : 0.25`. It does not — there is no 0.12
    anywhere in viewer.html, and the drag handler's duration was a plain elapsed-time
    clamp. Anyone re-reading the report will otherwise think 0.15 is a regression
    from a proven 0.12; it is a fresh, unmeasured guess.

**Do not re-propose** (measured dead or already correct, from the same review):
`find_first` switching to POST /element; `waitForIdleTimeout=0` as a general lever
or as a wedge cure; `snapshotMaxDepth`; `defaultActiveApplication` pinning;
`enforceCustomSnapshots`; `shouldUseCompactResponses` (already YES);
`activeAppDetectionPoint`; lowering MJPEG_FPS; merging the viewer's `_app_is_open`
read into `press_home`'s loop; queueing or lengthening `_ACTION_LOCK`'s 409 drop;
batching gestures into one pointer source.

---

## 2026-08-17 — TikTok's feed wedges WDA outright, and every diagnostic blamed the signature (issue #2)

**Symptom.** Reported by tqninh: `swipe(200, 700, 200, 250, 0.5)` works in Photos,
the Home Screen and Facebook reels, and in TikTok's For You feed it raises
`WDATimeout` after 30s. Everything after that is dead: `up()` fails with XCTest
error 103, the doctor says "WDA not answering", and the tail sends the human to
Sideloadly. They re-signed for nothing, on a signature with 6 days left.

**Root cause (measured on device 2026-08-17, real TikTok 46.4.0, iOS 26.6).** Not
the gesture, and not the idle wait. Any WDA call that RESOLVES THE ACTIVE
APPLICATION can block with no upper bound while TikTok's feed is in front. A
plain read proves it with no gesture involved: `GET /wda/activeAppInfo` hung and
took WDA with it, while the calls that touch no app answered instantly BETWEEN
hangs on the same screen — `/status` 0.004s, `/screenshot` 0.22s, `/orientation`
0.01s. That is a gap between occurrences, NOT a probe that survives one: while a
call is stuck, the AX-free calls queue behind it and time out too (tqninh, 4/4,
below). Silence is the only symptom there is. `/actions`
resolves the active app, so every gesture inherits it. WDA serves requests ONE AT
A TIME, so the whole agent stops behind the stuck call: `/status` timed out for
321s straight while polling, and `:9100` refused connections too, so the viewer
goes dark, not merely slow. Killing TikTok released WDA in ~5s and backgrounding
it in ~20s, which is what proves the block is a synchronous wait on that app's
accessibility server.

**It is a HEAVY TAIL, not a hard block, and the first version of this entry got
that wrong.** The same screen answers in 0.08s or never, depending on what the
app is doing. Measured the same night: TikTok WARM (already resident) 0.08-1.1s
per call, 0/3 trials wedged; COLD (killed and relaunched) 2.9-5.5s, 1/3 wedged.
The rate collapses over time — it fired within a call or two, repeatedly, for
~20 minutes after the app was first installed, and hours later 28 swipes across
7 cold starts produced none. So it cannot be reproduced on demand ON THIS PHONE,
and any experiment against it MUST re-run its control (see the retraction below).

**It IS reproducible on demand on another device (tqninh, 2026-08-17, issue #2,
4 runs).** No gesture, no SideTap, no Python: `ios launch com.ss.iphone.ugc.Ame`,
sleep 3s, then poll `GET /wda/activeAppInfo` over curl with a 10s timeout. Wedged
on round 1 three times and round 2 once, 4/4, over 16 minutes — one run was
already wedged at baseline. So the rate is a property of the DEVICE and the app's
state, not of the harness, and their phone is the one to run any future
experiment on. Their post-wedge probes are what corrects the paragraph above:
`/status` and `/screenshot` timed out at 10s in every run. `ios launch
com.apple.springboard` recovered all four (14.35s once, then back on the first
2s poll each time), which is the first confirmation of `_unwedge` by someone
other than us, on hardware other than ours.

**Three hypotheses killed by measurement, do not retry them.** (1) Quiescence:
`waitForIdleTimeout=0` (the standard Appium answer for video apps, and the exact
trick `_enter_passcode` uses) changed nothing — WDA's own source shows the
setting only bounds `_XCTSetApplicationStateTimeout`, a different wait.
(2) `defaultActiveApplication` pinned to TikTok's bundle, to skip active-app
detection: swipe 1 returned in 24.4s and looked like a fix, swipe 2 wedged it
permanently. (3) `enforceCustomSnapshots: true` made it strictly worse — wedged
on the first read.

**RETRACTED: the `snapshotMaxDepth` "fix".** Depth 1 answered `activeAppInfo` in
0.06s and landed a swipe in 0.89s where the default had just wedged, and a sweep
of 24/16/12/8/4/2 was clean at every step. It was a false positive: re-running
the CONTROL at the default depth 50 then survived 8 swipes too, so the sweep had
measured a quiet phone, not a working setting. Lower depth does cut latency and
variance (≤8: 0.90-0.95s; 50: 0.94-6.94s) but nothing shows it prevents the tail,
and depth 16 already truncates Settings' tree (55 nodes → 31). Not shipped. The
lesson is L1, exactly: a check whose control never fails has been run, not
verified.

**Nothing in WDA can bound it, and that is a regression upstream.** No gesture
route avoids resolving the active application (read from `FBElementCommands.m`,
every one of them), and no snapshot timeout setting exists any more:
`snapshotTimeout` and `customSnapshotTimeout` were removed in
appium/WebDriverAgent#970 (merged 2025-01-16) as a documented breaking change,
and they existed for exactly this class of problem (#89, #181). Filed as
appium/WebDriverAgent#1210. Pinning an older WDA to get the setting back is dead:
every iOS 26 fix landed after #970.

**Fix.** SideTap cannot drive that screen and does not pretend to. What it can do
is stop lying about the state and recover without a restart. `WDAClient.link_state()`
returns up / wedged / down — a wedged WDA accepts the socket and never answers, a
down one refuses the connection, and the two need opposite repairs.
`device.foreground_springboard()` (`ios launch com.apple.springboard`) releases
the accessibility wait over USB with no WDA involvement; `up()` tries that FIRST
when the link is wedged and never restarts (measured end to end: wedge, then
`up` recovered in 38.9s, next gesture 1.04s). The doctor reports "WDA is wedged
by the app in front, not down" with that repair, and the failure tail refuses to
name Sideloadly when the profile on disk is still valid. `a3ba172` then made the
recovery automatic — the viewer's existing heal loop covers a wedge, gated on 45s
of CONTINUOUS silence so it cannot press Home during a legitimately slow call
(8.3s swipe, 20.5s first gesture after deep sleep, unlock's own 45s client), and
it logs "recovered a wedged link (pressed Home)" to the activity feed so a phone
that moves on its own says why. The REPAIR is now verified against four natural
wedges on tqninh's device (springboard launch, recovered 4/4); the 45s WATCHDOG
gate is still unverified there, because their script presses the repair by hand.

**Amendment (2026-08-20): the viewer said the same wrong thing the failure tail
used to.** `/api/status` flattened every failure into `input: False`, and the
page unhid the Sideloadly "Fix input" wizard for all of them — so a WEDGED link,
which by definition proves the signed runner is on the phone (the socket
accepted), was still being prescribed the re-sign that cost this issue's
reporter a full Sideloadly round with 6 days left on a good signature. The
endpoint now answers `link` ("up"/"wedged"/"down") beside `input` and the page
hides Fix input on "wedged" only; "Restart link" stays visible in every failing
state, because `POST /api/up` unwedges before it restarts and is the repair for
both. It costs no extra WDA call — `_request` already raised `WDATimeout` for
accepts-then-silence and a plain `WDAError` for refused, so it is an `isinstance`
on an exception the handler was catching anyway. Same pass: a wedge leaves the
MJPEG `<img>` holding its last frame with no `onerror`, so the live view looked
perfectly alive while nothing answered; `/api/status` now publishes `silent_for`
(the age of the watchdog's `down_since`, zeroed on any answered request) and the
page dims the picture and names it frozen past 60s.

**What does NOT reproduce it (measured 2026-08-17, 68 trials, zero wedges).**
"TikTok wedges and YouTube Shorts does not" is not a property of the apps. Paired
against YouTube on the identical routes, warm TikTok was the same speed
(`activeAppInfo` median 76ms vs 72ms, swipe 853ms vs 827ms, 0/32 wedged), and
`ios kill` + relaunch did not bring the failure back either (0/36 wedged) — cold
TikTok was actually FASTER than cold YouTube on the first call after launch
(0.06s against a consistent 0.37-0.40s, 3/3 cycles). The AX-free control
(`/status`) sat at 2-6ms throughout, so the link was never the variable. So
killing and relaunching is NOT the cold state that mattered: the original
occurrence was ~20 minutes after TikTok was INSTALLED, and a kill leaves every
cache, login and downloaded feed in place. Reproducing it plausibly needs a
FRESH INSTALL, not a fresh process, which costs a real login. Do not spend
another night on kill/relaunch. The one reproducible effect found: the first
`activeAppInfo` after a cold launch pays the app's startup work (YouTube 0.37s
against its own 0.07s steady state), which is the same phenomenon bounded — it
supports the maintainer's own suggestion that a trivial app blocking its main
thread is a better repro than any real app.

**Evidence for upstream.** #1210's maintainer asked for one thing: device syslog
captured ACROSS an occurrence. `syslog.py` does that. It cannot be armed on
demand, because the wedge cannot be scheduled and `ios syslog` costs ~100 MB/hour
on disk (27 KB/s measured), so the stream runs the whole session as a ~2-minute
in-memory ring and only reaches disk when `admin._unwedge` marks it. The mark
happens BEFORE the Home press, because the repair ends the occurrence, and the
ring has to outlast the 45s DETECTION delay, not just the event — the log worth
reading is from when the app stopped answering, already 45s+ old by the time the
watchdog calls it a wedge. Verified live end to end (901 lines in 10s, a 175 KB
dump with 120 lines recorded after the mark), still not against a natural wedge.

**Prevention.** Error 103 right after a hang is the stuck runner, not the
signature — a restart cannot land while the old runner is still on the phone.
Before blaming a signature, check whether the socket ACCEPTS: connect-refused is
a dead link, accept-then-silence is an app holding WDA hostage. And note what the
viewer itself does: `/api/phone` reads `active_app()` every 10s, so sitting in an
app like this with the viewer open can trigger the wedge with no agent involved.
There is no cheap guard, because knowing which app is in front requires the call
that hangs.

---

**2026-08-16 — one-liner.** `pip install -r requirements.txt` for the new
pymobiledevice3 dep pulled `typer` to 0.27.1 and `rich` to 15.0.0 in Wes's
global Python, breaking the pins `repowise` (rich<14) and `huggingface-hub`
(typer<0.26) declare. Root cause: installing into a shared kitchen-sink
interpreter with no constraint on the transitive upgrade. Fixed by pinning back
to `typer 0.25.1` + `rich 13.9.4`, which satisfies pymobiledevice3 (typer>=0.25,
no rich constraint) and both neighbours; textual's `rich>=14.2` pin is violated
on paper but it imports and runs fine. Prevention: check what a new dep drags
along before installing into the global interpreter.

---

## 2026-08-16 (later) — Sideloadly 0.60 writes NO profile to disk, ever: the watcher premise is dead

**Symptom.** Same night, second run. Both watcher nets armed, Sideloadly signed
and installed cleanly, and the wizard failed with "Sideloadly signed but wrote
no provisioning profile we could find".

**Root cause.** Not a dropped event, not a missed directory, not a race. A full
mtime scan of every temp root (`%TEMP%`, `C:\Windows\Temp`, all three Sideloadly
dirs) across the whole 23:05-23:20 sign window found Sideloadly wrote **exactly
three files**: `account-appids.json`, `sessions.json`, `installations.db`. No
staging dir, no `.mobileprovision`, nothing. **Sideloadly 0.60 signs in memory
and streams the IPA straight to the device.** No watcher can ever catch a file
that is never written. Ruled out on the way: both Sideloadly processes run as
Wes (no service, so no SYSTEM-temp theory); the cached IPA is the Aug-8 *input*,
not the signed output; the only GUID `.tmp` files in the window were a browser
PNG and JPEG.

**Also disproved:** "a post-expiry bare install might be enough". It is not —
`up()` after tonight's fresh sign still died on `Failed to load the test bundle
(Error code: 103)`. The local re-sign is mandatory, so the profile is mandatory.

**Fix.** Stop watching the PC. Read the profile off the **phone**, where iOS
keeps it at `/var/MobileDevice/ProvisioningProfiles/` after installd runs.
go-ios cannot: it has no misagent (`ios profile list` is MCInstall and returned
`[]`, `fsync`/AFC returned error 8, and `sign app`/`ui install` both make
`--profile` mandatory). `pymobiledevice3 provision dump <dir>` can, and **still
works on iOS 26.6** — it pulled the profile Sideloadly had minted minutes
earlier, expiry a full 7 days out. Feeding that to
`phone-harness fix-input <path>` took all 11 checks green.

**Lesson.** Two fixes in one night both patched the watcher; the third question
should have been "does the file exist at all". When a capture keeps missing,
prove the artifact is written before improving how you listen for it.

---

## 2026-08-16 — Fix input sat on "Waiting for Sideloadly" after Sideloadly said Done, for ten silent minutes

**Symptom.** Wes clicked Start in Sideloadly with the wizard armed. Sideloadly
reached "Done. 100%". The wizard never left step 2 and never ran the rest of
the list.

**Root cause.** `capture_profile` never saw a profile, and had no way to say
so. Three separate things:

1. **The watcher's premise was never verified.** The script's own comment says
   Sideloadly writes `embedded.mobileprovision` into a `%TEMP%\tmpXXXX\...app`
   folder — but of the writes in the 21:40:45-21:41:15 window around a sign
   that *succeeded* (`installations.db` row updated, `last_error` empty), **no
   temp staging directory was created at all**. The watcher itself was fine: a
   decoy profile dropped into `%TEMP%` was captured in 3s. Every documented run
   of this path since 2026-08-13 either "missed the capture" or timed out — it
   has never been observed succeeding.
2. **One net, no fallback.** A `FileSystemWatcher`'s kernel buffer is 64KB and
   drops events silently on a busy tree. Nothing polled, and the `Error` event
   was not even registered, so a drop was indistinguishable from no write.
3. **Silence for the full window.** The only progress line was "armed - click
   Start now", printed once. 600s of nothing looks identical to a hang.

**Fix.** The watcher now runs two nets (events + a 250ms scan of anything born
since it armed, plus Sideloadly's own folders as roots), prints every sighting,
every buffer overflow and a 5s countdown, and watches
`%LOCALAPPDATA%\Sideloadly\installations.db` — when that moves without a
profile appearing, it stops 60s later and says "Sideloadly signed but wrote no
profile" instead of timing out generically. Everything it prints goes to
`.state/fix_input.log` and to the wizard live. The wizard's last line is now
the doctor checks, re-rendered until green.

**Lesson.** A capture that can only report "timed out" teaches nobody
anything — the log of what it *looked at* is the whole diagnosis. And the
comment describing a mechanism is not evidence the mechanism happens: this one
described a temp folder that does not exist.

---

## 2026-08-14 — The 17s first-gesture-after-sleep block: a recovery path that could only fire on an error it never received

**Symptom.** Wes: the 17s block a previous session saw during live verification
and filed as "pre-existing, separate issue" without trying to fix it.

**What it is.** Reproduced on device: after the phone sleeps, the first
`/actions` on a session that predates the sleep hangs **16.25s** inside
XCTest's snapshot timeout, then fails `point.x != INFINITY`. A fresh session is
0.01s and its swipe 1.18s. The trigger is the display **WAKING**, not the lock:
in the same run a real tap on the still-dark screen answered in 0.59s, and the
gesture right after `press_button("home")` was the one that hung. Reproduced at
16 minutes asleep and again at 5. Three earlier attempts (lock + 2s/30s/90s
dark) never reproduced it — because none of them woke the display, which is
what made it look unreproducible at first.

**Why only unlock() was safe.** `unlock()` mints a fresh session before it
wakes anything, so it never meets the poison (10.28s end-to-end from 16 minutes
asleep). Every other gesture path met it and relied on `_session_request`
healing after the fact — and that heal can only fire on an error it SEES:

- On a patient client (unlock's 45s clone) the INFINITY error does arrive, so
  it self-heals — at the cost of the whole 16s. That is exactly the 17.58s in
  the activity log at 11:02:20.
- On any client whose timeout is SHORTER than the hang, it never arrives.
  `viewer.py`'s `Handler.client` is `timeout=10` and serves every human tap,
  swipe and keystroke. Reproduced against a fake WDA hanging 16.2s: tap #1
  failed at 10.01s, the session was NOT replaced, tap #2 failed at 10.02s —
  unchanged forever, because a timeout message matches neither string in
  `_session_unusable`.

**Fix.** `requests.Timeout` now raises `WDATimeout(WDAError)`; an `/actions`
timeout marks the session and the NEXT gesture replaces it (adopting a
published replacement if one exists, else minting). The timed-out gesture is
NEVER replayed — a timeout does not cancel it, the gesture may still be
landing, and a replay double-taps. And because that only saves the second
gesture, a gesture following 30s without this client landing one now mints up
front (0.01s) rather than paying 16s to discover the poison. Verified live on
the same sequence that produced the control: the first gesture after a
5-minute sleep and a wake went **16.25s → 0.59s**, with the session id changing
to prove the mint is what did it.

**The trap, found only by running it live.** The first version keyed "nothing
has driven the phone lately" on the shared activity log, which every action
POST stamps. The `press_button("home")` that wakes the display is such a POST,
so the wake reset the clock and the rule never fired on the one sequence it
exists for — the live run showed the control hanging 16.25s and the fix sitting
there doing nothing, with a fully green suite. The clock now counts only LANDED
GESTURES: a wake answered in 0.47s on the very session whose next gesture hung
16.25s, so it proves nothing about whether that session can still act.

**Lesson.** A recovery keyed off an error message can only fire on an error it
actually receives; when the failure it guards is a HANG, every client with a
shorter timeout is unprotected — and here the shortest-timeout client was the
human-facing one. Second lesson: five sabotage runs against the new tests found
two that could not fail (one derived its own threshold from the constant it was
testing), including the one that should have caught the trap above.

---

- **2026-08-17 — pushed a green suite to a red CI: the syslog capture ate the heal loop's scripted clock.** `854fd0b` called `syslog.start()` inside `viewer._heal_loop`. `start()` reads `time.monotonic()`, and `_run_heal_loop` scripts `monotonic` on the REAL time module, so each pass consumed one extra tick and `test_heal_loop_heals_after_sustained_silence` saw 0 heals instead of 1. It passed here 403/403 twice because this machine HAS go-ios: the first call spawned a real capture, `_proc` went non-None, and every later call early-returned before touching the clock — which also means the unit suite was spawning `ios syslog` against the phone. CI has no go-ios, so nothing ever spawned and every pass shifted. Fix: the capture gets its own `viewer._syslog_loop` (60s), touching nothing under a scripted clock. Prevention: a shared loop is shared state — a call added to one is driven by every test that drives it. And when a green local run and a red CI disagree, the difference is the environment, so reproduce the CI environment (here: `ios_path()` → None) rather than re-running locally. The new guard is a source scan because the viewer's own heal tests pass on this machine with the bug present.
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

---

## 2026-08-20 — Unlock ran ~20s then "nothing happens" with a priority notification: lit ≠ unlocked

**Symptom.** Wes: the viewer's Unlock button, with a priority notification on
the lock screen, ran ~20s showing "Unlocking…" and then did nothing — the phone
stayed locked but the button reported done.

**Root cause.** `unlock()` had a shortcut: after the wake+swipe, if no passcode
pad appeared but the screen was LIT (`>= _LIT_SCREEN_BYTES`), it returned
declaring success on the theory "lit + no pad = phone was merely asleep and the
swipe took it to the home screen." A priority notification KEEPS THE LOCK SCREEN
LIT while the phone is still locked. When the wake swipe failed to raise the pad
(the ~16s wake-transition hang from 2026-08-14, after which the notification-lit
screen never darkens to trigger the dark-screen retry), that shortcut fired and
returned `{ok: true}` over a phone still on its lock screen. Without a
notification the screen re-sleeps to dark between reads, so the code took the
correct retry path and eventually unlocked — which is why it only broke WITH a
notification. Confirmed by trace: no-notification unlock reaches the dark retry
and succeeds in ~35s; the lit branch returns early.

**Fix.** `_on_lock_screen(tree)` reads the CoverSheet markers already present in
the tree pad_appears fetched — `SBCoverSheetWindow`, "Swipe up to unlock",
"Locked" (device dump 2026-08-20). The two "lit and no pad → return usable"
branches now also require NOT being on the lock screen; a lit lock screen falls
through to the second wake+swipe (which typically raises the pad) and, if the
pad still never comes, raises an honest error naming the notification instead of
lying success. Same lying-success class as the dark-screen silent return fixed
2026-08-13, pointed at the lit case. Verified live: a locked phone unlocks to the
887 KB home-screen frame, no false early return.

**Not fixed (pre-existing, out of scope).** The ~16s first-gesture-after-wake
hang still costs the unlock ~35s end-to-end. Twelve session-ordering trials on
device 2026-08-20 showed it fires ~50% regardless of session freshness (a
fresh session born while dark hung 3/3 in one run), so the 2026-08-14
mint-up-front does NOT reliably avoid it — it is a WDA wake-transition AX-snapshot
heavy tail, the same class as the TikTok wedge, and nothing cheap predicts it
(a coversheet-readiness probe that hit fast correlated with a fast swipe but did
not prevent the hang when it missed). Left alone deliberately: unlock() has three
ERRORS.md entries and its flat waits are the one untuned path in helpers.py.

---

## 2026-08-31 — Lock-screen notifications cannot be tapped at all: their own AX snapshot hangs every gesture ~16.9s, which then lands on a re-slept dark screen

**Symptom.** Wes: "priority notifications still break this product, I can't click
on them, it takes forever to unlock." Clicking a notification in the viewer
showed nothing but a red dot; unlock with a priority stack up ran ~36s.

**Reproduced live** (real Priority Notifications stack + a TIME SENSITIVE
reminder on the lock screen, measured):

- Lock-screen `/source` is EMPTY of notifications: 4 rows total
  (`SBCoverSheetWindow`, one `ListCell`), so `find_text`/`tap_text` can never
  find one — agents are blind to them.
- Tap on the DARK lock screen: lands in 0.70s, silently swallowed (synthetic
  taps do not wake the display).
- Tap on the LIT lock screen: hangs 16.8-16.9s in `/actions`, 4/4 tries —
  adopted session, warmed session, and a `fresh_session()` minted after the
  wake all hang identically, and waiting 3s into the lit window changes
  nothing. The viewer's 10s client aborts at 10s (red dot, no words — the 502's
  error text was dropped by `postGesture`). The lit window is only ~8-10s, so
  when the gesture finally fires the screen is ALREADY DARK again — swallowed.
  Lit → hang until dark; dark → no-op: a perfect catch-22. No wake cycle can
  ever deliver a tap to a lock-screen notification.
- `press_button("home")` never hangs (0.50s every time): button presses are HID
  events, no AX snapshot. Only touch gestures pay the CoverSheet snapshot.
- **Control, same night: a CLEAN lit lock screen taps fine (0.5-0.6s, 2/2)** —
  after the unlock cleared the stack, the same wake+tap through the same
  lock-crossing session landed instantly. The hang is the AX snapshot OF THE
  NOTIFICATION STACK, not the lock screen, not session poisoning. That is why
  2026-08-20's twelve trials saw it "fire ~50% regardless of session
  freshness": the variable nobody controlled was what sat on the lock screen.
  The irony is exact: when there IS a notification to tap, the snapshot of it
  is what makes it untappable.
- `unlock()` end-to-end with the priority stack: 35.9s — one full ~16.9s hang
  burned on the first wake swipe, then the second cycle does the real work.
  Matches the 2026-08-20 entry; nothing new to tune there. With the priority
  stack on screen the hang fired 4/4 tonight (vs ~50% bare on 2026-08-20).

**Fix (what a fix can honestly be).** The hang is the same unboundable XCTest
AX-snapshot tail as the TikTok wedge — no WDA setting bounds it (see
2026-08-17/20). So the product change is honesty, not gymnastics:
`WDATimeout`'s message now names the locked-phone case FIRST and says
lock-screen notifications cannot be tapped (unlock instead), and viewer.html's
`postGesture`/`gesturePost` now surface the 502 error text as a hint instead of
a silent red dot. The working flow for "open that notification" is: Unlock,
then the Notifs edge button (Notification Center on an unlocked phone has a
full tree and normal-speed taps). skills/phone-gotchas carries the trap.

**Lesson.** "I can't click on X" on a lock screen is not a click bug — it is
two silent swallows stacked (abort at 10s while lit, landing on dark), and the
viewer's failure path was eating the one message that explained it.

## 2026-09-04 — Wrapping the MCP server as a declick adapter: three misses in a row

**Goal.** Expose the helpers as shell verbs through declick so subagents (no
MCP) can drive the phone and the ~2.3k-token tool catalog leaves every session.

1. `declick add "mcp:phone-harness mcp"` died with `spawn phone-harness ENOENT`.
   Node's `child_process.spawn` without a shell resolves `.exe` but never a
   `.cmd`, and `phone-harness.cmd` is all the repo has (deliberately not pip
   installable). Fix: `scripts/phone_mcp.py`, a plain `python <file>` that puts
   `src` on `sys.path` and runs `phone_harness mcp`.
2. `--name phone` and `--name sidetap` were both refused: declick installs a
   SKILL.md and a PATH launcher under the adapter name, and the hand-written
   `phone` skill and `sidetap.cmd` already own those names. The adapter is
   `iphone`.
3. `act --steps '[...]'` failed inside declick before the server was spawned
   ("--steps must be a JSON object, got [object Object]"): its MCP engine maps
   each array item through the object coercion, which re-parsed an already
   parsed object. Fixed in declick with a fixture tool and a test.

Also learned: `ocr`/`find-text` rows sit under the envelope's `screen` key, so
the trimming idiom is `--rows screen --fields text,x,y`, and the warning
survives in `meta.extra`. Warm daemon call ~0.6s, cold 2-4s, measured.

**Lesson.** The launch command for a Node spawner was asserted from the README
instead of tried: prove the one-line spawn first, then build on it.

## 2026-09-04 — The app switcher cannot be swiped open: WDA never reaches the home-indicator zone

**Symptom.** Wes asked for a viewer button that opens the app switcher. The
standing note said it "needs a press/pause/release chain in wda_client" since
`swipe()` never holds before it lifts.

**What was measured.** Four hold shapes (move 300-800ms, hold 500-1200ms, from
y=h, h-1 and h-10) all left Settings untouched — active app unchanged, tree
unchanged, one of them merely scrolled the list. Then the PLAIN Home flick
(0.2s from y=h, h-1, h-5 to 0.4h) could not leave Settings either. The first
attempt had a keyboard up, which is a known swallow, so it was re-run from
Settings with no keyboard.

**Root cause.** XCTest's synthesised touches are delivered to the frontmost
app; SpringBoard's home-indicator recogniser never sees them. The top edge
(Notification Center, Control Center) and the left edge (Back) work because
those recognisers hit-test inside the app's own window. So no gesture shape
can open the switcher and the hold theory was wrong.

**Fix.** Read the switcher instead of opening it: `ios ps` lists the running
processes over USB (0.68s) and, joined against `ios apps --list` (0.34s) and
`BUNDLE_IDS`, it is exactly the switcher's contents. `ios kill <bundle>` is the
swipe-up. Shipped as `helpers.open_apps()` / `close_app()`, the viewer's
**Open apps** overlay, and two MCP tools.

**Lesson.** A "needs X first" note about a gesture is a hypothesis until the
simplest gesture in the same zone has been tried. Test the cheapest thing that
would have to work (a plain flick) before building the elaborate one.
