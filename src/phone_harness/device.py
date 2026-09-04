"""go-ios wrapper: device discovery, tunnel, WDA launch, port forwards.

Long-running processes (tunnel, runwda, forwards) are started detached with
their pids and logs kept in .state/ so `up` and `down` can manage them.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config


class DeviceError(RuntimeError):
    pass


PROCS = ("tunnel", "runwda", "forward8100", "forward9100", "syslog")


# ---- per-run subprocess memoization -----------------------------------------
# `ios list`, `ios apps --list` and netstat each get called more than once
# inside a SINGLE doctor pass (see admin._check_wda_installed / _check_ports_local).
# At a 207ms-per-spawn floor that is real, repeated cost for nothing — but a
# stale check is a lying check, so this must never survive past one call to
# `memoized_run()`. Nothing outside that contextmanager is ever cached.
# threading.local()-backed, not a module global: viewer.py runs a
# ThreadingHTTPServer (one thread per request) plus _heal_loop and
# _refresh_lan_state background threads, and a doctor pass takes ~4s. A shared
# global let two overlapping blocks' exits stomp each other's restore, leaving
# a non-None dict frozen forever after both had exited, and let a thread that
# never entered memoized_run() at all read whatever thread currently had a
# block open. Each thread now gets its own independent `.cache`.
_run_cache = threading.local()


@contextlib.contextmanager  # noqa: vulture  (memoized_run is called from admin.py)
def memoized_run():
    """Scope subprocess results to one caller-defined "run" (e.g. one doctor
    pass). Entering resets the cache to empty; exiting restores whatever was
    there before (supports nesting, though nothing today nests it)."""
    previous = getattr(_run_cache, "cache", None)
    _run_cache.cache = {}
    try:
        yield
    finally:
        _run_cache.cache = previous


def ios_path() -> str | None:
    found = shutil.which("ios")
    if found:
        return found
    # Windows truncates a registry PATH past ~4095 chars when it builds the
    # logon environment, so shortcut/Startup launches can miss the npm global
    # dir even though terminals (which rebuild PATH in shell profiles) see it.
    npm_exe = Path(os.environ.get("APPDATA", "")) / "npm" / "ios.exe"
    if npm_exe.is_file():
        return str(npm_exe)
    # The sidetap.io one-line installer downloads go-ios here directly (no
    # Node/npm at all), so this is the only place ios.exe exists on a machine
    # set up that way.
    installer_exe = (
        Path(os.environ.get("LOCALAPPDATA", "")) / "SideTap" / "bin" / "ios.exe"
    )
    return str(installer_exe) if installer_exe.is_file() else None


def pin_udid(args: list[str]) -> list[str]:
    """Append --udid when this instance is pinned to one phone (SIDETAP_UDID).

    The multi-device seam: unset (the default, every single-phone install)
    changes nothing. `list` stays global — the doctor and the fleet roster
    must see every connected phone — and `tunnel` is go-ios's all-devices
    daemon, owned by whichever instance starts it first (design doc §2.3;
    per-command --udid scoping is UNVERIFIED until a second phone exists).
    Read from config at CALL time so one process can never cache another
    instance's pin.
    """
    udid = config.SIDETAP_UDID
    if not udid or not args or args[0] in ("list", "tunnel"):
        return args
    return [*args, f"--udid={udid}"]


def _run(args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    exe = ios_path()
    if not exe:
        raise DeviceError("go-ios not found on PATH. Install it: npm install -g go-ios")
    return subprocess.run(
        [exe, *pin_udid(args)],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _json_lines(text: str) -> list[dict]:
    """go-ios prints one JSON object per line (or a single object)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") or line.startswith("["):
            try:
                parsed = json.loads(line)
                out.extend(parsed if isinstance(parsed, list) else [parsed])
            except json.JSONDecodeError:
                pass
    return out


def list_devices() -> list[str]:
    """UDIDs of USB-connected iPhones."""
    cache = getattr(_run_cache, "cache", None)
    if cache is not None and "list_devices" in cache:
        return cache["list_devices"]
    proc = _run(["list"])
    result = []
    for obj in _json_lines(proc.stdout + proc.stderr):
        if "deviceList" in obj:
            result = list(obj["deviceList"])
            break
    if cache is not None:
        cache["list_devices"] = result
    return result


def _apps_cache_file() -> Path:
    return config.STATE_DIR / "apps_cache.json"


def list_apps() -> list[dict]:
    """Installed apps as [{bundle_id, name}]. Parses go-ios output tolerantly.

    Deep sleep EMPTIES `ios apps --list` while the apps stay installed (same
    failure mode as detect_wda_bundle's own cache, docs/ERRORS.md) — a live
    empty result never overwrites a good persisted list, and a live non-empty
    result always does (so a newly installed app is still findable).
    """
    cache = getattr(_run_cache, "cache", None)
    if cache is not None and "list_apps" in cache:
        return cache["list_apps"]
    proc = _run(["apps", "--list"], timeout=15)
    apps = []
    for obj in _json_lines(proc.stdout):
        # newer go-ios: JSON objects with CFBundleIdentifier / CFBundleName
        bid = (
            obj.get("CFBundleIdentifier") or obj.get("bundleId") or obj.get("bundle_id")
        )
        if bid:
            apps.append(
                {
                    "bundle_id": bid,
                    "name": obj.get("CFBundleName") or obj.get("name", ""),
                }
            )
    if not apps:
        # fallback: plain "bundleid name" lines
        for line in proc.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) >= 1 and "." in parts[0]:
                apps.append(
                    {"bundle_id": parts[0], "name": parts[1] if len(parts) > 1 else ""}
                )
    if apps:
        try:
            config.STATE_DIR.mkdir(exist_ok=True)
            _apps_cache_file().write_text(json.dumps(apps), encoding="utf-8")
        except OSError:
            pass
    else:
        try:
            apps = json.loads(_apps_cache_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            apps = []
    if cache is not None:
        cache["list_apps"] = apps
    return apps


def running_apps() -> list[dict]:
    """Apps with a live process, newest launch first: [{name, pid, started}].

    `ios ps` over USB (0.69s measured 2026-09-04). IsApplication drops the
    daemons but NOT the system apps that never show in a switcher (Siri,
    Spotlight, InputUI, the WDA runner), so callers join this against what is
    installed; `name` is the process name ("MobileSMS", "Hinge"), not a display
    name. This list is the iOS app switcher's contents and the only way to read
    them: WebDriverAgent's touches never reach the home-indicator zone (measured
    2026-09-04 — even the plain Home flick from y=h, h-1 and h-5 could not leave
    Settings, while the top and left edge gestures work), so the switcher
    screen itself cannot be opened and this stands in for it.
    """
    proc = _run(["ps"], timeout=15)
    apps = [
        {
            "name": str(obj.get("Name", "")),
            "pid": obj.get("Pid"),
            "started": str(obj.get("StartDate", "")),
        }
        for obj in _json_lines(proc.stdout)
        if obj.get("IsApplication")
    ]
    apps.sort(key=lambda a: a["started"], reverse=True)
    return apps


def kill_app(bundle_id: str) -> bool:
    """Force-quit an app by bundle id (`ios kill`): the switcher's swipe-up.
    False when go-ios reports nothing to kill (already gone)."""
    proc = _run(["kill", bundle_id], timeout=15)
    return proc.returncode == 0


def _wda_cache_file() -> Path:
    return config.STATE_DIR / "wda_bundle"


def detect_wda_bundle() -> str | None:  # noqa: vulture  (called from admin.py/viewer.py)
    """Find the installed WebDriverAgent runner. .env WDA_BUNDLE_ID wins.

    Deep sleep gates the app list (`ios apps --list` comes back EMPTY while
    the app is still installed — seen live 2026-08-10), so a successful live
    detection is cached in .state/wda_bundle and an empty list falls back to
    that cache. A NON-empty list without WDA means genuinely uninstalled and
    ignores the cache.
    """
    if config.WDA_BUNDLE_ID:
        return config.WDA_BUNDLE_ID
    try:
        apps = list_apps()
    except (DeviceError, subprocess.TimeoutExpired):
        apps = []
    for app in apps:
        bid = app["bundle_id"].lower()
        if "webdriveragent" in bid or bid.endswith(".xctrunner"):
            try:
                config.STATE_DIR.mkdir(exist_ok=True)
                _wda_cache_file().write_text(app["bundle_id"], encoding="utf-8")
            except OSError:
                pass
            return app["bundle_id"]
    if not apps:
        try:
            return _wda_cache_file().read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


def lockdown_ready() -> bool:  # noqa: vulture
    """Can we talk lockdown right now? Deep sleep gates it (ReadPair errors,
    exit 1) while tunnel services like screenshot keep working — so this is
    the cheap "has the human woken the phone yet" probe."""
    try:
        return _run(["date"], timeout=10).returncode == 0
    except (DeviceError, subprocess.TimeoutExpired):
        return False


def tunnel_running(timeout: float = 10.0) -> bool:  # noqa: vulture  (called from admin.py/viewer.py, outside this commit)
    """`timeout` exists so start_tunnel()'s readiness poll can hand each probe
    only the budget it has left — without it a `tunnel ls` started just before
    the cap would run to its own 10s and stretch the cap to 12.9s. Every other
    caller keeps the 10s it always had."""
    try:
        proc = _run(["tunnel", "ls"], timeout=timeout)
    except (DeviceError, subprocess.TimeoutExpired):
        return False
    for obj in _json_lines(proc.stdout + proc.stderr):
        # skip go-ios log lines ({"level": ..., "msg": ...}); a real tunnel
        # entry carries an address + RSD port
        if obj.get("level"):
            continue
        if obj.get("address") and obj.get("rsdPort"):
            return True
    return False


def ddi_mounted() -> bool:
    """Is the personalized Developer Disk Image mounted? An iOS UPDATE silently
    unmounts it, and without it testmanagerd refuses every test session — runwda
    dies in dtx channel timeouts that look like a broken tunnel (bit live
    2026-08-10, the 26.5→26.6 update). Mounted: `image list` prints a line with
    a "signature" key; unmounted: msg "none"."""
    try:
        proc = _run(["image", "list"], timeout=15)
    except (DeviceError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    return any(obj.get("signature") for obj in _json_lines(proc.stdout + proc.stderr))


def mount_ddi() -> tuple[bool, str]:  # noqa: vulture  (called from admin.py)
    """Mount the developer image (`ios image auto`). The phone must be UNLOCKED
    (iOS answers DeviceLocked otherwise); the first mount after an iOS update
    also needs internet (Apple TSS signs the image). Success is verified by
    re-probing, not by parsing the mount log."""
    try:
        proc = _run(["image", "auto"], timeout=180)
    except (DeviceError, subprocess.TimeoutExpired) as exc:
        return False, f"`ios image auto` failed: {exc}"
    if ddi_mounted():
        return True, "developer image mounted"
    out = proc.stdout + proc.stderr
    if "DeviceLocked" in out:
        return False, "phone is locked — unlock it, then retry"
    tail = out.strip().splitlines()[-1] if out.strip() else "no output"
    return False, f"`ios image auto` did not mount: {tail}"


# ---- detached process management ------------------------------------------


def _pid_file(name: str) -> Path:
    return config.STATE_DIR / f"{name}.pid"


def _log_file(name: str) -> Path:
    return config.STATE_DIR / f"{name}.log"


def _spawn(name: str, args: list[str]) -> int:
    """Start `ios <args>` detached; record pid and send output to a log file."""
    exe = ios_path()
    if not exe:
        raise DeviceError("go-ios not found on PATH. Install it: npm install -g go-ios")
    config.STATE_DIR.mkdir(exist_ok=True)
    log = open(_log_file(name), "w", encoding="utf-8")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [exe, *pin_udid(args)],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )
    _pid_file(name).write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return str(pid) in proc.stdout
    try:
        import os

        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_image(pid: int) -> str:
    """Executable name for a live PID, lowercased ('' if dead or unknown)."""
    if sys.platform == "win32":
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        first = proc.stdout.strip().splitlines()
        if first and first[0].startswith('"'):
            return first[0].split('","')[0].strip('"').lower()
        return ""
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip().lower()
    except OSError:
        return ""


def _safe_kill(pid: int, expected_prefix: str, tree: bool = True) -> bool:
    """Force-kill `pid` only if its executable name starts with
    `expected_prefix`. Pid files outlive their process and Windows reuses
    pids, so an unchecked kill could hit an innocent process. Returns True
    if a kill was issued.

    `tree=False` spares the children. The tunnel and the forwards are children
    of whatever launched them, so killing a viewer's tree takes the phone link
    down with it — see _kill_stale_viewer."""
    if not expected_prefix or not _pid_image(pid).startswith(expected_prefix.lower()):
        return False
    if sys.platform == "win32":
        cmd = ["taskkill", "/PID", str(pid), "/F"] + (["/T"] if tree else [])
        subprocess.run(
            cmd,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        import os
        import signal

        os.kill(pid, signal.SIGTERM)
    return True


def proc_status(name: str) -> str:
    """'running', 'dead', or 'not started'."""
    pf = _pid_file(name)
    if not pf.exists():
        return "not started"
    try:
        pid = int(pf.read_text().strip())
    except ValueError:
        return "not started"
    return "running" if _pid_alive(pid) else "dead"


def log_tail(name: str, lines: int = 5) -> str:
    lf = _log_file(name)
    if not lf.exists():
        return ""
    return "\n".join(
        lf.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    )


def stop_all(names: tuple[str, ...] = PROCS) -> list[str]:  # noqa: vulture  (called from admin.py/viewer.py, outside this commit)
    """Kill every process we started. Returns names of processes stopped.

    `names` narrows it to a subset — syslog.start() uses that to clear one
    orphaned capture without taking the phone link down with it.
    """
    exe = ios_path()
    expected = Path(exe).name.lower() if exe else "ios"
    stopped = []
    for name in names:
        pf = _pid_file(name)
        if not pf.exists():
            continue
        try:
            pid = int(pf.read_text().strip())
        except ValueError:
            pf.unlink()
            continue
        if _safe_kill(pid, expected):
            stopped.append(name)
        pf.unlink()
    return stopped


# ---- bring-up --------------------------------------------------------------


# The flat sleep this replaced was 3s from the first commit and was never
# measured. `tunnel ls` is an HTTP query to the local go-ios tunnel daemon (it
# does not touch the phone), but it is still a ~207ms process spawn, so the
# interval stays coarse. The cap EQUALS the sleep it replaced, so the worst
# case is unchanged and a bad result is a one-line revert.
_TUNNEL_READY_TIMEOUT = (
    3.0  # needs device check: how long a userspace tunnel really takes
)
_TUNNEL_READY_POLL = 0.5


def start_tunnel() -> None:  # noqa: vulture  (called from admin.py/viewer.py, outside this commit)
    """Start the iOS 17+ tunnel. Tries userspace mode first (no admin needed).

    Returns as soon as `tunnel ls` reports an entry with an address AND an
    rsdPort — the same signal _up() already trusts to skip starting a tunnel at
    all, and the RSD handshake the very next step (`ios image list`) dials.
    Never returns early on "the process is still alive": that would hand a
    half-established tunnel to ddi_mounted() and turn into a spurious
    "developer image not mounted".
    """
    _spawn("tunnel", ["tunnel", "start", "--userspace"])
    deadline = time.monotonic() + _TUNNEL_READY_TIMEOUT
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        # Each probe gets only the budget that is left, so one hung
        # `tunnel ls` cannot stretch the 3.0s cap to its own 10s timeout.
        if tunnel_running(timeout=left):
            return
        time.sleep(min(_TUNNEL_READY_POLL, max(0.0, deadline - time.monotonic())))
    if proc_status("tunnel") == "dead":
        raise DeviceError(
            "Tunnel failed to start. Log tail:\n" + log_tail("tunnel") + "\n"
            "Fix: run `ios tunnel start` in an **admin** terminal (needs wintun.dll in "
            "C:\\Windows\\System32, from https://www.wintun.net), keep it open, then retry."
        )


def start_wda(bundle_id: str) -> None:  # noqa: vulture  (called from admin.py/viewer.py, outside this commit)
    _spawn(
        "runwda",
        [
            "runwda",
            f"--bundleid={bundle_id}",
            f"--testrunnerbundleid={bundle_id}",
            "--xctestconfig=WebDriverAgentRunner.xctest",
        ],
    )


def foreground_springboard() -> bool:  # noqa: vulture  (called from admin.py)
    """Put the Home Screen in front over USB, with no WDA involvement.

    The only escape from a WEDGED WebDriverAgent. An app whose accessibility
    server never answers blocks every WDA call that resolves the active
    application - gestures included - and WDA serves requests one at a time, so
    the whole agent stops: /status, /screenshot and the viewer all queue behind
    the blocked call. WDA's own /wda/homescreen queues there too, and restarting
    the runner on top of the stuck one fails with XCTest error 103.
    Foregrounding another app releases the AX wait: measured 2026-08-17 against
    TikTok's For You feed, WDA answered again ~20s later, no restart needed.
    """
    ios = ios_path()
    if not ios:
        return False
    try:
        return _run(["launch", "com.apple.springboard"], timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError, DeviceError):
        return False


def _free_port(port: int) -> None:
    """Kill our own leftover `ios forward` bound to `port`.

    Repeated bring-ups spawn a new forwarder and overwrite its pid file,
    orphaning the previous one - which keeps the port and blocks WDA from ever
    being reachable. Only ios.exe listeners are killed; unrelated ports are left
    alone.
    """
    if sys.platform != "win32":
        return
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError:
        return
    pids = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if (
            len(parts) >= 5
            and parts[3] == "LISTENING"
            and parts[1].endswith(f":{port}")
        ):
            pids.add(parts[4])
    for pid in pids:
        info = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if "ios.exe" in info.stdout.lower():
            subprocess.run(
                ["taskkill", "/F", "/PID", pid],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )


def _netstat_ano_lines() -> list[str]:
    """`netstat -ano -p tcp` output lines, memoized within one memoized_run()
    block (doctor checks WDA_PORT and MJPEG_PORT with two separate calls that
    would otherwise spawn netstat twice for the identical output)."""
    cache = getattr(_run_cache, "cache", None)
    if cache is not None and "netstat" in cache:
        return cache["netstat"]
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        lines = proc.stdout.splitlines()
    except OSError:
        lines = []
    if cache is not None:
        cache["netstat"] = lines
    return lines


def port_exposed_to_lan(port: int) -> bool:  # noqa: vulture  (called from admin.py/viewer.py, outside this commit)
    """True if `port` is LISTENING on any address other than loopback.

    go-ios 1.2.1 has no bind-address flag, so `ios forward` listens on 0.0.0.0 —
    reachable by the whole LAN.
    """
    if sys.platform != "win32":
        return False
    for line in _netstat_ano_lines():
        parts = line.split()
        if (
            len(parts) >= 4
            and parts[3] == "LISTENING"
            and parts[1].endswith(f":{port}")
        ):
            local = parts[1].rsplit(":", 1)[0]
            if local not in ("127.0.0.1", "[::1]"):
                return True
    return False


def lan_block_rule_active(rule_name: str = "phone-harness block LAN") -> bool:  # noqa: vulture  (called from admin.py/viewer.py, outside this commit)
    """True if the firewall rule that blocks LAN access to the ports is enabled.

    A block rule drops inbound LAN packets but does NOT rebind the socket, so
    netstat still shows 0.0.0.0 after locking — `port_exposed_to_lan` alone can
    never notice the fix. This is how the doctor confirms the lock took.
    """
    if sys.platform != "win32":
        return False
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-NetFirewallRule -DisplayName '{rule_name}' "
                "-ErrorAction SilentlyContinue | Where-Object Enabled -eq 'True' "
                "| Measure-Object).Count",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    try:
        return int(proc.stdout.strip()) > 0
    except ValueError:
        return False


def start_forwards() -> None:  # noqa: vulture  (called from admin.py/viewer.py, outside this commit)
    _free_port(config.WDA_PORT)
    _free_port(config.MJPEG_PORT)
    _spawn("forward8100", ["forward", str(config.WDA_PORT), "8100"])
    _spawn("forward9100", ["forward", str(config.MJPEG_PORT), "9100"])


def current_udid() -> str | None:  # noqa: vulture  (used by signing.py)
    """This instance's phone: the SIDETAP_UDID pin, else the first connected."""
    if config.SIDETAP_UDID:
        return config.SIDETAP_UDID
    try:
        udids = list_devices()
    except (DeviceError, subprocess.TimeoutExpired):
        return None
    return udids[0] if udids else None


def sign_app(  # noqa: vulture  (used by signing.py)
    ipa: Path,
    p12: Path,
    profile: Path,
    p12password: str = "",
    bundleid: str | None = None,
    install: bool = True,
) -> str:
    """Sign an IPA (nested .xctest included) with `ios sign app` and install it.

    This is the step Sideloadly skips: go-ios re-signs the nested
    WebDriverAgentRunner.xctest with the same Team ID as the host app, which is
    what iOS Library Validation requires. `bundleid` overrides the app's bundle
    id so it matches the provisioning profile. Returns the combined go-ios output.
    """
    args = [
        "sign",
        "app",
        f"--path={ipa}",
        f"--p12file={p12}",
        f"--profile={profile}",
    ]
    if p12password:
        args.append(f"--p12password={p12password}")
    if bundleid:
        args.append(f"--bundleid={bundleid}")
    if install:
        args.append("--install")
    proc = _run(args, timeout=300)
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise DeviceError(f"`ios sign app` failed:\n{out[-1200:]}")
    return out
