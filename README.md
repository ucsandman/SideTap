# SideTap

**Let an LLM agent see and control a real iPhone from a Windows desktop. No Mac, no Xcode, no Appium server, no jailbreak, free Apple ID.**

[![tests](https://github.com/ucsandman/SideTap/actions/workflows/tests.yml/badge.svg)](https://github.com/ucsandman/SideTap/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)
[![Website](https://img.shields.io/badge/web-sidetap.io-4493f8.svg)](https://sidetap.io)

![SideTap driving a real iPhone: the agent takes over, the kill switch stops it](docs/media/readme.gif)

Every iPhone automation route assumes you own a Mac. This one does not. SideTap drives a real iPhone over USB using [go-ios](https://github.com/danielpaulus/go-ios) and [WebDriverAgent](https://github.com/appium/WebDriverAgent), wrapped in a small Python harness an agent (or a human) can use directly.

It is a Windows rebuild of [phone-harness](https://github.com/ShawnPana/phone-harness), which relies on macOS iPhone Mirroring. Bonus over the original: WebDriverAgent exposes the real UI element tree, so the agent reads exact buttons and labels instead of OCR guesses.

```
[iPhone iOS 17+] .. WebDriverAgent app (sideloaded once, free Apple ID)
      | USB
[Windows]  go-ios (tunnel + launch + port forward)  ->  Python harness  ->  live viewer
      |
[Agent]    phone-harness <<'PY' ... PY   (helpers pre-imported)
```

## Set it up with an agent

Skip the manual setup: paste this into Claude Code (or Codex) on your Windows PC and let it drive, asking you only for the steps that need your hands.

```text
Set up SideTap (github.com/ucsandman/SideTap) on this Windows machine so LLM agents can drive my iPhone over USB.

1. Clone https://github.com/ucsandman/SideTap and run: pip install -r requirements.txt
2. Install go-ios globally: npm install -g go-ios
3. Read docs/setup-windows.md, then walk me through the parts only I can do: installing the Apple Devices app from the Microsoft Store, enabling Developer Mode on my iPhone, trusting this PC from the phone, and sideloading wda/WebDriverAgent.ipa with Sideloadly using my Apple ID.
4. After WebDriverAgent is installed, run from the repo root: phone-harness fix-input (I will click Start in Sideloadly when you tell me to).
5. Run phone-harness doctor after every step. Every FAIL names its own fix. Loop until all checks pass. Never guess at connection problems.
6. When doctor is green, run: python launch.py and confirm the live viewer shows my phone screen at http://127.0.0.1:8770.
7. If I use Claude Code, register the phone as native tools: claude mcp add --scope user sidetap --env PYTHONPATH=<absolute path to the clone>/src -- python -m phone_harness mcp (skip this step otherwise).
8. If I use Claude Code, install the bundled skills so every session reads the harness's traps before its first tap: copy the skills/phone and skills/phone-gotchas folders into ~/.claude/skills/ (skip this step otherwise).
9. Remind me at the end: free Apple ID signatures expire every 7 days. When touch input dies, phone-harness fix-input brings it back.

Ask me before anything that touches my Apple ID, my phone's settings, or sends anything from my phone.
```

## Features

- **Real UI tree, not screenshots.** `tap_text("General")` finds the actual element and taps its center. Coordinates are points, exact.
- **Live viewer in your browser** at ~34 fps: click to tap, drag to swipe, type on your keyboard, save screenshots. One-click **Unlock** (types your passcode from `.env`) and **Restart link** (the fix after a replug). Buttons for the system gestures that are easy to get wrong by hand (Back, Spotlight, scroll), numbered chips that walk you to any Home Screen page and say which one you are on, and a **Read thread** button that pulls the last messages of a conversation back into the page. Enter sends from the message and paste boxes, Shift+Enter or Ctrl+Enter breaks the line. Hotkeys: **Ctrl+Shift+S** copies the screen to your clipboard as a PNG (works even with the link down), **Ctrl+Shift+H** is Home and **Ctrl+Shift+B** is Back. Swipes and wheel flicks draw the line that was sent, and a picture that stops moving because the phone stopped answering is dimmed and says so instead of pretending to be live. Works even before touch input is set up.
- **One-call flows** like `send_message("Mom", "on my way")` that open Messages, find the thread, type, and send, with guardrails (see Security). It empties the compose bar first, because typing appends at the cursor and iOS keeps an unsent draft per thread, then reads the field back and refuses to send anything that is not what you approved. `read_messages("Mom")` reads the replies back.
- **Fast where it used to wait.** Settle detection compares the screen straight away instead of sleeping first: 877ms down to 318ms, measured on device, which returns about five seconds on a scroll that hunts through nine screens. The client keeps one connection to WebDriverAgent open, taking another 46% off each call. Screen reads drop wrappers and repeated labels for 64% fewer tokens. The first gesture after the phone has slept used to stall about 16 seconds inside the driver: it now takes a fresh session first, measured at 0.59s.
- **Camera roll to your PC over the cable.** The **Pull to PC** button in the viewer (or `phone-harness pull-photos`) copies new photos and videos from the phone to a folder you pick (`PHOTOS_DIR` in `.env`, default `~/Pictures/iPhone`) — no iCloud, no email-it-to-yourself. Already-pulled files are skipped, so after the first sync it only fetches what's new, and it runs over USB without touching the screen, so it works even while an agent is driving.
- **Native MCP tools.** `claude mcp add sidetap -- phone-harness mcp` gives any Claude Code or Claude Desktop session the whole helper API as typed tool calls — no Python piping.
- **Agent skills in the box.** Copy [`skills/phone`](skills/phone) and [`skills/phone-gotchas`](skills/phone-gotchas) into `~/.claude/skills/` and any Claude Code session picks up the helper API *and* the traps that otherwise cost an hour of debugging: Home Screen icons only drag in jiggle mode (and fail silently outside it), coordinates are points and not pixels, the page editor is too heavy for the UI-tree read, and what the harness genuinely cannot do.
- **Live activity feed.** Every tap, swipe, and keystroke count any agent sends shows up in the viewer as it happens, so you always know what just drove the screen.
- **A doctor that names the fix.** `phone-harness doctor` walks the whole chain and every FAIL tells you the exact command or click that repairs it, including a countdown before the 7-day free-ID signature expires. The viewer runs the same checks and re-runs them by itself while any of them fails, so a link that is still coming up settles to green with nothing to click. On a first run it opens a guided setup instead of a wall of red: five steps, each turning green as its checks pass, with the exact next action spelled out.
- **Free Apple ID signing that actually works.** Sideloadly leaves the nested `.xctest` bundle unsigned, so the driver never launches. `phone-harness fix-input` repairs that locally: no Apple password scripting, no paid developer account. See [How the signing fix works](#how-the-signing-fix-works).
- **Kill switch.** A red STOP button in the viewer freezes every agent action while you keep watching the screen.
- **Prompt injection gate.** Anyone who can text you can put words in your agent's input. So once the agent has read your screen or your messages, a send stops and waits for you to approve the exact text in the viewer. Running out of time refuses it. Set it to **Always**, **Flagged** (only asks when something looks off), or **Off** with one click in the viewer. The text you approve is also the text that sends: the compose bar is read back before the send and a mismatch is refused, so a draft left in the thread cannot ride along with an approved message. It bounds what an injected instruction can send, not what it can tap, and [Security](#security-and-responsible-use) says exactly where that line is.

## Quick start

Needs Windows 10/11 and an iPhone on iOS 17+. One line in PowerShell installs the PC side — Python and go-ios included, no Node.js needed:

```powershell
irm https://sidetap.io/install.ps1 | iex
```

It puts the app in `%LOCALAPPDATA%\SideTap`, adds a **SideTap** shortcut to your Desktop and Start Menu, and starts the viewer, which walks you through the phone side (Developer Mode, WebDriverAgent). It also installs the free **[Apple Devices](https://apps.microsoft.com/detail/9np83lwlpz9k)** app from the Microsoft Store. That app is Apple's USB driver, and Windows cannot see an iPhone without it: if it is missing, SideTap stops at "No iPhone found over USB". When the automatic install fails, the installer opens the Store page so you can click **Get** yourself, then replug the iPhone and tap **Trust**. Re-running the installer later updates SideTap and keeps your `.env` and state. Everything it does is plain to read: [site/install.ps1](site/install.ps1).

Working from a clone instead? (Python 3.10+, Node.js for go-ios):

```bat
git clone https://github.com/ucsandman/SideTap
cd SideTap
npm install -g go-ios     :: the USB bridge (this is why Node.js is needed)
pip install -r requirements.txt
python launch.py          :: opens the live viewer; the link comes up in the background
```

First time? Follow **[docs/setup-windows.md](docs/setup-windows.md)** (about 20 minutes, one-time: USB driver, Developer Mode, sideload WebDriverAgent).

Prefer one click? Run `powershell scripts\install_shortcut.ps1` once — it puts a **SideTap** shortcut on your Desktop and in the Start Menu. Double-click it (or double-click `sidetap.cmd`) instead of typing `python launch.py`. Add `-Startup` to also start SideTap when Windows starts.

Day-to-day commands:

```bat
phone-harness doctor      :: diagnose the whole chain, each FAIL names its fix
phone-harness up          :: start tunnel + WebDriverAgent + port forwards
phone-harness view        :: live viewer (click = tap, drag = swipe, keys = type)
phone-harness pull-photos :: copy new camera-roll photos to this PC over USB
phone-harness fix-input   :: re-sign the input driver (free Apple ID, 7-day cycle)
phone-harness notify-expiry --install  :: daily desktop toast before the signature lapses
phone-harness down        :: stop background processes
```

The side panel is a dashboard: quick actions (text someone, open an app), the agent activity feed, passive phone info, and a collapsed Debug card that runs helper one-liners by hand when a helper misbehaves.

## Agent usage

Pipe Python to stdin. Helpers are pre-imported:

```bat
phone-harness <<'PY'
open_app("Settings")
wait_stable()
tap_text("General")
screenshot("general.png")
PY
```

| Helper | What it does |
|---|---|
| `screenshot(path=None)` | PNG of the screen |
| `screen_info()` | screen size in points |
| `ocr()` | all visible text with center coordinates (from the real UI tree) |
| `ui_tree()` | full raw element tree (cached ~2s; every action invalidates it) |
| `tap(x, y)` / `long_press(x, y)` | touch at points |
| `tap_text("General")` | find text and tap it |
| `scroll_until_found("Notifications")` | scroll until it sits tappable mid-screen; returns the element |
| `find_on_home_screen("Brain Dump")` | walk to page 1, then sweep Home Screen pages for an icon; returns the element (~7s per page) |
| `type_text("hello")` | type into the focused field (APPENDS at the cursor) |
| `set_field_text(field, "hello")` | clear the field first, type, return what actually landed |
| `compact(ocr())` | drop wrapper/duplicate rows: ~64% smaller read, capped at 60 rows |
| `swipe(x1,y1,x2,y2)` / `scroll("down")` | gestures |
| `open_app("Settings")` | launch by friendly name or bundle id |
| `current_app()` / `wait_for_app(bundle_id)` | which app is frontmost / wait until one is |
| `send_message("Mom", "hi")` | open Messages, open the thread, type, send |
| `read_messages("Mom")` | read the open thread back: `[{text, from_me}, ...]` |
| `press_home()` | leave the app to the Home Screen; does NOT change which page you are on |
| `current_page()` | exact Home Screen position: `{"index", "total", "zone"}` (Today View is 0, App Library is past the end) |
| `goto_home_page(1)` | land on a specific Home Screen page, from anywhere, verified |
| `wait_stable()` | wait until the screen stops changing |
| `wait_for_text("Done")` | wait until specific text appears; returns the element |
| `unlock()` | wake + unlock (passcode opt-in via `.env`) |

Add your own helpers in `agent-workspace/agent_helpers.py`. They auto-load into every script.

### MCP (Claude Code / Claude Desktop)

The same helpers are available as native typed MCP tools:

```bat
claude mcp add --scope user sidetap --env PYTHONPATH=C:/path/to/sidetap/src -- python -m phone_harness mcp
```

(Adjust the path to your clone. `--scope user` makes the tools available in
every project; new sessions pick the server up automatically.)

Then any session can call `tap_text`, `ocr`, `send_message`, `screenshot`, and the rest directly — argument schemas and descriptions come from the Python signatures, so the two surfaces never drift.

One difference on purpose: the tools that hand phone content to the model (`ocr`, `find_text`, `read_messages`, `wait_for_text`) return `{"warning", "source", "flags", "screen"}` instead of a bare list, with the content under `screen`. The Python helpers still return plain lists. That envelope is where the agent is told the screen is data, not instructions, so it belongs at the model boundary and nowhere else.

The other difference is size. `ocr` and `find_text` **compact** what they return, because the model pays for every byte of it: whole-screen wrappers go, `rect` goes (`x`/`y` is what a tap needs), a `StaticText` or `Image` label repeating the text of the control that encloses it goes, and identical text landing twice in the same place collapses to whichever entry is worth tapping. Measured across four real screens, that is **64% fewer tokens** per read.

Dropping the inner label is also the safer target, since tapping it instead of its button is the classic mis-tap. Only those two label types are ever droppable, so anything independently tappable or stateful — a `Switch` inside its row, a `checkmark` reporting which option is selected — always survives. `Other` is deliberately **not** treated as a wrapper: the Home Screen search affordance is an `Other`, and dropping the type loses the only way to tap it.

Pass `ocr(full=True)` for the raw tree with rects. The Python helpers are untouched, so `viewer.py` and `send_message` still see everything.

## Security and responsible use

This tool is for **your own phone, under your supervision**. The guardrails are part of the product:

- **Lock the ports.** go-ios forwards WDA (:8100) and its MJPEG stream (:9100) on `0.0.0.0`, and WebDriverAgent has no auth, so by default anyone on your Wi-Fi could drive the phone. The viewer shows a red banner whenever the ports are exposed (the doctor flags it too); click **Lock ports** there (or run `scripts\lock_ports.ps1`, one-time, needs admin) to add a firewall rule. Loopback keeps working.
- **Kill switch.** The red **STOP** button in the viewer (or a `.state/STOP` file) blocks every phone action at the client chokepoint until you click **RESUME**, and the doctor calls out a forgotten STOP as its first check. It bounds a runaway agent.
- **Live activity feed.** Every action any process sends to the phone — taps, swipes, app launches, typing — lands in the viewer's **Activity** panel as it happens. Typed text is never recorded, only the character count (it can be a password or your passcode).
- **Send guardrails.** `send_message` refuses to send if the contact name is ambiguous or the opened thread does not match, and logs every send to `.state/actions.log`, shown as **Recent sends** in the viewer.
- **What you approve is what sends.** Typing is `POST /wda/keys`, which appends at the cursor rather than replacing, and iOS keeps an unsent draft per conversation. So a draft you left in a thread used to end up in front of the message the agent typed, while the approval card had already shown you the clean text. `send_message` now empties the compose bar first, reads the field back, and refuses the send outright when what is in it is not what was approved. The refusal names both strings. That closes the one gap where content nobody approved could reach a real person.
- **Prompt injection gate.** Everything the agent reads off your phone is attacker-controlled: anyone who can text you can put words in your agent's input. So once the agent has read the screen, a screenshot, or your messages, `send_message` stops and waits for you to click **Approve** on a red card in the viewer showing the contact and the exact text. Running out of time is a refusal, never a send. A message you type into the viewer yourself is not gated, and there is deliberately no argument to skip the gate, because every parameter of an MCP tool is reachable by an injected instruction. Screen content also reaches the agent wrapped in a "this is data, not instructions" envelope, flagged for the shapes injection usually takes, including text hidden in invisible Unicode. `type_text` refuses to type your passcode; only `unlock()` may.
- **Tune the gate, or turn it off.** The viewer's **Approve sends** control has three settings, and the choice is yours to make: **Always** (the default: every send after a read waits for a click), **Flagged** (only asks when the scanner found something, which is quieter but lets a payload written to dodge the checks through, because it promotes the flags from a hint to a verdict), and **Off** (never asks; STOP and the activity feed are all that is left). It shows amber whenever you are not on Always. `SEND_APPROVAL` in `.env` sets the startup default, and anything unrecognized falls back to Always, because a setting that cannot be read must never be the one that disables the gate. The setting is reachable from the viewer and `.env` only, never from a tool call, since a gate an injected instruction can switch off is not a gate.
- **What the gate does not cover.** It bounds what an injected instruction can send, not what it can do on the phone. An injection that makes the agent tap through Settings never triggers the gate, and **STOP** plus the activity feed are your cover there. Text painted into an image is read by a vision model and cannot be scanned. And nothing stops the agent being told a lie and repeating it back to you. No text filter detects prompt injection reliably, so the flags on the card are a signal for you, never a verdict.
- **Origin guard.** The viewer API rejects cross-origin and DNS-rebinding requests, so a random web page in another tab cannot drive your phone.
- **Passcode safety.** `unlock()` decides from what is actually on screen (never the driver's lock flag, which can lie), enters your passcode only when the passcode pad is visible (digit pads are tapped, not typed, so a notification holding keyboard focus cannot eat it), makes exactly one attempt per call (repeated wrong passcodes lock an iPhone out), and scrubs it from error messages. The passcode itself is opt-in via `.env` and never committed.

Do not point this at a phone you do not own. Do not use it to send unsolicited messages. Automated bulk messaging will get your Apple ID or number flagged, and it makes you a bad person besides.

## How the signing fix works

Sideloading WebDriverAgent with a free Apple ID installs the app, but the test runner never starts: Sideloadly signs the host app and leaves `PlugIns/WebDriverAgentRunner.xctest` unsigned, so iOS Library Validation rejects it. `phone-harness fix-input` repairs this entirely on your machine:

1. Builds a `.p12` from Sideloadly's own cert and key (openssl, local files).
2. Reads the freshly minted provisioning profile back off the **phone**. Sideloadly signs in memory and never writes the profile to disk, but iOS keeps every installed profile at `/var/MobileDevice/ProvisioningProfiles/`, so the profile is pulled over USB (pymobiledevice3) instead of watched for on your PC.
3. Re-signs the whole IPA with go-ios `ios sign app`, which signs the nested `.xctest` with the same Team ID, then installs.

No Apple servers are contacted, no Apple password is scripted, no session tokens are reused. The 7-day free-ID expiry still applies; the doctor counts it down and one command re-signs.

Because step 2 reads the phone, a profile that has not expired yet is found immediately and Sideloadly is not needed at all — mid-week repairs just run. You can also pass one explicitly: `phone-harness fix-input .state\profile.mobileprovision`. Nothing moves the clock mid-week: Apple pins every re-sign — Sideloadly included — to the App ID's original 7-day window, so the countdown resets only with the first sign after it expires. The phone must be unlocked during any install — iOS refuses installs on a locked phone.

## Architecture

| Module | Role |
|---|---|
| `wda_client.py` | thin HTTP client for WebDriverAgent (requests only), kill-switch chokepoint, activity feed |
| `device.py` | go-ios wrapper: tunnel, runwda, port forwards, pids and logs in `.state/` |
| `capture.py` | screenshots: WDA HTTP when up, go-ios subprocess fallback (perception works before input does) |
| `helpers.py` | the agent API: tap, tap_text, ocr, set_field_text, send_message, read_messages, unlock |
| `trust.py` | the trust boundary: taint tracking, the injection scanner, the data-not-instructions envelope |
| `approval.py` | the human approval handshake that blocks a send until the viewer answers |
| `mcp_server.py` | the helper API as native MCP tools (`phone-harness mcp`) |
| `admin.py` | doctor, up, down |
| `signing.py` | the free-Apple-ID re-signing flow |
| `viewer.py` + `viewer.html` | the human surface: live screen, remote control, doctor panel, STOP |

## Tests

330 unit tests, none of which need a phone plugged in:

```bat
pip install pytest
python -m pytest tests -q
```

They cover the pure logic that is painful to debug on device: tree walking and
compaction, the injection scanner, the approval handshake and its fail-closed
paths, session recovery, passcode refusal, and the viewer's markup (no duplicate
element ids, and its inline script still parses).

## Known limits

- Free Apple ID signatures expire every 7 days. Run `phone-harness fix-input` again; the doctor warns you 48 hours ahead.
- No Face ID, camera, or DRM video flows. One phone per session.
- The phone can stay locked between tasks: set `PHONE_PASSCODE` in `.env`, then agents call `unlock()` and the viewer has an **Unlock** button. Exception: installs (the weekly re-sign) need the phone unlocked in hand.
- Unplugged the phone? Click **Restart link** in the viewer (or run `phone-harness up`).
- iOS 17+ tunnel needs wintun.dll once (admin) if userspace mode fails.

## Contributing

Issues and PRs are welcome. Ground rules:

- `python -m pytest tests -q` must pass without a phone attached.
- New agent primitives go in `helpers.py` and `__all__`.
- Keep `wda_client.py` free of go-ios knowledge and `device.py` free of HTTP knowledge.
- No new runtime dependencies beyond `requests` and `mcp` without a stated reason.

## Credits

- [ShawnPana/phone-harness](https://github.com/ShawnPana/phone-harness) for the original macOS concept.
- [danielpaulus/go-ios](https://github.com/danielpaulus/go-ios) for the USB transport that makes Windows possible.
- [appium/WebDriverAgent](https://github.com/appium/WebDriverAgent) for the automation driver.
- [Sideloadly](https://sideloadly.io/) for free-Apple-ID sideloading.

## License

[MIT](LICENSE)

## Support

If my tools save you time, you can support my work here:

[![Sponsor on GitHub](https://img.shields.io/badge/GitHub%20Sponsors-%E2%9D%A4-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/ucsandman)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-%E2%98%95-ffdd00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/wes_sander)
