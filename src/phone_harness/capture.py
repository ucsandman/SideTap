"""Screen capture: WDA's HTTP screenshot when the session is up, the device's own otherwise.

`ios screenshot` uses the mounted Developer Disk Image's screenshot service,
so perception (viewing, OCR, wait_stable) works with zero app signing — but it
spawns a fresh subprocess + temp file per frame (~100-300ms each on Windows).
Once WDA answers, its GET /screenshot over the already-forwarded :8100
connection is much cheaper, so hot paths like wait_stable() prefer it.
Only touch INPUT needs the signed WDA driver.
"""

from __future__ import annotations

import time

from . import link
from .wda_client import WDAClient, WDAError


class CaptureError(RuntimeError):
    pass


_last_png: bytes | None = None
_last_at: float = 0.0

# One sessionless client for GET /screenshot (it never creates a WDA session,
# so it cannot steal the single session helpers/viewer hold). When WDA is down
# we back off instead of paying a connection error on every frame.
_wda: WDAClient | None = None
_wda_dead_until: float = 0.0
_WDA_RETRY_SECONDS = 10.0


def _wda_screenshot() -> bytes | None:
    """PNG via WDA's HTTP endpoint, or None if WDA is not answering."""
    global _wda, _wda_dead_until
    if time.time() < _wda_dead_until:
        return None
    if _wda is None:
        _wda = WDAClient(timeout=5)
    try:
        return _wda.screenshot()
    except WDAError:
        _wda_dead_until = time.time() + _WDA_RETRY_SECONDS
        return None


def _device_screenshot() -> bytes:
    """PNG from the developer image's screenshot service, through the tunnel
    (USB or Wi-Fi). Works with zero app signing."""
    try:
        return link.screenshot_png()
    except link.LinkError as exc:
        raise CaptureError(
            "screenshot failed. Is the phone reachable (USB or Wi-Fi), unlocked, and the "
            f"developer image mounted? Detail: {str(exc)[-300:]}"
        ) from exc


def screenshot_png(max_age: float = 0.0, via_wda: bool = True) -> bytes:  # noqa: vulture  (called from viewer.py/mcp_server.py)
    """Return the current screen as PNG bytes.

    max_age > 0 returns a cached frame if it is younger than max_age seconds,
    which keeps the viewer smooth without hammering the device.
    via_wda=False skips WDA: any connection to it starts an idle-stopped WDA
    again (relay self-heal), and with it the "Automation Running" banner.
    """
    global _last_png, _last_at
    if max_age and _last_png is not None and (time.time() - _last_at) < max_age:
        return _last_png

    png = _wda_screenshot() if via_wda else None
    if png is None:
        png = _device_screenshot()

    _last_png, _last_at = png, time.time()
    return png
