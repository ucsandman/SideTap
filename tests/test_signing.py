"""Signing helpers: profile parsing/validation and temp-folder capture.

No phone or Sideloadly needed - a synthetic .mobileprovision exercises the
same slice-the-plist-out-of-the-CMS-blob path a real one hits.
"""

import plistlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import signing  # noqa: E402

UDID = "00008120-0123456789ABCDEF"
WDA_APP_ID = "ABCDE12345.com.facebook.WebDriverAgentRunner.xctrunner"


def make_profile(
    app_id=WDA_APP_ID,
    expires=None,
    devices=(UDID,),
    name="WDA dev profile",
):
    """A CMS-ish blob wrapping a real plist, like a .mobileprovision."""
    expires = expires or (datetime.now(timezone.utc) + timedelta(days=6))
    plist = plistlib.dumps(
        {
            "Name": name,
            "TeamIdentifier": ["ABCDE12345"],
            "ExpirationDate": expires,
            "ProvisionedDevices": list(devices),
            "Entitlements": {"application-identifier": app_id},
        }
    )
    return b"0\x82\x0agarbage-pkcs7-header" + plist + b"\x00\x00signature-trailer"


def test_parse_profile_extracts_fields():
    info = signing.parse_profile(make_profile())
    assert info["app_id"] == WDA_APP_ID
    assert info["team_id"] == "ABCDE12345"
    assert UDID in info["udids"]
    assert isinstance(info["expires"], datetime)


def test_parse_profile_derives_bundle_id_dropping_team_prefix():
    # Sideloadly makes the free-account id unique by appending the team id.
    app_id = "ABCDE12345.com.facebook.WebDriverAgentRunner.xctrunner.ABCDE12345"
    info = signing.parse_profile(make_profile(app_id=app_id))
    assert info["bundle_id"] == "com.facebook.WebDriverAgentRunner.xctrunner.ABCDE12345"


def test_parse_profile_rejects_non_profile():
    with pytest.raises(signing.SigningError):
        signing.parse_profile(b"not a provisioning profile at all")


def test_valid_wda_profile_passes():
    info = signing.parse_profile(make_profile())
    ok, why = signing.profile_is_valid(info, UDID)
    assert ok, why


def test_non_wda_app_id_rejected():
    info = signing.parse_profile(make_profile(app_id="ABCDE12345.com.some.other.app"))
    ok, why = signing.profile_is_valid(info, UDID)
    assert not ok and "WebDriverAgent" in why


def test_expired_profile_rejected():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    info = signing.parse_profile(make_profile(expires=past))
    ok, why = signing.profile_is_valid(info, UDID)
    assert not ok and "expired" in why


def test_wrong_device_rejected():
    info = signing.parse_profile(make_profile(devices=("00000000-DIFFERENT",)))
    ok, why = signing.profile_is_valid(info, UDID)
    assert not ok and "different device" in why


def test_capture_profile_finds_dropped_file(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "embedded.mobileprovision").write_bytes(make_profile())
    info = signing.capture_profile(
        UDID, timeout=5, dest=tmp_path / "out.mobileprovision", temp_roots=[tmp_path]
    )
    assert info["team_id"] == "ABCDE12345"
    assert (tmp_path / "out.mobileprovision").exists()


def test_capture_profile_extracts_from_ipa(tmp_path):
    import zipfile

    ipa = tmp_path / "WebDriverAgent-signed.ipa"
    with zipfile.ZipFile(ipa, "w") as z:
        z.writestr("Payload/WDA.app/embedded.mobileprovision", make_profile())
    info = signing.capture_profile(
        UDID, timeout=5, dest=tmp_path / "out.mobileprovision", temp_roots=[tmp_path]
    )
    assert info["team_id"] == "ABCDE12345"


def test_capture_profile_times_out_when_absent(tmp_path):
    with pytest.raises(signing.SigningError):
        signing.capture_profile(
            UDID,
            timeout=1,
            dest=tmp_path / "out.mobileprovision",
            temp_roots=[tmp_path],
        )


def _wire_fix_input(monkeypatch, tmp_path, sign=None, up=lambda: 0):
    """Stub everything fix_input touches except the profile bookkeeping."""
    from phone_harness import admin, device

    ipa = tmp_path / "WebDriverAgent.ipa"
    ipa.write_bytes(b"ipa")
    monkeypatch.setattr(signing, "WDA_IPA", ipa)
    monkeypatch.setattr(signing, "PROFILE_PATH", tmp_path / "profile.mobileprovision")
    monkeypatch.setattr(signing, "build_p12", lambda *a, **k: None)
    monkeypatch.setattr(device, "current_udid", lambda: UDID)
    signed = []

    def record_sign(_ipa, _p12, profile, **_kwargs):
        signed.append(Path(profile).read_bytes())

    monkeypatch.setattr(device, "sign_app", sign or record_sign)
    monkeypatch.setattr(admin, "up", up)
    return signed


def test_fix_input_keeps_old_profile_when_signing_fails(monkeypatch, tmp_path):
    """The countdown must describe what is ON THE PHONE. Writing the new
    profile to PROFILE_PATH before `ios sign app` succeeds made a failed
    re-sign flip the doctor to a fresh 7-day PASS while the phone kept the
    old, dying signature (adversarial review 2026-08-13)."""
    from phone_harness import device

    def explode(*_a, **_k):
        raise device.DeviceError("phone locked mid-install")

    _wire_fix_input(monkeypatch, tmp_path, sign=explode)
    old = make_profile(name="old, on the phone")
    signing.PROFILE_PATH.write_bytes(old)
    new_file = tmp_path / "fresh.mobileprovision"
    new_file.write_bytes(make_profile(name="fresh, never installed"))

    result = signing.fix_input(profile=new_file)
    assert result["ok"] is False
    assert signing.PROFILE_PATH.read_bytes() == old


def test_fix_input_commits_profile_only_after_signing(monkeypatch, tmp_path):
    """Happy path: sign_app sees the new profile, and PROFILE_PATH still holds
    the old one at that moment — the commit happens after signing succeeds."""
    old = make_profile(name="old")
    new = make_profile(name="new")
    seen_at_sign_time = []

    def sign(_ipa, _p12, profile, **_kwargs):
        seen_at_sign_time.append(signing.PROFILE_PATH.read_bytes())
        assert Path(profile).read_bytes() == new

    _wire_fix_input(monkeypatch, tmp_path, sign=sign)
    signing.PROFILE_PATH.write_bytes(old)
    new_file = tmp_path / "fresh.mobileprovision"
    new_file.write_bytes(new)

    result = signing.fix_input(profile=new_file)
    assert result["ok"] is True
    assert seen_at_sign_time == [old]
    assert signing.PROFILE_PATH.read_bytes() == new


def test_fix_input_reports_a_hung_sign_instead_of_raising(monkeypatch, tmp_path):
    """`ios sign app` hanging past its subprocess timeout raised TimeoutExpired
    straight through fix_input, which killed the viewer's worker thread and
    left the Fix input wizard at 'running' forever (adversarial review
    2026-08-13). It must come back as a normal ok:False result."""
    import subprocess

    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="ios sign app", timeout=300)

    _wire_fix_input(monkeypatch, tmp_path, sign=hang)
    new_file = tmp_path / "fresh.mobileprovision"
    new_file.write_bytes(make_profile())

    result = signing.fix_input(profile=new_file)
    assert result["ok"] is False
    assert "300" in result["message"] or "timed out" in result["message"]


def test_capture_profile_ignores_wrong_profile(tmp_path):
    (tmp_path / "other.mobileprovision").write_bytes(
        make_profile(app_id="ABCDE12345.com.other.app")
    )
    with pytest.raises(signing.SigningError):
        signing.capture_profile(
            UDID,
            timeout=1,
            dest=tmp_path / "out.mobileprovision",
            temp_roots=[tmp_path],
        )


def _fake_watcher(monkeypatch, out_path, write_bytes=None, rc=0):
    """Stand in for the watch_profile.ps1 subprocess (the real Windows path,
    previously untested). Optionally 'captures' write_bytes into out_path."""

    class FakeProc:
        def __init__(self, *_a, **_k):
            if write_bytes is not None:
                out_path.write_bytes(write_bytes)
            self.stdout = iter(["READY\n"])
            self.returncode = rc

        def wait(self):
            return self.returncode

    monkeypatch.setattr(signing.subprocess, "Popen", FakeProc)


def test_watcher_capture_accepts_a_fresh_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(signing.config, "STATE_DIR", tmp_path)
    out = tmp_path / "captured.mobileprovision"
    fresh = make_profile()
    _fake_watcher(monkeypatch, out, write_bytes=fresh)
    dest = tmp_path / "dest.mobileprovision"
    info = signing._capture_via_watcher(UDID, 5, dest, lambda *_a: None)
    assert info["team_id"] == "ABCDE12345"
    assert dest.read_bytes() == fresh


def test_watcher_capture_never_accepts_a_stale_file(monkeypatch, tmp_path):
    """A leftover captured.mobileprovision from an earlier run must not be
    accepted as this run's mint. The PS script's own delete runs under
    SilentlyContinue, so Python clears the file itself before arming
    (adversarial review 2026-08-13)."""
    monkeypatch.setattr(signing.config, "STATE_DIR", tmp_path)
    out = tmp_path / "captured.mobileprovision"
    out.write_bytes(make_profile(name="stale, from last week"))
    _fake_watcher(monkeypatch, out, write_bytes=None, rc=0)  # captures nothing
    with pytest.raises(signing.SigningError, match="timed out"):
        signing._capture_via_watcher(UDID, 5, tmp_path / "dest", lambda *_a: None)
    assert not out.exists()  # Python deleted it; the PS net was not needed


def test_watcher_capture_fails_loud_when_stale_file_is_stuck(monkeypatch, tmp_path):
    monkeypatch.setattr(signing.config, "STATE_DIR", tmp_path)
    (tmp_path / "captured.mobileprovision").write_bytes(b"stuck")

    def refuse(self, missing_ok=False):  # noqa: vulture  (must match Path.unlink's signature)
        raise OSError("locked by another process")

    monkeypatch.setattr(Path, "unlink", refuse)
    with pytest.raises(signing.SigningError, match="cannot clear stale capture"):
        signing._capture_via_watcher(UDID, 5, tmp_path / "dest", lambda *_a: None)
