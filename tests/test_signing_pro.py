"""The sidetap-pro seam in fix_input: absent = byte-identical open source flow,
present = pro identity signs and both Sideloadly steps are skipped, broken =
loud error, never a silent downgrade. No pro package needed - a fake module in
sys.modules plays the provider."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import admin, device, signing  # noqa: E402
from test_signing import UDID, make_profile  # noqa: E402


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """fix_input with everything device-touching stubbed; records sign_app."""
    calls = {}
    ipa = tmp_path / "WebDriverAgent.ipa"
    ipa.write_bytes(b"ipa")
    monkeypatch.setattr(signing, "WDA_IPA", ipa)
    monkeypatch.setattr(signing, "PROFILE_PATH", tmp_path / "profile.mobileprovision")
    monkeypatch.setattr(device, "current_udid", lambda: UDID)

    def fake_sign(ipa, p12, profile, p12password="", bundleid=None):
        calls["sign"] = {"p12": p12, "password": p12password, "bundleid": bundleid}
        return "ok"

    monkeypatch.setattr(device, "sign_app", fake_sign)
    monkeypatch.setattr(admin, "up", lambda: 0)
    monkeypatch.setattr(
        signing, "build_p12", lambda *_a, **_k: calls.setdefault("build_p12", True)
    )
    monkeypatch.setattr(
        signing,
        "capture_profile",
        lambda *_a, **_k: calls.setdefault("capture", True) and {},
    )
    monkeypatch.delenv("SIDETAP_PRO_PATH", raising=False)
    sys.modules.pop("sidetap_pro", None)
    yield calls
    sys.modules.pop("sidetap_pro", None)


def _arm_pro(monkeypatch, tmp_path, identity_for):
    fake = types.ModuleType("sidetap_pro")
    fake.identity_for = identity_for
    sys.modules["sidetap_pro"] = fake
    monkeypatch.setenv("SIDETAP_PRO_PATH", str(tmp_path / "pro-src"))


def test_pro_identity_signs_and_skips_sideloadly(harness, tmp_path, monkeypatch):
    p12 = tmp_path / "identity.p12"
    p12.write_bytes(b"p12")
    _arm_pro(
        monkeypatch, tmp_path, lambda udid, progress: (p12, "s3cret", make_profile())
    )
    out = signing.fix_input()
    assert out["ok"], out
    assert harness["sign"]["p12"] == p12
    assert harness["sign"]["password"] == "s3cret"
    assert "build_p12" not in harness and "capture" not in harness
    assert signing.PROFILE_PATH.exists()  # staged profile committed after sign


def test_no_pro_runs_the_sideloadly_flow(harness, monkeypatch):
    def fake_capture(udid, timeout, dest, progress):
        dest.write_bytes(make_profile())
        return signing.parse_profile(make_profile())

    monkeypatch.setattr(signing, "capture_profile", fake_capture)
    out = signing.fix_input()
    assert out["ok"], out
    assert harness["build_p12"] is True
    assert harness["sign"]["p12"] == signing.P12_PATH


def test_pro_broken_is_a_loud_error_not_a_downgrade(harness, tmp_path, monkeypatch):
    def broken(udid, progress):
        raise RuntimeError("developer certificate expired 2026-08-30")

    _arm_pro(monkeypatch, tmp_path, broken)
    out = signing.fix_input()
    assert not out["ok"]
    assert "sidetap-pro: developer certificate expired" in out["message"]
    assert "sign" not in harness and "build_p12" not in harness


def test_pro_mid_setup_falls_back_to_sideloadly(harness, tmp_path, monkeypatch):
    _arm_pro(monkeypatch, tmp_path, lambda udid, progress: None)

    def fake_capture(udid, timeout, dest, progress):
        dest.write_bytes(make_profile())
        return signing.parse_profile(make_profile())

    monkeypatch.setattr(signing, "capture_profile", fake_capture)
    out = signing.fix_input()
    assert out["ok"], out
    assert harness["build_p12"] is True


def test_pro_refuses_an_explicit_profile_argument(harness, tmp_path, monkeypatch):
    _arm_pro(monkeypatch, tmp_path, lambda udid, progress: (tmp_path / "x", "p", b""))
    supplied = tmp_path / "some.mobileprovision"
    supplied.write_bytes(make_profile())
    out = signing.fix_input(profile=supplied)
    assert not out["ok"]
    assert "sidetap-pro manages its own profile" in out["message"]
    assert "sign" not in harness


def test_import_failure_prints_and_falls_back(harness, tmp_path, monkeypatch, capsys):
    broken_src = tmp_path / "pro-src"
    (broken_src / "sidetap_pro").mkdir(parents=True)
    (broken_src / "sidetap_pro" / "__init__.py").write_text(
        "raise RuntimeError('boom')"
    )
    monkeypatch.setenv("SIDETAP_PRO_PATH", str(broken_src))
    assert signing._pro_provider() is None
    assert "failed to import" in capsys.readouterr().err
