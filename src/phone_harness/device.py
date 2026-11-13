"""Device layer: discovery, WDA launch, the loopback relay, app control.

Talks to the phone through pymobiledevice3 and tunneld (link.py), over USB or
Wi-Fi alike; go-ios is kept only for `ios sign app` (fix-input). Long-running
processes (runwda, relay) are started detached with their pids and logs kept
in .state/ so `up` and `down` can manage them. tunneld is not one of them: it
needs admin rights and runs as its own logon task (config.TUNNELD_TASK).
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

from . import config, link


class DeviceError(RuntimeError):
    pass


PROCS = ("runwda", "relay", "syslog")
# Pid files the go-ios versions wrote (`ios tunnel`, `ios forward`); stop_all
# still clears them so an upgrade leaves no orphaned 0.0.0.0 forwarder behind.
LEGACY_PROCS = ("tunnel", "forward8100", "forward9100")

# Every go-ios child inherits this, so the tunnel server and every client that
# looks it up (`tunnel ls`, runwda, image, screenshot, syslog) agree on one
# port without each call site passing it. See config.GO_IOS_AGENT_PORT.
os.environ["GO_IOS_AGENT_PORT"] = config.GO_IOS_AGENT_PORT


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


def _link(fn, *args):
    """Call a link.py function, reporting its failures as DeviceError.

    When tunneld itself is not answering, start it and try once more: device
    calls (app list, open apps, kill) reach the phone without the relay, so the
    relay's self-heal never sees them."""
    try:
        return fn(*args)
    except link.TunneldDown as exc:
        if not _restart_tunneld():
            raise DeviceError(str(exc)) from exc
    except link.LinkError as exc:
        raise DeviceError(str(exc)) from exc
    try:
        return fn(*args)
    except link.LinkError as exc:
        raise DeviceError(str(exc)) from exc


_TUNNELD_RESTART_INTERVAL = 30.0  # seconds between two restarts from this process
_last_tunneld_restart = -1e9
_TUNNELD_HUNG_PROBE = 10.0  # a tunneld silent this long while its task runs is hung


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks", *args, "/TN", config.TUNNELD_TASK],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _start_tunneld_task() -> None:
    """Start tunneld's task; end it first if it runs but tunneld never answers.

    The task is registered with MultipleInstances IgnoreNew, so /Run does
    nothing while a hung instance holds it. A tunneld that answers within the
    longer probe is left alone: only a silent one is ended."""
    if link.tunneld_up():
        return
    query = _schtasks("/Query", "/FO", "CSV", "/NH")
    if '"Running"' in (query.stdout or ""):
        if link.tunneld_up(timeout=_TUNNELD_HUNG_PROBE):
            return  # slow, not hung
        _schtasks("/End")
    _schtasks("/Run")


def _restart_tunneld() -> bool:
    """Start tunneld (rate-limited) and wait for its API. True once it answers."""
    global _last_tunneld_restart
    now = time.monotonic()
    if now - _last_tunneld_restart < _TUNNELD_RESTART_INTERVAL:
        return False
    _last_tunneld_restart = now
    _start_tunneld_task()
    deadline = time.monotonic() + _TUNNEL_READY_TIMEOUT
    while time.monotonic() < deadline:
        if link.tunneld_up():
            return True
        time.sleep(_TUNNEL_READY_POLL)
    return False


def list_devices() -> list[str]:
    """UDIDs of phones with a live tunnel (USB or Wi-Fi)."""
    cache = getattr(_run_cache, "cache", None)
    if cache is not None and "list_devices" in cache:
        return cache["list_devices"]
    result = _link(link.list_devices)
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
    try:
        apps = _link(link.list_apps)
    except DeviceError as exc:
        # Asleep or unreachable: app names do not change while the phone sleeps,
        # so a saved list still resolves them. With none saved, the caller gets
        # the real reason, not an empty list that reads as "no such app".
        try:
            apps = json.loads(_apps_cache_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise exc from None
        if cache is not None:
            cache["list_apps"] = apps
        return apps
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


def running_apps() -> list[dict]:  # noqa: vulture  (called from helpers.py)
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
    apps = _link(link.running_apps)
    apps.sort(key=lambda a: a["started"], reverse=True)
    return apps


def kill_app(bundle_id: str) -> bool:  # noqa: vulture  (called from helpers.py)
    """Force-quit an app by bundle id: the switcher's swipe-up.
    False when it is not running (already gone)."""
    return bool(_link(link.kill_app, bundle_id))


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
    return link.device_answers()


def tunnel_running(timeout: float = 10.0) -> bool:  # noqa: vulture  (called from admin.py/viewer.py, outside this commit)
    """`timeout` exists so start_tunnel()'s readiness poll can hand each probe
    only the budget it has left — without it a `tunnel ls` started just before
    the cap would run to its own 10s and stretch the cap to 12.9s. Every other
    caller keeps the 10s it always had."""
    try:
        link.tunnels(timeout=max(0.1, min(timeout, 3.0)))
        link.tunnel_for()
    except link.LinkError:
        return False
    return True


def ddi_mounted() -> bool:
    """Is the personalized Developer Disk Image mounted? An iOS UPDATE silently
    unmounts it, and without it testmanagerd refuses every test session — runwda
    dies in dtx channel timeouts that look like a broken tunnel (bit live
    2026-08-10, the 26.5→26.6 update). Mounted: `image list` prints a line with
    a "signature" key; unmounted: msg "none"."""
    try:
        return bool(link.ddi_mounted())
    except link.LinkError:
        return False


def mount_ddi() -> tuple[bool, str]:  # noqa: vulture  (called from admin.py)
    """Mount the personalized developer image. The phone must be UNLOCKED
    (iOS answers DeviceLocked otherwise); the first mount after an iOS update
    also needs internet (Apple TSS signs the image). pymobiledevice3, not
    `ios image auto`: go-ios's TSS request fails on Windows because Apple's
    signing server chains to a root Windows does not ship. Success is verified
    by re-probing, not by parsing the mount log."""
    try:
        link.mount_ddi()
        err = ""
    except link.LinkError as exc:
        err = str(exc)
    if ddi_mounted():
        return True, "developer image mounted"
    if "DeviceLocked" in err:
        return False, "phone is locked — unlock it, then retry"
    return False, f"developer image did not mount: {err or 'no error reported'}"


# ---- detached process management ------------------------------------------


def _pid_file(name: str) -> Path:
    return config.STATE_DIR / f"{name}.pid"


def _log_file(name: str) -> Path:
    return config.STATE_DIR / f"{name}.log"


def _idle_file() -> Path:
    return config.STATE_DIR / "wda-idle"


def mark_wda_idle() -> None:
    """Record that the relay stopped WDA for being idle, not because it died."""
    _idle_file().write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def release_wda() -> bool:
    """Stop WDA now because the agent is done, as the idle stop would later.

    iOS shows "Automation Running" and dims the screen for as long as WDA runs, so an
    agent that is done should end it rather than wait for the idle stop. The
    relay stays up and WDA is marked idle, so doctor stays green and the next
    call through the relay starts WDA again. Returns False when it was not
    running."""
    with wda_lifecycle_lock():
        if proc_status("runwda") != "running":
            return False
        stop_all(("runwda",))
        mark_wda_idle()
    return True


def wda_lifecycle_lock():
    """Held, across processes, while WDA is started or idle-stopped.

    The relay idle-stops WDA from its own process; without this a manual
    `phone-harness up` or the viewer could start a runner between the relay's
    "still idle?" check and its kill, and the relay would kill the new one."""
    return _file_lock("wda-lifecycle.lock")


def relay_start_lock():
    """Held, across processes, while the relay is checked and started.

    Agent clients start a relay they find missing; two at once
    would both spawn one, and the loser of the port bind could own relay.pid
    while the winner runs unrecorded, out of reach of `down`."""
    return _file_lock("relay-start.lock")


@contextlib.contextmanager
def _file_lock(name: str):
    """An exclusive lock on .state/<name>, across processes. Not reentrant."""
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.STATE_DIR / name, "a+b") as f:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def proc_age(name: str) -> float | None:
    """Seconds since the process was started (its pid file written), or None."""
    try:
        return time.time() - _pid_file(name).stat().st_mtime
    except OSError:
        return None


def wda_idle_since() -> str | None:
    """When the relay idle-stopped WDA, or None when WDA was not idle-stopped."""
    try:
        return _idle_file().read_text(encoding="utf-8").strip() or "?"
    except OSError:
        return None


_LOG_KEEP = 1024 * 1024  # bytes of a spawned process's log kept across restarts


def _open_log(name: str):
    """A spawned process's log, opened for appending: the end of the previous
    run's log is the only record of how it stopped. Cut back to
    its last _LOG_KEEP bytes, then marked with this start. Never cut while an
    earlier run still writes it: on Windows its handle keeps the old offset
    and fills the gap with NUL bytes."""
    path = _log_file(name)
    try:
        if path.stat().st_size > _LOG_KEEP and proc_status(name) != "running":
            path.write_bytes(path.read_bytes()[-_LOG_KEEP:])
    except OSError:
        pass
    log = open(path, "a", encoding="utf-8")
    log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {name} started\n")
    log.flush()
    return log


def _spawn(name: str, args: list[str]) -> int:
    """Start `ios <args>` detached; record pid and send output to a log file."""
    exe = ios_path()
    if not exe:
        raise DeviceError("go-ios not found on PATH. Install it: npm install -g go-ios")
    config.STATE_DIR.mkdir(exist_ok=True)
    log = _open_log(name)
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


def _pythonw() -> str:
    """The windowless interpreter next to this one (no console flash on Windows)."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if sys.platform == "win32" and windowless.is_file() else exe)


def _spawn_py(name: str, args: list[str]) -> int:
    """Start `pythonw <args>` detached with this package importable; pid + log in .state/."""
    config.STATE_DIR.mkdir(exist_ok=True)
    log = _open_log(name)
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [_pythonw(), *args],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
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


# What each process we start has on its command line. A pid file outlives its
# process and Windows reuses pids, so a live pid is ours only if this matches.
_PROC_MARKERS = {
    "relay": "phone_harness.relay",
    "runwda": "xcuitest",
    "syslog": "syslog",
}


def _pid_cmdline(pid: int) -> str:
    """The live process's command line, lowercased ('' if dead or unreadable)."""
    import psutil

    try:
        return " ".join(psutil.Process(pid).cmdline()).lower()
    except (psutil.Error, OSError):
        return ""


def _pid_is(name: str, pid: int) -> bool:
    """Is `pid` still the `name` process we started?"""
    marker = _PROC_MARKERS.get(name)
    if marker is None:
        return _pid_alive(pid)
    return marker in _pid_cmdline(pid)


def _safe_kill(pid: int, expected_prefix: str, tree: bool = True, marker: str | None = None) -> bool:
    """Force-kill `pid` only if its executable name starts with
    `expected_prefix`. Pid files outlive their process and Windows reuses
    pids, so an unchecked kill could hit an innocent process. Returns True
    if a kill was issued.

    `tree=False` spares the children. The tunnel and the forwards are children
    of whatever launched them, so killing a viewer's tree takes the phone link
    down with it — see _kill_stale_viewer."""
    if not expected_prefix or not _pid_image(pid).startswith(expected_prefix.lower()):
        return False
    if marker is not None and marker not in _pid_cmdline(pid):
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


def _kill_own(pid: int, expected_prefix: str, marker: str) -> bool:
    """Kill `pid` and those descendants that are the same program, sparing the
    rest. A venv's pythonw.exe is a launcher that runs the real interpreter as
    its child, so a plain kill leaves the program running; a tree kill takes
    everything it started, and the relay's self-heal starts WDA, so restarting
    the relay used to kill WDA with it (seen 2026-10-18 23:36 ET)."""
    import psutil

    try:
        same = [
            child.pid
            for child in psutil.Process(pid).children(recursive=True)
            if marker in _pid_cmdline(child.pid)
        ]
    except (psutil.Error, OSError):
        same = []
    if not _safe_kill(pid, expected_prefix, tree=False, marker=marker):
        return False
    for child in same:
        _safe_kill(child, expected_prefix, tree=False, marker=marker)
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
    return "running" if _pid_is(name, pid) else "dead"


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
    legacy = Path(exe).name.lower() if exe else "ios"
    stopped = []
    if names is PROCS:
        names = (*PROCS, *LEGACY_PROCS)
    for name in names:
        expected = legacy if name in LEGACY_PROCS else "python"
        pf = _pid_file(name)
        if not pf.exists():
            continue
        try:
            pid = int(pf.read_text().strip())
        except ValueError:
            pf.unlink()
            continue
        # A runwda.pid written before the tunneld link points at go-ios's
        # `ios runwda`, whose WDA listens on every address: stop that one too.
        marker = _PROC_MARKERS.get(name)
        if name in LEGACY_PROCS or marker is None:
            killed = _safe_kill(pid, expected, marker=marker)
        else:
            killed = _kill_own(pid, expected, marker)
        if killed or (name == "runwda" and _safe_kill(pid, legacy)):
            stopped.append(name)
        pf.unlink()
    return stopped


# ---- bring-up --------------------------------------------------------------


# The flat sleep this replaced was 3s from the first commit and was never
# measured. `tunnel ls` is an HTTP query to the local go-ios tunnel daemon (it
# does not touch the phone), but it is still a ~207ms process spawn, so the
# interval stays coarse. The cap EQUALS the sleep it replaced, so the worst
# case is unchanged and a bad result is a one-line revert.
# tunneld found the phone over Wi-Fi within 5 s of the link appearing (measured
# 2026-10-18); a fresh start of tunneld itself adds its own start-up.
_TUNNEL_READY_TIMEOUT = 20.0
_TUNNEL_READY_POLL = 0.5


def start_tunnel() -> None:  # noqa: vulture  (called from admin.py/viewer.py, outside this commit)
    """Make sure tunneld runs and has a tunnel to the phone.

    tunneld creates TUN interfaces, so it needs admin rights and runs as its own
    logon task; this starts that task if tunneld is not answering, then waits
    for a tunnel. USB or Wi-Fi: tunneld picks whichever link the phone is on.
    """
    _start_tunneld_task()
    deadline = time.monotonic() + _TUNNEL_READY_TIMEOUT
    while time.monotonic() < deadline:
        if tunnel_running():
            return
        time.sleep(_TUNNEL_READY_POLL)
    if not link.tunneld_up():
        raise DeviceError(
            f"tunneld is not answering and the '{config.TUNNELD_TASK}' task did not start it. "
            "Register the task from an admin PowerShell: powershell -ExecutionPolicy Bypass "
            "-File scripts\\install_tunneld_task.ps1 -Python <the python.exe SideTap uses>"
        )
    try:
        link.tunnel_for()
    except link.LinkError as exc:
        # tunnel_for() names a failed tunnel request (pairing, service) apart
        # from "the phone is away"; pass that on instead of a generic remedy.
        raise DeviceError(f"tunneld is running but has no tunnel to the phone: {exc}") from exc
    # The tunnel came up just as the wait ended.


def start_wda(bundle_id: str) -> None:  # noqa: vulture  (called from admin.py/viewer.py, outside this commit)
    """Start WebDriverAgent through the current tunnel, listening on it only.

    USE_IP binds WDA's HTTP and MJPEG servers to the device's tunnel address;
    without it WDA listens on every interface and anyone on the home Wi-Fi can
    drive the phone (no authentication). A runner left from an earlier tunnel
    is killed first, or the new one dies with XCTest error 103.
    """
    address, port, _ = _link(link.tunnel_for)
    with wda_lifecycle_lock():
        with contextlib.suppress(link.LinkError):
            link.kill_app(bundle_id)
        _idle_file().unlink(missing_ok=True)
        _spawn_py(
            "runwda",
            [
                "-m", "pymobiledevice3", "developer", "dvt", "xcuitest", bundle_id,
                "--rsd", address, str(port),
                "--env", f"USE_IP={address}",
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
    try:
        return bool(link.launch("com.apple.springboard"))
    except link.LinkError:
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
        # Only a go-ios forwarder on this port: agent calls now reach this
        # path on their own, so an ios.exe doing anything else
        # (another session's tunnel, a syslog) is never killed by name alone.
        cmd = _pid_cmdline(int(pid)) if pid.isdigit() else ""
        if "ios.exe" in info.stdout.lower() and " forward" in cmd and str(port) in cmd:
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
    """Start the loopback relay (relay.py) unless it already runs.

    It follows the tunnel on its own, so a replug or a Wi-Fi reconnect needs no
    restart of the relay. Leftover go-ios forwarders on the same ports are
    cleared first. Serialized across processes (relay_start_lock)."""
    with relay_start_lock():
        if proc_status("relay") == "running":
            return
        _free_port(config.WDA_PORT)
        _free_port(config.MJPEG_PORT)
        _spawn_py("relay", ["-m", "phone_harness.relay"])


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
