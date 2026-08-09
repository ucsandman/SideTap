# phone-claude

Let an LLM agent (Claude Code) see and control a **real iPhone from a Windows desktop**.

This is a Windows rebuild of [phone-harness](https://github.com/ShawnPana/phone-harness).
The original uses macOS iPhone Mirroring, which does not exist on Windows. This version uses
**USB + WebDriverAgent** driven by [go-ios](https://github.com/danielpaulus/go-ios) — no Mac,
no Xcode, no Appium server, no jailbreak.

Bonus over the original: WebDriverAgent exposes the real UI element tree, so the agent reads
exact buttons and labels instead of OCR guesses.

```
[iPhone iOS 17+] — WebDriverAgent app (sideloaded once)
      | USB
[Windows]  go-ios (tunnel + launch + port forward)  →  Python harness  →  live viewer page
      |
[Agent]    phone-harness <<'PY' … PY   (helpers pre-imported)
```

## Run

```bat
pip install -r requirements.txt
python launch.py          :: brings the link up + opens the live viewer
```

Or piece by piece:

```bat
phone-harness doctor      :: diagnose the whole chain, each FAIL names its fix
phone-harness up          :: start tunnel + WebDriverAgent + port forwards
phone-harness view        :: live viewer: watch the screen; click = tap, drag = swipe, keyboard = type
phone-harness down        :: stop background processes
```

First time? Follow **[docs/setup-windows.md](docs/setup-windows.md)** (~20 min, one-time).

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
| `ui_tree()` | full raw element tree |
| `tap(x, y)` / `long_press(x, y)` | touch at points |
| `tap_text("General")` | find text and tap it |
| `type_text("hello")` | type into the focused field |
| `swipe(x1,y1,x2,y2)` / `scroll("down")` | gestures |
| `open_app("Settings")` | launch by friendly name or bundle id |
| `send_message("Mom", "hi")` | open Messages, open the thread, type, send |
| `press_home()` | home screen |
| `wait_stable()` | wait until the screen stops changing |
| `unlock()` | wake + unlock (passcode opt-in via `.env`) |

Add your own helpers in `agent-workspace/agent_helpers.py` — auto-loaded into every script.

## Security

- **Lock the ports.** go-ios forwards WDA (:8100) and its MJPEG stream (:9100) on
  `0.0.0.0`, and WebDriverAgent has no auth — so by default anyone on your Wi-Fi can
  drive the phone. The doctor **LAN exposure** check flags this; click **Lock ports**
  in the viewer (or run `scripts\lock_ports.ps1`, one-time, needs admin) to add a
  firewall rule. Loopback is never filtered, so the local viewer and client keep working.
- The viewer API rejects cross-origin and DNS-rebinding requests, so a random web page
  in another tab cannot drive your phone.
- `send_message` refuses to send if the contact name is ambiguous or the thread that
  opens doesn't match, and logs every send to `.state/actions.log` (shown as **Recent
  sends** in the viewer).

## Tests

```bat
pip install pytest
python -m pytest tests -q
```

## Known limits

- Free Apple ID signing expires every 7 days → one re-sign click in Sideloadly (doctor tells you).
- No Face ID, camera, or DRM video flows. One phone per session.
- Phone must be unlocked (or set `PHONE_PASSCODE` in `.env` and call `unlock()`).
- iOS 17+ tunnel needs wintun.dll once (admin) if userspace mode fails.
