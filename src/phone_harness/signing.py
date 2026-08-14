"""Fix touch input on a free Apple ID: capture Sideloadly's provisioning
profile locally, then re-sign WDA (nested .xctest included) with go-ios.

Why this module exists (kept out of device.py, which only manages go-ios
processes): the input driver fails because Sideloadly signs the WebDriverAgent
host app but leaves the nested WebDriverAgentRunner.xctest unsigned, so iOS
Library Validation rejects it. The fix is:

  1. Build a .p12 from Sideloadly's own developer cert/key (same Team ID it uses
     to mint the profile) with openssl.
  2. Let the human run Sideloadly once (a real Apple login - we never script
     Apple auth or reuse session tokens). While it signs, it writes the freshly
     minted `embedded.mobileprovision` into a temp folder; we watch for it and
     copy it out.
  3. Re-sign the whole IPA with `ios sign app` - this signs the nested .xctest
     with the Team ID, which is what was missing.

Everything here is local: openssl on the user's own key, a filesystem watch, and
go-ios signing. No network calls, no Apple authentication.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import config, device

WDA_IPA = config.REPO_ROOT / "wda" / "WebDriverAgent.ipa"
P12_PATH = config.STATE_DIR / "wda.p12"
PROFILE_PATH = config.STATE_DIR / "profile.mobileprovision"
P12_PASSWORD = "wda"

# Sideloadly stores the free-account developer identity here.
SIDELOADLY_DIR = Path(os.environ.get("APPDATA", "")) / "Sideloadly"

# What a WDA provisioning profile's app id looks like (case-insensitive match).
_WDA_MARKER = "webdriveragent"

Progress = Callable[[str, str], None]


class SigningError(RuntimeError):
    pass


def _noop(*_args) -> None:  # default progress sink
    pass


# ---- Sideloadly developer identity -> .p12 ---------------------------------


def sideloadly_identity() -> tuple[Path, Path]:
    """Return (cert.pem, key.pem) from Sideloadly, or raise with a fix hint."""
    key = SIDELOADLY_DIR / "key.pem"
    certs = sorted(SIDELOADLY_DIR.glob("cert-*.pem"))
    if not certs or not key.exists():
        raise SigningError(
            "Sideloadly developer cert not found in "
            f"{SIDELOADLY_DIR}. Sign WDA in Sideloadly once so it creates the "
            "cert, then retry."
        )
    return certs[0], key


def _find_openssl() -> str | None:
    """openssl from PATH, else Git for Windows' copies (PowerShell-launched
    processes often lack Git's bin dirs on PATH; bash-launched ones have them)."""
    found = shutil.which("openssl")
    if found:
        return found
    for cand in (
        Path("C:/Program Files/Git/mingw64/bin/openssl.exe"),
        Path("C:/Program Files/Git/usr/bin/openssl.exe"),
    ):
        if cand.exists():
            return str(cand)
    return None


def build_p12(dest: Path = P12_PATH, password: str = P12_PASSWORD) -> Path:
    """Build a .p12 from Sideloadly's cert/key so the signing identity matches
    the profile Sideloadly mints (same Team ID)."""
    cert, key = sideloadly_identity()
    openssl = _find_openssl()
    if not openssl:
        raise SigningError(
            "openssl not found on PATH (Git for Windows ships it in "
            "C:\\Program Files\\Git\\mingw64\\bin)."
        )
    dest.parent.mkdir(exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.run(
        [
            openssl,
            "pkcs12",
            "-export",
            "-out",
            str(dest),
            "-inkey",
            str(key),
            "-in",
            str(cert),
            "-passout",
            f"pass:{password}",
            "-name",
            "WDA",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=flags,
    )
    if proc.returncode != 0 or not dest.exists():
        raise SigningError(f"openssl failed to build the .p12:\n{proc.stderr.strip()}")
    return dest


# ---- provisioning profile parsing / validation -----------------------------


def parse_profile(data: bytes) -> dict:
    """Pull the plist out of a CMS-signed .mobileprovision and summarize it.

    A .mobileprovision is a PKCS#7 blob wrapping an XML plist; the plist sits
    verbatim between the <?xml ...> and </plist> markers.
    """
    start = data.find(b"<?xml")
    end = data.find(b"</plist>")
    if start == -1 or end == -1:
        raise SigningError("not a provisioning profile (no embedded plist)")
    plist = plistlib.loads(data[start : end + len(b"</plist>")])
    ent = plist.get("Entitlements", {})
    app_id = ent.get("application-identifier", "")
    team_id = (plist.get("TeamIdentifier") or [""])[0]
    # application-identifier is "<TeamID>.<bundleid>"; the bundle id is what the
    # re-signed app must carry so the profile accepts it. On a free account
    # Sideloadly makes the bundle id unique by appending the team id.
    bundle_id = (
        app_id[len(team_id) + 1 :] if app_id.startswith(team_id + ".") else app_id
    )
    return {
        "name": plist.get("Name", ""),
        "app_id": app_id,
        "bundle_id": bundle_id,
        "team_id": team_id,
        "expires": plist.get("ExpirationDate"),
        "udids": list(plist.get("ProvisionedDevices", []) or []),
    }


def profile_is_valid(info: dict, udid: str | None) -> tuple[bool, str]:
    """Is this the profile we want: WDA app id, our device, not expired."""
    if _WDA_MARKER not in info["app_id"].lower():
        return False, f"app id {info['app_id']!r} is not WebDriverAgent"
    exp = info.get("expires")
    if isinstance(exp, datetime):
        exp_utc = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        if exp_utc <= datetime.now(timezone.utc):
            return False, f"profile expired {exp_utc:%Y-%m-%d}"
    if udid and info["udids"] and udid not in info["udids"]:
        return False, "profile is for a different device"
    return True, "ok"


def _recent_subdirs(root: Path, newer_than: float) -> list[Path]:
    """Immediate subdirectories of root touched since `newer_than`.

    Sideloadly signs inside a fresh `%TEMP%/tmpXXXXXXXX` folder (Python
    tempfile). Walking only those - not all of %TEMP%, which can hold tens of
    thousands of files - keeps each scan fast enough to catch the profile before
    Sideloadly deletes it on cleanup.
    """
    out = []
    try:
        with os.scandir(root) as it:
            for entry in it:
                try:
                    if entry.is_dir() and entry.stat().st_mtime >= newer_than:
                        out.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        pass
    return out


def _read_profile_file(fp: Path, newer_than: float):
    """Yield (source, bytes) if fp is a recent profile or a resigned ipa that
    holds one; silent on anything else."""
    low = fp.name.lower()
    try:
        if fp.stat().st_mtime < newer_than:
            return
    except OSError:
        return
    if low.endswith(".mobileprovision"):
        try:
            yield fp, fp.read_bytes()
        except OSError:
            return
    elif low.endswith(".ipa"):
        try:
            with zipfile.ZipFile(fp) as z:
                for n in z.namelist():
                    if n.lower().endswith("embedded.mobileprovision"):
                        yield fp, z.read(n)
        except (OSError, zipfile.BadZipFile):
            return


def _profiles_in(path: Path, newer_than: float):
    """Yield (source, bytes) for profiles anywhere under directory `path`."""
    try:
        walker = os.walk(path)
    except OSError:
        return
    for dirpath, _dirs, files in walker:
        for fname in files:
            yield from _read_profile_file(Path(dirpath) / fname, newer_than)


def _iter_profile_bytes(root: Path, newer_than: float):
    """Yield (source, bytes) for freshly written profiles: loose files sitting
    directly in root, plus everything inside recently touched subdirs."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        entries = []
    for entry in entries:
        if entry.is_file():
            yield from _read_profile_file(Path(entry.path), newer_than)
    for sub in _recent_subdirs(root, newer_than):
        yield from _profiles_in(sub, newer_than)


WATCHER_PS = config.REPO_ROOT / "scripts" / "watch_profile.ps1"


def capture_profile(
    udid: str | None,
    timeout: float = 600.0,
    dest: Path = PROFILE_PATH,
    progress: Progress = _noop,
    temp_roots: list[Path] | None = None,
) -> dict:
    """Capture the profile Sideloadly writes while it signs WDA.

    On Windows this uses an event-driven FileSystemWatcher (scripts/
    watch_profile.ps1) - the profile exists for only a few hundred ms, so a
    polling scan loses the race. `temp_roots` forces the polling path (used by
    tests). Returns the parsed, validated profile info and copies bytes to dest.
    """
    if temp_roots is None and sys.platform == "win32" and WATCHER_PS.exists():
        return _capture_via_watcher(udid, timeout, dest, progress)
    return _capture_via_poll(udid, timeout, dest, progress, temp_roots)


def _capture_via_watcher(
    udid: str | None, timeout: float, dest: Path, progress: Progress
) -> dict:
    out = config.STATE_DIR / "captured.mobileprovision"
    config.STATE_DIR.mkdir(exist_ok=True)
    # The watcher script treats "out exists" as "captured", so a stale file
    # from an earlier run would be accepted as this run's mint. The script
    # deletes it too, but under SilentlyContinue — a locked file slips
    # through there. Clear it HERE, loudly, before the watcher arms.
    try:
        out.unlink(missing_ok=True)
    except OSError as exc:
        raise SigningError(f"cannot clear stale capture {out.name}: {exc}") from exc
    proc = subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WATCHER_PS),
            "-OutFile",
            str(out),
            "-TimeoutSec",
            str(int(timeout)),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for line in proc.stdout or []:
        line = line.strip()
        if line == "READY":
            progress("waiting", "armed - click Start in Sideloadly now")
        elif line.startswith("CAPTURED_FROM"):
            progress("captured", "profile written by Sideloadly")
    proc.wait()
    if proc.returncode != 0 or not out.exists():
        raise SigningError(
            "timed out waiting for the profile. Click Start in Sideloadly during "
            "the watch, or pass the profile directly: "
            "phone-harness fix-input <path-to.mobileprovision>"
        )
    data = out.read_bytes()
    info = parse_profile(data)
    ok, why = profile_is_valid(info, udid)
    if not ok:
        raise SigningError(f"captured a profile but it is unusable: {why}")
    dest.write_bytes(data)
    info["source"] = str(out)
    return info


def _capture_via_poll(
    udid: str | None,
    timeout: float,
    dest: Path,
    progress: Progress,
    temp_roots: list[Path] | None,
) -> dict:
    """Polling fallback: scan fresh temp subdirs for the profile."""
    roots = temp_roots or [Path(tempfile.gettempdir())]
    override = os.environ.get("SIDELOADLY_TEMP")
    if override:
        roots.insert(0, Path(override))
    start = time.time()
    # allow a small backdate so a file written just as we start still counts
    newer_than = start - 3
    deadline = start + timeout
    ticks = 0
    while time.time() < deadline:
        for root in roots:
            for src, data in _iter_profile_bytes(root, newer_than):
                try:
                    info = parse_profile(data)
                except SigningError:
                    continue  # partial write; try again next poll
                ok, _why = profile_is_valid(info, udid)
                if ok:
                    dest.parent.mkdir(exist_ok=True)
                    dest.write_bytes(data)
                    info["source"] = str(src)
                    progress("captured", f"got profile '{info['name']}'")
                    return info
        ticks += 1
        progress("waiting", f"watching for Sideloadly to sign... ({ticks})")
        time.sleep(0.7)
    raise SigningError(
        "timed out waiting for the profile. Make sure you clicked Start in "
        "Sideloadly and it finished signing. You can also pass the profile path "
        "directly: phone-harness fix-input <path-to.mobileprovision>"
    )


# ---- orchestration ---------------------------------------------------------


def fix_input(  # noqa: vulture  (viewer worker + dispatched by name from run.py)
    profile: Path | None = None,
    timeout: float = 600.0,
    progress: Progress = _noop,
) -> dict:
    """End to end: build p12, capture (or accept) the profile, re-sign, bring up.

    Returns {"ok": bool, "step": str, "message": str}. `progress(step, message)`
    is called at each stage so a CLI or the viewer can show live status.
    """
    if not WDA_IPA.exists():
        return {
            "ok": False,
            "step": "error",
            "message": f"WDA IPA not found at {WDA_IPA}. Run docs/setup step 3 first.",
        }

    try:
        progress("p12", "building signing identity from Sideloadly cert")
        build_p12()

        udid = device.current_udid()
        # The new profile goes to a staging path first. PROFILE_PATH is what
        # the doctor's countdown reads, so it must describe what is ON THE
        # PHONE: committing before `ios sign app` succeeds turned a failed
        # re-sign into a fresh 7-day PASS over the old, dying signature.
        pending = PROFILE_PATH.with_name(PROFILE_PATH.name + ".pending")
        if profile is not None:
            data = profile.read_bytes()
            info = parse_profile(data)
            ok, why = profile_is_valid(info, udid)
            if not ok:
                return {"ok": False, "step": "error", "message": f"bad profile: {why}"}
            pending.parent.mkdir(exist_ok=True)
            pending.write_bytes(data)
            progress("captured", f"using profile '{info['name']}'")
        else:
            progress(
                "waiting",
                "Open Sideloadly, load wda/WebDriverAgent.ipa, click Start now.",
            )
            info = capture_profile(
                udid, timeout=timeout, dest=pending, progress=progress
            )

        bundleid = info.get("bundle_id")
        if bundleid == "*":  # wildcard profile: keep the app's own id
            bundleid = None
        progress(
            "signing",
            f"re-signing WDA as {bundleid or 'its own id'} (nested .xctest incl.)",
        )
        device.sign_app(
            WDA_IPA, P12_PATH, pending, p12password=P12_PASSWORD, bundleid=bundleid
        )
        os.replace(pending, PROFILE_PATH)  # signed and installed: commit

        progress("up", "starting tunnel + WDA")
        from . import admin

        rc = admin.up()
        if rc != 0:
            return {
                "ok": False,
                "step": "up",
                "message": "signed and installed, but WDA did not answer. "
                "Check `phone-harness doctor`.",
            }
        exp = info.get("expires")
        when = f" (good until {exp:%Y-%m-%d})" if isinstance(exp, datetime) else ""
        return {
            "ok": True,
            "step": "done",
            "message": f"Input is live{when}. Taps work now.",
        }
    except (SigningError, device.DeviceError) as exc:
        return {"ok": False, "step": "error", "message": str(exc)}
    except subprocess.TimeoutExpired as exc:
        # openssl (30s) and `ios sign app` (300s) both run under a subprocess
        # timeout. Letting TimeoutExpired escape killed the viewer's fix-input
        # worker thread, which froze the wizard at "running" forever.
        return {"ok": False, "step": "error", "message": str(exc)}
