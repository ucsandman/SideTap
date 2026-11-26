"""link.py (tunneld lookup) and relay.py (loopback relay). No phone needed."""

import asyncio
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import link, relay  # noqa: E402

UDID = "00008020-000A1B2C3D4E5F60"
WIFI = {"tunnel-address": "fd59::1", "tunnel-port": 51392, "interface": "2001:db8::93"}
USB = {"tunnel-address": "fd11::1", "tunnel-port": 50001, "interface": "usbmux-00008020"}


class _Resp:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _no_tunnel_requests(monkeypatch):
    # Tests never reach the real tunneld or usbmux; the request tests below opt in.
    monkeypatch.setattr(link, "_last_request", float("inf"))
    monkeypatch.setattr(link, "_known_udids", lambda: [])


def _listing(monkeypatch, body):
    monkeypatch.setattr(link.urllib.request, "urlopen", lambda url, timeout=3.0: _Resp(body))


def test_tunnels_drops_devices_without_a_tunnel(monkeypatch):
    _listing(monkeypatch, {UDID: [WIFI], "other": []})
    assert link.tunnels() == {UDID: [WIFI]}


def test_tunneld_down_is_its_own_error(monkeypatch):
    def refused(url, timeout=3.0):
        raise link.urllib.error.URLError("refused")

    monkeypatch.setattr(link.urllib.request, "urlopen", refused)
    with pytest.raises(link.LinkError, match="tunneld is not answering"):
        link.tunnels()
    assert link.tunneld_up() is False


def test_tunnel_for_prefers_usb_over_wifi(monkeypatch):
    _listing(monkeypatch, {UDID: [WIFI, USB]})
    assert link.tunnel_for(UDID) == ("fd11::1", 50001, "usb")


def test_tunnel_for_uses_wifi_when_that_is_all(monkeypatch):
    _listing(monkeypatch, {UDID: [WIFI]})
    assert link.tunnel_for() == ("fd59::1", 51392, "wifi")


def test_tunnel_for_ipv4_interface_is_wifi(monkeypatch):
    _listing(monkeypatch, {UDID: [dict(WIFI, interface="192.0.2.10")]})
    assert link.tunnel_for()[2] == "wifi"


def test_tunnel_for_no_phone_says_how_to_fix(monkeypatch):
    _listing(monkeypatch, {})
    with pytest.raises(link.LinkError, match="not on USB, or not on the same Wi-Fi"):
        link.tunnel_for()


def test_tunnel_for_honours_the_udid_pin(monkeypatch):
    other = dict(WIFI, **{"tunnel-address": "fd99::1"})
    _listing(monkeypatch, {"AAAA": [other], UDID: [WIFI]})
    monkeypatch.setattr(link.config, "SIDETAP_UDID", UDID)
    assert link.tunnel_for()[0] == "fd59::1"


def test_relay_closes_the_client_when_there_is_no_tunnel(monkeypatch):
    def no_tunnel():
        raise link.LinkError("no tunnel to the phone")

    monkeypatch.setattr(relay, "_cached", None)
    monkeypatch.setattr(relay.link, "tunnel_for", lambda udid=None: no_tunnel())

    class Writer:
        closed = False

        def close(self):
            Writer.closed = True

    async def connect():
        await relay._handler(8100)(asyncio.StreamReader(), Writer())

    asyncio.run(connect())
    assert Writer.closed


def test_relay_pipes_bytes_to_the_tunnel_address(monkeypatch):
    async def scenario():
        received = []

        async def device(reader, writer):
            data = await reader.read(100)
            received.append(data)
            writer.write(b"pong")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(device, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(relay, "_cached", None)
        monkeypatch.setattr(relay.link, "tunnel_for", lambda udid=None: ("127.0.0.1", 1, "usb"))
        front = await asyncio.start_server(relay._handler(port), "127.0.0.1", 0)
        front_port = front.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", front_port)
        writer.write(b"ping")
        await writer.drain()
        reply = await asyncio.wait_for(reader.read(100), 5)
        writer.close()
        server.close()
        front.close()
        return received, reply

    received, reply = asyncio.run(scenario())
    assert received == [b"ping"]
    assert reply == b"pong"


def test_relay_listens_on_loopback_only():
    src = Path(relay.__file__).read_text(encoding="utf-8")
    assert '"0.0.0.0"' not in src
    assert src.count('"127.0.0.1"') == 2


def test_refused_wda_connection_heals_once_per_cooldown(monkeypatch):
    heals = []
    monkeypatch.setattr(relay, "_cached", None)
    monkeypatch.setattr(relay, "_healing", False)
    monkeypatch.setattr(relay, "_heal_started", -1e9)
    monkeypatch.setattr(relay.link, "tunnel_for", lambda udid=None: ("127.0.0.1", 1, "usb"))
    monkeypatch.setattr(relay, "_heal", lambda: heals.append(1))

    class Writer:
        def close(self):
            pass

    async def scenario():
        # port 9 on loopback refuses: WDA not listening on the tunnel
        for _ in range(3):
            await relay._handler(9)(asyncio.StreamReader(), Writer())
        await asyncio.sleep(0.05)

    monkeypatch.setattr(relay, "DEVICE_WDA_PORT", 9)
    asyncio.run(scenario())
    assert heals == [1]  # three refusals inside the cooldown: one repair


def test_no_tunnel_does_not_trigger_a_heal(monkeypatch):
    monkeypatch.setattr(relay, "_cached", None)
    monkeypatch.setattr(relay, "_healing", False)
    monkeypatch.setattr(relay, "_heal_started", -1e9)

    def no_tunnel(udid=None):
        raise link.LinkError("no tunnel to the phone")

    monkeypatch.setattr(relay.link, "tunnel_for", no_tunnel)
    monkeypatch.setattr(relay, "_heal", lambda: pytest.fail("healed with no tunnel"))

    class Writer:
        def close(self):
            pass

    async def scenario():
        await relay._handler(relay.DEVICE_WDA_PORT)(asyncio.StreamReader(), Writer())

    asyncio.run(scenario())


def test_no_tunnel_asks_tunneld_for_one(monkeypatch):
    listings = iter([{}, {UDID: [USB]}])
    asked = []

    def urlopen(url, timeout=3.0):
        if "/start-tunnel" in url:
            asked.append(url)
            r = _Resp({})
            r.status = 200
            return r
        return _Resp(next(listings))

    monkeypatch.setattr(link.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(link, "_last_request", -1e9)
    monkeypatch.setattr(link, "_known_udids", lambda: [UDID])
    assert link.tunnel_for() == ("fd11::1", 50001, "usb")
    assert asked == [link.TUNNELD_URL + "/start-tunnel?udid=" + UDID + "&connection_type=usbmux"]


def _not_on_usb(url):
    # What tunneld answers for connection_type=usbmux when the phone is not on
    # USB: its DeviceNotFoundError escapes the handler as a 501 (2026-10-22).
    body = json.dumps({"error": {"exception": "DeviceNotFoundError", "traceback": "..."}}).encode()
    return link.urllib.error.HTTPError(url, 501, "Not Implemented", {}, io.BytesIO(body))


def test_no_usb_tunnel_asks_for_a_wifi_one(monkeypatch):
    # tunneld runs with --no-wifi, so a Wi-Fi tunnel exists only once asked for,
    # and an untyped request dies on the USB attempt before it ever tries Wi-Fi.
    listings = iter([{}, {UDID: [WIFI]}])
    asked = []

    def urlopen(url, timeout=3.0):
        if "/start-tunnel" in url:
            asked.append(url.rsplit("connection_type=", 1)[-1])
            if url.endswith("usbmux"):
                raise _not_on_usb(url)
            r = _Resp({})
            r.status = 200
            return r
        return _Resp(next(listings))

    monkeypatch.setattr(link.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(link, "_last_request", -1e9)
    assert link.tunnel_for(UDID)[2] == "wifi"
    assert asked == ["usbmux", "wifi"]


def test_phone_on_neither_link_is_reported_as_away(monkeypatch):
    def urlopen(url, timeout=3.0):
        if "/start-tunnel" in url:
            if url.endswith("usbmux"):
                raise _not_on_usb(url)
            raise link.urllib.error.HTTPError(
                url, 501, "Not Implemented", {}, io.BytesIO(b'{"error": "task not created"}')
            )
        return _Resp({})

    monkeypatch.setattr(link.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(link, "_last_request", -1e9)
    with pytest.raises(link.LinkError) as err:
        link.tunnel_for(UDID)
    assert "request failed" not in str(err.value)


def test_tunnel_requests_are_rate_limited(monkeypatch):
    asked = []

    def urlopen(url, timeout=3.0):
        if "/start-tunnel" in url:
            asked.append(url)
            raise link.urllib.error.HTTPError(url, 501, "task not created", {}, None)
        return _Resp({})

    monkeypatch.setattr(link.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(link, "_last_request", -1e9)
    monkeypatch.setattr(link, "_known_udids", lambda: [UDID])
    for _ in range(3):
        with pytest.raises(link.LinkError, match="no tunnel to the phone"):
            link.tunnel_for()
    assert len(asked) == len(link._REQUEST_TYPES)  # one round per interval, not one per connection


def test_tunnel_request_failure_is_named(monkeypatch):
    # A failed request is not "the phone is away": the error says what failed.
    def urlopen(url, timeout=3.0):
        if "/start-tunnel" in url:
            raise link.urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)
        return _Resp({})

    monkeypatch.setattr(link.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(link, "_last_request", -1e9)
    with pytest.raises(link.LinkError, match="tunnel request failed: HTTP 500"):
        link.tunnel_for(UDID)


def test_device_calls_work_from_inside_a_running_event_loop(monkeypatch):
    # The MCP server runs synchronous tools on its event-loop thread, where
    # asyncio.run() refuses to start.
    async def answer():
        return 42

    async def tool():
        return link._run_coroutine(answer)

    assert asyncio.run(tool()) == 42


def test_a_timeout_says_what_timed_out(monkeypatch):
    monkeypatch.setattr(link, "tunnel_for", lambda udid=None: ("fd00::1", 5000, "wifi"))

    class FakeRSD:
        def __init__(self, addr):
            pass

        async def connect(self):
            pass

        async def close(self):
            pass

    import pymobiledevice3.remote.remote_service_discovery as rsd_mod

    monkeypatch.setattr(rsd_mod, "RemoteServiceDiscoveryService", FakeRSD)

    async def slow(rsd):
        await asyncio.sleep(1)

    with pytest.raises(link.LinkError, match="did not answer within"):
        link.run(slow, timeout=0.01)
