"""Ports, paths, and .env loading for phone-harness."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = REPO_ROOT / ".state"
ENV_FILE = REPO_ROOT / ".env"

WDA_PORT = 8100
MJPEG_PORT = 9100  # noqa: vulture

WDA_URL = f"http://127.0.0.1:{WDA_PORT}"


def _load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse a KEY=VALUE .env file. Missing file is fine."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_env = _load_env()


def get(key: str, default: str | None = None) -> str | None:
    """Read a setting: process env wins, then .env file, then default."""
    return os.environ.get(key) or _env.get(key) or default


# Optional overrides
# Read by device.detect_wda_bundle; else auto-detected from installed apps.
WDA_BUNDLE_ID = get("WDA_BUNDLE_ID")  # noqa: vulture
PHONE_PASSCODE = get("PHONE_PASSCODE")  # noqa: vulture  (opt-in: lets helpers.unlock() type it)

# Prompt-injection gate: always | flagged | off. The default for this machine;
# the viewer's toggle writes .state/send_approval, which wins at send time.
# Anything unrecognized falls back to "always" (approval.mode fails safe).
SEND_APPROVAL = get("SEND_APPROVAL", "always")  # noqa: vulture

# How long a gated send waits for the human to click Approve in the viewer.
# Running out is a denial, never a send: the safe direction is not sending.
SEND_APPROVAL_TIMEOUT = float(get("SEND_APPROVAL_TIMEOUT", "120") or "120")  # noqa: vulture

# Default moved off 8765: Practical Systems' pipeline API owns that port on
# this workstation, and both stacks must run at the same time.
VIEWER_PORT = int(get("VIEWER_PORT", "8770") or "8770")  # noqa: vulture

# Post-gesture waits, applied by whichever client creates the shared session.
# Measured on device: WDA's default animationCoolOffTimeout=2 made every swipe
# cost 2-5s under scroll momentum; at 0, idle waits of 0/1/2s all measure
# ~0.7s per swipe. idle=2 keeps settle protection on genuinely busy screens.
WDA_IDLE_WAIT = float(get("WDA_IDLE_WAIT", "2") or "2")
WDA_ANIM_COOLOFF = float(get("WDA_ANIM_COOLOFF", "0") or "0")

# Live-view stream tuning (measured on device: WDA captures ~34fps max; 50%
# scale halves frame weight with no fps cost, and the viewer shows ~1100px max).
MJPEG_FPS = int(get("MJPEG_FPS", "60") or "60")
MJPEG_QUALITY = int(get("MJPEG_QUALITY", "70") or "70")
MJPEG_SCALE = int(get("MJPEG_SCALE", "50") or "50")

# Viewer poll for /api/phone: seconds between polls. 0 disables the periodic
# poll (viewer will still call loadPhone() on load). Default 0 avoids the
# viewer accidentally triggering a WDA wedge; operators can enable it in
# .env with VIEWER_PHONE_POLL_SECONDS=10 for a 10s poll.
VIEWER_PHONE_POLL_SECONDS = float(get("VIEWER_PHONE_POLL_SECONDS", "0") or "0")  # noqa: vulture

# Accessibility snapshot timeout (seconds). Added upstream in WDA #1214
# (appium/WebDriverAgent#1214) to avoid indefinite hangs on apps with busy main
# event loops (e.g. TikTok video feeds). 0 disables the bound; default is 2.0s.
WDA_ACCESSIBILITY_DEADLINE = float(get("WDA_ACCESSIBILITY_DEADLINE", "2.0") or "2.0")
