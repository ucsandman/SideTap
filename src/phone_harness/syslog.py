"""Rolling capture of the phone's system log, dumped when the link wedges.

This exists for appium/WebDriverAgent#1210. The maintainer's one ask was
"device syslog captured across an occurrence" of the wedge described in
device.py — an app that stops answering accessibility requests blocks every
WDA call that resolves the active application, and WDA serves requests one at
a time, so the whole agent stops.

The wedge cannot be scheduled: it fired every call or two for ~20 minutes
shortly after TikTok was installed, then not once in 28 swipes across 7 cold
starts the same night. So the capture has to run for the whole session. But
`ios syslog` streams ~27 KB/s (1.79 MB in 67s, measured against the real
device), which is ~100 MB/hour of disk for a file that is noise 99.9% of the
time. So the stream is held in memory as a ring of the last ~110 seconds and
written to disk only when something calls `mark()`.

The ring has to outlast the DETECTION delay, not just the event. The viewer
waits `_HEAL_MIN_SILENCE` (45s) of continuous silence before it will call a
dead link wedged, and the log worth reading is from around the moment the app
stopped answering — already 45s+ in the past by the time anyone notices.
"""

from __future__ import annotations

import collections
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config, device

# ~110s at the measured ~180 lines/s, ~4 MB of strings. Must clear viewer's
# _HEAL_MIN_SILENCE (45s) by a wide margin — see the module docstring.
RING_LINES = 20_000
# Keep recording after a mark: pressing Home releases the accessibility wait
# ~20s later, and that recovery is half of what the maintainer needs to see.
TAIL_SECONDS = 45.0
# `ios syslog` fails while the phone is asleep or the tunnel is down. The
# watchdog calls start() every 20s and must not respawn it that fast.
_RETRY_SECONDS = 120.0

_lines: collections.deque[str] = collections.deque(maxlen=RING_LINES)
_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_next_attempt = 0.0
_dump = None  # open file while a mark's tail is still recording


def _pid_file() -> Path:
    return config.STATE_DIR / "syslog.pid"


def _plain(raw: str) -> str | None:
    """One line of `ios syslog` output as the phone's own log text.

    go-ios wraps every device line as {"msg": "..."} and mixes in its own
    {"level":"INFO","module":"go-ios"} chatter. Both are noise in a dump
    somebody else has to read, and the wrapper is ~20% of the bytes.
    """
    raw = raw.strip()
    if not raw:
        return None
    if not raw.startswith("{"):
        return raw
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if obj.get("module") == "go-ios":
        return None
    msg = obj.get("msg")
    return msg if isinstance(msg, str) and msg else None


def _pump(proc: subprocess.Popen) -> None:
    """Drain the syslog stream into the ring (and into an open dump, if any).

    Never stops on a bad line: this thread going down means the next wedge is
    captured as an empty file, which is worse than useless because it looks
    like the phone said nothing.
    """
    for raw in proc.stdout:  # type: ignore[union-attr]
        try:
            line = _plain(raw)
        except Exception:
            continue
        if line is None:
            continue
        with _lock:
            _lines.append(line)
            if _dump is not None:
                try:
                    _dump.write(line + "\n")
                except OSError:
                    pass


def _close_dump() -> None:
    global _dump
    with _lock:
        if _dump is not None:
            try:
                _dump.close()
            except OSError:
                pass
            _dump = None


def start() -> bool:
    """Begin (or resume) capturing. Idempotent; a no-op once running."""
    global _proc, _next_attempt
    if _proc is not None and _proc.poll() is None:
        return True
    now = time.monotonic()
    if now < _next_attempt:
        return False
    _next_attempt = now + _RETRY_SECONDS
    exe = device.ios_path()
    if not exe:
        return False
    # A viewer killed with taskkill /F leaves its `ios syslog` child orphaned,
    # blocked forever on a pipe nobody reads but still holding a syslog relay
    # to the device. One accumulates per viewer restart without this.
    device.stop_all(("syslog",))
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(
            [exe, *device.pin_udid(["syslog"])],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
    except OSError:
        return False
    try:
        config.STATE_DIR.mkdir(exist_ok=True)
        _pid_file().write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass
    _proc = proc
    threading.Thread(target=_pump, args=(proc,), daemon=True).start()
    return True


def mark(label: str) -> Path | None:
    """Dump the ring, then the next TAIL_SECONDS of log, to a dated file.

    Returns the path, or None if nothing is being captured (CLI runs never
    start the reader) or a previous mark is still recording its tail — a
    second file starting mid-tail would split one occurrence across two dumps.
    """
    global _dump
    if _proc is None or _proc.poll() is not None:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = config.STATE_DIR / f"syslog-{label}-{stamp}.log"
    with _lock:
        if _dump is not None:
            return None
        try:
            config.STATE_DIR.mkdir(exist_ok=True)
            handle = open(path, "w", encoding="utf-8", errors="replace")
            handle.write(
                f"# {label} at {stamp}: {len(_lines)} lines of ring buffer, "
                f"then {TAIL_SECONDS:.0f}s of live tail\n"
                "# READ BEFORE SHARING: this is the phone's own log. It carries "
                "the device name, installed bundle ids, and whatever any daemon "
                "chose to log. Skim it before attaching it to a public issue.\n"
            )
            handle.write("\n".join(_lines))
            handle.write("\n# --- mark ---\n")
        except OSError:
            return None
        _dump = handle
    timer = threading.Timer(TAIL_SECONDS, _close_dump)
    timer.daemon = True  # never hold up interpreter exit for a debugging aid
    timer.start()
    return path
