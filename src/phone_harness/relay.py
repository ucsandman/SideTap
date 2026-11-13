"""Loopback relay: 127.0.0.1:WDA_PORT/MJPEG_PORT -> WebDriverAgent on the tunnel.

WebDriverAgent is started bound to the device's tunnel address (USE_IP), so
nothing on the home network can reach it, and that address changes with every
tunnel (each replug, each Wi-Fi reconnect). Everything else in this package
talks to http://127.0.0.1:8100, so this relay looks the tunnel up per new
connection and pipes bytes both ways. It listens on loopback only: unlike
`ios forward` (0.0.0.0, needing a firewall block), nothing here faces the LAN.

It also stops WDA after config.WDA_IDLE_STOP_MINUTES with no traffic, because
iOS shows its "Automation Running" banner for as long as WDA runs; the next
call finds WDA stopped and the self-heal starts it again.

Run detached by device.start_forwards(): `pythonw -m phone_harness.relay`.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from . import config, link

DEVICE_WDA_PORT = 8100
DEVICE_MJPEG_PORT = 9100
_LOOKUP_TTL = 2.0  # seconds a tunnel lookup is reused across a burst of connections

_cached: tuple[float, str] | None = None
_last_failure = ""  # last reason a connection could not reach the phone, logged once


def _tunnel_address() -> str:
    global _cached
    now = time.monotonic()
    if _cached and now - _cached[0] < _LOOKUP_TTL:
        return _cached[1]
    address = link.tunnel_for()[0]
    # Lookups run in worker threads; one that started earlier and finished
    # later must not put an older answer back in the cache.
    if _cached is None or _cached[0] <= now:
        _cached = (now, address)
    return address


_last_traffic = time.monotonic()  # bytes through either port, or a new connection
_gate: asyncio.Event | None = None  # set while an idle stop runs; new connections wait


def _touch() -> None:
    global _last_traffic
    _last_traffic = time.monotonic()


class _Piped:
    """One client connection piped to the phone."""

    def __init__(
        self,
        client_w: asyncio.StreamWriter,
        dev_w: asyncio.StreamWriter,
        wda: bool,
        address: str = "",
    ):
        self.client_w, self.dev_w, self.wda = client_w, dev_w, wda
        self.address = address  # the tunnel address this pipe leads to
        self.cut = False
        self.last_bytes = time.monotonic()  # last byte either way

    def close(self) -> None:
        """Close both sides now. A client whose connection is idle in its pool
        sees it closed and opens a fresh one for its next request, so nothing
        is lost; a request still under way fails as a broken call (reads are
        resent by the client, gestures never). Never raises."""
        for w in (self.dev_w, self.client_w):
            try:
                w.transport.abort()  # close() would wait on a dead tunnel to drain
            except Exception:
                pass

    def cut_phone_side(self) -> None:
        """Idle stop: drop only the phone side of a WDA connection. HTTP
        clients keep it pooled, and a request already on its way in would be
        lost with a bare close, which the client cannot tell from WDA dying
        mid-call; instead it reaches _pipe_up after the cut and gets the
        marked 503, which the client may retry."""
        self.cut = True
        self.dev_w.close()
        if not self.wda:
            self.client_w.close()


_open: set[_Piped] = set()


_QUIET_CHECK = 2.0  # seconds of silence after which a pipe's next request checks its tunnel


def _close_refused(address: str) -> None:
    """WDA just refused a connection at `address`, so nothing listens there:
    close the pipes that lead to it, and a client's pool opens a fresh
    connection for its next request instead of sending it into a dead pipe.
    Pipes the idle stop already cut are left to answer their marked 503."""
    for conn in list(_open):
        if conn.address == address and not conn.cut:
            conn.close()


async def _tunnel_changed(conn: _Piped) -> str:
    """Why this pipe's tunnel is no longer the current one, or "" when it is
    (or when that cannot be told: tunneld down)."""
    try:
        current = await asyncio.to_thread(_tunnel_address)
    except link.TunneldDown:
        return ""
    except link.LinkError as exc:
        return f"the link to the phone is gone ({exc})"
    except Exception:
        return ""
    return "" if current == conn.address else "the link to the phone changed"


async def _pipe_up(client_r: asyncio.StreamReader, conn: _Piped) -> None:
    """Client to phone. After a cut, the next request is answered, not sent."""
    try:
        while data := await client_r.read(65536):
            _touch()
            quiet = time.monotonic() - conn.last_bytes
            conn.last_bytes = time.monotonic()
            if conn.cut:
                await _answer_unreachable(
                    client_r, conn.client_w, "WDA stopped while idle", head=data
                )
                conn.client_w.close()
                return
            if conn.wda and conn.address and quiet > _QUIET_CHECK:
                # A request on a pooled connection after a quiet spell: if the
                # link changed meanwhile (USB/Wi-Fi switch), this pipe leads to
                # a WDA that is gone and the request would hang in the dead
                # tunnel. It has not been forwarded, so the marked 503 is
                # true and safe to resend, gestures included.
                why = await _tunnel_changed(conn)
                if why:
                    conn.cut = True
                    try:
                        conn.dev_w.close()
                    except Exception:
                        pass
                    await _answer_unreachable(client_r, conn.client_w, why, head=data)
                    conn.client_w.close()
                    return
            conn.dev_w.write(data)
            await conn.dev_w.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        try:
            conn.dev_w.close()
        except Exception:
            pass


async def _pipe_down(dev_r: asyncio.StreamReader, conn: _Piped) -> None:
    """Phone to client. A cut WDA connection stays open for _pipe_up to answer."""
    try:
        while data := await dev_r.read(65536):
            _touch()
            conn.last_bytes = time.monotonic()
            conn.client_w.write(data)
            await conn.client_w.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        if not (conn.cut and conn.wda):
            try:
                conn.client_w.close()
            except Exception:
                pass


UNREACHABLE_HEADER = "X-Phone-Relay"  # value "unreachable"; read by wda_client


async def _answer_unreachable(
    client_r: asyncio.StreamReader,
    client_w: asyncio.StreamWriter,
    reason: str,
    head: bytes = b"",
) -> None:
    """Answer an HTTP request that never reached WDA with a marked 503.

    The request is read first (headers and any body; `head` is what was
    already read of it), so closing afterwards does not reset the connection
    under the reply. Never raises."""
    try:
        buf = head
        while b"\r\n\r\n" not in buf:
            chunk = await asyncio.wait_for(client_r.read(65536), timeout=2)
            if not chunk:
                return
            buf += chunk
        header, _, body_start = buf.partition(b"\r\n\r\n")
        length = 0
        for line in header.split(b"\r\n"):
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value.strip() or 0)
        if length > len(body_start):
            await asyncio.wait_for(client_r.readexactly(length - len(body_start)), timeout=2)
        body = json.dumps(
            {"value": {"error": "wda unreachable", "message": f"relay: {reason}"}}
        ).encode()
        client_w.write(
            b"HTTP/1.1 503 Service Unavailable\r\n"
            + f"{UNREACHABLE_HEADER}: unreachable\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        await asyncio.wait_for(client_w.drain(), timeout=2)
    except Exception:
        pass  # a bare close still reads as "WDA not answering"


_HEAL_COOLDOWN = 30.0  # seconds between two self-heal attempts
_heal_started = 0.0
_healing = False
# Held while WDA is being started or stopped, so an idle stop can never kill
# the runner a self-heal has just started.
_lifecycle = threading.Lock()


def _heal() -> None:
    """Bring WDA back with the same routine as `phone-harness up`.

    WDA is bound to the tunnel it was started on (USE_IP), so a replug, a
    Wi-Fi reconnect or a switch between USB and Wi-Fi leaves it unreachable at
    the new tunnel address, and the MCP tools and helpers never restart it
    themselves. Two faults land here: a REFUSED connection to WDA, and tunneld
    not answering (link.TunneldDown), which admin.up repairs by starting
    tunneld's scheduled task (device.start_tunnel) before it starts WDA on the
    new tunnel. device.start_tunnel checks tunneld again right before it acts,
    so a tunneld that answers by then is left alone, and the task's
    MultipleInstances=IgnoreNew never lets a second copy start. A wedged WDA
    accepts the connection and never lands here: restarting a wedged runner is
    the wrong repair (admin.up handles that state itself). Neither does "no
    tunnel to the phone" while tunneld answers: the phone is away, and
    link.tunnel_for already asks tunneld for a tunnel on every lookup."""
    global _healing
    try:
        from . import admin

        with _lifecycle:
            admin.up()
    except Exception as exc:  # the relay must outlive a failed repair
        print(f"self-heal failed: {exc}", flush=True)
    finally:
        _healing = False


def _maybe_heal() -> None:
    global _heal_started, _healing
    now = time.monotonic()
    if _healing or now - _heal_started < _HEAL_COOLDOWN:
        return
    _healing, _heal_started = True, now
    print(f"WDA unreachable through the tunnel; starting self-heal at {time.ctime()}", flush=True)
    asyncio.get_running_loop().run_in_executor(None, _heal)


def _heals(exc: BaseException) -> bool:
    """Whether the self-heal can repair this failure to reach WDA: WDA not
    listening on the tunnel, or tunneld stopped. Not "no tunnel to the phone"
    while tunneld answers (see _heal)."""
    return isinstance(exc, link.TunneldDown) or not isinstance(exc, link.LinkError)


def _handler(device_port: int):
    async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        global _cached, _last_failure
        _touch()
        while (gate := _gate) is not None:
            await gate.wait()
        address = None
        try:
            address = await asyncio.to_thread(_tunnel_address)
            dev_r, dev_w = await asyncio.wait_for(
                asyncio.open_connection(address, device_port), timeout=5
            )
        except (link.LinkError, OSError, asyncio.TimeoutError) as exc:
            if device_port == DEVICE_WDA_PORT and address and isinstance(exc, ConnectionRefusedError):
                _close_refused(address)  # before the answer below, which can wait ~2 s
            if device_port == DEVICE_WDA_PORT:
                # Nothing was forwarded, so say so: the client may replay any
                # request after this answer (wda_client.UNREACHABLE_HEADER),
                # which it could not do after a bare close (a WDA dying
                # mid-gesture looks the same).
                await _answer_unreachable(client_r, client_w, f"{type(exc).__name__}: {exc}")
            client_w.close()
            _cached = None  # look the tunnel up afresh next time
            reason = f"{type(exc).__name__}: {exc}"
            if reason != _last_failure:
                _last_failure = reason
                print(f"{time.ctime()} cannot reach the phone: {reason}", flush=True)
            if device_port == DEVICE_WDA_PORT and _heals(exc):
                _maybe_heal()
            return
        _last_failure = ""
        conn = _Piped(client_w, dev_w, wda=device_port == DEVICE_WDA_PORT, address=address)
        _open.add(conn)
        try:
            await asyncio.gather(_pipe_up(client_r, conn), _pipe_down(dev_r, conn))
        finally:
            _open.discard(conn)

    return handle


_IDLE_CHECK = 30.0  # seconds between two idle checks


def _idle_stop(idle_seconds: float) -> bool:
    """Stop WDA if nothing has gone through the relay for `idle_seconds`.

    Idle means no bytes and no new connection on either port. Open but silent
    connections do not count: an agent's HTTP client keeps one pooled for
    good, while a live MJPEG stream sends bytes all the time."""
    from . import device

    with _lifecycle, device.wda_lifecycle_lock():
        if _healing or time.monotonic() - _last_traffic < idle_seconds:
            return False
        if device.proc_status("runwda") != "running":
            return False
        age = device.proc_age("runwda")
        if age is not None and age < idle_seconds:
            return False  # started from outside the relay (`up`, the viewer) just now
        device.stop_all(("runwda",))
        device.mark_wda_idle()
    print(f"{time.ctime()} WDA idle for {idle_seconds / 60:g} min; stopped it", flush=True)
    return True


async def _idle_watch(idle_seconds: float) -> None:
    while True:
        await asyncio.sleep(_IDLE_CHECK)
        await _idle_check(idle_seconds)


async def _idle_check(idle_seconds: float) -> None:
    """Decide on the event loop, where every byte is piped, so nothing can be
    forwarded between "idle" and the gate closing: a request that reached WDA
    must never meet a WDA being killed under it (it could not be replayed).
    Open connections are silent ones (pooled by HTTP clients); their phone
    side is cut (see _Piped), and a fresh connection waits at the gate, then
    starts WDA through the self-heal."""
    global _gate
    if _gate is not None or _healing or time.monotonic() - _last_traffic < idle_seconds:
        return
    _gate = asyncio.Event()
    for conn in list(_open):
        conn.cut_phone_side()
    try:
        await asyncio.to_thread(_idle_stop, idle_seconds)
    except Exception as exc:  # the relay must outlive a failed stop
        print(f"{time.ctime()} idle stop failed: {exc}", flush=True)
    finally:
        _gate.set()
        _gate = None


async def serve() -> None:
    servers = [
        await asyncio.start_server(_handler(DEVICE_WDA_PORT), "127.0.0.1", config.WDA_PORT),
        await asyncio.start_server(_handler(DEVICE_MJPEG_PORT), "127.0.0.1", config.MJPEG_PORT),
    ]
    tasks = [s.serve_forever() for s in servers]
    if config.WDA_IDLE_STOP_MINUTES > 0:
        tasks.append(_idle_watch(config.WDA_IDLE_STOP_MINUTES * 60))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(serve())
