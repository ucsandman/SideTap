"""go-ios wrapper: device discovery, tunnel, WDA launch, port forwards.

Long-running processes (tunnel, runwda, forwards) are started detached with
their pids and logs kept in .state/ so `up` and `down` can manage them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config


class DeviceError(RuntimeError):
    pass


PROCS = ("tunnel", "runwda", "forward8100", "forward9100")


def ios_path() -> str | None:
    found = shutil.which("ios")
    if found:
        return found
    # Windows truncates a registry PATH past ~4095 chars when it builds the
    # logon environment, so shortcut/Startup launches can miss the npm global
    # dir even though terminals (which rebuild PATH in shell profiles) see it.
    npm_exe = Path(os.environ.get("APPDATA", "")) / "npm" / "ios.exe"
    return str(npm_exe) if npm_exe.is_file() else None


def _run(args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    exe = ios_path()
    if not exe:
        raise DeviceError("go-ios not found on PATH. Install it: npm install -g go-ios")
    return subprocess.run(
        [exe, *args],
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
    proc = _run(["list"])
    for obj in _json_lines(proc.stdout + proc.stderr):
        if "deviceList" in obj:
            return list(obj["deviceList"])
    return []


def list_apps() -> list[dict]:
    """Installed apps as [{bundle_id, name}]. Parses go-ios output tolerantly."""
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
    if apps:
        return apps
    # fallback: plain "bundleid name" lines
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) >= 1 and "." in parts[0]:
            apps.append(
                {"bundle_id": parts[0], "name": parts[1] if len(parts) > 1 else ""}
            )
    return apps


def _wda_cache_file() -> Path:
    return config.STATE_DIR / "wda_bundle"


def detect_wda_bundle() -> str | None:
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


def tunnel_running() -> bool:
    try:
        proc = _run(["tunnel", "ls"], timeout=10)
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


def mount_ddi() -> tuple[bool, str]:
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
        [exe, *args],
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


def stop_all() -> list[str]:
    """Kill every process we started. Returns names of processes stopped."""
    exe = ios_path()
    expected = Path(exe).name.lower() if exe else "ios"
    stopped = []
    for name in PROCS:
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


def start_tunnel() -> None:
    """Start the iOS 17+ tunnel. Tries userspace mode first (no admin needed)."""
    _spawn("tunnel", ["tunnel", "start", "--userspace"])
    time.sleep(3)
    if proc_status("tunnel") == "dead":
        raise DeviceError(
            "Tunnel failed to start. Log tail:\n" + log_tail("tunnel") + "\n"
            "Fix: run `ios tunnel start` in an **admin** terminal (needs wintun.dll in "
            "C:\\Windows\\System32, from https://www.wintun.net), keep it open, then retry."
        )


def start_wda(bundle_id: str) -> None:
    _spawn(
        "runwda",
        [
            "runwda",
            f"--bundleid={bundle_id}",
            f"--testrunnerbundleid={bundle_id}",
            "--xctestconfig=WebDriverAgentRunner.xctest",
        ],
    )


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


def port_exposed_to_lan(port: int) -> bool:
    """True if `port` is LISTENING on any address other than loopback.

    go-ios 1.2.1 has no bind-address flag, so `ios forward` listens on 0.0.0.0 —
    reachable by the whole LAN. Reuses the netstat parse from `_free_port`.
    """
    if sys.platform != "win32":
        return False
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError:
        return False
    for line in proc.stdout.splitlines():
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


def lan_block_rule_active(rule_name: str = "phone-harness block LAN") -> bool:
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


def start_forwards() -> None:
    _free_port(config.WDA_PORT)
    _free_port(config.MJPEG_PORT)
    _spawn("forward8100", ["forward", str(config.WDA_PORT), "8100"])
    _spawn("forward9100", ["forward", str(config.MJPEG_PORT), "9100"])


def current_udid() -> str | None:  # noqa: vulture  (used by signing.py)
    """First connected iPhone's UDID, or None."""
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
