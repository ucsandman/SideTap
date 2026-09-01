"""Multi-device instance parameterization: SIDETAP_UDID pins go-ios calls,
SIDETAP_STATE_DIR / WDA_PORT / MJPEG_PORT re-home an instance, and an
environment with none of them set is byte-identical to the pre-multi-device
harness. No phone needed."""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import config, device  # noqa: E402

UDID = "00008120-0123456789ABCDEF"


# ---- pin_udid: the one chokepoint every go-ios invocation goes through ------


def test_unpinned_args_pass_through_untouched(monkeypatch):
    monkeypatch.setattr(config, "SIDETAP_UDID", None)
    args = ["apps", "--list"]
    assert device.pin_udid(args) == args


def test_pinned_appends_udid(monkeypatch):
    monkeypatch.setattr(config, "SIDETAP_UDID", UDID)
    assert device.pin_udid(["apps", "--list"]) == ["apps", "--list", f"--udid={UDID}"]


@pytest.mark.parametrize("cmd", ["list", "tunnel"])
def test_global_commands_stay_unpinned(monkeypatch, cmd):
    # `list` must see every phone (doctor, fleet roster); `tunnel` is the
    # all-devices daemon owned by whichever instance starts it first.
    monkeypatch.setattr(config, "SIDETAP_UDID", UDID)
    args = [cmd, "start"]
    assert device.pin_udid(args) == args


def test_run_and_spawn_route_through_pin(monkeypatch, tmp_path):
    """The pin must sit in the chokepoints, not in each caller."""
    monkeypatch.setattr(config, "SIDETAP_UDID", UDID)
    monkeypatch.setattr(device, "ios_path", lambda: "ios")
    seen = {}

    class FakeProc:
        pid = 4242
        stdout = ""
        stderr = ""

    def fake_run(cmd, **_kw):
        seen["run"] = cmd
        return FakeProc()

    def fake_popen(cmd, **_kw):
        seen["spawn"] = cmd
        return FakeProc()

    monkeypatch.setattr(device.subprocess, "run", fake_run)
    monkeypatch.setattr(device.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    device._run(["date"])
    device._spawn("runwda", ["runwda", "--bundleid=x"])
    assert seen["run"][-1] == f"--udid={UDID}"
    assert seen["spawn"][-1] == f"--udid={UDID}"


def test_current_udid_returns_the_pin_without_spawning(monkeypatch):
    monkeypatch.setattr(config, "SIDETAP_UDID", UDID)
    monkeypatch.setattr(
        device,
        "list_devices",
        lambda: pytest.fail("pinned udid must not spawn ios list"),
    )
    assert device.current_udid() == UDID


# ---- config: instance env vars land, defaults byte-identical ----------------


def _reload_config(monkeypatch, **env):
    for key in ("SIDETAP_UDID", "SIDETAP_STATE_DIR", "WDA_PORT", "MJPEG_PORT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(config)


@pytest.fixture
def restored_config(monkeypatch):
    yield monkeypatch
    # monkeypatch undoes the env at teardown; one more reload rebinds the
    # module constants to this machine's real environment for later tests.
    monkeypatch.undo()
    importlib.reload(config)


def test_instance_env_rehomes_state_and_ports(restored_config):
    cfg = _reload_config(
        restored_config,
        SIDETAP_UDID=UDID,
        SIDETAP_STATE_DIR=".state-b",
        WDA_PORT="8102",
        MJPEG_PORT="9102",
    )
    assert cfg.STATE_DIR == cfg.REPO_ROOT / ".state-b"
    assert cfg.SIDETAP_UDID == UDID
    assert cfg.WDA_PORT == 8102 and cfg.MJPEG_PORT == 9102
    assert cfg.WDA_URL.endswith(":8102")


def test_defaults_are_the_pre_multidevice_harness(restored_config):
    cfg = _reload_config(restored_config)
    assert cfg.STATE_DIR == cfg.REPO_ROOT / ".state"
    assert cfg.SIDETAP_UDID in (None, "")
    assert cfg.WDA_PORT == 8100 and cfg.MJPEG_PORT == 9100
    assert cfg.WDA_URL == "http://127.0.0.1:8100"
