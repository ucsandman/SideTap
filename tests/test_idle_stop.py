"""The relay stops an idle WDA so iOS drops its "Automation Running" banner."""

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import admin, config, device, relay  # noqa: E402


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(relay, "_healing", False)


@pytest.fixture
def runner(monkeypatch):
    """A fake runwda: running until stop_all stops it."""
    state = {"running": True, "stops": 0}

    def stop_all(names):
        assert names == ("runwda",)
        state["running"] = False
        state["stops"] += 1
        return ["runwda"]

    monkeypatch.setattr(
        device, "proc_status", lambda name: "running" if state["running"] else "not started"
    )
    monkeypatch.setattr(device, "stop_all", stop_all)
    monkeypatch.setattr(device, "proc_age", lambda name: 3600.0)
    return state


def test_quiet_relay_stops_wda_and_marks_it_idle(runner, monkeypatch):
    monkeypatch.setattr(relay, "_last_traffic", time.monotonic() - 601)
    assert relay._idle_stop(600)
    assert runner["stops"] == 1
    assert device.wda_idle_since()


def test_recent_traffic_keeps_wda_running(runner, monkeypatch):
    monkeypatch.setattr(relay, "_last_traffic", time.monotonic() - 30)
    assert not relay._idle_stop(600)
    assert runner["stops"] == 0
    assert device.wda_idle_since() is None


def test_no_stop_during_a_heal(runner, monkeypatch):
    monkeypatch.setattr(relay, "_last_traffic", time.monotonic() - 601)
    monkeypatch.setattr(relay, "_healing", True)
    assert not relay._idle_stop(600)
    assert runner["stops"] == 0


def test_no_stop_when_wda_is_not_running(runner, monkeypatch):
    runner["running"] = False
    monkeypatch.setattr(relay, "_last_traffic", time.monotonic() - 601)
    assert not relay._idle_stop(600)
    assert runner["stops"] == 0
    assert device.wda_idle_since() is None


def test_idle_stop_waits_for_a_running_start(runner, monkeypatch):
    """A start holds the lifecycle lock; the stop must not run inside it."""
    monkeypatch.setattr(relay, "_last_traffic", time.monotonic() - 601)
    done = threading.Event()
    with relay._lifecycle:
        threading.Thread(target=lambda: (relay._idle_stop(600), done.set()), daemon=True).start()
        assert not done.wait(0.2)
        assert runner["stops"] == 0
        relay._touch()  # the call that started WDA went through the relay
    assert done.wait(2)
    assert runner["stops"] == 0


def test_bytes_through_the_pipe_count_as_traffic(monkeypatch):
    monkeypatch.setattr(relay, "_last_traffic", 0.0)

    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(b"GET /status HTTP/1.1\r\n\r\n")
        reader.feed_eof()

        class Sink:
            def write(self, data):
                pass

            async def drain(self):
                pass

            def close(self):
                pass

        sink = Sink()
        await relay._pipe_up(reader, relay._Piped(sink, sink, wda=True))

    asyncio.run(run())
    assert time.monotonic() - relay._last_traffic < 5


def test_starting_wda_clears_the_idle_mark(monkeypatch):
    device.mark_wda_idle()
    monkeypatch.setattr(device, "_link", lambda fn: ("fd00::1", 1234, None))
    monkeypatch.setattr(device.link, "kill_app", lambda bundle: None)
    monkeypatch.setattr(device, "_spawn_py", lambda name, args: None)
    device.start_wda("com.example.wda")
    assert device.wda_idle_since() is None


def test_doctor_reports_idle_without_probing(monkeypatch):
    """A probe would make the relay restart WDA and bring the banner back."""
    device.mark_wda_idle()
    monkeypatch.setattr(
        device, "proc_status", lambda name: "running" if name == "relay" else "not started"
    )

    def no_probe(*a, **k):
        raise AssertionError("doctor probed an idle-stopped WDA")

    monkeypatch.setattr(admin, "WDAClient", no_probe)
    ok, detail, _ = admin._check_wda_responding()
    assert ok and "idle" in detail


def test_doctor_probes_when_wda_runs_despite_a_stale_mark(monkeypatch):
    device.mark_wda_idle()
    monkeypatch.setattr(device, "proc_status", lambda name: "running")

    class Up:
        def __init__(self, *a, **k):
            pass

        def link_state(self):
            return "up"

    monkeypatch.setattr(admin, "WDAClient", Up)
    ok, detail, _ = admin._check_wda_responding()
    assert ok and "answering" in detail


def test_doctor_says_down_when_no_relay_would_restart_it(monkeypatch):
    """An idle mark left behind by `phone-harness down` is not a healthy idle."""
    device.mark_wda_idle()
    monkeypatch.setattr(device, "proc_status", lambda name: "not started")

    class Down:
        def __init__(self, *a, **k):
            pass

        def link_state(self):
            return "down"

    monkeypatch.setattr(admin, "WDAClient", Down)
    ok, detail, _ = admin._check_wda_responding()
    assert not ok and "not answering" in detail


def test_a_runner_started_outside_the_relay_is_not_killed(runner, monkeypatch):
    monkeypatch.setattr(relay, "_last_traffic", time.monotonic() - 601)
    monkeypatch.setattr(device, "proc_age", lambda name: 5.0)
    assert not relay._idle_stop(600)
    assert runner["stops"] == 0


def test_idle_stop_waits_for_a_start_in_another_process(runner, monkeypatch):
    """`up` in another process holds the file lock while it starts WDA."""
    monkeypatch.setattr(relay, "_last_traffic", time.monotonic() - 601)
    done = threading.Event()
    with device.wda_lifecycle_lock():
        threading.Thread(target=lambda: (relay._idle_stop(600), done.set()), daemon=True).start()
        assert not done.wait(0.3)
        monkeypatch.setattr(device, "proc_age", lambda name: 0.5)  # the new runner
    assert done.wait(3)
    assert runner["stops"] == 0


class _Writer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _pooled():
    return relay._Piped(_Writer(), _Writer(), wda=True)


def test_idle_check_cuts_silent_connections_and_holds_new_ones(runner, monkeypatch):
    monkeypatch.setattr(relay, "_last_traffic", time.monotonic() - 601)
    pooled = _pooled()
    monkeypatch.setattr(relay, "_open", {pooled})
    order = []

    def stop(idle):
        order.append("stop")
        time.sleep(0.2)
        return True

    monkeypatch.setattr(relay, "_idle_stop", stop)

    async def run():
        check = asyncio.create_task(relay._idle_check(600))
        await asyncio.sleep(0.05)
        assert pooled.cut and pooled.dev_w.closed
        assert not pooled.client_w.closed  # left open to answer its next request
        gate = relay._gate
        assert gate is not None  # a new connection now waits
        await gate.wait()
        order.append("new connection proceeds")
        await check

    asyncio.run(run())
    assert order == ["stop", "new connection proceeds"]
    assert relay._gate is None


def test_idle_check_does_nothing_with_recent_traffic(runner, monkeypatch):
    monkeypatch.setattr(relay, "_last_traffic", time.monotonic())
    pooled = _pooled()
    monkeypatch.setattr(relay, "_open", {pooled})
    asyncio.run(relay._idle_check(600))
    assert not pooled.cut and not pooled.dev_w.closed and runner["stops"] == 0


@pytest.fixture
def relay_to_fake_wda(monkeypatch):
    """The real relay handler in front of a fake WDA HTTP server."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class FakeWDA(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive, like WDA
        seen = []

        def log_message(self, *args):  # noqa: vulture
            pass

        def do_POST(self):  # noqa: vulture
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            FakeWDA.seen.append(self.path)
            body = b'{"value": null}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    wda = ThreadingHTTPServer(("127.0.0.1", 0), FakeWDA)
    threading.Thread(target=wda.serve_forever, daemon=True).start()
    monkeypatch.setattr(relay, "DEVICE_WDA_PORT", wda.server_port)
    monkeypatch.setattr(relay, "_tunnel_address", lambda: "127.0.0.1")
    monkeypatch.setattr(relay, "_maybe_heal", lambda: None)
    monkeypatch.setattr(relay, "_open", set())
    loop = asyncio.new_event_loop()
    holder = {}
    started = threading.Event()

    async def run():
        server = await asyncio.start_server(
            relay._handler(wda.server_port), "127.0.0.1", 0
        )
        holder["port"] = server.sockets[0].getsockname()[1]
        holder["stop"] = asyncio.Event()
        started.set()
        await holder["stop"].wait()
        server.close()
        await server.wait_closed()

    t = threading.Thread(target=lambda: loop.run_until_complete(run()), daemon=True)
    t.start()
    started.wait(5)
    yield f"http://127.0.0.1:{holder['port']}", loop, FakeWDA.seen
    loop.call_soon_threadsafe(holder["stop"].set)
    t.join(5)
    loop.close()
    wda.shutdown()


def test_a_request_on_a_pooled_connection_after_the_cut_is_marked_unreachable(
    relay_to_fake_wda, runner, monkeypatch
):
    """The request a client sends on its pooled connection just after the idle
    stop never reaches WDA; it must come back as WDAUnreachable (safe to retry),
    not as a dropped connection the client cannot tell from WDA dying mid-call."""
    from phone_harness.wda_client import WDAClient, WDAUnreachable

    url, loop, seen = relay_to_fake_wda
    c = WDAClient(base_url=url, timeout=5)
    c._request("POST", "/wda/homescreen", {})  # opens and pools a connection
    assert seen == ["/wda/homescreen"] and len(relay._open) == 1
    monkeypatch.setattr(relay, "_last_traffic", time.monotonic() - 601)
    asyncio.run_coroutine_threadsafe(relay._idle_check(600), loop).result(5)
    assert runner["stops"] == 1
    with pytest.raises(WDAUnreachable, match="idle"):
        c._request("POST", "/wda/homescreen", {"x": 1})  # same pooled connection
    assert seen == ["/wda/homescreen"]  # the second call never reached WDA


def test_stop_link_stops_wda_and_marks_it_idle(runner):
    """Agents end their automation at once: banner gone, doctor green."""
    from phone_harness import helpers

    assert helpers.stop_link() == "stopped"
    assert runner["stops"] == 1
    assert device.wda_idle_since()
    assert helpers.stop_link() == "was not running"
    assert runner["stops"] == 1


def test_stop_link_cli(runner, capsys):
    from phone_harness import run

    assert run.main(["stop-link"]) == 0
    assert "WDA stopped" in capsys.readouterr().out
    assert runner["stops"] == 1


def test_stop_link_is_an_mcp_tool():
    from phone_harness import mcp_server

    names = {t.name for t in asyncio.run(mcp_server.server.list_tools())}
    assert "stop_link" in names
