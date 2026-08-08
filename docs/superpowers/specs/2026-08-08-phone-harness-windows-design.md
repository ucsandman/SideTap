# phone-claude — phone-harness for Windows + iPhone (design)

Date: 2026-08-08. Status: approved by user.

## Goal

Recreate what [ShawnPana/phone-harness](https://github.com/ShawnPana/phone-harness) does — an LLM agent
sees and controls a real iPhone — but from a **Windows** desktop. The original uses macOS iPhone
Mirroring, which does not exist on Windows. We replace the transport with **USB + WebDriverAgent (WDA)**.

## Decisions (made with user)

- Transport: USB + WebDriverAgent, driven by **go-ios** (no Xcode, no Mac, no Appium server).
- iPhone runs iOS 17+ → requires the go-ios tunnel (wintun.dll + admin once, or userspace mode).
- WDA signing: **free Apple ID via Sideloadly** (re-sign every 7 days; doctor detects expiry).
- Language: Python, `requests` as the only runtime dependency.
- Agent interface: same as original — pipe Python to stdin with helpers pre-imported.

## Architecture

```
[iPhone iOS 17+] — WebDriverAgent app (sideloaded, signed with free Apple ID)
      | USB
[Windows]
  go-ios: tunnel start / runwda / forward 8100 & 9100 / apps list
  Python harness: thin HTTP client to WDA at 127.0.0.1:8100
  Viewer: local web page (MJPEG live screen at :9100, click-to-tap, doctor panel)
[Agent] phone-harness CLI: stdin Python with helpers in scope
```

Improvement over the original: WDA exposes the real UI element tree, so `ocr()` returns exact
elements instead of OCR guesses.

## Components

| File | Purpose |
|---|---|
| `src/phone_harness/config.py` | ports, paths, tiny .env loader (PHONE_PASSCODE, WDA_BUNDLE_ID overrides) |
| `src/phone_harness/wda_client.py` | thin WDA HTTP client: status, session (auto-recover), screenshot, tap/swipe/long-press (W3C actions), type, source, app launch, home, lock state |
| `src/phone_harness/device.py` | go-ios wrapper: list devices/apps, detect WDA bundle, start/stop tunnel + runwda + port forwards (detached, pid/log files in `.state/`) |
| `src/phone_harness/admin.py` | `doctor` (ordered checks, each names its fix), `up`, `down` |
| `src/phone_harness/helpers.py` | agent API, same names as original: `tap`, `tap_text`, `type_text`, `open_app`, `ocr`, `wait_stable`, `screen_info` + new `ui_tree`, `press_home`, `swipe`, `screenshot` |
| `src/phone_harness/run.py` | CLI: no-arg/stdin exec mode, `doctor`, `up`, `down`, `view`; autoloads `agent-workspace/agent_helpers.py` |
| `src/phone_harness/viewer.py` + `viewer.html` | human surface: live screen, click = tap, Home button, screenshot download, doctor status |
| `phone-harness.cmd`, `launch.py` | one-command entry points |

## Error handling

- Doctor-first: every check failure prints the exact fix.
- WDA "invalid session" → recreate session, retry once, then fail loudly.
- `up` is idempotent; if WDA already answers, it skips.
- Scripts exit non-zero on failure.

## Human setup (one-time, ~20 min, documented in docs/setup-windows.md)

1. iPhone: enable Developer Mode.
2. Windows: Apple Devices app (USB driver), `npm i -g go-ios`, wintun.dll → System32, Sideloadly.
3. Sideloadly: install prebuilt WDA IPA (Appium WebDriverAgent releases) with free Apple ID; trust profile.
4. `phone-harness doctor` until green. Every 7 days: one Sideloadly re-sign (doctor detects expiry).

## Verification

- Unit tests: WDA client against a mocked HTTP server; tree-walk text extraction on a sample tree.
- End-to-end (needs user's phone + signing): doctor green → open Settings → tap General → screenshots.
- Viewer seen rendered before done.

## Known limits

No Face ID/camera flows, one phone per session, phone must be unlocked (opt-in passcode unlock via
`.env`), WDA re-sign every 7 days on a free Apple ID, tunnel needs admin once (wintun.dll install).
