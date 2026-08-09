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
