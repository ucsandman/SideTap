"""Fix touch input on a free Apple ID: capture Sideloadly's provisioning
profile locally, then re-sign WDA (nested .xctest included) with go-ios.

Why this module exists (kept out of device.py, which only manages go-ios
processes): the input driver fails because Sideloadly signs the WebDriverAgent
host app but leaves the nested WebDriverAgentRunner.xctest unsigned, so iOS
Library Validation rejects it. The fix is:

  1. Build a .p12 from Sideloadly's own developer cert/key (same Team ID it uses
     to mint the profile) with openssl.
  2. Let the human run Sideloadly once (a real Apple login - we never script
     Apple auth or reuse session tokens) so Apple mints a fresh 7-day profile.
  3. Read that profile back off the PHONE, not off this PC (see below).
  4. Re-sign the whole IPA with `ios sign app` - this signs the nested .xctest
     with the Team ID, which is what was missing.

Step 3 used to watch %TEMP% for the `embedded.mobileprovision` Sideloadly was
believed to stage there. It does not: an mtime scan of every temp root across a
sign that SUCCEEDED found Sideloadly 0.60 wrote exactly three files
(account-appids.json, sessions.json, installations.db) and no profile anywhere -
it signs in memory and streams the IPA to the device (docs/ERRORS.md,
2026-08-16). No watcher can catch a file that is never written. iOS, though,
keeps every installed profile at /var/MobileDevice/ProvisioningProfiles/, and
misagent still hands them over on iOS 26.6, so that is where we read it from.
go-ios cannot do this (no misagent: `ios profile list` is MCInstall, AFC returns
error 8, and both `sign app` and `ui install` make --profile mandatory), which
is why pymobiledevice3 is a dependency.

Reading from the phone also means a profile that is still valid needs no
Sideloadly click at all - mid-week re-signs just work.

Everything here is local: openssl on the user's own key, a USB read from the
phone, and go-ios signing. No network calls, no Apple authentication.
"""

from __future__ import annotations

import asyncio
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


def _pro_provider():
    """The optional sidetap-pro package, or None (open-source flow, untouched).

    Discovery is one .env key: SIDETAP_PRO_PATH points at the private repo's
    src/. An import failure PRINTS to stderr and falls back - a pro user whose
    install broke should see why they are suddenly on 7-day signing - but never
    raises: the open core must work with no pro package anywhere near it.
    """
    pro_path = config.get("SIDETAP_PRO_PATH")
    if not pro_path:
        return None
    if pro_path not in sys.path:
        sys.path.insert(0, pro_path)
    try:
        import sidetap_pro
    except Exception as exc:  # any import-time breakage, not just ImportError
        print(f"sidetap-pro at {pro_path} failed to import: {exc}", file=sys.stderr)
        return None
    return sidetap_pro


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


def _expires_at(info: dict) -> datetime:
    """Profile expiry as an aware datetime; missing/odd values sort oldest."""
    exp = info.get("expires")
    if not isinstance(exp, datetime):
        return datetime.min.replace(tzinfo=timezone.utc)
    return exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)


def _device_profiles(udid: str | None) -> list[bytes]:
    """Every provisioning profile iOS is holding, as raw .mobileprovision bytes.

    This is the only copy that exists after a Sideloadly sign (module docstring),
    so it is the whole capture path - not a fallback.
    """
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.misagent import MisagentService
    except ImportError as exc:
        raise SigningError(
            "pymobiledevice3 is required to read the profile off the phone. "
            "Install it with: pip install -r requirements.txt"
        ) from exc

    async def pull():
        lockdown = await create_using_usbmux(serial=udid)
        return await MisagentService(lockdown=lockdown).copy_all()

    try:
        return [bytes(p.buf) for p in asyncio.run(pull())]
    except Exception as exc:  # any transport/pairing failure, named not swallowed
        raise SigningError(
            f"could not read provisioning profiles from the phone: {exc}"
        ) from exc


def capture_profile(
    udid: str | None,
    timeout: float = 600.0,
    dest: Path = PROFILE_PATH,
    progress: Progress = _noop,
    temp_roots: list[Path] | None = None,
) -> dict:
    """Get the WDA profile Apple minted, reading it back off the phone.

    Polls the device rather than this PC, because Sideloadly never writes the
    profile to disk (module docstring). The first read happens immediately, so
    a still-valid profile returns without the human touching Sideloadly at all.
    `temp_roots` forces the legacy filesystem scan (used by tests). Returns the
    parsed, validated profile info and copies bytes to dest.
    """
    if temp_roots is not None:
        return _capture_via_poll(udid, timeout, dest, progress, temp_roots)
    return _capture_from_device(udid, timeout, dest, progress)


def _capture_from_device(
    udid: str | None, timeout: float, dest: Path, progress: Progress
) -> dict:
    """Poll /var/MobileDevice/ProvisioningProfiles until a usable one shows up."""
    deadline = time.time() + timeout
    prompted = False
    while True:
        best: tuple[dict, bytes] | None = None
        for data in _device_profiles(udid):
            try:
                info = parse_profile(data)
            except SigningError:
                continue  # some other app's profile, or one we can't read
            if not profile_is_valid(info, udid)[0]:
                continue
            # Several WDA profiles can coexist; the newest expiry is this mint.
            if best is None or _expires_at(info) > _expires_at(best[0]):
                best = (info, data)
        if best is not None:
            info, data = best
            dest.parent.mkdir(exist_ok=True)
            dest.write_bytes(data)
            info["source"] = "phone: /var/MobileDevice/ProvisioningProfiles"
            progress("captured", f"read profile '{info['name']}' off the phone")
            return info
        left = int(deadline - time.time())
        if left <= 0:
            break
        if prompted:
            progress(
                "waiting",
                f"click Start in Sideloadly - {left // 60}m {left % 60:02d}s left",
            )
        else:
            progress("waiting", "armed - click Start in Sideloadly now")
            prompted = True
        time.sleep(3)
    raise SigningError(
        "no usable WebDriverAgent profile on the phone. Click Start in Sideloadly "
        "during the wait so Apple mints one, or pass a profile directly: "
        "phone-harness fix-input <path-to.mobileprovision>"
    )


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

    pro = _pro_provider()
    if pro is not None and profile is not None:
        # A supplied profile is a Sideloadly-era tool; signing it with the pro
        # cert would mix teams. Refuse rather than guess which identity wins.
        return {
            "ok": False,
            "step": "error",
            "message": "sidetap-pro manages its own profile; run fix-input "
            "without a profile argument (or remove SIDETAP_PRO_PATH from .env).",
        }

    try:
        udid = device.current_udid()
        # The new profile goes to a staging path first. PROFILE_PATH is what
        # the doctor's countdown reads, so it must describe what is ON THE
        # PHONE: committing before `ios sign app` succeeds turned a failed
        # re-sign into a fresh 7-day PASS over the old, dying signature.
        pending = PROFILE_PATH.with_name(PROFILE_PATH.name + ".pending")

        ident = None
        if pro is not None:
            try:
                # None while setup is incomplete (fall through to Sideloadly);
                # raises when configured-but-broken (expired identity), which
                # must NOT silently downgrade a pro user to 7-day signing.
                ident = pro.identity_for(udid, progress)
            except Exception as exc:
                return {"ok": False, "step": "error", "message": f"sidetap-pro: {exc}"}

        if ident is not None:
            p12_path, p12_password, data = ident
            info = parse_profile(data)
            ok, why = profile_is_valid(info, udid)
            if not ok:
                return {"ok": False, "step": "error", "message": f"bad profile: {why}"}
            pending.parent.mkdir(exist_ok=True)
            pending.write_bytes(data)
        else:
            progress("p12", "building signing identity from Sideloadly cert")
            build_p12()
            p12_path, p12_password = P12_PATH, P12_PASSWORD
            if profile is not None:
                data = profile.read_bytes()
                info = parse_profile(data)
                ok, why = profile_is_valid(info, udid)
                if not ok:
                    return {
                        "ok": False,
                        "step": "error",
                        "message": f"bad profile: {why}",
                    }
                pending.parent.mkdir(exist_ok=True)
                pending.write_bytes(data)
                progress("captured", f"using profile '{info['name']}'")
            else:
                # No "click Start in Sideloadly" prompt here: the capture reads
                # the phone first, and a still-valid profile means the human is
                # never needed. capture_profile raises that prompt itself, when
                # it is true.
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
            WDA_IPA, p12_path, pending, p12password=p12_password, bundleid=bundleid
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
