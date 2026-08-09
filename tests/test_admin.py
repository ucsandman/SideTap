"""Doctor signature-expiry countdown. No phone needed."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from test_signing import make_profile  # noqa: E402

from phone_harness import admin, signing  # noqa: E402


def _use_profile(monkeypatch, tmp_path, data=None):
    path = tmp_path / "profile.mobileprovision"
    if data is not None:
        path.write_bytes(data)
    monkeypatch.setattr(signing, "PROFILE_PATH", path)


def test_signature_check_passes_with_days_left(monkeypatch, tmp_path):
    expires = datetime.now(timezone.utc) + timedelta(days=5, hours=1)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    ok, detail, _fix = admin._check_signature()
    assert ok
    assert "5" in detail and "day" in detail


def test_signature_check_fails_when_expiring_soon(monkeypatch, tmp_path):
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    ok, detail, fix = admin._check_signature()
    assert not ok
    assert "expires in" in detail
    assert "fix-input" in fix


def test_signature_check_fails_when_expired(monkeypatch, tmp_path):
    expires = datetime.now(timezone.utc) - timedelta(hours=1)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    ok, detail, fix = admin._check_signature()
    assert not ok
    assert "expired" in detail
    assert "fix-input" in fix


def test_signature_check_skips_when_no_profile(monkeypatch, tmp_path):
    _use_profile(monkeypatch, tmp_path)  # file absent
    ok, detail, _fix = admin._check_signature()
    assert ok
    assert "no captured profile" in detail


def test_signature_check_in_doctor_checks():
    assert any(fn is admin._check_signature for _name, fn in admin.CHECKS)


def test_stop_check_flags_engaged(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.config, "STATE_DIR", tmp_path)
    (tmp_path / "STOP").touch()
    ok, detail, fix = admin._check_stop_engaged()
    assert not ok
    assert "ENGAGED" in detail
    assert "RESUME" in fix


def test_stop_check_passes_without_file(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.config, "STATE_DIR", tmp_path)
    ok, _detail, _fix = admin._check_stop_engaged()
    assert ok


def test_stop_check_runs_first():
    # Doctor's advice is "fix the first FAIL"; a forgotten kill switch must be
    # the first thing named, not buried under infra checks.
    assert admin.CHECKS[0][1] is admin._check_stop_engaged
