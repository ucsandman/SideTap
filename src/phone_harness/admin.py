"""Doctor diagnostics, up/down orchestration, and the expiry reminder."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from . import capture, config, device
from .wda_client import WDAClient, stop_file


def _check_stop_engaged():
    """A leftover .state/STOP blocks every action while all infra checks pass."""
    if stop_file().exists():
        return (
            False,
            "kill switch ENGAGED (.state/STOP exists) — every action is blocked",
            "Click RESUME in the viewer, or delete .state/STOP.",
        )
    return True, "kill switch off", ""


def _check_go_ios():
    path = device.ios_path()
    if path:
        return True, f"go-ios found: {path}", ""
    return (
        False,
        "go-ios (`ios`) not on PATH",
        "Install Node.js, then: npm install -g go-ios",
    )


def _check_device():
    try:
        udids = device.list_devices()
    except Exception as exc:
        return (
            False,
            f"`ios list` failed: {exc}",
            "Reconnect the iPhone over USB and tap Trust.",
        )
    if udids:
        return True, f"iPhone connected: {udids[0]}", ""
    return (
        False,
        "No iPhone found over USB",
        (
            "Plug in the cable, unlock the phone, tap Trust. "
            "Install the 'Apple Devices' app from Microsoft Store for the USB driver. "
            "Also enable Developer Mode: Settings > Privacy & Security > Developer Mode."
        ),
    )


def _check_tunnel():
    if device.tunnel_running():
        return True, "iOS 17+ tunnel is up", ""
    return False, "Tunnel not running", "Run: phone-harness up   (starts it for you)"


def _check_wda_installed():
    try:
        if not device.list_devices():
            return (
                False,
                "cannot check: no iPhone connected",
                "Connect the phone first.",
            )
    except Exception as exc:
        return (
            False,
            f"cannot check: {exc}",
            "Install go-ios and connect the phone first.",
        )
    bundle = device.detect_wda_bundle()
    if bundle:
        return True, f"WebDriverAgent installed: {bundle}", ""
    # No bundle: separate "phone asleep (empty app list)" from "really absent".
    # Deep sleep empties the list while the app is still installed (2026-08-10).
    try:
        apps = device.list_apps()
    except Exception:
        apps = []
    if not apps:
        return (
            False,
            "cannot list apps — phone asleep or locked?",
            (
                "Wake the phone (side button). The viewer restarts the link "
                "automatically; or run: phone-harness up"
            ),
        )
    return (
        False,
        "WebDriverAgent app not found on the phone",
        (
            "Sideload it with Sideloadly + your free Apple ID. See docs/setup-windows.md step 3. "
            "If it IS installed under a custom name, set WDA_BUNDLE_ID in .env."
        ),
    )


def _check_perception():
    """Screenshots via go-ios need no app signing. This is the 'seeing' half."""
    try:
        png = capture.screenshot_png()
    except Exception as exc:
        return (
            False,
            f"screenshot failed: {exc}",
            "Unlock the phone; make sure the developer image is mounted "
            "(`ios image auto`).",
        )
    return True, f"live screen capture works ({len(png) // 1024} KB frame)", ""


def _check_wda_responding():
    client = WDAClient(timeout=5)
    if client.is_up():
        return True, f"WDA answering at {config.WDA_URL}", ""
    return (
        False,
        f"WDA not answering at {config.WDA_URL}",
        (
            "If the phone was asleep, wake it (side button) - the viewer restarts "
            "the link automatically. After a replug: click 'Restart link' (or run: "
            "phone-harness up). If it fails right after a working week, the "
            "free-ID signature likely expired (7 days) - re-sign WDA in Sideloadly."
        ),
    )


def _check_signature():
    """Free-Apple-ID signatures die after 7 days; count down before input drops."""
    from . import signing

    path = signing.PROFILE_PATH
    if not path.exists():
        return True, "no captured profile to check (fix-input records one)", ""
    try:
        info = signing.parse_profile(path.read_bytes())
    except signing.SigningError as exc:
        return (
            False,
            f"cannot read {path.name}: {exc}",
            "Re-run: phone-harness fix-input",
        )
    exp = info.get("expires")
    if not isinstance(exp, datetime):
        return True, "profile has no expiry date", ""
    exp_utc = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
    left = exp_utc - datetime.now(timezone.utc)
    fix = "Run phone-harness fix-input (free-ID signatures last 7 days)."
    if left.total_seconds() <= 0:
        return False, f"input signature expired {exp_utc:%Y-%m-%d %H:%M} UTC", fix
    if left < timedelta(hours=48):
        hours = int(left.total_seconds() // 3600)
        return (
            False,
            f"input signature expires in {hours}h ({exp_utc:%Y-%m-%d %H:%M} UTC)",
            fix,
        )
    return (
        True,
        f"input signature good for {left.days} more day(s) (until {exp_utc:%Y-%m-%d})",
        "",
    )


def _check_ports_local():
    """WDA (:8100) and MJPEG (:9100) must not be reachable from the LAN.

    go-ios has no bind flag, so the forwards listen on 0.0.0.0 by default and
    WebDriverAgent has no auth — anyone on the same Wi-Fi could drive the phone.
    """
    exposed = [
        p for p in (config.WDA_PORT, config.MJPEG_PORT) if device.port_exposed_to_lan(p)
    ]
    if not exposed:
        return True, "WDA/MJPEG ports are loopback-only", ""
    # go-ios binds 0.0.0.0, so the socket alone always looks exposed; the firewall
    # block rule is what actually protects it.
    if device.lan_block_rule_active():
        return True, "firewall blocks LAN access to WDA/MJPEG", ""
    ports = ", ".join(str(p) for p in exposed)
    return (
        False,
        f"port(s) {ports} are reachable from your whole network (WDA has no auth)",
        "Click 'Lock ports' below, or run scripts/lock_ports.ps1 (one-time, needs admin).",
    )


# ---- signature-expiry reminder ---------------------------------------------
# The 7-day free-Apple-ID signature can only be renewed with one click in
# Sideloadly (Apple auth mints the new profile — nothing local can). What CAN
# be automated is never being surprised: a daily toast when <36h remain.

_REMINDER_TASK = "phone-harness signature reminder"
_REMIND_HOURS = 36.0
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _hours_left() -> float | None:
    """Hours until the captured profile expires; None if unknown/not set up."""
    from . import signing

    if not signing.PROFILE_PATH.exists():
        return None
    try:
        exp = signing.parse_profile(signing.PROFILE_PATH.read_bytes()).get("expires")
    except signing.SigningError:
        return None
    if not isinstance(exp, datetime):
        return None
    exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
    return (exp - datetime.now(timezone.utc)).total_seconds() / 3600


def _toast(title: str, body: str) -> bool:
    """Windows toast via WinRT (no modules needed). False if it could not show."""
    xml = (
        '<toast scenario="reminder"><visual><binding template="ToastGeneric">'
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual></toast>"
    )
    aumid = (
        "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
        "\\WindowsPowerShell\\v1.0\\powershell.exe"
    )
    ps = (
        # Each WinRT type must be loaded explicitly before use (PS 5.1).
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.UI.Notifications.ToastNotification, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, "
        "Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null; "
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        f"$x.LoadXml('{xml}'); "
        "$t = New-Object Windows.UI.Notifications.ToastNotification $x; "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        f"'{aumid}').Show($t)"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            timeout=30,
            creationflags=_NO_WINDOW,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def notify_expiry() -> int:
    """Toast if the signature has <36h left. Silent (exit 0) otherwise."""
    hours = _hours_left()
    if hours is None:
        print("no captured profile; nothing to remind about")
        return 0
    if hours > _REMIND_HOURS:
        print(f"signature good for {hours / 24:.1f} more days; no reminder needed")
        return 0
    when = "has EXPIRED" if hours <= 0 else f"expires in {hours:.0f}h"
    shown = _toast(
        f"sidetap: WDA signature {when}",
        "Run `phone-harness fix-input` and click Start in Sideloadly.",
    )
    print(f"signature {when} — toast {'shown' if shown else 'FAILED to show'}")
    return 0 if shown else 1


def _reminder_command() -> str:
    # The scheduled task starts in System32 with a bare env; the .cmd wrapper
    # sets PYTHONPATH itself, so the task needs no environment of its own.
    return f'"{config.REPO_ROOT / "phone-harness.cmd"}" notify-expiry'


def _reminder_installed() -> bool:
    try:
        return (
            subprocess.run(
                ["schtasks", "/Query", "/TN", _REMINDER_TASK],
                capture_output=True,
                timeout=15,
                creationflags=_NO_WINDOW,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def reminder_install() -> int:
    proc = subprocess.run(
        ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", "10:00",
         "/TN", _REMINDER_TASK, "/TR", _reminder_command()],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=_NO_WINDOW,
    )  # fmt: skip
    if proc.returncode == 0:
        print(f"Daily 10:00 reminder installed (task: {_REMINDER_TASK!r}).")
        return 0
    print(f"schtasks failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return 1


def reminder_uninstall() -> int:
    proc = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", _REMINDER_TASK],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=_NO_WINDOW,
    )
    print("Reminder removed." if proc.returncode == 0 else proc.stderr.strip())
    return 0 if proc.returncode == 0 else 1


def _check_reminder():
    """Opt-in, so never a FAIL: the detail carries the install hint instead."""
    if _reminder_installed():
        return True, "daily expiry toast scheduled (10:00)", ""
    return (
        True,
        "no daily expiry toast — install: phone-harness notify-expiry --install",
        "",
    )


CHECKS = [
    ("kill switch (STOP)", _check_stop_engaged),
    ("go-ios installed", _check_go_ios),
    ("iPhone on USB", _check_device),
    ("tunnel", _check_tunnel),
    ("perception (view/OCR)", _check_perception),
    ("WDA installed (input)", _check_wda_installed),
    ("WDA responding (input)", _check_wda_responding),
    ("input signature (7-day)", _check_signature),
    ("expiry reminder", _check_reminder),
    ("LAN exposure", _check_ports_local),
]


def doctor_results() -> list[dict]:
    """Run all checks. Later checks still run so the user sees the full picture."""
    results = []
    for name, fn in CHECKS:
        try:
            ok, detail, fix = fn()
        except Exception as exc:  # a check must never crash the doctor
            ok, detail, fix = False, f"check crashed: {exc}", ""
        results.append({"name": name, "ok": ok, "detail": detail, "fix": fix})
    return results


def doctor() -> int:  # noqa: vulture
    results = doctor_results()
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"[{mark}] {r['name']}: {r['detail']}")
        if not r["ok"] and r["fix"]:
            print(f"       fix: {r['fix']}")
    if all(r["ok"] for r in results):
        print("\nAll green. The agent can drive the phone.")
        return 0
    print("\nFix the first FAIL above, then run `phone-harness doctor` again.")
    return 1


# launch.py's background bring-up and the viewer's Restart link can overlap;
# two concurrent up() runs would spawn duplicate tunnel/WDA processes.
_UP_LOCK = threading.Lock()


def up(wait_seconds: float = 60.0) -> int:  # noqa: vulture
    """Bring the whole chain up. Idempotent: skips whatever already runs.

    Serialized: a second caller waits, then returns fast via the is_up check.
    """
    with _UP_LOCK:
        return _up(wait_seconds)


def _up(wait_seconds: float) -> int:
    client = WDAClient(timeout=3)
    if client.is_up():
        print("Already up: WDA is answering.")
        return 0

    ok, detail, fix = _check_go_ios()
    if not ok:
        print(f"FAIL: {detail}\n  fix: {fix}")
        return 1
    ok, detail, fix = _check_device()
    if not ok:
        print(f"FAIL: {detail}\n  fix: {fix}")
        return 1
    print(f"OK: {detail}")

    if not device.tunnel_running():
        print("Starting tunnel (userspace)...")
        device.start_tunnel()
        print("OK: tunnel started")
    else:
        print("OK: tunnel already running")

    bundle = device.detect_wda_bundle()
    if not bundle:
        _, detail, fix = _check_wda_installed()
        print(f"FAIL: {detail}\n  fix: {fix}")
        return 1
    print(f"Starting WebDriverAgent ({bundle})...")
    device.start_wda(bundle)
    device.start_forwards()

    print("Waiting for WDA to answer", end="", flush=True)
    deadline = time.time() + wait_seconds
    client = WDAClient(timeout=3)
    while time.time() < deadline:
        if client.is_up():
            print("\nUp. WDA answering at", config.WDA_URL)
            try:
                client.configure_mjpeg()
            except Exception:
                pass  # viewer falls back to polling screenshots
            return 0
        print(".", end="", flush=True)
        time.sleep(2)
    print("\nFAIL: WDA never answered. runwda log tail:")
    print(device.log_tail("runwda", 10))
    print(
        "Common cause on a free Apple ID: the 7-day signature expired — re-sign in Sideloadly."
    )
    return 1


def down() -> int:  # noqa: vulture
    stopped = device.stop_all()
    print("Stopped: " + (", ".join(stopped) if stopped else "nothing was running"))
    return 0
