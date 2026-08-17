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


def test_signature_check_counts_down_without_failing(monkeypatch, tmp_path):
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    ok, detail, fix = admin._check_signature()
    # Renewing early is IMPOSSIBLE on a free ID (proven 2026-08-14: a fresh
    # Sideloadly sign left Apple's App ID TTL at the original expiry), and
    # input still works the whole time. So the countdown must NOT fail: a FAIL
    # here reddened the header, counted as a failing check, auto-opened the
    # overlay and raised the amber banner for up to 48h over a system that was
    # working, while its own fix line admitted no click could help.
    assert ok, "a working signature must not report FAIL just because it is dated"
    assert "expires in" in detail, "the countdown must stay visible"
    # ...and it must never prescribe fix-input mid-week: that chase broke a
    # working install twice in one night.
    assert "fix-input" not in fix
    assert fix == ""


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
    assert "cannot be renewed early" in toasts[0][1]  # heads-up, not a chase


def test_notify_expiry_toast_prescribes_fix_only_after_expiry(monkeypatch, tmp_path):
    expires = datetime.now(timezone.utc) - timedelta(hours=2)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    toasts = []
    monkeypatch.setattr(admin, "_toast", lambda t, b: toasts.append((t, b)) or True)
    assert admin.notify_expiry() == 0
    assert len(toasts) == 1
    assert "EXPIRED" in toasts[0][0]
    assert "fix-input" in toasts[0][1]  # now the action is real: fresh 7 days


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


def test_ddi_check_in_doctor_checks():
    # iOS updates silently unmount the DDI (bit live 2026-08-10); doctor must
    # name it before the WDA checks so "fix the first FAIL" points at it.
    names = [name for name, _fn in admin.CHECKS]
    assert "developer image (DDI)" in names
    assert names.index("developer image (DDI)") < names.index("WDA responding (input)")


def test_ddi_check_fails_when_unmounted(monkeypatch):
    monkeypatch.setattr(admin.device, "ddi_mounted", lambda: False)
    ok, detail, fix = admin._check_ddi()
    assert not ok
    assert "not mounted" in detail
    assert "phone-harness up" in fix


def test_ddi_check_passes_when_mounted(monkeypatch):
    monkeypatch.setattr(admin.device, "ddi_mounted", lambda: True)
    ok, detail, _fix = admin._check_ddi()
    assert ok
    assert "mounted" in detail


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


def test_bringing_up_tracks_an_in_flight_up(monkeypatch):
    # launch.py brings the link up in a background thread while the browser is
    # already open. The viewer asks this to tell "starting" apart from "broken"
    # instead of freezing a mid-bring-up red result on screen.
    seen = []
    assert admin.bringing_up() is False
    monkeypatch.setattr(
        admin,
        "_up",
        lambda wait_seconds: seen.append(admin.bringing_up()) or 0,  # noqa: vulture
    )
    assert admin.up(wait_seconds=0) == 0
    assert seen == [True]  # true for the whole run...
    assert admin.bringing_up() is False  # ...and false again after it


# ---- boundary pins (adversarial review 2026-08-13): the 48h warn threshold
# and the expired/not-expired line had no tests anywhere near them, so a
# flipped comparison or a mistyped constant would have shipped silently. A
# real clock cannot hit an instant exactly, so each boundary is pinned from
# both sides with a 5s margin.


def test_signature_check_switches_to_the_countdown_just_under_48h(
    monkeypatch, tmp_path
):
    # The 48h boundary still changes the WORDING (days-left -> hours-left), it
    # just no longer changes ok. Nothing is broken on either side of it.
    expires = datetime.now(timezone.utc) + timedelta(hours=48)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    ok, detail, _fix = admin._check_signature()  # runs ms later: left < 48h
    assert ok
    assert "expires in 47h" in detail


def test_signature_check_passes_just_over_48h(monkeypatch, tmp_path):
    expires = datetime.now(timezone.utc) + timedelta(hours=48, seconds=5)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    ok, detail, _fix = admin._check_signature()
    assert ok
    assert "good for" in detail


def test_signature_check_expired_at_exactly_now(monkeypatch, tmp_path):
    expires = datetime.now(timezone.utc)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    ok, detail, _fix = admin._check_signature()  # runs ms later: left <= 0
    assert not ok
    assert "expired" in detail


def test_signature_check_seconds_left_is_countdown_not_expired(monkeypatch, tmp_path):
    # Five seconds left is still WORKING, so it must read as the countdown and
    # not tip into the expired branch — which is the one that fails loudly and
    # tells the human to re-sign.
    expires = datetime.now(timezone.utc) + timedelta(seconds=5)
    _use_profile(monkeypatch, tmp_path, make_profile(expires=expires))
    ok, detail, _fix = admin._check_signature()
    assert ok
    assert "expires in 0h" in detail
    assert "expired" not in detail


# ---- doctor_results() must memoize its subprocess-backed checks for the
# DURATION OF ONE PASS ONLY. `ios list` is called by both _check_device and
# _check_wda_installed; `ios apps --list` is called inside detect_wda_bundle
# and again directly. At the 207ms spawn floor that is real, wasted cost —
# but a stale check is a lying check, so a second, separate doctor_results()
# call must always re-spawn.


class _FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_doctor_results_memoizes_ios_list_within_one_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(admin.device.config, "WDA_BUNDLE_ID", "")
    calls = {"list": 0, "apps": 0}

    # timeout is unused but must keep this name: device._run is called with it
    # as a keyword, so a rename would break the stand-in.
    def fake_run(args, timeout=30.0):  # noqa: vulture
        if args[0] == "list":
            calls["list"] += 1
            return _FakeProc('{"deviceList":["X"]}')
        if args[:2] == ["apps", "--list"]:
            calls["apps"] += 1
            return _FakeProc(
                '{"CFBundleIdentifier":"com.x.WebDriverAgentRunner.xctrunner",'
                '"CFBundleName":"WDA"}'
            )
        return _FakeProc("")

    monkeypatch.setattr(admin.device, "_run", fake_run)
    # CHECKS entries are bound to function objects at import time, so
    # monkeypatching admin._check_x doesn't affect them — isolate the two
    # subprocess-backed checks under test rather than faking every other
    # check's unrelated external dependency (tunnel, DDI, screenshot, ...).
    monkeypatch.setattr(
        admin,
        "CHECKS",
        [
            (n, fn)
            for n, fn in admin.CHECKS
            if fn in (admin._check_device, admin._check_wda_installed)
        ],
    )

    results = admin.doctor_results()
    assert all(r["ok"] for r in results)
    # _check_device calls list_devices() once; _check_wda_installed calls it
    # again as its own connected-phone guard — both inside ONE pass.
    assert calls["list"] == 1
    # detect_wda_bundle() finds WDA on the first apps call, so
    # _check_wda_installed's own extra list_apps() call is never reached —
    # still proves the memo: without it this would still read 1, so pin the
    # cross-run behaviour below to actually catch a missing memo.
    assert calls["apps"] == 1

    # A SECOND, separate pass must re-spawn — never read stale results.
    admin.doctor_results()
    assert calls["list"] == 2
    assert calls["apps"] == 2


# ---- _wait_for_wda(): WDA's /status probe is 3.8ms median (measured on
# device), so the old 2s sleep between checks wasted up to ~2s per iteration.
# Pinned at 0.25s so a refactor can't quietly reintroduce the 2s wait.


class _FakeClient:
    def __init__(self, up_on_call):
        self.up_on_call = up_on_call
        self.calls = 0

    def is_up(self):
        self.calls += 1
        return self.calls >= self.up_on_call


# ---- a WEDGED link is repaired by pressing Home, never by a restart. The
# stuck runner is still on the phone, so a restart dies with XCTest error 103
# and the old tail blamed the signature - one reporter re-signed for nothing
# (issue #2, 2026-08-17).


class _StateClient:
    """WDAClient stand-in: wedged until the Home Screen comes to the front."""

    def __init__(self, state, recovers=True):
        self.state = state
        self.recovers = recovers

    def link_state(self):
        return self.state

    def is_up(self):
        return self.state == "up"


def test_up_unwedges_by_pressing_home_and_never_restarts(monkeypatch, capsys):
    client = _StateClient("wedged")
    monkeypatch.setattr(admin, "WDAClient", lambda *a, **k: client)
    pressed = []

    def press_home():
        pressed.append(True)
        client.state = "up"  # what the device does ~20s later, measured
        return True

    monkeypatch.setattr(admin.device, "foreground_springboard", press_home)
    started = []
    monkeypatch.setattr(admin.device, "start_wda", lambda b: started.append(b))

    assert admin._up(wait_seconds=5) == 0
    assert pressed == [True]
    assert started == []  # the restart is the WRONG repair here
    out = capsys.readouterr().out
    assert "wedged" in out.lower()


def test_up_on_a_wedge_blames_the_runner_not_the_signature(monkeypatch, capsys):
    # foreground_springboard fails, so we fall through to a restart that fails
    # too. The tail must NOT send the human to Sideloadly.
    client = _StateClient("wedged")
    monkeypatch.setattr(admin, "WDAClient", lambda *a, **k: client)
    monkeypatch.setattr(admin.device, "foreground_springboard", lambda: False)
    monkeypatch.setattr(admin, "_check_go_ios", lambda: (True, "go-ios", ""))
    monkeypatch.setattr(admin, "_check_device", lambda: (True, "iPhone", ""))
    monkeypatch.setattr(admin.device, "tunnel_running", lambda: True)
    monkeypatch.setattr(admin.device, "ddi_mounted", lambda: True)
    monkeypatch.setattr(admin.device, "detect_wda_bundle", lambda: "com.x.wda")
    monkeypatch.setattr(admin.device, "start_wda", lambda b: None)
    monkeypatch.setattr(admin.device, "start_forwards", lambda: None)
    monkeypatch.setattr(admin.device, "log_tail", lambda *a, **k: "Error code: 103")
    monkeypatch.setattr(admin, "_wait_for_wda", lambda *a, **k: False)

    assert admin._up(wait_seconds=1) == 1
    out = capsys.readouterr().out
    assert "stuck runner" in out
    assert "Sideloadly" not in out


def test_doctor_reports_a_wedge_as_its_own_state(monkeypatch):
    monkeypatch.setattr(admin, "WDAClient", lambda *a, **k: _StateClient("wedged"))
    ok, detail, fix = admin._check_wda_responding()
    assert ok is False
    assert "wedged" in detail
    assert "Home Screen" in fix  # the repair, not "replug / re-sign"
    assert "Sideloadly" not in fix


def test_wait_for_wda_polls_at_quarter_second_interval(monkeypatch):
    sleeps = []
    monkeypatch.setattr(admin.time, "sleep", lambda s: sleeps.append(s))
    clock = [0.0]
    monkeypatch.setattr(admin.time, "time", lambda: clock[0])
    client = _FakeClient(up_on_call=3)
    deadline = 100.0  # far away; is_up() succeeding ends the loop first
    assert admin._wait_for_wda(client, deadline) is True
    assert sleeps == [0.25, 0.25]  # slept once per FAILED check, not 2s


def test_wait_for_wda_gives_up_at_the_deadline(monkeypatch):
    sleeps = []
    clock = [0.0]

    def fake_sleep(s):
        sleeps.append(s)
        clock[0] += s

    monkeypatch.setattr(admin.time, "sleep", fake_sleep)
    monkeypatch.setattr(admin.time, "time", lambda: clock[0])
    client = _FakeClient(up_on_call=10_000)  # never succeeds
    deadline = 1.0
    assert admin._wait_for_wda(client, deadline) is False
    assert clock[0] >= deadline
    assert clock[0] < deadline + 0.25  # stopped promptly, not overshooting far
