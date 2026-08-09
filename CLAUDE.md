# phone-claude

LLM agents drive a real iPhone from Windows over USB + WebDriverAgent (go-ios).
Windows rebuild of ShawnPana/phone-harness (which is macOS-only).

## Architecture

- `src/phone_harness/wda_client.py` — thin HTTP client for WebDriverAgent (:8100). requests only.
- `src/phone_harness/device.py` — go-ios wrapper; detached tunnel/runwda/forward processes, pids+logs in `.state/`.
- `src/phone_harness/admin.py` — `doctor` (ordered checks, each names its fix, incl. LAN exposure), `up`, `down`.
- `scripts/lock_ports.ps1` — self-elevating firewall rule blocking LAN access to :8100/:9100 (WDA has no auth). Fired by the viewer's Lock ports button or run by hand.
- `src/phone_harness/signing.py` — `fix-input`: builds a p12 from Sideloadly's cert, captures the profile Sideloadly mints (Temp watcher), re-signs WDA incl. the nested `.xctest` via `ios sign app`. All local; no Apple auth.
- `src/phone_harness/helpers.py` — agent API (`tap`, `tap_text`, `ocr`, `open_app`, …). `ocr()` reads the UI tree.
- `src/phone_harness/run.py` — CLI; no-arg mode executes stdin with helpers in scope; autoloads `agent-workspace/agent_helpers.py`.
- `src/phone_harness/viewer.py` + `viewer.html` — human surface: live MJPEG screen (:9100), click-to-tap, drag-to-swipe, keyboard typing, doctor panel, Recent sends audit list (:8770, VIEWER_PORT to override; 8765 belongs to the Practical Systems API). All `/api/*` calls are origin-guarded against cross-site/DNS-rebinding.

## Commands

- Run everything: `python launch.py`
- Tests: `python -m pytest tests -q`
- Diagnose: `phone-harness doctor` (never guess at connection problems — run this)
- Enable touch input (free Apple ID): `phone-harness fix-input`, then click Start in Sideloadly

## Rules

- Coordinates are points, not pixels. The UI tree and taps use the same units.
- Keep `wda_client.py` free of go-ios knowledge and `device.py` free of HTTP knowledge.
- New agent primitives go in `helpers.py` and must be added to `__all__`.
- No new runtime dependencies beyond `requests` without a stated reason.
- Driving the phone needs the user's hardware set up (docs/setup-windows.md); unit tests must pass without a phone.
