"""Viewer HTTP origin-guard tests. No phone; loopback only."""

import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import viewer  # noqa: E402


class StubClient:
    """Stands in for WDAClient so endpoints work without a phone."""

    def __init__(self):
        self.calls = []

    def home(self):
        self.calls.append("home")


@pytest.fixture()
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), viewer.Handler)
    original = viewer.Handler.client
    viewer.Handler.client = StubClient()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        viewer.Handler.client = original


def test_same_origin_get_ok(base_url):
    assert requests.get(base_url + "/", timeout=5).status_code == 200


def test_bad_host_rejected(base_url):
    r = requests.get(base_url + "/", headers={"Host": "evil.example:80"}, timeout=5)
    assert r.status_code == 403


def test_cross_origin_rejected(base_url):
    r = requests.get(
        base_url + "/", headers={"Origin": "http://evil.example"}, timeout=5
    )
    assert r.status_code == 403


def test_cross_site_fetch_rejected(base_url):
    r = requests.get(
        base_url + "/", headers={"Sec-Fetch-Site": "cross-site"}, timeout=5
    )
    assert r.status_code == 403


def test_post_text_plain_rejected(base_url):
    # A CORS-"simple" text/plain POST must not reach the phone.
    r = requests.post(
        base_url + "/api/home",
        data="{}",
        headers={"Content-Type": "text/plain"},
        timeout=5,
    )
    assert r.status_code == 403


def test_post_same_origin_json_ok(base_url):
    r = requests.post(base_url + "/api/home", json={}, timeout=5)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_stop_toggle_creates_and_removes_file(base_url, tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    r = requests.post(base_url + "/api/stop", json={"stop": True}, timeout=5)
    assert r.status_code == 200
    assert r.json() == {"stopped": True}
    assert (tmp_path / "STOP").exists()
    r = requests.post(base_url + "/api/stop", json={"stop": False}, timeout=5)
    assert r.json() == {"stopped": False}
    assert not (tmp_path / "STOP").exists()


def test_stop_state_readable(base_url, tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    r = requests.get(base_url + "/api/stop", timeout=5)
    assert r.json() == {"stopped": False}
    (tmp_path / "STOP").touch()
    r = requests.get(base_url + "/api/stop", timeout=5)
    assert r.json() == {"stopped": True}
