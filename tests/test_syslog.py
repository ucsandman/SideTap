"""The wedge syslog capture (appium/WebDriverAgent#1210)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import syslog  # noqa: E402


class _FakeProc:
    """A live `ios syslog` as far as mark() is concerned."""

    def poll(self):
        return None


@pytest.fixture(autouse=True)
def clean_ring(monkeypatch, tmp_path):
    monkeypatch.setattr(syslog.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(syslog, "_proc", None)
    monkeypatch.setattr(syslog, "_dump", None)
    syslog._lines.clear()
    yield
    syslog._close_dump()
    syslog._lines.clear()


def test_plain_unwraps_the_device_line():
    raw = '{"msg":"Aug  8 20:21:03 iPhone-86 geod(libxpc.dylib)[148] \\u003cNotice\\u003e: hi"}'
    assert syslog._plain(raw) == (
        "Aug  8 20:21:03 iPhone-86 geod(libxpc.dylib)[148] <Notice>: hi"
    )


def test_plain_drops_go_ios_chatter_and_keeps_unknown_lines():
    chatter = '{"time":"2026-08-08T20:21:03Z","level":"INFO","msg":"no udid specified","module":"go-ios"}'
    assert syslog._plain(chatter) is None
    assert syslog._plain("") is None
    assert syslog._plain("not json at all") == "not json at all"
    # Malformed JSON is still evidence — never silently dropped.
    assert syslog._plain('{"msg": broken') == '{"msg": broken'


def test_mark_writes_the_ring_then_records_the_tail(monkeypatch):
    monkeypatch.setattr(syslog, "_proc", _FakeProc())
    syslog._lines.extend(["before-1", "before-2"])

    path = syslog.mark("wedge")
    assert path is not None and path.exists()

    # Lines arriving AFTER the mark (the recovery) land in the same file.
    syslog._pump(type("P", (), {"stdout": iter(['{"msg":"after-the-press"}'])})())
    syslog._close_dump()

    text = path.read_text(encoding="utf-8")
    assert "before-1" in text and "before-2" in text
    assert "after-the-press" in text
    assert text.index("before-2") < text.index("after-the-press")


def test_mark_is_a_no_op_when_nothing_is_capturing():
    # CLI runs never start the reader; a dump of an empty ring would read as
    # "the phone said nothing", which is worse than no file at all.
    assert syslog.mark("wedge") is None


def test_second_mark_does_not_split_one_occurrence(monkeypatch):
    monkeypatch.setattr(syslog, "_proc", _FakeProc())
    first = syslog.mark("wedge")
    assert first is not None
    assert syslog.mark("wedge") is None  # tail still recording


def test_ring_is_bounded():
    assert syslog._lines.maxlen == syslog.RING_LINES
    # It must outlast the viewer's detection delay, not just the event.
    from phone_harness import viewer

    assert syslog.RING_LINES / 180.0 > viewer._HEAL_MIN_SILENCE * 2


def test_unwedge_marks_before_it_presses_home(monkeypatch, tmp_path):
    """The repair ENDS the occurrence, so the dump has to come first."""
    from phone_harness import admin

    order = []
    monkeypatch.setattr(syslog, "mark", lambda label: order.append("mark") or None)
    monkeypatch.setattr(
        admin.device, "foreground_springboard", lambda: order.append("home") or True
    )
    monkeypatch.setattr(admin, "_wait_for_wda", lambda *a, **k: True)
    monkeypatch.setattr(admin.wda_client, "log_event", lambda *a, **k: None)

    assert admin._unwedge(object()) is True
    assert order == ["mark", "home"]


def test_recovery_line_names_the_dump(monkeypatch, tmp_path):
    from phone_harness import admin

    logged = []
    monkeypatch.setattr(syslog, "mark", lambda label: tmp_path / "syslog-wedge-x.log")
    monkeypatch.setattr(admin.device, "foreground_springboard", lambda: True)
    monkeypatch.setattr(admin, "_wait_for_wda", lambda *a, **k: True)
    monkeypatch.setattr(admin.wda_client, "log_event", logged.append)

    admin._unwedge(object())
    assert logged == [
        "recovered a wedged link (pressed Home); log saved to syslog-wedge-x.log"
    ]


def test_tail_closes_itself(monkeypatch):
    """A phone that goes quiet must not leave the dump open forever."""
    monkeypatch.setattr(syslog, "_proc", _FakeProc())
    monkeypatch.setattr(syslog, "TAIL_SECONDS", 0.05)
    path = syslog.mark("wedge")
    assert path is not None
    deadline = time.time() + 2.0
    while syslog._dump is not None and time.time() < deadline:
        time.sleep(0.01)
    assert syslog._dump is None
