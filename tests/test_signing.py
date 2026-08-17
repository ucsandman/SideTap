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
    monkeypatch.setattr(signing, "build_p12", lambda *_a, **_k: None)
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


def _no_sleep(_seconds):
    """Keep the poll loop's real 3s pacing out of the test suite."""


def _fake_device(monkeypatch, *reads):
    """Stand in for the phone: each call to _device_profiles returns one read."""
    calls = iter(reads)
    monkeypatch.setattr(signing, "_device_profiles", lambda _udid: next(calls, []))


def test_device_capture_picks_the_freshest_wda_profile(monkeypatch, tmp_path):
    """The phone holds every profile it was ever given - 13 of them on the real
    device, six of them valid WDA ones with different expiries. Expired and
    other apps' must be filtered out, and among the survivors the newest expiry
    has to win: signing with an older-but-still-valid mint would hand back input
    that dies days early.
    """
    expired = make_profile(
        expires=datetime.now(timezone.utc) - timedelta(days=1), name="last week"
    )
    other = make_profile(app_id="ABCDE12345.com.other.app", name="not WDA")
    stale = make_profile(  # valid, so only the expiry comparison rejects it
        expires=datetime.now(timezone.utc) + timedelta(days=2), name="older mint"
    )
    fresh = make_profile(
        expires=datetime.now(timezone.utc) + timedelta(days=7), name="this week"
    )
    _fake_device(monkeypatch, [expired, other, stale, fresh])
    monkeypatch.setattr(signing.time, "sleep", _no_sleep)  # must not wait at all
    dest = tmp_path / "dest.mobileprovision"
    info = signing.capture_profile(UDID, timeout=5, dest=dest)
    assert info["name"] == "this week"
    assert dest.read_bytes() == fresh


def test_device_capture_waits_for_sideloadly_then_reads(monkeypatch, tmp_path):
    """Nothing usable yet means Apple has not minted one, so the human is told
    to click Start - and the next read has to be picked up."""
    fresh = make_profile()
    _fake_device(monkeypatch, [], [fresh])
    monkeypatch.setattr(signing.time, "sleep", _no_sleep)
    seen = []
    info = signing.capture_profile(
        UDID,
        timeout=30,
        dest=tmp_path / "dest.mobileprovision",
        progress=lambda s, m: seen.append((s, m)),
    )
    assert info["team_id"] == "ABCDE12345"
    assert ("waiting", "armed - click Start in Sideloadly now") in seen
    assert seen[-1][0] == "captured"


def test_device_capture_times_out_naming_the_next_move(monkeypatch, tmp_path):
    """The 2026-08-16 failure: no profile ever arrives. That must name what to
    do, not sit silent - and the old watcher wording is gone with the watcher."""
    _fake_device(monkeypatch)  # every read comes back empty
    monkeypatch.setattr(signing.time, "sleep", _no_sleep)
    with pytest.raises(signing.SigningError, match="Click Start in Sideloadly"):
        signing.capture_profile(
            UDID, timeout=0.01, dest=tmp_path / "dest.mobileprovision"
        )


def test_device_capture_refuses_another_devices_profile(monkeypatch, tmp_path):
    """A profile for someone else's phone parses fine and would sign fine - and
    then iOS refuses to launch it. It must never be picked."""
    _fake_device(monkeypatch, [make_profile(devices=("00008120-OTHERDEVICE",))])
    monkeypatch.setattr(signing.time, "sleep", _no_sleep)
    with pytest.raises(signing.SigningError):
        signing.capture_profile(
            UDID, timeout=0.01, dest=tmp_path / "dest.mobileprovision"
        )
