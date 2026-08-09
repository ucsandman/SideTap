# sidetap

LLM agents drive a real iPhone from Windows over USB + WebDriverAgent (go-ios).
Windows rebuild of ShawnPana/phone-harness (which is macOS-only).

## Architecture

- `src/phone_harness/wda_client.py` — thin HTTP client for WebDriverAgent (:8100). requests only. Kill switch: a `.state/STOP` file blocks every action POST at the `_request` chokepoint (perception GETs keep working). The same chokepoint appends every successful action POST to `.state/agent_activity.log` (bounded ring; typed text NEVER recorded, only char counts — it can be a passcode).
- `src/phone_harness/device.py` — go-ios wrapper; detached tunnel/runwda/forward processes, pids+logs in `.state/`.
- `src/phone_harness/admin.py` — `doctor` (ordered checks, each names its fix; the STOP kill switch is checked FIRST, then LAN exposure and the 7-day signature countdown), `up` (serialized by `_UP_LOCK`: launch.py's background bring-up and the viewer's Restart link can overlap), `down`.
- `src/phone_harness/capture.py` — screenshots. Prefers WDA's HTTP `/screenshot` (sessionless client, cannot steal the one WDA session) with a 10s back-off; falls back to the `ios screenshot` subprocess, which is the only path that works before signing.
- `scripts/lock_ports.ps1` — self-elevating firewall rule blocking LAN access to :8100/:9100 (WDA has no auth). Fired by the viewer's Lock ports button or run by hand.
- `src/phone_harness/signing.py` — `fix-input`: builds a p12 from Sideloadly's cert, captures the profile Sideloadly mints (Temp watcher), re-signs WDA incl. the nested `.xctest` via `ios sign app`. All local; no Apple auth.
- `src/phone_harness/helpers.py` — agent API (`tap`, `tap_text`, `ocr`, `open_app`, …). `ocr()` reads the UI tree; the tree is cached ~2s and every action invalidates it (WDA `/elements` queries were measured SLOWER than `/source` on device — don't reintroduce them). `unlock()` decides from the screen — NEVER from `/wda/locked`, which can report unlocked with the pad on screen (a test pins this): wake + bottom-edge swipe, type PHONE_PASSCODE only if the pad actually appeared, one attempt only (iOS lockout), success = pad gone.
- `src/phone_harness/run.py` — CLI; no-arg mode executes stdin with helpers in scope; autoloads `agent-workspace/agent_helpers.py`; `mcp` subcommand starts the MCP server.
- `src/phone_harness/mcp_server.py` — helpers as native typed MCP tools (`claude mcp add sidetap -- phone-harness mcp`). Registers the helpers functions directly so schemas track their real signatures; `screenshot`/`unlock` get wrappers (bytes return / internal client param). Needs the `mcp` package (the one dependency beyond requests).
- `src/phone_harness/viewer.py` + `viewer.html` — human surface: live MJPEG screen (:9100), click-to-tap, drag-to-swipe, keyboard typing, paste box for long text, Notifs/Control edge-gesture buttons, doctor panel, live Activity feed, Recent sends audit list, Unlock and Restart link buttons, red STOP/RESUME kill switch, red LAN-exposure banner (loud by default; probed in the background) (:8770, VIEWER_PORT to override; 8765 belongs to the Practical Systems API). All `/api/*` calls are origin-guarded against cross-site/DNS-rebinding. Gestures serialize through `_ACTION_LOCK`, and `/api/unlock` passes the viewer's own client into `helpers.unlock(c)` — WDA holds ONE session; a second client steals it mid-sequence.

## Commands

- Run everything: `python launch.py` (viewer opens immediately; `up()` runs in a background thread)
- Tests: `python -m pytest tests -q`
- Diagnose: `phone-harness doctor` (never guess at connection problems — run this)
- After a replug: the viewer's Restart link button, or `phone-harness up`
- Enable touch input (free Apple ID): `phone-harness fix-input`, then click Start in Sideloadly (mid-week: `phone-harness fix-input .state/profile.mobileprovision` skips Sideloadly; installs need the phone unlocked)

## Rules

- Coordinates are points, not pixels. The UI tree and taps use the same units.
- Keep `wda_client.py` free of go-ios knowledge and `device.py` free of HTTP knowledge.
- New agent primitives go in `helpers.py` and must be added to `__all__` (mcp_server.py picks most of them up from its `_TOOLS` list — add there too if MCP-safe).
- No new runtime dependencies beyond `requests` and `mcp` without a stated reason.
- Driving the phone needs the user's hardware set up (docs/setup-windows.md); unit tests must pass without a phone.
