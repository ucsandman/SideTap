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


def test_notify_expiry_silent_when_fresh(monkeypatch, tmp_path):
    expires = datetime.now(timezone.utc) + timedelta(days=6)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    toasts = []
    monkeypatch.setattr(admin, "_toast", lambda t, b: toasts.append((t, b)) or True)
    assert admin.notify_expiry() == 0
    assert not toasts


def test_notify_expiry_toasts_when_expiring(monkeypatch, tmp_path):
    expires = datetime.now(timezone.utc) + timedelta(hours=20)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    toasts = []
    monkeypatch.setattr(admin, "_toast", lambda t, b: toasts.append((t, b)) or True)
    assert admin.notify_expiry() == 0
    assert len(toasts) == 1
    assert "fix-input" in toasts[0][1]  # the toast tells you what to run


def test_notify_expiry_quiet_without_profile(monkeypatch, tmp_path):
    _use_profile(monkeypatch, tmp_path)  # file absent: not set up, don't nag
    toasts = []
    monkeypatch.setattr(admin, "_toast", lambda t, b: toasts.append((t, b)) or True)
    assert admin.notify_expiry() == 0
    assert not toasts


def test_reminder_command_uses_cmd_wrapper():
    # The scheduled task runs from System32 with a bare env; the .cmd wrapper
    # sets PYTHONPATH itself, so the task needs no environment of its own.
    cmd = admin._reminder_command()
    assert cmd.endswith('phone-harness.cmd" notify-expiry')
    assert cmd.startswith('"')


def test_reminder_check_never_fails_doctor(monkeypatch):
    # The reminder is opt-in: doctor stays all-green either way, the detail
    # carries the install hint instead.
    monkeypatch.setattr(admin, "_reminder_installed", lambda: False)
    ok, detail, _fix = admin._check_reminder()
    assert ok
    assert "--install" in detail
    monkeypatch.setattr(admin, "_reminder_installed", lambda: True)
    ok, detail, _fix = admin._check_reminder()
    assert ok
    assert "scheduled" in detail


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


# ---- WDA-installed check: deep sleep empties the app list (seen live
# 2026-08-10). "Empty list" must read as "phone asleep", never as "go
# re-sideload the app you installed yesterday".


def test_wda_check_passes_via_cache_while_asleep(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(admin.device.config, "WDA_BUNDLE_ID", "")
    monkeypatch.setattr(admin.device, "list_devices", lambda: ["00008150-X"])
    monkeypatch.setattr(admin.device, "list_apps", lambda: [])
    (tmp_path / "wda_bundle").write_text("com.x.cached.xctrunner", encoding="utf-8")
    ok, detail, _fix = admin._check_wda_installed()
    assert ok
    assert "com.x.cached.xctrunner" in detail


def test_wda_check_names_sleep_when_list_empty_and_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(admin.device.config, "WDA_BUNDLE_ID", "")
    monkeypatch.setattr(admin.device, "list_devices", lambda: ["00008150-X"])
    monkeypatch.setattr(admin.device, "list_apps", lambda: [])
    ok, detail, fix = admin._check_wda_installed()
    assert not ok
    assert "asleep" in detail.lower() or "locked" in detail.lower()
    assert "wake" in fix.lower()
    assert "sideload" not in fix.lower()


def test_wda_check_still_fails_when_really_uninstalled(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(admin.device.config, "WDA_BUNDLE_ID", "")
    monkeypatch.setattr(admin.device, "list_devices", lambda: ["00008150-X"])
    monkeypatch.setattr(
        admin.device,
        "list_apps",
        lambda: [{"bundle_id": "com.apple.mobilesafari", "name": ""}],
    )
    ok, detail, fix = admin._check_wda_installed()
    assert not ok
    assert "not found" in detail
    assert "Sideload" in fix
