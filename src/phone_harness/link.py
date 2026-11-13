"""Device link over USB or Wi-Fi: pymobiledevice3 through tunneld.

go-ios finds devices only through Apple's usbmux service, so it cannot reach a
phone that is on Wi-Fi only (checked 2026-10-18 on iPadOS 26.5.2: `Device not
found` even with --address/--rsd-port). pymobiledevice3's `remote tunneld` keeps
one tunnel per device over whichever link exists (USB or Wi-Fi) and lists them on
a local HTTP API; every call here connects to the device's RemoteServiceDiscovery
(RSD) endpoint through that tunnel. tunneld itself runs as its own long-lived
admin process (it creates TUN interfaces); this module never starts it, but
says when it is down (TunneldDown) so the relay's self-heal can.

The calls are in-process (import ~1.4 s once, connect ~0.45 s, app list ~0.22 s
measured over Wi-Fi) rather than one `python -m pymobiledevice3` per call (3-7 s).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from . import config

T = TypeVar("T")

TUNNELD_URL = config.get("TUNNELD_URL", "http://127.0.0.1:49151") or "http://127.0.0.1:49151"


class LinkError(RuntimeError):
    pass


class TunneldDown(LinkError):
    """tunneld itself is not answering on its local API.

    Kept apart from the other LinkErrors ("no tunnel to the phone"), because
    the repair differs: a stopped tunneld is restarted (its scheduled task),
    while a phone that is away needs nothing from this PC."""


def tunnels(timeout: float = 3.0) -> dict[str, list[dict]]:
    """tunneld's listing: {udid: [{"tunnel-address", "tunnel-port", "interface", ...}]}.

    Raises TunneldDown (a LinkError) when tunneld itself is not answering, which
    is a different fault from "no phone": the fix is to start tunneld, not to
    plug anything in.
    """
    try:
        with urllib.request.urlopen(TUNNELD_URL + "/", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise TunneldDown(f"tunneld is not answering at {TUNNELD_URL}: {exc}") from exc
    return {udid: list(entries or []) for udid, entries in (data or {}).items() if entries}


def tunneld_up(timeout: float = 3.0) -> bool:
    try:
        tunnels(timeout)
    except LinkError:
        return False
    return True


def list_devices() -> list[str]:
    """UDIDs with a live tunnel, USB or Wi-Fi."""
    return sorted(tunnels())


def _is_usb(entry: dict) -> bool:
    # tunneld names a USB tunnel's interface after the usbmux/CoreDevice link and a
    # Wi-Fi one after the phone's IP address; a ':' or '.' means an address.
    iface = str(entry.get("interface", ""))
    return not (":" in iface or iface.replace(".", "").isdigit())


_REQUEST_INTERVAL = 10.0  # seconds between two tunnel requests to tunneld
_last_request = -1e9


def _known_udids() -> list[str]:
    """Devices this PC could open a tunnel to: attached by USB, then remote-paired (Wi-Fi)."""
    udids: list[str] = []
    try:
        from pymobiledevice3 import usbmux

        udids += [d.serial for d in _run_coroutine(usbmux.list_devices)]
    except Exception:
        pass
    records = Path.home() / ".pymobiledevice3"
    udids += sorted(p.stem.removeprefix("remote_") for p in records.glob("remote_*.plist"))
    return list(dict.fromkeys(udids))


_UNREACHABLE = "unreachable"  # request_tunnel(): tunneld found no way to the device


_REQUEST_TYPES = ("usbmux", "wifi")  # the links request_tunnel() tries, USB first
# What tunneld answers when the device is not on the link asked for: its own
# "no task" reply, or usbmux's DeviceNotFoundError, which escapes its handler.
_NOT_ON_LINK = {"task not created", "DeviceNotFoundError"}


def request_tunnel(udid: str, timeout: float = 15.0) -> str | None:
    """Ask tunneld to build a tunnel to `udid` now (its /start-tunnel API).

    Returns None when tunneld has a tunnel, else why not. tunneld runs with
    --no-wifi (scripts/tunneld.pyw), so a Wi-Fi tunnel exists only once asked
    for here: its Wi-Fi monitor kept waking a sleeping iPad. Each
    link is asked for by name, USB first: an untyped request tries usbmux first,
    and when the device is not on USB that attempt fails with an error tunneld
    does not catch, so Wi-Fi is never tried (pymobiledevice3 11.36.0). Asking
    also covers the USB case tunneld's own monitor misses: a device attached
    while it had a Wi-Fi tunnel is never revisited when that tunnel drops
    (doronz88/pymobiledevice3#2052). Asking is cheap when a tunnel exists
    (tunneld returns it) and fails fast otherwise.
    """
    failures: list[str] = []
    for link_type in _REQUEST_TYPES:
        url = (
            f"{TUNNELD_URL}/start-tunnel?udid={urllib.parse.quote(udid)}"
            f"&connection_type={link_type}"
        )
        try:
            with urllib.request.urlopen(url, timeout=timeout):
                return None
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read().decode("utf-8")).get("error")
            except (ValueError, AttributeError, OSError):
                error = None
            if isinstance(error, dict):
                error = error.get("exception")
            if error in _NOT_ON_LINK:
                continue  # not on this link: the usual case, try the next one
            failures.append(
                f"tunneld's {link_type} tunnel request failed: HTTP {exc.code} {error or exc.reason}"
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            failures.append(f"tunneld's {link_type} tunnel request failed: {exc}")
    return "; ".join(failures) if failures else _UNREACHABLE


def tunnel_for(udid: str | None = None) -> tuple[str, int, str]:
    """(address, rsd port, "usb"|"wifi") of the device's tunnel, USB first.

    With no tunnel listed, asks tunneld for one (at most every _REQUEST_INTERVAL)."""
    global _last_request
    listing = tunnels()
    if udid is None:
        udid = config.SIDETAP_UDID or (sorted(listing)[0] if listing else None)
    entries = listing.get(udid or "", [])
    now = time.monotonic()
    request_failures: list[str] = []
    if not entries and now - _last_request >= _REQUEST_INTERVAL:
        _last_request = now
        for candidate in [udid] if udid else _known_udids():
            failure = request_tunnel(candidate)
            if failure is None:
                udid = candidate
                entries = tunnels().get(candidate, [])
                break
            if failure != _UNREACHABLE:
                request_failures.append(failure)
    if not entries:
        detail = f" ({'; '.join(request_failures)})" if request_failures else ""
        raise LinkError(
            "no tunnel to the phone: it is not on USB, or not on the same Wi-Fi as this PC, "
            f"or it is asleep{detail}"
        )
    entries = sorted(entries, key=lambda e: not _is_usb(e))
    best = entries[0]
    return str(best["tunnel-address"]), int(best["tunnel-port"]), (
        "usb" if _is_usb(best) else "wifi"
    )


def _run_coroutine(make: Callable[[], Awaitable[T]]) -> T:
    """asyncio.run(make()), also from a thread whose event loop is running.

    The MCP server calls synchronous tools on its own event-loop thread, where
    asyncio.run() refuses to start; there the coroutine runs on a short-lived
    worker thread with its own loop, and this call waits for it."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(make())).result()


def run(fn: Callable[[Any], Awaitable[T]], udid: str | None = None, timeout: float = 30.0) -> T:
    """Connect to the device's RSD through its tunnel, await fn(rsd), close."""
    from pymobiledevice3.remote.remote_service_discovery import (
        RemoteServiceDiscoveryService,
    )

    address, port, _ = tunnel_for(udid)

    async def main() -> T:
        rsd = RemoteServiceDiscoveryService((address, port))
        await rsd.connect()
        try:
            return await fn(rsd)
        finally:
            await rsd.close()

    async def bounded() -> T:
        return await asyncio.wait_for(main(), timeout)

    try:
        return _run_coroutine(bounded)
    except LinkError:
        raise
    except TimeoutError as exc:  # its message is empty: say what timed out
        raise LinkError(
            f"the phone did not answer within {timeout:.0f} s at [{address}]:{port} "
            "(asleep, locked, or the link changed); retry, or run: phone-harness up"
        ) from exc
    except Exception as exc:  # transport, lockdown or DVT failure, named
        raise LinkError(f"{type(exc).__name__}: {exc}") from exc


# ---- the calls SideTap needs ------------------------------------------------


def list_apps() -> list[dict]:
    """User-installed apps as [{bundle_id, name}]."""
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    async def fn(rsd):
        return await InstallationProxyService(lockdown=rsd).get_apps(application_type="User")

    apps = run(fn)
    return [
        {"bundle_id": bid, "name": info.get("CFBundleName") or info.get("CFBundleDisplayName", "")}
        for bid, info in apps.items()
    ]


def running_apps() -> list[dict]:
    """Processes that are applications: [{name, pid, started}], as go-ios `ps` gave."""
    from pymobiledevice3.services.dvt.instruments.device_info import DeviceInfo
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider

    async def fn(rsd):
        async with DvtProvider(rsd) as dvt, DeviceInfo(dvt) as info:
            return await info.proclist()

    return [
        {"name": str(p.get("name", "")), "pid": p.get("pid"), "started": str(p.get("startDate", ""))}
        for p in run(fn)
        if p.get("isApplication")
    ]


def kill_app(bundle_id: str) -> bool:
    """Kill the app's process. False when it is not running."""
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.process_control import ProcessControl

    async def fn(rsd):
        async with DvtProvider(rsd) as dvt, ProcessControl(dvt) as pc:
            pid = await pc.process_identifier_for_bundle_identifier(bundle_id)
            if not pid:
                return False
            await pc.kill(pid)
            return True

    return run(fn)


def launch(bundle_id: str) -> bool:
    """Launch (or bring to front) an app by bundle id."""
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.process_control import ProcessControl

    async def fn(rsd):
        async with DvtProvider(rsd) as dvt, ProcessControl(dvt) as pc:
            return bool(await pc.launch(bundle_id, kill_existing=False))

    return run(fn)


def screenshot_png() -> bytes:
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot

    async def fn(rsd):
        async with DvtProvider(rsd) as dvt, Screenshot(dvt) as shot:
            return await shot.get_screenshot()

    return run(fn)


def ddi_mounted() -> bool:
    from pymobiledevice3.services.mobile_image_mounter import MobileImageMounterService

    async def fn(rsd):
        return await MobileImageMounterService(rsd).is_image_mounted("Personalized")

    return run(fn)


def mount_ddi() -> None:
    """Mount the personalized developer image (Apple signs it per device: needs internet)."""
    from pymobiledevice3.services.mobile_image_mounter import auto_mount

    async def fn(rsd):
        await auto_mount(rsd)

    run(fn, timeout=300)


def device_answers() -> bool:
    """Cheap "is the phone reachable and talking" probe (replaces `ios date`)."""
    try:
        run(lambda rsd: _noop(), timeout=10)
    except LinkError:
        return False
    return True


async def _noop() -> None:
    return None
