"""Ports, paths, and .env loading for phone-harness."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = REPO_ROOT / ".state"
ENV_FILE = REPO_ROOT / ".env"

WDA_PORT = 8100
MJPEG_PORT = 9100

WDA_URL = f"http://127.0.0.1:{WDA_PORT}"
MJPEG_URL = f"http://127.0.0.1:{MJPEG_PORT}"


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
WDA_BUNDLE_ID = get("WDA_BUNDLE_ID")  # else auto-detected from installed apps
PHONE_PASSCODE = get("PHONE_PASSCODE")  # opt-in: lets helpers.unlock() type it

# Default moved off 8765: Practical Systems' pipeline API owns that port on
# this workstation, and both stacks must run at the same time.
VIEWER_PORT = int(get("VIEWER_PORT", "8770") or "8770")
