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

    session_id = "stub-session"
    window = None  # set to (w, h) to make /api/status succeed

    def home(self):
        self.calls.append("home")

    def lock(self):
        self.calls.append("lock")

    def window_size(self):
        if self.window is None:
            raise viewer.WDAError("no phone in tests")
        return self.window

    def configure_mjpeg(self):
        pass


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


def test_lock_endpoint_locks_phone(base_url):
    r = requests.post(base_url + "/api/lock", json={}, timeout=5)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert "lock" in viewer.Handler.client.calls


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


def _wait_for_lan_state(state, value, tries=100):
    import time

    for _ in range(tries):
        if state["exposed"] is value:
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def lan_state():
    yield viewer._LAN_STATE
    viewer._LAN_STATE["exposed"] = None


def test_refresh_lan_state_flags_exposure(monkeypatch, lan_state):
    monkeypatch.setattr(
        viewer.admin, "_check_ports_local", lambda: (False, "exposed", "fix")
    )
    viewer._refresh_lan_state()
    assert _wait_for_lan_state(lan_state, True)


def test_refresh_lan_state_clears_when_locked(monkeypatch, lan_state):
    monkeypatch.setattr(viewer.admin, "_check_ports_local", lambda: (True, "ok", ""))
    viewer._refresh_lan_state()
    assert _wait_for_lan_state(lan_state, False)


def test_viewer_html_has_no_duplicate_element_ids():
    # A second id="btn-lock" made getElementById wire the phone-Lock button to
    # the Lock-ports handler (clicking it opened an admin PowerShell) and the
    # doctor's visibility toggle hid it. Duplicate ids fail silently — ban them.
    import re

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    ids = re.findall(r'\bid="([^"]+)"', html)
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate element ids: {sorted(dupes)}"


def test_status_carries_boot_id_for_auto_reload(base_url):
    # A tab from before a viewer restart runs stale JS against new endpoints;
    # the page reloads itself when the boot id in /api/status changes.
    viewer.Handler.client.window = (390.0, 844.0)
    r = requests.get(base_url + "/api/status", timeout=5)
    assert r.json()["boot"] == viewer._BOOT_ID
    assert viewer._BOOT_ID  # non-empty


class TuneStub:
    """Counts tuning calls; session_id mutable like a real recreated session."""

    def __init__(self, session_id="s1"):
        self.session_id = session_id
        self.tuned = 0

    def configure_mjpeg(self):
        self.tuned += 1


def test_tune_mjpeg_reapplies_on_new_session(monkeypatch):
    # Settings are per-session: after an agent steals the session (or WDA
    # restarts), the viewer's recreated session is back on WDA defaults and
    # must be tuned again.
    monkeypatch.setattr(viewer, "_TUNED_SESSION", None)
    client = TuneStub()
    viewer._tune_mjpeg(client)
    viewer._tune_mjpeg(client)
    assert client.tuned == 1  # same session: once
    client.session_id = "s2"
    viewer._tune_mjpeg(client)
    assert client.tuned == 2  # recreated session: re-applied


def test_activity_endpoint_serves_feed(base_url, tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    r = requests.get(base_url + "/api/activity", timeout=5)
    assert r.json() == []
    (tmp_path / "agent_activity.log").write_text(
        json.dumps({"ts": 1.0, "action": "tap (10, 20)"}) + "\n", encoding="utf-8"
    )
    r = requests.get(base_url + "/api/activity", timeout=5)
    assert r.json() == [{"ts": 1.0, "action": "tap (10, 20)"}]
