"""go-ios wrapper: device discovery, tunnel, WDA launch, port forwards.

Long-running processes (tunnel, runwda, forwards) are started detached with
their pids and logs kept in .state/ so `up` and `down` can manage them.
"""

from __future__ import annotations

import json
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
    return shutil.which("ios")


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


def detect_wda_bundle() -> str | None:
    """Find the installed WebDriverAgent runner. .env WDA_BUNDLE_ID wins."""
    if config.WDA_BUNDLE_ID:
        return config.WDA_BUNDLE_ID
    try:
        apps = list_apps()
    except (DeviceError, subprocess.TimeoutExpired):
        return None
    for app in apps:
        bid = app["bundle_id"].lower()
        if "webdriveragent" in bid or bid.endswith(".xctrunner"):
            return app["bundle_id"]
    return None


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
        if _pid_alive(pid):
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                import os
                import signal

                os.kill(pid, signal.SIGTERM)
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


def start_forwards() -> None:
    _spawn("forward8100", ["forward", str(config.WDA_PORT), "8100"])
    _spawn("forward9100", ["forward", str(config.MJPEG_PORT), "9100"])
