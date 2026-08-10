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
