"""A stopped tunneld is restarted by the relay's self-heal. No phone needed.

When tunneld stops, every tunnel lookup fails, so every agent call got the
relay's marked 503 and the self-heal never ran: it skipped every LinkError,
which also covers "the phone is away". tunneld being down is now its own
error (link.TunneldDown), and the self-heal runs `up`, whose start_tunnel
starts tunneld's scheduled task.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import admin, device, link, relay  # noqa: E402

UDID = "00008020-000A1B2C3D4E5F60"


class _Writer:
    def __init__(self):
        self.sent = b""

    def write(self, data):
        self.sent += data

    async def drain(self):
        pass

    def close(self):
        pass


def _request() -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(b"GET /status HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    reader.feed_eof()
    return reader


@pytest.fixture
def tunneld_stopped(monkeypatch):
    """tunneld's API refuses, as when its process has exited."""

    def refused(url, timeout=3.0):
        raise link.urllib.error.URLError("[WinError 10061] connection refused")

    monkeypatch.setattr(link.urllib.request, "urlopen", refused)


@pytest.fixture
def heals(monkeypatch):
    """Relay heal state reset; _heal records instead of running `up`."""
    seen = []
    monkeypatch.setattr(relay, "_cached", None)
    monkeypatch.setattr(relay, "_last_failure", "")
    monkeypatch.setattr(relay, "_healing", False)
    monkeypatch.setattr(relay, "_heal_started", -1e9)
    monkeypatch.setattr(relay, "_heal", lambda: seen.append(1))
    return seen


def test_tunneld_down_is_a_link_error_of_its_own(tunneld_stopped):
    with pytest.raises(link.TunneldDown, match="tunneld is not answering"):
        link.tunnels()
    with pytest.raises(link.TunneldDown):
        link.tunnel_for()
    assert issubclass(link.TunneldDown, link.LinkError)  # every caller still catches it


def test_stopped_tunneld_gets_a_marked_503_and_starts_the_heal(tunneld_stopped, heals):
    writer = _Writer()

    async def scenario():
        await relay._handler(relay.DEVICE_WDA_PORT)(_request(), writer)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert b"503" in writer.sent.split(b"\r\n")[0]
    assert f"{relay.UNREACHABLE_HEADER}: unreachable".encode() in writer.sent
    assert b"tunneld is not answering" in writer.sent
    assert heals == [1]


def test_many_calls_at_once_start_one_heal(tunneld_stopped, heals):
    async def scenario():
        await asyncio.gather(
            *(relay._handler(relay.DEVICE_WDA_PORT)(_request(), _Writer()) for _ in range(8))
        )
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert heals == [1]


def test_heals_are_rate_limited_then_retried(tunneld_stopped, heals):
    # The clock itself is not patched: asyncio schedules on time.monotonic.
    async def call():
        await relay._handler(relay.DEVICE_WDA_PORT)(_request(), _Writer())
        await asyncio.sleep(0.01)

    asyncio.run(call())
    relay._healing = False  # the first heal finished (and failed: tunneld still down)
    relay._heal_started = relay.time.monotonic() - (relay._HEAL_COOLDOWN - 5)
    asyncio.run(call())
    assert heals == [1]  # inside the cooldown
    relay._heal_started = relay.time.monotonic() - (relay._HEAL_COOLDOWN + 1)
    asyncio.run(call())
    assert heals == [1, 1]  # cooldown over, tunneld still down: try again


def test_no_heal_while_a_heal_runs(tunneld_stopped, heals, monkeypatch):
    monkeypatch.setattr(relay, "_healing", True)

    async def scenario():
        await relay._handler(relay.DEVICE_WDA_PORT)(_request(), _Writer())

    asyncio.run(scenario())
    assert heals == []


def test_mjpeg_port_never_heals(tunneld_stopped, heals):
    async def scenario():
        await relay._handler(relay.DEVICE_MJPEG_PORT)(_request(), _Writer())
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert heals == []


def test_healthy_tunneld_and_wda_never_heal(heals, monkeypatch):
    async def scenario():
        async def wda(r, w):
            await r.read(100)
            w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
            await w.drain()
            w.close()

        server = await asyncio.start_server(wda, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(relay, "DEVICE_WDA_PORT", port)
        monkeypatch.setattr(relay.link, "tunnel_for", lambda udid=None: ("127.0.0.1", 1, "usb"))
        front = await asyncio.start_server(relay._handler(port), "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", front.sockets[0].getsockname()[1]
        )
        writer.write(b"GET /status HTTP/1.1\r\n\r\n")
        await writer.drain()
        reply = await asyncio.wait_for(reader.read(100), 5)
        writer.close()
        front.close()
        server.close()
        return reply

    assert asyncio.run(scenario()).startswith(b"HTTP/1.1 200")
    assert heals == []


# ---- the heal itself: relay._heal -> admin.up -> device.start_tunnel ----------


@pytest.fixture
def up_env(monkeypatch):
    """admin._up with everything past the tunnel step stubbed out.

    Returns (schtasks commands run, tunneld state). The heal stops right after
    the tunnel step (the device check fails), which is all this path needs."""
    runs = []
    state = {"tunneld_up": False, "task": "Ready"}

    class DownClient:
        def __init__(self, *a, **k):
            pass

        def link_state(self):
            return "down"  # the relay answered its own marked 503

    monkeypatch.setattr(admin, "WDAClient", DownClient)
    monkeypatch.setattr(admin.device, "proc_status", lambda name: "running")
    monkeypatch.setattr(admin, "_check_device", lambda: (False, "stubbed", "stubbed"))
    monkeypatch.setattr(device.link, "tunneld_up", lambda timeout=3.0: state["tunneld_up"])

    def schtasks(cmd, **kw):
        out = ""
        if "/Query" in cmd:
            task, status = device.config.TUNNELD_TASK, state["task"]
            out = '"' + task + '","N/A","' + status + '"'
        else:
            runs.append(cmd)
        if "/End" in cmd:
            state["task"] = "Ready"
        if "/Run" in cmd and state["task"] != "Running":
            state["tunneld_up"] = True  # the task started tunneld
            state["task"] = "Running"

        class P:
            returncode, stdout, stderr = 0, out, ""

        return P()

    monkeypatch.setattr(device.subprocess, "run", schtasks)
    monkeypatch.setattr(device, "tunnel_running", lambda timeout=10.0: state["tunneld_up"])
    monkeypatch.setattr(relay, "_healing", True)
    return runs, state


def test_the_heal_starts_the_tunneld_task_once(up_env):
    runs, _ = up_env
    relay._heal()
    assert runs == [["schtasks", "/Run", "/TN", device.config.TUNNELD_TASK]]
    assert relay._healing is False


def test_the_heal_leaves_a_tunneld_that_answers_again_alone(up_env, monkeypatch):
    # tunneld missed one lookup (slow) but answers by the time the heal acts:
    # its task is not touched.
    runs, state = up_env
    state["tunneld_up"] = True
    monkeypatch.setattr(device, "tunnel_running", lambda timeout=10.0: False)
    monkeypatch.setattr(device.time, "sleep", lambda s: None)
    clock = [0.0]

    def tick():
        clock[0] += 1.0
        return clock[0]

    monkeypatch.setattr(device.time, "monotonic", tick)
    monkeypatch.setattr(device.link, "tunnel_for", lambda udid=None: ("fd00::1", 5000, "usb"))
    relay._heal()
    assert runs == []


def test_a_hung_tunneld_is_ended_then_started(up_env):
    # The task runs but its API never answers: /Run alone does nothing under
    # MultipleInstances IgnoreNew, so the heal ends the task first.
    runs, state = up_env
    state["task"] = "Running"
    relay._heal()
    task = device.config.TUNNELD_TASK
    assert runs == [["schtasks", "/End", "/TN", task], ["schtasks", "/Run", "/TN", task]]


def test_a_slow_tunneld_that_answers_the_longer_probe_is_left_running(up_env, monkeypatch):
    runs, state = up_env
    state["task"] = "Running"
    monkeypatch.setattr(device.link, "tunneld_up", lambda timeout=3.0: timeout > 3.0)
    device._start_tunneld_task()
    assert runs == []


def test_a_device_call_restarts_a_stopped_tunneld_and_retries(up_env, monkeypatch):
    # open_apps/close_app/list_apps reach the phone without the relay.
    runs, state = up_env
    monkeypatch.setattr(device, "_last_tunneld_restart", -1e9)
    calls = []

    def running_apps():
        calls.append(state["tunneld_up"])
        if not state["tunneld_up"]:
            raise device.link.TunneldDown("tunneld is not answering")
        return [{"bundle": "com.apple.Preferences"}]

    monkeypatch.setattr(device.link, "running_apps", running_apps)
    assert device._link(device.link.running_apps) == [{"bundle": "com.apple.Preferences"}]
    assert calls == [False, True]
    assert runs == [["schtasks", "/Run", "/TN", device.config.TUNNELD_TASK]]


def test_device_call_restarts_are_rate_limited(up_env, monkeypatch):
    runs, state = up_env
    monkeypatch.setattr(device, "_last_tunneld_restart", device.time.monotonic())

    def down():
        raise device.link.TunneldDown("tunneld is not answering")

    with pytest.raises(device.DeviceError, match="not answering"):
        device._link(down)
    assert runs == []
