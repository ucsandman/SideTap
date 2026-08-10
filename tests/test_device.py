"""ios_path fallback for truncated-PATH launches (shortcut/Startup). No phone needed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import device  # noqa: E402


def test_ios_path_prefers_path_lookup(monkeypatch):
    monkeypatch.setattr(device.shutil, "which", lambda _: r"C:\somewhere\ios.exe")
    assert device.ios_path() == r"C:\somewhere\ios.exe"


def test_ios_path_falls_back_to_npm_global_dir(monkeypatch, tmp_path):
    # Windows truncates a registry PATH past ~4095 chars when it builds the
    # logon environment, so shortcut/Startup launches can miss the npm dir
    # even though terminals see it (bit the Startup shortcut live 2026-08-10).
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    exe = tmp_path / "npm" / "ios.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert device.ios_path() == str(exe)


def test_ios_path_none_when_missing_everywhere(monkeypatch, tmp_path):
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert device.ios_path() is None


# ---- WDA bundle cache: deep sleep empties `ios apps --list` (seen live
# 2026-08-10) while the app is still installed. The cache keeps up() working
# so the link can heal the moment the phone wakes.


def test_wda_bundle_cached_on_live_detection(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    monkeypatch.setattr(
        device,
        "list_apps",
        lambda: [{"bundle_id": "com.x.WebDriverAgentRunner.xctrunner", "name": "WDA"}],
    )
    assert device.detect_wda_bundle() == "com.x.WebDriverAgentRunner.xctrunner"
    cached = (tmp_path / "wda_bundle").read_text(encoding="utf-8").strip()
    assert cached == "com.x.WebDriverAgentRunner.xctrunner"


def test_wda_bundle_falls_back_to_cache_when_list_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    (tmp_path / "wda_bundle").write_text("com.x.cached.xctrunner", encoding="utf-8")
    monkeypatch.setattr(device, "list_apps", lambda: [])
    assert device.detect_wda_bundle() == "com.x.cached.xctrunner"


def test_wda_bundle_none_when_empty_list_and_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    monkeypatch.setattr(device, "list_apps", lambda: [])
    assert device.detect_wda_bundle() is None


# ---- Developer Disk Image: an iOS update silently unmounts it (bit live
# 2026-08-10, the 26.6 update) — runwda then dies in dtx channel timeouts.
# `image list` prints a "signature" line when mounted, msg "none" when not.


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_ddi_mounted_sees_signature_line(monkeypatch):
    out = (
        '{"level":"INFO","msg":"no udid specified using first device in list"}\n'
        '{"level":"INFO","msg":"image signature","signature":"28080689ce6e"}\n'
    )
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _Proc(out))
    assert device.ddi_mounted()


def test_ddi_mounted_false_on_none(monkeypatch):
    out = '{"level":"INFO","msg":"none"}\n'
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _Proc(out))
    assert not device.ddi_mounted()


def test_mount_ddi_names_locked_phone(monkeypatch):
    # The one mount failure a human can fix on the spot must be named, not
    # buried in a log tail: DeviceLocked -> "unlock it".
    out = '{"level":"ERROR","msg":"error mounting image","err":"map[Error:DeviceLocked]"}\n'
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _Proc(out))
    monkeypatch.setattr(device, "ddi_mounted", lambda: False)
    ok, msg = device.mount_ddi()
    assert not ok
    assert "unlock" in msg.lower()


def test_mount_ddi_verifies_by_reprobe(monkeypatch):
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _Proc(""))
    monkeypatch.setattr(device, "ddi_mounted", lambda: True)
    ok, msg = device.mount_ddi()
    assert ok
    assert "mounted" in msg


def test_wda_bundle_no_cache_fallback_when_app_really_absent(monkeypatch, tmp_path):
    # A non-empty list without WDA means genuinely uninstalled: the stale
    # cache must NOT resurrect it.
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    (tmp_path / "wda_bundle").write_text("com.x.cached.xctrunner", encoding="utf-8")
    monkeypatch.setattr(
        device,
        "list_apps",
        lambda: [{"bundle_id": "com.apple.mobilesafari", "name": ""}],
    )
    assert device.detect_wda_bundle() is None
