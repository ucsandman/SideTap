"""The app switcher, read over USB: device.running_apps / kill_app and the
helpers.open_apps join. The switcher SCREEN cannot be swiped open through WDA
(device.running_apps has the measurement), so this list is the feature."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phone_harness import device, helpers  # noqa: E402


class _Proc:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


_PS = [
    {
        "IsApplication": False,
        "Name": "nptocompaniond",
        "Pid": 1,
        "StartDate": "2026-09-04T12:33:31-04:00",
    },
    {
        "IsApplication": True,
        "Name": "Hinge",
        "Pid": 2251,
        "StartDate": "2026-09-04T12:59:09-04:00",
    },
    {
        "IsApplication": True,
        "Name": "Siri",
        "Pid": 40,
        "StartDate": "2026-09-04T12:40:00-04:00",
    },
    {
        "IsApplication": True,
        "Name": "MobileSMS",
        "Pid": 900,
        "StartDate": "2026-09-04T13:01:00-04:00",
    },
    {
        "IsApplication": True,
        "Name": "Photos",
        "Pid": 901,
        "StartDate": "2026-09-04T12:50:00-04:00",
    },
    {
        "IsApplication": True,
        "Name": "My Verizon",
        "Pid": 902,
        "StartDate": "2026-09-04T12:55:00-04:00",
    },
    {
        "IsApplication": True,
        "Name": "WebDriverAgentRunner-Runner",
        "Pid": 903,
        "StartDate": "2026-09-04T12:30:00-04:00",
    },
]
_INSTALLED = [
    {"bundle_id": "co.hinge.mobile.ios", "name": "Hinge 10.2.0"},
    {"bundle_id": "com.vzw.hss.myverizon", "name": "My Verizon 8.1"},
    {
        "bundle_id": "com.facebook.WebDriverAgentRunner.xctrunner.QQQ",
        "name": "WebDriverAgentRunner-Runner 1.0",
    },
]


def test_running_apps_keeps_applications_newest_first(monkeypatch):
    seen = []
    monkeypatch.setattr(
        device,
        "_run",
        lambda args, timeout=30.0: (seen.append(args), _Proc(json.dumps(_PS)))[1],
    )
    apps = device.running_apps()
    assert seen == [["ps"]]
    assert [a["name"] for a in apps] == [
        "MobileSMS",
        "Hinge",
        "My Verizon",
        "Photos",
        "Siri",
        "WebDriverAgentRunner-Runner",
    ]
    assert apps[0] == {
        "name": "MobileSMS",
        "pid": 900,
        "started": "2026-09-04T13:01:00-04:00",
    }


def test_running_apps_survives_empty_and_non_json_output(monkeypatch):
    monkeypatch.setattr(
        device,
        "_run",
        lambda args, timeout=30.0: _Proc('{"level":"INFO","msg":"no udid"}\n'),
    )
    assert device.running_apps() == []


def test_kill_app_is_ios_kill_by_bundle_id(monkeypatch):
    seen = []
    monkeypatch.setattr(
        device, "_run", lambda args, timeout=30.0: (seen.append(args), _Proc("", 0))[1]
    )
    assert device.kill_app("com.apple.Preferences") is True
    assert seen == [["kill", "com.apple.Preferences"]]
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _Proc("", 1))
    assert device.kill_app("com.apple.Preferences") is False


def test_open_apps_joins_processes_to_installed_and_system_apps(monkeypatch):
    monkeypatch.setattr(device, "running_apps", _running)
    monkeypatch.setattr(device, "list_apps", lambda: _INSTALLED)
    apps = helpers.open_apps()
    # Display names carry no version, system apps resolve through BUNDLE_IDS
    # (MobileSMS -> Messages, Photos -> mobileslideshow), Siri and the WDA
    # runner are not switcher entries, and the order is the launch order.
    assert apps == [
        {"name": "Messages", "bundle_id": "com.apple.MobileSMS", "pid": 900},
        {"name": "Hinge", "bundle_id": "co.hinge.mobile.ios", "pid": 2251},
        {"name": "My Verizon", "bundle_id": "com.vzw.hss.myverizon", "pid": 902},
        {"name": "Photos", "bundle_id": "com.apple.mobileslideshow", "pid": 901},
    ]


def _running():
    apps = [
        {"name": p["Name"], "pid": p["Pid"], "started": p["StartDate"]}
        for p in _PS
        if p["IsApplication"]
    ]
    apps.sort(key=lambda a: a["started"], reverse=True)
    return apps


def test_close_app_resolves_names_exactly_like_open_app(monkeypatch):
    killed = []
    monkeypatch.setattr(device, "kill_app", lambda bid: (killed.append(bid), True)[1])
    monkeypatch.setattr(device, "list_apps", lambda: _INSTALLED)
    assert helpers.close_app("Settings") is True
    assert helpers.close_app("co.hinge.mobile.ios") is True
    assert helpers.close_app("Hinge 10.2.0") is True
    assert killed == [
        "com.apple.Preferences",
        "co.hinge.mobile.ios",
        "co.hinge.mobile.ios",
    ]
    import pytest

    with pytest.raises(helpers.WDAError, match="Unknown app"):
        helpers.close_app("Hinj")
