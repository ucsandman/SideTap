"""Capture path selection: WDA HTTP first, go-ios fallback. No phone needed."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import capture  # noqa: E402
from phone_harness.wda_client import WDAError  # noqa: E402


class StubWDA:
    def __init__(self, png=b"wda-frame", error=None):
        self.png = png
        self.error = error
        self.calls = 0

    def screenshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.png


@pytest.fixture()
def fresh(monkeypatch):
    """Reset capture's module state so tests cannot leak into each other."""
    monkeypatch.setattr(capture, "_last_png", None)
    monkeypatch.setattr(capture, "_last_at", 0.0)
    monkeypatch.setattr(capture, "_wda_dead_until", 0.0)

    def use(stub):
        monkeypatch.setattr(capture, "_wda", stub)
        return stub

    return use


def test_screenshot_prefers_wda_http(fresh, monkeypatch):
    stub = fresh(StubWDA())
    # go-ios must not even be consulted when WDA answers
    monkeypatch.setattr(capture.device, "ios_path", lambda: pytest.fail("spawned"))
    assert capture.screenshot_png() == b"wda-frame"
    assert stub.calls == 1


def test_screenshot_falls_back_to_go_ios_when_wda_down(fresh, monkeypatch):
    fresh(StubWDA(error=WDAError("down")))
    monkeypatch.setattr(capture, "_go_ios_screenshot", lambda: b"go-ios-frame")
    assert capture.screenshot_png() == b"go-ios-frame"


def test_wda_failure_backs_off(fresh, monkeypatch):
    stub = fresh(StubWDA(error=WDAError("down")))
    monkeypatch.setattr(capture, "_go_ios_screenshot", lambda: b"go-ios-frame")
    capture.screenshot_png()
    capture.screenshot_png()  # inside the back-off window: WDA not retried
    assert stub.calls == 1


def test_max_age_serves_cached_frame(fresh):
    stub = fresh(StubWDA())
    assert capture.screenshot_png(max_age=5.0) == b"wda-frame"
    assert capture.screenshot_png(max_age=5.0) == b"wda-frame"
    assert stub.calls == 1


def test_no_go_ios_and_no_wda_raises(fresh, monkeypatch):
    fresh(StubWDA(error=WDAError("down")))
    monkeypatch.setattr(capture.device, "ios_path", lambda: None)
    with pytest.raises(capture.CaptureError, match="go-ios not found"):
        capture.screenshot_png()
