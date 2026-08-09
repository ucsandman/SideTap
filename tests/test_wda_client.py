"""WDA client tests against a mocked WebDriverAgent HTTP server."""

import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import config  # noqa: E402
from phone_harness.wda_client import WDAClient, WDAError  # noqa: E402

FAKE_PNG = b"\x89PNG\r\n\x1a\nfakedata"


class FakeWDA(BaseHTTPRequestHandler):
    """Minimal WDA imitation. Counts requests; can kill sessions."""

    requests_seen = []
    kill_next_session = False
    session_counter = 0
    last_settings = None

    def log_message(self, *args):  # noqa: vulture
        pass

    def _reply(self, value, code=200):
        body = json.dumps({"value": value}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: vulture
        FakeWDA.requests_seen.append(("GET", self.path))
        if self.path == "/status":
            self._reply({"ready": True})
        elif self.path == "/screenshot":
            self._reply(base64.b64encode(FAKE_PNG).decode())
        elif self.path.endswith("/window/size"):
            if self._session_dead():
                return
            self._reply({"width": 390, "height": 844})
        elif self.path.endswith("/source?format=json"):
            self._reply({"type": "App", "children": []})
        else:
            self._reply({"error": "unknown command", "message": self.path}, 404)

    def do_POST(self):  # noqa: vulture
        FakeWDA.requests_seen.append(("POST", self.path))
        length = int(self.headers.get("Content-Length") or 0)
        self.payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/session":
            FakeWDA.session_counter += 1
            self._reply({"sessionId": f"sess-{FakeWDA.session_counter}"})
        elif self.path.endswith("/actions") or self.path.endswith("/wda/keys"):
            if self._session_dead():
                return
            self._reply(None)
        elif self.path.endswith("/wda/apps/launch"):
            self._reply(None)
        elif self.path.endswith("/appium/settings"):
            FakeWDA.last_settings = self.payload.get("settings")
            self._reply(None)
        elif self.path == "/wda/homescreen":
            self._reply(None)
        else:
            self._reply({"error": "unknown command", "message": self.path}, 404)

    def _session_dead(self):
        if FakeWDA.kill_next_session:
            FakeWDA.kill_next_session = False
            self._reply({"error": "invalid session id", "message": "session gone"}, 404)
            return True
        return False


@pytest.fixture()
def wda():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWDA)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeWDA.requests_seen = []
    FakeWDA.kill_next_session = False
    FakeWDA.session_counter = 0
    client = WDAClient(base_url=f"http://127.0.0.1:{server.server_port}", timeout=5)
    yield client
    server.shutdown()


def test_status(wda):
    assert wda.status()["ready"] is True
    assert wda.is_up()


def test_screenshot_decodes_base64(wda):
    assert wda.screenshot() == FAKE_PNG


def test_session_created_once_and_reused(wda):
    wda.window_size()
    wda.tap(10, 20)
    session_posts = [
        p for m, p in FakeWDA.requests_seen if m == "POST" and p == "/session"
    ]
    assert len(session_posts) == 1


def test_tap_sends_w3c_pointer_actions(wda):
    wda.tap(15, 25)
    assert any(p.endswith("/actions") for m, p in FakeWDA.requests_seen if m == "POST")


def test_dead_session_recovers_once(wda):
    wda.window_size()  # creates sess-1
    FakeWDA.kill_next_session = True
    assert wda.window_size() == (390.0, 844.0)  # auto-recreates and retries
    assert FakeWDA.session_counter == 2


def test_unreachable_server_raises_clear_error():
    client = WDAClient(base_url="http://127.0.0.1:1", timeout=1)
    with pytest.raises(WDAError, match="phone-harness up"):
        client.status()


def test_slow_server_raises_wda_error_not_timeout(monkeypatch):
    # A reachable-but-slow WDA raises ReadTimeout, which is NOT a
    # ConnectionError subclass — it must still surface as WDAError so
    # is_up() and the viewer's error paths keep working.
    import requests

    def slow(*_args, **_kwargs):
        raise requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(requests, "request", slow)
    client = WDAClient(base_url="http://127.0.0.1:1", timeout=1)
    with pytest.raises(WDAError, match="did not answer"):
        client.status()
    assert client.is_up() is False


def test_type_text_sends_characters(wda):
    wda.type_text("hi")
    assert any(p.endswith("/wda/keys") for m, p in FakeWDA.requests_seen if m == "POST")


def test_configure_mjpeg_defaults_from_config(wda):
    # Measured on device: WDA tops out ~34fps; 50% scale halves frame weight
    # with no fps cost. Defaults live in config so .env can override.
    wda.configure_mjpeg()
    assert FakeWDA.last_settings == {
        "mjpegServerFramerate": config.MJPEG_FPS,
        "mjpegServerScreenshotQuality": config.MJPEG_QUALITY,
        "mjpegScalingFactor": config.MJPEG_SCALE,
    }
    assert config.MJPEG_FPS == 60
    assert config.MJPEG_QUALITY == 70
    assert config.MJPEG_SCALE == 50


def test_stop_file_blocks_actions(wda, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    (tmp_path / "STOP").touch()
    with pytest.raises(WDAError, match="STOP"):
        wda.tap(10, 20)
    with pytest.raises(WDAError, match="STOP"):
        wda.type_text("hi")


def test_stop_file_still_allows_perception(wda, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    (tmp_path / "STOP").touch()
    # GETs (and the session-creating POST they need) must keep working so the
    # viewer can still show the screen while stopped.
    assert wda.status()["ready"] is True
    assert wda.window_size() == (390.0, 844.0)


def test_removing_stop_file_restores_actions(wda, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    stop = tmp_path / "STOP"
    stop.touch()
    with pytest.raises(WDAError, match="STOP"):
        wda.tap(1, 1)
    stop.unlink()
    wda.tap(1, 1)  # must not raise
