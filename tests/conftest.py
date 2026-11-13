"""Unit tests never reach the real phone.

On a machine that drives a phone, the relay (WDA_PORT, MJPEG_PORT) and tunneld
(49151) are live on loopback, and a test that forgets to stub one of them
starts WebDriverAgent on the real device: its "Automation Running" banner, a
tunnel built to a sleeping iPad. Seen 2026-10-22: five viewer tests reached
both. Connections to those ports are refused for the whole run,
so such a test sees what it would see on a machine with no phone.
"""

import socket
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phone_harness import config, link  # noqa: E402

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_LIVE_PORTS = {
    config.WDA_PORT,
    config.MJPEG_PORT,
    urllib.parse.urlsplit(link.TUNNELD_URL).port or 49151,
}


@pytest.fixture(autouse=True)
def _no_live_phone_ports(monkeypatch):
    connect = socket.socket.connect

    def guarded(self, address):
        if (
            isinstance(address, tuple)
            and len(address) >= 2
            and str(address[0]) in _LOOPBACK
            and address[1] in _LIVE_PORTS
        ):
            raise ConnectionRefusedError(f"unit tests do not reach the live phone ({address[1]})")
        return connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
