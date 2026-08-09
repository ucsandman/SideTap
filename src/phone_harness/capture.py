"""Screen capture via go-ios, no WebDriverAgent required.

`ios screenshot` uses the mounted Developer Disk Image's screenshot service,
so perception (viewing, OCR, wait_stable) works with zero app signing.
Only touch INPUT needs the signed WDA driver.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import device


class CaptureError(RuntimeError):
    pass


_last_png: bytes | None = None
_last_at: float = 0.0


def screenshot_png(max_age: float = 0.0) -> bytes:
    """Return the current screen as PNG bytes via go-ios.

    max_age > 0 returns a cached frame if it is younger than max_age seconds,
    which keeps the viewer smooth without hammering the device.
    """
    global _last_png, _last_at
    if max_age and _last_png is not None and (time.time() - _last_at) < max_age:
        return _last_png

    exe = device.ios_path()
    if not exe:
        raise CaptureError(
            "go-ios not found on PATH. Install it: npm install -g go-ios"
        )

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "shot.png"
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            [exe, "screenshot", "--output", str(out)],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=flags,
        )
        if not out.exists() or out.stat().st_size == 0:
            raise CaptureError(
                "go-ios screenshot failed. Is the phone unlocked and the developer "
                f"image mounted? Detail: {proc.stderr.strip()[-300:]}"
            )
        png = out.read_bytes()

    _last_png, _last_at = png, time.time()
    return png
