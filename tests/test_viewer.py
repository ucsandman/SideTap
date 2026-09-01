"""Viewer HTTP origin-guard tests. No phone; loopback only."""

import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import helpers, viewer  # noqa: E402


class StubClient:
    """Stands in for WDAClient so endpoints work without a phone."""

    def __init__(self):
        self.calls = []
        # waitForIdleTimeout values in the order they were set. Kept off
        # self.calls: tests index that list positionally.
        self.idle_waits = []

    session_id = "stub-session"
    window = None  # set to (w, h) to make /api/status succeed

    def long_press(self, x, y, seconds):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        self.calls.append(("long_press", x, y, seconds))

    def source(self):  # noqa: vulture  (duck-typed: helpers.ui_tree calls it)
        return {"type": "Application", "children": []}

    # /api/home walks with helpers.goto_home_page, which reads the PageIndicator
    # through one targeted lookup rather than a whole-tree dump. Reporting page 1
    # makes the walk a verified no-op.
    def find_first(self, class_chain):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        self.calls.append(("find_first", class_chain))
        return "page-indicator"

    def element_value(self, element_id):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        return "Page 1 of 8"

    def swipe(self, x1, y1, x2, y2, seconds=0.3):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        self.calls.append(("swipe", x1, y1, x2, y2))

    def lock(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        self.calls.append("lock")

    clipboard_content = ""

    def tap(self, x, y):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        self.calls.append(("tap", x, y))

    def type_text(self, text):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        self.calls.append(("type_text", text))

    def key_press(self, key):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        self.calls.append(("key_press", key))

    def set_wait_for_idle(self, seconds):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        self.idle_waits.append(seconds)

    def get_clipboard(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        return self.clipboard_content

    def set_clipboard(self, text):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        self.clipboard_content = text
        self.calls.append(("set_clipboard", text))

    window_size_calls = 0

    def window_size(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        self.window_size_calls += 1
        if self.window is None:
            raise viewer.WDAError("no phone in tests")
        return self.window

    orient = "PORTRAIT"
    orientation_calls = 0
    # Which WDAError a dead link raises. A refused socket is a plain WDAError
    # and a wedged one is a WDATimeout, and /api/status tells the two apart,
    # so a test has to be able to pick. None keeps the default refused case.
    orientation_error = None

    def orientation(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        # Raises on a dead link exactly like the real session request, which is
        # the whole point of it: /api/status serves a memoised window size and
        # this is the only thing left that can still notice WDA is gone.
        self.orientation_calls += 1
        if self.window is None:
            raise self.orientation_error or viewer.WDAError("no phone in tests")
        return self.orient

    def configure_mjpeg(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        pass

    battery_info = None  # set to a dict to make battery() succeed
    locked = None  # set to a bool to make is_locked() succeed
    app = None  # set to a dict to make active_app() succeed
    up = True  # set False to simulate WDA not answering

    def is_up(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        return self.up

    def battery(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        if self.battery_info is None:
            raise viewer.WDAError("no phone in tests")
        return self.battery_info

    def is_locked(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        if self.locked is None:
            raise viewer.WDAError("no phone in tests")
        return self.locked

    def active_app(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        if self.app is None:
            raise viewer.WDAError("no phone in tests")
        return self.app


@pytest.fixture()
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), viewer.Handler)
    original = viewer.Handler.client
    stub = StubClient()
    viewer.Handler.client = stub
    # /api/home reaches the phone through helpers, whose client is a separate
    # object from Handler.client. Without this the endpoint would build a real
    # WDAClient and try to talk to a phone that is not there.
    original_helpers_client = helpers._client
    helpers._client = stub
    helpers._invalidate_tree()
    # window_size() is cached per orientation; every test gets a fresh
    # StubClient reporting the same PORTRAIT, so a stale cache from a previous
    # test would otherwise be served here without ever calling window_size().
    viewer._WINDOW_SIZE = None  # noqa: vulture  (viewer.py reads these)
    viewer._WINDOW_ORIENT = None  # noqa: vulture  (viewer.py reads these)
    # helpers keeps its own size memo, on the same reasoning, and /api/home
    # reaches the phone through it.
    helpers._size_cache.update({"wh": None, "session_id": None, "orientation": None})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        viewer.Handler.client = original
        helpers._client = original_helpers_client
        helpers._invalidate_tree()
        viewer._WINDOW_SIZE = None  # noqa: vulture  (viewer.py reads these)
        viewer._WINDOW_ORIENT = None  # noqa: vulture  (viewer.py reads these)
        helpers._size_cache.update(
            {"wh": None, "session_id": None, "orientation": None}
        )


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


def test_same_site_iframe_navigation_ok(base_url):
    # The fleet dashboard (127.0.0.1:8769) iframes this viewer: a loopback
    # port-crossing navigation is Sec-Fetch-Site "same-site" with NO Origin.
    r = requests.get(base_url + "/", headers={"Sec-Fetch-Site": "same-site"}, timeout=5)
    assert r.status_code == 200


def test_same_site_cross_port_post_still_rejected(base_url):
    # fetch() always sends Origin; only Origin-less NAVIGATIONS ride same-site.
    r = requests.post(
        base_url + "/api/home",
        json={},
        headers={
            "Sec-Fetch-Site": "same-site",
            "Origin": "http://127.0.0.1:8769",
        },
        timeout=5,
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


def test_phone_endpoint_serves_passive_info(base_url):
    c = viewer.Handler.client
    c.battery_info = {"level": 0.78, "state": 2}
    c.locked = False
    c.app = {"bundleId": "com.apple.mobilesafari", "name": "Safari", "pid": 4242}
    r = requests.get(base_url + "/api/phone", timeout=5)
    assert r.status_code == 200
    assert r.json() == {
        "battery": {"level": 0.78, "state": 2},
        "locked": False,
        "app": {"bundleId": "com.apple.mobilesafari", "name": "Safari", "pid": 4242},
        "session": "stub-session",
        # Safari is frontmost, so the Home Screen page is not read at all.
        "page": None,
    }


def test_phone_endpoint_degrades_per_field(base_url):
    # One failing read must not blank the others (spec: strip degrades).
    c = viewer.Handler.client
    c.battery_info = None  # battery() raises
    c.locked = True
    c.app = None  # active_app() raises
    r = requests.get(base_url + "/api/phone", timeout=5)
    assert r.json() == {
        "battery": None,
        "locked": True,
        "app": None,
        "session": "stub-session",
        "page": None,
    }


def test_phone_endpoint_reads_page_only_on_the_home_screen(base_url, monkeypatch):
    # current_page() is a real WDA lookup on a 10s poll. It must fire on the
    # springboard and nowhere else, or every app screen pays for a page
    # indicator that is not on it.
    from phone_harness import helpers

    calls = []

    def fake_current_page():
        calls.append(1)
        return {"index": 4, "total": 8, "zone": "page"}

    monkeypatch.setattr(helpers, "current_page", fake_current_page)
    c = viewer.Handler.client
    c.battery_info = None
    c.locked = False

    c.app = {"bundleId": "com.apple.mobilesafari", "name": "Safari", "pid": 1}
    assert requests.get(base_url + "/api/phone", timeout=5).json()["page"] is None
    assert calls == []

    c.app = {"bundleId": "com.apple.springboard", "name": "SpringBoard", "pid": 1}
    body = requests.get(base_url + "/api/phone", timeout=5).json()
    assert body["page"] == {"index": 4, "total": 8, "zone": "page"}
    assert calls == [1]


def test_phone_page_poll_does_not_arm_the_send_gate(base_url, monkeypatch):
    # The viewer polls this every 10s. Two integers iOS generates about its own
    # page indicator are bookkeeping, not content: if this marked the process
    # tainted, the send gate would arm itself forever on the Home Screen.
    from phone_harness import helpers, trust

    seen = {}

    def fake_current_page():
        seen["internal"] = getattr(trust._local, "internal", False)
        return {"index": 1, "total": 3, "zone": "page"}

    monkeypatch.setattr(helpers, "current_page", fake_current_page)
    viewer.Handler.client.app = {"bundleId": "com.apple.springboard", "pid": 1}
    requests.get(base_url + "/api/phone", timeout=5)
    assert seen["internal"] is True


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


def test_show_hint_clamps_and_names_the_lock():
    # WDA answers a refused request with a ~1.4KB blob of nested NSError text,
    # and pressing Home on a LOCKED phone earns one every time: /wda/homescreen
    # cannot open the springboard while the device is locked, and both halves of
    # the Home button reach it (goto_home_page calls press_home to leave an app).
    # Rendered raw it filled a third of the page, reported 2026-08-12. Every UI
    # error funnels through showHint, so the clamp and the plain-English lock
    # message have to stay inside it.
    import re

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    body = re.search(r"function showHint\(text\) \{(.*?)\n\}", html, re.S)
    assert body, "showHint changed shape — the error clamp lives inside it"
    src = body.group(1)
    assert "HINT_MAX" in src, (
        "showHint must clamp: no error may paste paragraphs into the pane"
    )
    assert "could not be, unlocked" in src, (
        "showHint must name the lock, not dump the WDA blob"
    )


def test_viewer_html_javascript_parses():
    # The whole page is one inline <script>: a syntax error anywhere in it
    # leaves every button dead, and no Python test can see that — a stray
    # `finally` shipped past a green 254-test run on 2026-08-11. Node is the
    # parser we already have on this machine; skip where there isn't one
    # rather than add a dependency for it.
    import re
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("no node to parse the page's JS with")
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts, "viewer.html has no inline script — did the page change shape?"
    with tempfile.TemporaryDirectory() as tmp:
        js = Path(tmp) / "viewer.js"
        js.write_text("\n".join(scripts), encoding="utf-8")
        done = subprocess.run(
            [node, "--check", str(js)], capture_output=True, text=True
        )
    assert done.returncode == 0, f"viewer.html JS does not parse:\n{done.stderr}"


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

    def configure_mjpeg(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
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


def test_window_size_is_cached_across_session_changes(base_url, monkeypatch):
    # window_size() is a 201ms device constant; /api/status polls every 5s and
    # must not re-fetch it unless the SCREEN changed. The session id is not
    # that signal: it changes on every 30s-idle remint and every unlock, so
    # keying on it paid 201ms — unlocked, with no _ACTION_LOCK held, so it
    # could start microseconds before the user's next tap — for a size that
    # cannot have changed. Rotation is the only thing that changes it, and
    # orientation() (7.7ms) already keys that AND does the two jobs the
    # session key was added for: it heals an evicted session and it raises
    # when WDA is gone.
    monkeypatch.setattr(viewer, "_WINDOW_SIZE", None)
    monkeypatch.setattr(viewer, "_WINDOW_ORIENT", None)
    client = viewer.Handler.client
    client.window = (390.0, 844.0)
    client.session_id = "s1"

    r1 = requests.get(base_url + "/api/status", timeout=5)
    r2 = requests.get(base_url + "/api/status", timeout=5)
    assert r1.json()["window"] == {"width": 390.0, "height": 844.0}
    assert r2.json()["window"] == {"width": 390.0, "height": 844.0}
    assert client.window_size_calls == 1  # second call served from cache

    client.session_id = "s2"
    requests.get(base_url + "/api/status", timeout=5)
    assert client.window_size_calls == 1  # a remint does not resize the screen


def test_status_reports_input_down_after_wda_dies_behind_the_cache(
    base_url, monkeypatch
):
    # The memo removed the ONLY WDA request /api/status made, so a cache hit
    # answered "input": True over a dead link forever. Deep sleep kills WDA
    # ~15min after the screen darkens, and viewer.html HIDES btn-up ("Restart
    # link") and btn-fix while input is true — so the lie also removed the two
    # buttons that fix it. orientation() is the 7.7ms liveness probe that keeps
    # the cached path honest.
    monkeypatch.setattr(viewer, "_WINDOW_SIZE", None)
    monkeypatch.setattr(viewer, "_WINDOW_ORIENT", None)
    client = viewer.Handler.client
    client.window = (390.0, 844.0)
    client.session_id = "s1"

    assert requests.get(base_url + "/api/status", timeout=5).json()["input"] is True
    # Warm the memo: from here window_size() is never called again.
    before = client.window_size_calls
    probes = client.orientation_calls
    assert requests.get(base_url + "/api/status", timeout=5).json()["input"] is True
    assert client.window_size_calls == before, "precondition: serving from cache"
    assert client.orientation_calls > probes, (
        "the cached path must still ask the phone something, or it cannot "
        "notice WDA has died"
    )

    client.window = None  # WDA dies; session_id keeps its stale in-memory value
    assert requests.get(base_url + "/api/status", timeout=5).json()["input"] is False


def test_status_refetches_the_window_after_a_rotation(base_url, monkeypatch):
    # /window/size reports the ACTIVE APPLICATION's frame, so width and height
    # swap on rotation and the session id cannot see it.
    monkeypatch.setattr(viewer, "_WINDOW_SIZE", None)
    monkeypatch.setattr(viewer, "_WINDOW_ORIENT", None)
    client = viewer.Handler.client
    client.window = (390.0, 844.0)
    client.session_id = "s1"
    client.orient = "PORTRAIT"
    requests.get(base_url + "/api/status", timeout=5)

    client.orient = "LANDSCAPE"
    client.window = (844.0, 390.0)
    r = requests.get(base_url + "/api/status", timeout=5)
    assert r.json()["window"] == {"width": 844.0, "height": 390.0}


def test_status_names_a_wedged_link_apart_from_a_refused_one(base_url, monkeypatch):
    # /api/status flattened every failure into "input": False, and viewer.html
    # answers that by unhiding the Sideloadly re-sign wizard — so a WEDGED link
    # (an app holding WDA's serial loop hostage) was prescribed a full re-sign.
    # That is what cost issue #2's reporter a re-sign with 6 days left on a good
    # signature. The distinction costs nothing: a socket that accepts and never
    # answers raises WDATimeout, a refused one raises a plain WDAError, and both
    # already arrive at this handler.
    monkeypatch.setattr(viewer, "_WINDOW_SIZE", None)
    monkeypatch.setattr(viewer, "_WINDOW_ORIENT", None)
    client = viewer.Handler.client
    client.window = None

    client.orientation_error = viewer.WDATimeout("no response within 10s")
    body = requests.get(base_url + "/api/status", timeout=5).json()
    assert body["input"] is False
    assert body["link"] == "wedged"

    client.orientation_error = viewer.WDAError("connection refused")
    body = requests.get(base_url + "/api/status", timeout=5).json()
    assert body["input"] is False
    assert body["link"] == "down"


def test_status_reports_a_live_link_as_up(base_url, monkeypatch):
    monkeypatch.setattr(viewer, "_WINDOW_SIZE", None)
    monkeypatch.setattr(viewer, "_WINDOW_ORIENT", None)
    client = viewer.Handler.client
    client.window = (390.0, 844.0)
    assert requests.get(base_url + "/api/status", timeout=5).json()["link"] == "up"


def test_viewer_html_hides_fix_input_on_a_wedge_but_never_restart_link():
    # The re-sign wizard is the wrong repair for a wedge — a socket that accepts
    # proves the signed runner is on the phone — so btn-fix hides there. But
    # btn-up ("Restart link") is the repair for BOTH failures (POST /api/up
    # unwedges before it restarts anything), so it must stay visible in every
    # failing state: hiding the one button that fixes it is the regression this
    # change could introduce.
    import re

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    fix = re.search(r"getElementById\('btn-fix'\)\.hidden = ([^;]+);", html)
    up = re.search(r"getElementById\('btn-up'\)\.hidden = ([^;]+);", html)
    assert fix and up, "btn-fix/btn-up visibility moved — re-point this test"
    assert "wedged" in fix.group(1), (
        "btn-fix must not offer the Sideloadly re-sign wizard for a wedged link"
    )
    assert up.group(1).strip() == "inputEnabled", (
        "btn-up is the repair for every failing state and must stay unconditional"
    )


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


def test_text_endpoint_sends_message(base_url, monkeypatch):
    sent = {}

    def fake_send(contact, text):
        sent["args"] = (contact, text)
        return {"sent": True, "contact": contact}

    monkeypatch.setattr("phone_harness.helpers.send_message", fake_send)
    r = requests.post(
        base_url + "/api/text", json={"to": "Mom", "message": "hi"}, timeout=5
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert sent["args"] == ("Mom", "hi")


def test_text_endpoint_validates_fields(base_url):
    r = requests.post(base_url + "/api/text", json={"to": "  "}, timeout=5)
    assert r.status_code == 400
    assert "required" in r.json()["error"]


def test_text_endpoint_surfaces_helper_error(base_url, monkeypatch):
    def boom(contact, text):
        raise RuntimeError("thread not found")

    monkeypatch.setattr("phone_harness.helpers.send_message", boom)
    r = requests.post(
        base_url + "/api/text", json={"to": "Mom", "message": "hi"}, timeout=5
    )
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "thread not found"}


def test_open_app_endpoint(base_url, monkeypatch):
    opened = []
    monkeypatch.setattr("phone_harness.helpers.open_app", opened.append)
    r = requests.post(base_url + "/api/open-app", json={"name": "Safari"}, timeout=5)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert opened == ["Safari"]
    r = requests.post(base_url + "/api/open-app", json={}, timeout=5)
    assert r.status_code == 400


def test_apps_endpoint_lists_known_names(base_url):
    r = requests.get(base_url + "/api/apps", timeout=5)
    names = r.json()["known"]
    assert "settings" in names and names == sorted(names)


def test_clipboard_endpoint_get_and_post(base_url):
    # Set clipboard via POST
    r = requests.post(
        base_url + "/api/clipboard", json={"text": "Hello from PC!"}, timeout=5
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert viewer.Handler.client.clipboard_content == "Hello from PC!"

    # Get clipboard via GET
    r = requests.get(base_url + "/api/clipboard", timeout=5)
    assert r.status_code == 200 and r.json() == {"ok": True, "text": "Hello from PC!"}


def test_screen_text_endpoint(base_url, monkeypatch):
    monkeypatch.setattr(
        "phone_harness.helpers.ocr",
        lambda: [
            {"text": "Settings", "type": "Application"},
            {"text": "General", "type": "Cell"},
            {"text": "Settings", "type": "StaticText"},  # duplicate text
            {"text": "  ", "type": "StaticText"},  # empty/whitespace
        ],
    )
    r = requests.get(base_url + "/api/screen-text", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["texts"] == [
        {"text": "Settings", "type": "Application"},
        {"text": "General", "type": "Cell"},
    ]


def test_parse_console_accepts_literal_call():
    name, args, kwargs = viewer._parse_console('tap_text("General", exact=True)')
    assert (name, args, kwargs) == ("tap_text", ["General"], {"exact": True})
    assert viewer._parse_console('set_clipboard("test")') == (
        "set_clipboard",
        ["test"],
        {},
    )
    assert viewer._parse_console("get_clipboard()") == ("get_clipboard", [], {})
    assert viewer._parse_console("swipe(10, -20, 10.5, 400)")[1] == [10, -20, 10.5, 400]


@pytest.mark.parametrize(
    "line",
    [
        "os.system('calc')",  # attribute call
        "__import__('os')",  # not whitelisted
        "screenshot()",  # deliberately excluded
        "tap(1+2, 3)",  # non-literal arg
        "tap_text(open('x'))",  # call as arg
        "tap(1); tap(2)",  # not a single expression
        "ocr",  # not a call
        "send_message(**{'contact': 'Mom'})",  # **kwargs
        "tap({[1, 2]: 3})",  # unhashable dict key (list)
        "tap({(1, 2): 3})",  # unhashable dict key (tuple, still literal)
        "",
    ],
)
def test_parse_console_rejects(line):
    with pytest.raises(ValueError):
        viewer._parse_console(line)


def test_console_endpoint_runs_whitelisted_helper(base_url, monkeypatch):
    monkeypatch.setattr(
        "phone_harness.helpers.ocr", lambda: [{"text": "General", "x": 1, "y": 2}]
    )
    r = requests.post(base_url + "/api/console", json={"line": "ocr()"}, timeout=5)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "result": [{"text": "General", "x": 1, "y": 2}]}


def test_console_endpoint_rejects_bad_line(base_url):
    r = requests.post(
        base_url + "/api/console", json={"line": "__import__('os')"}, timeout=5
    )
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_console_endpoint_rejects_unhashable_dict_key(base_url):
    # Regression: _console_literal used to let a TypeError (unhashable dict
    # key) escape _parse_console instead of the documented ValueError, which
    # skipped the 400 branch and dropped the connection.
    r = requests.post(
        base_url + "/api/console", json={"line": "tap({[1, 2]: 3})"}, timeout=5
    )
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_console_endpoint_non_dict_body_returns_500(base_url):
    # A JSON array body has no .get(): payload.get("line", ...) used to raise
    # AttributeError with no except clause, dropping the connection instead
    # of answering. The generic Exception -> 500 fallback must catch it.
    r = requests.post(base_url + "/api/console", json=[1, 2], timeout=5)
    assert r.status_code == 500
    assert "error" in r.json()


def test_console_endpoint_surfaces_helper_error(base_url, monkeypatch):
    def boom():
        raise RuntimeError("no text 'X' on screen")

    monkeypatch.setattr("phone_harness.helpers.ocr", boom)
    r = requests.post(base_url + "/api/console", json={"line": "ocr()"}, timeout=5)
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "no text 'X' on screen"}


# ---- self-healing link: deep sleep kills WDA (iOS killed the test runner,
# seen live 2026-08-10) and nothing over USB can wake the phone. The watchdog
# waits for the human to wake it (lockdown answers again) and reruns up().


def test_should_heal_only_when_woken_and_down(monkeypatch):
    monkeypatch.setitem(viewer._HEAL, "cooldown_until", 0.0)
    heal = viewer._should_heal
    down = 999.0  # silent long enough that the wedge gate is not what is tested
    assert heal(100.0, wda_up=False, lockdown_ok=True, stopped=False, down_for=down)
    assert not heal(100.0, wda_up=True, lockdown_ok=True, stopped=False, down_for=down)
    assert not heal(
        100.0, wda_up=False, lockdown_ok=False, stopped=False, down_for=down
    )  # still asleep
    assert not heal(
        100.0, wda_up=False, lockdown_ok=True, stopped=True, down_for=down
    )  # kill switch


def test_should_heal_honors_cooldown(monkeypatch):
    monkeypatch.setitem(viewer._HEAL, "cooldown_until", 500.0)
    heal = viewer._should_heal
    assert not heal(499.0, wda_up=False, lockdown_ok=True, stopped=False, down_for=999)
    assert heal(500.0, wda_up=False, lockdown_ok=True, stopped=False, down_for=999)


def test_should_heal_waits_for_sustained_silence(monkeypatch):
    # The recovery presses Home on the real phone. A single silent poll is not
    # proof of a wedge: unlock() drives a 45s client on purpose and the first
    # gesture after a deep sleep measured 20.5s, and WDA answers nothing while
    # it works. Healing then would yank the phone Home mid-unlock.
    monkeypatch.setitem(viewer._HEAL, "cooldown_until", 0.0)
    heal = viewer._should_heal
    assert not heal(100.0, wda_up=False, lockdown_ok=True, stopped=False, down_for=21.0)
    assert not heal(100.0, wda_up=False, lockdown_ok=True, stopped=False, down_for=44.9)
    assert heal(100.0, wda_up=False, lockdown_ok=True, stopped=False, down_for=45.0)


def _run_heal_loop(monkeypatch, *, answers, clock, activity=None, lockdown=None):
    """Drive _heal_loop over scripted (is_up, monotonic) values. Returns the
    number of admin.up() calls; the loop ends when the fakes run out.

    `activity` is the shared action feed the loop stats. It defaults to a path
    that does not exist: the repo's real .state/agent_activity.log is touched
    by any local phone session, and a fresh one would silently turn every
    healing test into a no-op.

    `lockdown` replaces the `ios date` probe when a test needs to count how
    often it is asked; it defaults to a phone that is awake.
    """
    monkeypatch.setattr(
        viewer, "activity_file", lambda: Path(activity or "no-such-activity.log")
    )
    monkeypatch.setitem(viewer._HEAL, "down_since", 0.0)
    monkeypatch.setitem(viewer._HEAL, "cooldown_until", 0.0)
    monkeypatch.setattr(viewer.time, "sleep", lambda _s: None)
    monkeypatch.setattr(viewer.device, "lockdown_ready", lockdown or (lambda: True))
    monkeypatch.setattr(viewer, "stop_engaged", lambda: False)
    healed = []
    monkeypatch.setattr(viewer.admin, "up", lambda: (healed.append(True), 0)[1])
    ups, ticks = iter(answers), iter(clock)

    class _Probe:
        def is_up(self):
            return next(ups)

    monkeypatch.setattr(viewer, "WDAClient", lambda **k: _Probe())
    monkeypatch.setattr(viewer.time, "monotonic", lambda: next(ticks))
    with pytest.raises(StopIteration):
        viewer._heal_loop()
    return len(healed)


def test_heal_loop_heals_a_socket_that_accepts_and_never_answers(monkeypatch):
    """Integration, through the real client and a real socket.

    That signature - connection accepted, no reply, ever - is what a wedged WDA
    looks like from here, and it is the ONLY thing the watchdog can see. The
    natural trigger (an app whose accessibility server stops answering) is not
    reproducible on demand, so the condition is reproduced directly instead.
    """
    from http.server import BaseHTTPRequestHandler

    class Hang(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: vulture  (http.server dispatches by name)
            time.sleep(30)  # far past the probe's timeout

        def log_message(self, *args):  # noqa: vulture
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Hang)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port

    monkeypatch.setitem(viewer._HEAL, "down_since", 0.0)
    monkeypatch.setitem(viewer._HEAL, "cooldown_until", 0.0)
    monkeypatch.setattr(viewer, "_HEAL_POLL", 0.05)
    monkeypatch.setattr(viewer, "_HEAL_MIN_SILENCE", 1.0)
    # Same reason as _run_heal_loop's default: the repo's real
    # .state/agent_activity.log is touched by any local phone session, and a
    # fresh one is now proof the link is busy rather than wedged.
    monkeypatch.setattr(viewer, "activity_file", lambda: Path("no-such-activity.log"))
    # Bounded through the once-per-poll call, so a watchdog that never fires
    # FAILS here instead of hanging the suite (it hung for 240s when the gate
    # was broken on purpose to check that this test can fail at all). Counting
    # via time.sleep would also silence the hanging server below.
    polls = []

    def one_poll():
        polls.append(1)
        if len(polls) > 20:
            raise SystemExit("watchdog never healed")
        return True

    monkeypatch.setattr(viewer.device, "lockdown_ready", one_poll)
    monkeypatch.setattr(viewer, "stop_engaged", lambda: False)
    real_client = viewer.WDAClient  # capture before patching, or the lambda recurses
    monkeypatch.setattr(
        viewer,
        "WDAClient",
        lambda **_kw: real_client(base_url=f"http://127.0.0.1:{port}", timeout=0.5),
    )
    healed = []

    def fake_up():
        healed.append(True)
        raise SystemExit  # BaseException: escapes the loop's except Exception

    monkeypatch.setattr(viewer.admin, "up", fake_up)
    try:
        with pytest.raises(SystemExit):
            viewer._heal_loop()
    finally:
        server.shutdown()
    assert healed == [True]


def test_heal_loop_heals_after_sustained_silence(monkeypatch):
    # Silent at t=100 and still silent at t=200: 100s > the 45s floor.
    assert (
        _run_heal_loop(monkeypatch, answers=[False, False], clock=[100.0, 200.0, 201.0])
        == 1
    )


def test_heal_loop_clears_the_silence_clock_when_wda_answers(monkeypatch):
    # A blip must not accumulate toward the 45s. Silent at 100, ANSWERING at
    # 120, silent again at 160 and 170. From the last silence that is 10s, so
    # nothing fires; without the reset it would measure 70s from t=100 and
    # press Home on a link that was up 50 seconds ago.
    assert (
        _run_heal_loop(
            monkeypatch,
            answers=[False, True, False, False],
            clock=[100.0, 120.0, 160.0, 170.0],
        )
        == 0
    )


def test_heal_loop_only_probes_lockdown_when_it_could_actually_heal(monkeypatch):
    # lockdown_ready() spawns `ios date` (a ~207ms go-ios process) and
    # _should_heal throws its answer away on its very first clause whenever
    # wda_up, so an answering poll must not pay for it: at _HEAL_POLL = 20s
    # that was ~4,300 spawns a day for a value nothing reads. The silent poll
    # still asks, because that is the branch this loop exists for.
    calls = []
    assert (
        _run_heal_loop(
            monkeypatch,
            answers=[True, False],
            clock=[100.0, 120.0],
            lockdown=lambda: calls.append(1) or True,
        )
        == 0
    )
    assert calls == [1]


def test_heal_loop_leaves_a_phone_another_process_is_driving_alone(
    monkeypatch, tmp_path
):
    # A wedge means NO request lands, for anyone. A 3s probe timing out three
    # times over 45s only proves the serial queue is deep — and it was, live on
    # 2026-08-20: another process was running the old find_on_home_screen (a
    # 3-5.7s /source per page, back to back) while the watchdog pressed Home on
    # the phone every ~70s for ten minutes, and WDA answered /status in 49ms
    # whenever it was probed by hand in between. Every landed action POST
    # appends to the shared feed from every process, so its mtime is proof a
    # request got through. Pure stat, no WDA call.
    feed = tmp_path / "agent_activity.log"
    feed.write_text("2026-08-20 tap\n", encoding="utf-8")  # mtime = now
    assert (
        _run_heal_loop(
            monkeypatch,
            answers=[False, False],
            clock=[100.0, 200.0, 201.0],
            activity=feed,
        )
        == 0
    )


def test_heal_loop_still_heals_when_the_feed_is_stale(monkeypatch, tmp_path):
    # The other half: a real wedge lands nothing, so the feed stops moving and
    # the watchdog must behave exactly as it did before the guard.
    feed = tmp_path / "agent_activity.log"
    feed.write_text("2026-08-20 tap\n", encoding="utf-8")
    old = time.time() - 10 * viewer._HEAL_MIN_SILENCE
    os.utime(feed, (old, old))
    assert (
        _run_heal_loop(
            monkeypatch,
            answers=[False, False],
            clock=[100.0, 200.0, 201.0],
            activity=feed,
        )
        == 1
    )


def test_a_feed_older_than_one_poll_is_not_evidence_of_a_live_link(
    monkeypatch, tmp_path
):
    # The feed answers "is the link answering RIGHT NOW", beside a probe that
    # is refreshed every poll, so its evidence has to be as fresh as the
    # probe's. Reusing the 45s silence floor as the staleness window stacked
    # the two: a wedge whose last action landed at t=0 still read as answering
    # at t=44, down_since only started after that, and the 45s floor then ran
    # again from there — ~120s to press Home against ~80s before the guard, on
    # a condition that never ends on its own.
    feed = tmp_path / "agent_activity.log"
    feed.write_text("2026-08-20 tap\n", encoding="utf-8")
    old = time.time() - 1.5 * viewer._HEAL_POLL  # stale, but under the 45s floor
    os.utime(feed, (old, old))
    assert (
        _run_heal_loop(
            monkeypatch,
            answers=[False, False],
            clock=[100.0, 200.0, 201.0],
            activity=feed,
        )
        == 1
    )


def test_unlock_endpoint_names_the_fix_when_link_down(base_url, monkeypatch):
    # Unlock's wake/swipe goes THROUGH WDA; with WDA dead the button used to
    # time out silently. It must say what to actually do instead.
    def _no_unlock(*_a, **_k):
        raise AssertionError("unlock must not run while the link is down")

    monkeypatch.setattr("phone_harness.helpers.unlock", _no_unlock)
    viewer.Handler.client.up = False
    try:
        r = requests.post(base_url + "/api/unlock", json={}, timeout=5)
    finally:
        viewer.Handler.client.up = True
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False
    assert "wake" in j["error"].lower()


# ---- send approval (the prompt-injection gate's human surface) --------------


def test_pending_send_is_null_when_nothing_waits(base_url, tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    r = requests.get(base_url + "/api/pending_send", timeout=5)
    assert r.json() == {"pending": None}


def test_pending_send_shows_the_waiting_record(base_url, tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    (tmp_path / "pending_send.json").write_text(
        '{"id": "abc", "contact": "Mom", "text": "hi", "flags": [], '
        '"taint_source": "read_messages", "created": 1}',
        encoding="utf-8",
    )
    r = requests.get(base_url + "/api/pending_send", timeout=5)
    assert r.json()["pending"]["contact"] == "Mom"


def test_send_decision_writes_the_answer(base_url, tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    (tmp_path / "pending_send.json").write_text('{"id": "abc"}', encoding="utf-8")
    r = requests.post(
        base_url + "/api/send_decision",
        json={"id": "abc", "decision": "approve"},
        timeout=5,
    )
    assert r.json() == {"ok": True}
    assert "approve" in (tmp_path / "send_decision.json").read_text(encoding="utf-8")


def test_send_decision_rejects_a_stale_id(base_url, tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    (tmp_path / "pending_send.json").write_text('{"id": "abc"}', encoding="utf-8")
    r = requests.post(
        base_url + "/api/send_decision",
        json={"id": "gone", "decision": "approve"},
        timeout=5,
    )
    assert r.json() == {"ok": False}
    assert not (tmp_path / "send_decision.json").exists()


def test_send_decision_rejects_cross_origin(base_url):
    """A page in another tab must not be able to approve a send."""
    r = requests.post(
        base_url + "/api/send_decision",
        json={"id": "abc", "decision": "approve"},
        headers={"Origin": "http://evil.example"},
        timeout=5,
    )
    assert r.status_code == 403


def test_send_approval_mode_is_readable(base_url, tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(viewer.config, "SEND_APPROVAL", "always")
    r = requests.get(base_url + "/api/send_approval", timeout=5)
    assert r.json() == {"mode": "always", "modes": ["always", "flagged", "off"]}


def test_send_approval_mode_can_be_toggled(base_url, tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    r = requests.post(base_url + "/api/send_approval", json={"mode": "off"}, timeout=5)
    assert r.json()["mode"] == "off"
    assert (tmp_path / "send_approval").read_text(encoding="utf-8") == "off"
    r = requests.get(base_url + "/api/send_approval", timeout=5)
    assert r.json()["mode"] == "off"


def test_send_approval_mode_rejects_junk(base_url, tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    r = requests.post(
        base_url + "/api/send_approval", json={"mode": "disabled"}, timeout=5
    )
    assert r.status_code == 400
    assert not (tmp_path / "send_approval").exists()


def test_send_approval_mode_rejects_cross_origin(base_url):
    """The gate setting must not be flippable by a page in another tab."""
    r = requests.post(
        base_url + "/api/send_approval",
        json={"mode": "off"},
        headers={"Origin": "http://evil.example"},
        timeout=5,
    )
    assert r.status_code == 403


def test_gesture_during_a_long_action_is_dropped_not_queued(base_url, monkeypatch):
    """A gesture the human aimed at a frozen screen must never fire later.

    unlock() after deep sleep holds _ACTION_LOCK for 10-30s. Measured live
    2026-08-11: four taps made during one 11s unlock ALL executed after it
    finished, 4.8-9.5s stale, landing on the unlocked home screen instead of
    the lock screen the human was looking at. Queueing them is the bug.
    """
    monkeypatch.setattr(viewer, "_ACTION_WAIT", 0.2)
    client = viewer.Handler.client
    # /api/lock, not /api/home: home now walks through helpers, so it no longer
    # records anything on the stub and "did it run late" would be unobservable.
    with viewer._ACTION_LOCK:  # stand in for the unlock holding the phone
        r = requests.post(base_url + "/api/lock", json={}, timeout=5)
    assert r.status_code == 409
    assert r.json()["ok"] is False
    assert "busy" in r.json()["error"].lower()
    assert "lock" not in client.calls  # never ran, not even late


def test_a_gesture_still_waits_for_a_quick_one_ahead_of_it(base_url):
    """Two deliberate gestures in a row must still serialize, not get dropped."""
    client = viewer.Handler.client
    started = threading.Event()

    def hold():
        with viewer._ACTION_LOCK:
            started.set()
            time.sleep(0.15)

    holder = threading.Thread(target=hold)
    holder.start()
    started.wait(2)
    r = requests.post(base_url + "/api/lock", json={}, timeout=5)
    holder.join()
    assert r.status_code == 200
    assert "lock" in client.calls


def test_long_press_passes_point_and_duration(base_url):
    r = requests.post(
        base_url + "/api/long_press",
        json={"x": 100, "y": 200, "seconds": 0.8},
        timeout=5,
    )
    assert r.status_code == 200
    assert ("long_press", 100.0, 200.0, 0.8) in viewer.Handler.client.calls


def test_long_press_clamps_duration(base_url):
    requests.post(
        base_url + "/api/long_press", json={"x": 1, "y": 2, "seconds": 99}, timeout=5
    )
    assert viewer.Handler.client.calls[-1][3] == 3.0


def test_long_press_defaults_duration(base_url):
    requests.post(base_url + "/api/long_press", json={"x": 1, "y": 2}, timeout=5)
    assert viewer.Handler.client.calls[-1][3] == 0.8


def _home_calls(monkeypatch):
    """Record which of the two paths /api/home takes."""
    calls = []
    monkeypatch.setattr(helpers, "press_home", lambda: calls.append("press"))
    monkeypatch.setattr(
        helpers, "goto_home_page", lambda n=1: calls.append(("walk", n))
    )
    return calls


def test_home_only_leaves_the_app_when_one_is_open(base_url, monkeypatch):
    # Minimising an app is most of what this button gets used for, and walking
    # to page 1 to do it froze the phone for 6-8s behind a busy label. From an
    # app it costs one call, exactly like the physical Home gesture.
    calls = _home_calls(monkeypatch)
    viewer.Handler.client.app = {"bundleId": "com.apple.mobilesafari", "name": "Safari"}
    r = requests.post(base_url + "/api/home", json={}, timeout=5)
    assert r.status_code == 200
    assert calls == ["press"]


def test_home_walks_to_page_one_from_the_home_screen(base_url, monkeypatch):
    # Second press, springboard already frontmost: press_home is a no-op between
    # pages, so reaching page 1 has to walk. The walk itself is in test_helpers.
    calls = _home_calls(monkeypatch)
    viewer.Handler.client.app = {"bundleId": "com.apple.springboard", "name": ""}
    r = requests.post(base_url + "/api/home", json={}, timeout=5)
    assert r.status_code == 200
    assert calls == [("walk", 1)]


def test_home_walks_when_the_front_app_cannot_be_read(base_url, monkeypatch):
    # Unknown must not turn Home into a dead button: press_home() does nothing
    # at all on the Home Screen, so an unreadable front app falls back to the
    # walk, which leaves an app on its own anyway.
    calls = _home_calls(monkeypatch)
    viewer.Handler.client.app = None  # active_app() raises
    r = requests.post(base_url + "/api/home", json={}, timeout=5)
    assert r.status_code == 200
    assert calls == [("walk", 1)]


def test_no_gesture_post_nested_inside_with_busy():
    # withBusy sets busyLabel; gesturePost starts with a phoneBusy() guard that
    # refuses to send while busyLabel is set. Nesting them makes a button set a
    # label and then silently send nothing. That shipped for exactly one run of
    # the Home button on 2026-08-12: the phone never moved off page 4.
    import re

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    for match in re.finditer(r"withBusy\(", html):
        start = match.start()
        depth, i = 0, match.end() - 1
        while i < len(html):
            if html[i] == "(":
                depth += 1
            elif html[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = html[start:i]
        assert "gesturePost(" not in body, (
            "gesturePost nested inside withBusy near offset "
            f"{start}: the phoneBusy guard will refuse withBusy's own label, "
            "so the request is never sent"
        )


def test_visibilitychange_closes_stream_and_restores_instantly():
    # A hidden tab must stop pulling MJPEG video (screen.src = '') but the
    # send-approval poll (loadApproval) is a safety gate and must keep running
    # regardless of visibility — this is structural because no jsdom/Playwright
    # harness exists in this repo (test_viewer_html_javascript_parses only
    # runs `node --check`).
    import re

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    match = re.search(
        r"document\.addEventListener\('visibilitychange', \(\) => \{", html
    )
    assert match, "no visibilitychange handler found in viewer.html"
    start = match.end() - 1  # index of the opening '{'
    depth, i = 0, start
    while i < len(html):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = html[start:i]

    assert "screen.src = ''" in body
    assert "loadStatus()" in body
    assert "loadPhone()" in body
    assert re.search(r"\bpoll\(\)", body)
    assert "loadApproval" not in body, (
        "loadApproval must not be gated by visibility: the send-approval "
        "gate has to stay armed while the tab is hidden"
    )
    # The approval poll itself must still be running, unmodified, elsewhere.
    assert "setInterval(loadApproval, 1000)" in html


def _js_function_body(html, signature):
    """Brace-match a top-level function body starting at its signature text.

    A source-text scan of the visibilitychange handler alone cannot prove
    loadStatus/loadPhone stop polling while hidden: the guard has to live
    inside those functions themselves (their own setInterval keeps firing
    while hidden but must do nothing), not in the handler. This walks to each
    function by name and extracts its real body, the same brace-counting the
    handler test above already uses.
    """
    start = html.index(signature)
    brace = html.index("{", start)
    depth, i = 0, brace
    while i < len(html):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return html[brace:i]


def test_hidden_tab_gates_status_and_phone_polls_not_approval():
    # Closing screen.src in the visibilitychange handler is not enough by
    # itself: loadStatus's own `if (s.mjpeg) startStream(...)` re-opens the
    # stream the very next time its 5s setInterval fires, because Chrome only
    # coalesces hidden-tab timers to 1s granularity for the first 5 minutes —
    # it does not stop them running. loadStatus and loadPhone must each
    # refuse to run while document.hidden is true. loadApproval is the
    # send-approval safety gate and must keep polling regardless of
    # visibility, so it must NOT gain the same guard.
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    status_body = _js_function_body(html, "async function loadStatus() {")
    phone_body = _js_function_body(html, "async function loadPhone() {")
    approval_body = _js_function_body(html, "async function loadApproval() {")

    assert "document.hidden" in status_body, (
        "loadStatus must refuse to run while hidden, or its own startStream() "
        "call re-opens the MJPEG stream on the next 5s tick"
    )
    assert "document.hidden" in phone_body, (
        "loadPhone must refuse to run while hidden, or it keeps driving the "
        "phone (battery/is_locked/current_page) over the same USB link"
    )
    assert "document.hidden" not in approval_body, (
        "loadApproval is the send-approval gate and must keep polling while "
        "the tab is hidden — gating it is worse than the bandwidth it saves"
    )


def test_status_reports_a_bring_up_in_flight(base_url, monkeypatch):
    # launch.py opens the browser IMMEDIATELY and runs up() in a background
    # thread, so the page's first check run lands mid bring-up. Without this
    # flag it cannot tell "still starting" from "broken" and reports the red
    # one, which is then frozen on screen until someone clicks Refresh.
    viewer.Handler.client.window = (390.0, 844.0)
    assert requests.get(base_url + "/api/status", timeout=5).json()["starting"] is False
    monkeypatch.setattr(viewer.admin, "bringing_up", lambda: True)
    assert requests.get(base_url + "/api/status", timeout=5).json()["starting"] is True


def test_status_reports_bring_up_even_while_a_gesture_holds_the_phone(
    base_url, monkeypatch
):
    # /api/status serves its CACHED payload during a gesture. The flag must not
    # ride along in that cache: an unlock holds the lock 20-30s, which is long
    # enough for a bring-up to finish behind it.
    viewer.Handler.client.window = (390.0, 844.0)
    requests.get(base_url + "/api/status", timeout=5)  # prime the cache
    monkeypatch.setattr(viewer.admin, "bringing_up", lambda: True)
    with viewer._ACTION_LOCK:
        body = requests.get(base_url + "/api/status", timeout=5).json()
    assert body["starting"] is True
    assert body["window"] == {"width": 390.0, "height": 844.0}  # still the cache


def test_status_reports_no_silence_while_the_link_answers(base_url, monkeypatch):
    # down_since is 0 on every watchdog poll that was answered — by the probe
    # or by any process landing an action (_actions_landing) — so the page gets
    # a plain 0 rather than a stamp it would have to interpret for itself.
    viewer.Handler.client.window = (390.0, 844.0)
    monkeypatch.setitem(viewer._HEAL, "down_since", 0.0)
    assert requests.get(base_url + "/api/status", timeout=5).json()["silent_for"] == 0.0


def test_status_publishes_how_long_the_phone_has_been_silent(base_url, monkeypatch):
    # A frozen MJPEG frame looks perfectly live, and a wedge does not change
    # that: orientation() times out, the handler falls through to the go-ios
    # branch and still answers 200, so the page has nothing to dim on.
    # _heal_loop already keeps this clock. Publish the AGE and never the raw
    # monotonic stamp, which means nothing outside this process. The link has
    # to be genuinely wedged for the age to survive: a link that answers
    # reports zero silence whatever the watchdog's clock still holds (see
    # test_status_reports_no_silence_when_the_link_answers_this_instant).
    client = viewer.Handler.client
    client.window = None
    monkeypatch.setattr(client, "orientation_error", viewer.WDATimeout("no response"))
    monkeypatch.setitem(viewer._HEAL, "down_since", time.monotonic() - 90)
    body = requests.get(base_url + "/api/status", timeout=5).json()
    assert body["link"] == "wedged"
    assert 80 < body["silent_for"] < 200, body["silent_for"]


def test_status_reports_no_silence_when_the_link_answers_this_instant(
    base_url, monkeypatch
):
    # down_since is written ONLY by _heal_loop, on its 20s poll — POST /api/up
    # (the Restart link button, the documented repair after a replug) never
    # clears it. So a manual recovery used to answer {"link": "up",
    # "silent_for": 300} in ONE payload, and the page dimmed the freshly
    # restored, moving picture to opacity .25 under "this picture is frozen"
    # for up to a full _HEAL_POLL. A link whose orientation() just returned has
    # been silent for zero seconds by definition; fix it here so every consumer
    # of the payload inherits it, not in the one branch of the page that reads
    # it today.
    viewer.Handler.client.window = (390.0, 844.0)
    monkeypatch.setitem(viewer._HEAL, "down_since", time.monotonic() - 300)
    body = requests.get(base_url + "/api/status", timeout=5).json()
    assert body["link"] == "up"
    assert body["silent_for"] == 0.0


def test_status_reports_silence_even_while_a_gesture_holds_the_phone(
    base_url, monkeypatch
):
    # Same reasoning as `starting`: /api/status serves its CACHED payload
    # during a gesture, and an unlock holds the lock 20-30s — long enough for
    # the link to go silent behind it. silent_for must ride in the fresh dict.
    viewer.Handler.client.window = (390.0, 844.0)
    requests.get(base_url + "/api/status", timeout=5)  # prime the cache
    monkeypatch.setitem(viewer._HEAL, "down_since", time.monotonic() - 90)
    with viewer._ACTION_LOCK:
        body = requests.get(base_url + "/api/status", timeout=5).json()
    assert 80 < body["silent_for"] < 200
    assert body["window"] == {"width": 390.0, "height": 844.0}  # still the cache


def test_viewer_dims_a_frozen_stream_only_while_a_stream_is_open():
    # The dim must be gated on `streaming`, not on silence alone: where WDA has
    # never come up, silent_for grows without bound while screen.onerror has
    # already handed the picture to the go-ios /api/screenshot poll, which is
    # genuinely live. Dimming that is the same lying-status bug pointed the
    # other way.
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    start = html.index("async function loadStatus()")
    body = html[start : html.index("// Refresh the still image", start)]
    assert "const STALL_SECONDS" in html, "the threshold is a named one-line revert"
    assert "streaming && (s.silent_for || 0) >= STALL_SECONDS" in body
    assert "screen.classList.toggle('dead', stalled)" in body
    assert "screen.classList.remove('dead')" not in body, (
        "an unconditional remove('dead') is what put a frozen frame at full "
        "opacity under a caption asserting the live view is on"
    )
    assert "has not answered" in body
    # _should_heal wants lockdown_ok, and deep sleep gates lockdown — so
    # down_since accumulates for hours while nothing is being tried. The label
    # states silence and claims no recovery.
    assert "trying to recover" not in html


def test_the_status_poll_never_overlaps_itself():
    # On a wedge the handler runs the client's full 10s inside orientation()
    # while setInterval keeps firing every 5s, so up to four /api/status calls
    # each fire their own request into a WDA that serves one at a time. The
    # same "one request at a time (never overlapping)" rule poll() follows.
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    start = html.index("async function loadStatus()")
    body = html[start : html.index("// Refresh the still image", start)]
    assert "if (statusInFlight) return;" in body
    assert "statusInFlight = false;" in body
    # NOT an AbortSignal on getJSON: it would abort the BROWSER fetch while the
    # handler keeps running against the wedged WDA, and getJSON also carries
    # POST /api/stop (a kill switch that landed would report failure) and
    # /api/doctor (~2s of subprocesses, slowest during the very outage the
    # checks exist for).
    assert "AbortSignal" not in html


def test_doctor_is_not_rerun_while_a_gesture_holds_the_phone(base_url, monkeypatch):
    # The page re-runs the checks by itself while any fails, and a full run is
    # ~2s of go-ios subprocesses plus a screenshot. Landing that inside an
    # unlock is what lets a waking lock screen fall back asleep — same reason
    # /api/status and /api/phone serve their last payload there.
    runs = []

    def counted():
        runs.append(1)
        return [{"name": "tunnel", "ok": True, "detail": "up", "fix": ""}]

    monkeypatch.setattr(viewer.admin, "doctor_results", counted)
    assert (
        requests.get(base_url + "/api/doctor", timeout=5).json()[0]["name"] == "tunnel"
    )
    assert len(runs) == 1
    with viewer._ACTION_LOCK:
        served = requests.get(base_url + "/api/doctor", timeout=5).json()
    assert len(runs) == 1  # not re-run
    assert served[0]["name"] == "tunnel"  # last result, not an empty list


class _WatchedLock:
    """A threading.Lock that says when a request has ENTERED it.

    The concurrency tests below have to release the in-flight doctor pass only
    once the second request is really waiting behind it; a sleep long enough to
    be safe on a loaded CI box is also long enough to be flaky on a fast one.
    The semaphore is released BEFORE the real acquire, so the test can wait for
    it instead of guessing.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.entered = threading.Semaphore(0)

    def __enter__(self):
        self.entered.release()
        return self._lock.__enter__()

    def __exit__(self, *exc):
        return self._lock.__exit__(*exc)


_DOCTOR_PASS = [{"name": "tunnel", "ok": True, "detail": "up", "fix": ""}]
_DOCTOR_FAIL = [
    {"name": "tunnel", "ok": False, "detail": "gone", "fix": "phone-harness up"}
]


def _concurrent_doctor(base_url, monkeypatch, payload):
    """Fire two /api/doctor requests with the second one arriving mid-pass.

    Returns (runs, responses): how many passes actually ran, and what each
    request was served.
    """
    lock = _WatchedLock()
    monkeypatch.setattr(viewer, "_DOCTOR_LOCK", lock, raising=False)
    runs = []
    release = threading.Event()

    def counted():
        runs.append(1)
        assert release.wait(10), "the test never released the in-flight pass"
        return payload

    monkeypatch.setattr(viewer.admin, "doctor_results", counted)
    got = {}

    def fire(key):
        got[key] = requests.get(base_url + "/api/doctor", timeout=20).json()

    threads = [threading.Thread(target=fire, args=(k,)) for k in ("a", "b")]
    try:
        threads[0].start()
        assert lock.entered.acquire(timeout=5), (
            "no doctor pass took _DOCTOR_LOCK - /api/doctor does not serialize "
            "its passes"
        )
        threads[1].start()
        assert lock.entered.acquire(timeout=5), (
            "the second request never took _DOCTOR_LOCK - it ran a pass of its "
            "own alongside the one already in flight"
        )
    finally:
        release.set()
        for t in threads:
            if t.ident is not None:  # a failed wait can leave the second unstarted
                t.join(20)
    return runs, got


def test_two_concurrent_doctor_requests_run_one_pass(base_url, monkeypatch):
    # A pass is ~2s of go-ios subprocesses, and 5-15s on a wedged link
    # (_check_wda_responding builds a 5s client, _check_perception's screenshot
    # sits on WDA's 10s back-off first). The page has six independent
    # loadDoctor triggers and every open tab has its own, so two passes racing
    # each other - during the exact bring-up they are diagnosing - is what the
    # boot case looked like. The second request waits for the first instead.
    viewer._LAST_DOCTOR = None  # noqa: vulture  (viewer.py reads these)
    viewer._LAST_DOCTOR_AT = 0.0  # noqa: vulture  (viewer.py reads these)
    runs, got = _concurrent_doctor(base_url, monkeypatch, _DOCTOR_PASS)
    assert len(runs) == 1, "both requests spawned their own go-ios pass"
    assert got["a"] == _DOCTOR_PASS
    assert got["b"] == _DOCTOR_PASS  # the waiter is served, never left empty


def test_a_waiting_doctor_request_is_never_served_a_result_older_than_itself(
    base_url, monkeypatch
):
    # The tempting shape - serve _LAST_DOCTOR while a pass is in flight - is
    # the lying-status bug this file has already been bitten by twice (the
    # /api/status window_size memo, the module-global memoized_run): the last
    # pass is an ALL-GREEN snapshot from before the link died, the page stops
    # its retry chain on a green result, and the header then reads "All checks
    # pass" over a dead link until someone reloads by hand. So the waiter is
    # only ever served a result STAMPED AFTER its own request arrived.
    viewer._LAST_DOCTOR = list(_DOCTOR_PASS)  # noqa: vulture
    # A minute old: the green was recorded while the link still worked. Not
    # time.monotonic() itself - Windows' monotonic clock ticks every 15.6ms, so
    # a stamp taken here can read EQUAL to the one the handler takes next, and
    # a cache that landed inside the asking request's own tick is served on
    # purpose (that is the coalescing win, not a stale read).
    viewer._LAST_DOCTOR_AT = time.monotonic() - 60.0  # noqa: vulture
    runs, got = _concurrent_doctor(base_url, monkeypatch, _DOCTOR_FAIL)
    assert len(runs) == 1
    assert got["a"] == _DOCTOR_FAIL
    assert got["b"] == _DOCTOR_FAIL, "the waiter was served the stale green"


def test_checks_reschedule_themselves_while_failing():
    # The page used to run the checks EXACTLY once, on load — mid bring-up —
    # and freeze that red result until someone clicked Refresh checks. Every
    # exit from loadDoctor must hand off to scheduleDoctor, which re-runs while
    # anything fails and stops once green.
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    start = html.index("async function loadDoctor()")
    end = html.index("btn-refresh", start)
    body = html[start:end]
    assert body.count("scheduleDoctor(") == 2, (
        "loadDoctor must schedule the next run on BOTH paths (checks read, and "
        "the catch where the viewer is unreachable)"
    )
    assert "scheduleDoctor(fails > 0)" in body  # stops once green


def test_home_surfaces_a_failed_walk(base_url, monkeypatch):
    # A transient WDA drop mid-walk leaves the phone part-way. That must reach
    # the human as an error, never a silent no-op that looks like it worked.
    def boom(n=1):
        raise RuntimeError("wanted page 1, still on page 5")

    monkeypatch.setattr(helpers, "goto_home_page", boom)
    r = requests.post(base_url + "/api/home", json={}, timeout=5)
    assert r.status_code >= 400
    assert "page 5" in r.text


def test_page_endpoint_walks_to_the_requested_page(base_url, monkeypatch):
    walked = []
    monkeypatch.setattr(helpers, "goto_home_page", lambda n: walked.append(n))
    r = requests.post(base_url + "/api/page", json={"index": 6}, timeout=5)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert walked == [6]


def test_page_endpoint_surfaces_a_short_walk(base_url, monkeypatch):
    # goto_home_page raises rather than leaving you two pages short. That has to
    # reach the human, or the chips would highlight a page nobody is on.
    def boom(n):
        raise RuntimeError(f"wanted page {n}, still on page 2")

    monkeypatch.setattr(helpers, "goto_home_page", boom)
    r = requests.post(base_url + "/api/page", json={"index": 7}, timeout=5)
    assert r.json()["ok"] is False
    assert "still on page 2" in r.json()["error"]


def test_page_endpoint_rejects_a_missing_index(base_url):
    r = requests.post(base_url + "/api/page", json={}, timeout=5)
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_read_thread_returns_messages(base_url, monkeypatch):
    seen = {}

    def fake_read(contact, limit):
        seen.update(contact=contact, limit=limit)
        return [{"text": "hi", "from_me": False}, {"text": "hey", "from_me": True}]

    monkeypatch.setattr(helpers, "read_messages", fake_read)
    r = requests.post(
        base_url + "/api/read-thread", json={"contact": "Mom", "limit": 30}, timeout=5
    )
    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "messages": [
            {"text": "hi", "from_me": False},
            {"text": "hey", "from_me": True},
        ],
    }
    assert seen == {"contact": "Mom", "limit": 30}


def test_read_thread_requires_a_contact(base_url):
    r = requests.post(base_url + "/api/read-thread", json={"contact": "  "}, timeout=5)
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_read_thread_clamps_the_limit(base_url, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        helpers,
        "read_messages",
        lambda contact, limit: seen.update(limit=limit) or [],
    )
    requests.post(
        base_url + "/api/read-thread", json={"contact": "Mom", "limit": 9999}, timeout=5
    )
    assert seen["limit"] == 50


def test_read_thread_still_arms_the_send_gate(base_url, monkeypatch):
    # The OPPOSITE of the page poll: message text is the most direct injection
    # route into anything sharing this process. read_messages marks the process
    # tainted and the viewer must NOT wrap that in trust.internal().
    from phone_harness import trust

    seen = {}

    def fake_read(contact, limit):
        seen["internal"] = getattr(trust._local, "internal", False)
        return []

    monkeypatch.setattr(helpers, "read_messages", fake_read)
    requests.post(base_url + "/api/read-thread", json={"contact": "Mom"}, timeout=5)
    assert seen["internal"] is False


def test_gesture_buttons_give_focus_back_to_the_phone():
    # Keys reach the phone only while nothing focusable holds focus: the window
    # keydown handler returns early on input/textarea/button/select/[tabindex].
    # A clicked button KEEPS focus, so Search opened Spotlight and then ate
    # every letter typed at it, and the obvious repair — click the screen —
    # sends a TAP that closes Spotlight. The handler must blur itself.
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    start = html.index("Object.entries(GESTURES)")
    body = html[start : html.index("// ---- Thread:", start)]
    assert "ev.currentTarget.blur()" in body, (
        "a gesture button that keeps focus swallows every keystroke after it"
    )
    # ...but only for a pointer click. ev.detail is 0 for Enter/Space on a
    # focused button, and blurring there throws keyboard users out of the tab
    # order.
    assert "ev.detail > 0" in body
    # The guard that makes the blur necessary in the first place.
    assert "input,textarea,button,select,[tabindex]" in html


def test_arrow_keys_send_wda_text_caret_controls():
    # Printable text uses /wda/keys, but caret navigation must use a W3C key
    # action. The text endpoint inserts private-use values as content on this
    # WDA build.
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    start = html.index("window.addEventListener('keydown'")
    body = html[start : html.index("const JSON_HDR", start)]
    assert "const ARROW_KEYS" in html
    assert "ArrowLeft: '\\uE012'" in html
    assert "ArrowRight: '\\uE014'" in html
    assert "ArrowUp: '\\uE013'" in html
    assert "ArrowDown: '\\uE015'" in html
    assert "Delete: '\\uE017'" in html
    assert "NUMPAD_MAP" in html
    assert "sendArrowKey(arrow)" in body
    assert "'/api/key'" in html
    assert "text-cursor" not in html


def test_key_endpoint_executes_key_press(base_url):
    r = requests.post(base_url + "/api/key", json={"key": "\ue017"}, timeout=5)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert ("key_press", "\ue017") in viewer.Handler.client.calls


def test_enter_sends_and_only_a_bare_enter_does():
    # Enter in the message box sends; Shift/Ctrl+Enter break the line. Ctrl is
    # the half that cannot be left to the browser: Chromium inserts NOTHING for
    # Ctrl+Enter in a textarea (measured headless 2026-08-14), so the newline is
    # typed in by hand there while Shift+Enter keeps the browser's own.
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    start = html.index("function enterSends(")
    body = html[start : html.index("enterSends('text-msg'", start)]
    assert "if (ev.shiftKey) return;" in body, "Shift+Enter must keep its newline"
    assert "setRangeText('\\n'" in body, "Ctrl+Enter inserts no newline on its own"
    # Any modifier takes the newline path: a send drives a real phone, so no
    # combo may fire one by surprise.
    assert "ev.ctrlKey || ev.altKey || ev.metaKey" in body
    # An IME's Enter picks a candidate; sending there would ship half a word.
    assert "ev.isComposing" in body
    # updateActionAvail disables the button while a send is in flight — Enter
    # must respect that or it re-fires the send.
    assert "if (!btn.disabled) btn.click();" in body
    # Both "type it, send it" boxes, and nothing else.
    assert "enterSends('text-msg', 'btn-text-send');" in html
    assert "enterSends('paste-text', 'btn-paste-send');" in html
    assert html.count("enterSends(") == 3  # the definition plus those two


def test_thread_bubbles_are_never_innerhtml():
    # Bubbles carry message text straight off the phone. Rendering that as HTML
    # would put attacker-controlled markup in the operator's own dashboard.
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    start = html.index("function renderThread()")
    body = html[start : html.index("document.getElementById('text-to')", start)]
    assert "b.textContent = m.text;" in body
    assert "innerHTML = m." not in body and "innerHTML = '<div class=\"bub" not in body


def test_stale_viewer_kill_spares_the_phone_link(tmp_path, monkeypatch):
    # Starting SideTap while one is already open must not kill the tunnel and
    # the forwards, which are children of the older launch.py. up() returns
    # early ("Already up") BEFORE serve() gets here, so nothing would rebuild
    # them: the viewer came back with a dead link (measured 2026-08-12).
    import os

    from phone_harness import device

    monkeypatch.setattr(viewer.config, "STATE_DIR", tmp_path)
    (tmp_path / "viewer.pid").write_text("424242", encoding="utf-8")
    seen = {}

    def fake_kill(pid, prefix, tree=True):
        seen.update(pid=pid, prefix=prefix, tree=tree)
        return True

    monkeypatch.setattr(device, "_safe_kill", fake_kill)
    viewer._kill_stale_viewer()
    assert seen == {"pid": 424242, "prefix": "python", "tree": False}
    # ...and it still claims the port for this process.
    assert (tmp_path / "viewer.pid").read_text(encoding="utf-8").strip() == str(
        os.getpid()
    )


def test_setup_wizard_waits_on_real_check_names():
    # Each wizard step completes when its named doctor checks pass. A renamed
    # check in admin.CHECKS would strand that step forever (the wizard shows
    # "▶" on it for eternity), so the names are pinned to each other here.
    import re

    from phone_harness import admin

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    block = re.search(r"const SETUP_STEPS = \[(.*?)\n\];", html, re.S)
    assert block, "SETUP_STEPS gone from viewer.html — was the setup wizard removed?"
    step_check_lists = re.findall(r"\[\s*'[^']*',\s*\[([^\]]*)\]", block.group(1))
    assert len(step_check_lists) >= 4, "SETUP_STEPS shape changed; update this scan"
    known = {name for name, _ in admin.CHECKS}
    for raw in step_check_lists:
        for check in re.findall(r"'([^']+)'", raw):
            assert check in known, f"wizard waits on unknown check {check!r}"
    # The wizard is one of the overlay's bodies, and first-run failures open IT
    # rather than the wall-of-red checks panel.
    assert "'setup'" in html.split("const OV_BODIES")[1].split(";")[0]
    assert "if (setupNeeded) { renderSetup(); openOverlay('setup'" in html


def test_status_answers_without_phone_or_screenshot(base_url, monkeypatch):
    # A phoneless machine — a fresh install with nothing plugged in — must
    # still get this JSON: the first-run wizard rides on setup_done, and a 500
    # here is exactly what hid the wizard on the first clean-machine test
    # (2026-08-13, OpenClaw laptop). window is null; the page treats that the
    # same as its own fetch-failed state.
    def boom(**_kw):
        raise RuntimeError("no phone, no go-ios screenshot")

    monkeypatch.setattr(viewer.capture, "screenshot_png", boom)
    r = requests.get(base_url + "/api/status", timeout=10)
    assert r.status_code == 200
    j = r.json()
    assert j["window"] is None
    assert j["input"] is False
    assert j["setup_done"] in (True, False)


def test_fix_input_worker_never_stays_running_on_a_crash(monkeypatch):
    """_FIX_JOB["running"] is the wizard's only liveness signal. An exception
    fix_input does not catch used to kill the worker thread before the final
    update, so every later click just read the stuck 'running' state and the
    wizard hung forever with no error (adversarial review 2026-08-13)."""
    from phone_harness import signing

    def explode(progress):
        progress("signing", "about to blow up")
        raise RuntimeError("uncaught surprise from deep in the stack")

    monkeypatch.setattr(signing, "fix_input", explode)
    with viewer._FIX_LOCK:
        viewer._FIX_JOB.update(running=True, step="p12", message="starting…", ok=None)
    viewer._fix_input_worker()
    with viewer._FIX_LOCK:
        job = dict(viewer._FIX_JOB)
    assert job["running"] is False
    assert job["ok"] is False
    assert "uncaught surprise" in job["message"]


def test_wheel_scroll_flicks_instead_of_dragging():
    # A synthetic drag carries no iOS inertia, so distance had to be bought the
    # slow way: capped at 45% of a screen per ~0.7s round trip, sustained
    # wheeling topped out near 0.64 screens/s. Inertia scales with release
    # speed, so a shorter, faster gesture travels further than a longer one.
    # The duration is a named constant because a too-short synthetic swipe is
    # swallowed in silence and this one has not been checked on the device yet.
    import re

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    block = re.search(r"async function flushWheel\(\) \{(.*?)\n\}", html, re.S)
    assert block, "flushWheel changed shape — the wheel gesture lives inside it"
    src = block.group(1)
    assert "WHEEL_MAX_TRAVEL" in src, "the travel cap must be the named constant"
    assert "WHEEL_FLICK_SECONDS" in src, "the flick duration must be the constant"
    assert "seconds: 0.25}" not in src, "a fixed 0.25s drag is the slow gesture"
    assert "WHEEL_FLICK_MIN_PT" in src, "the flick threshold must be the constant"
    assert "dist > 150" not in src, "the threshold is a guess; it must be named"
    assert "const WHEEL_FLICK_SECONDS = 0.15;" in html, (
        "one named constant, so a device check that fails is a one-line revert"
    )
    assert "const WHEEL_FLICK_MIN_PT = 150;" in html, (
        "the travel below which the wheel stays a drag is the third knob to walk back"
    )
    # The swipe is anchored at mid-height and ends at cy +/- dist, so a cap
    # past 0.5 of a screen sends the endpoint OFF the phone: at 0.6 on a
    # 390x844 device a hard trackpad spin ended at y=-84, and an off-screen
    # gesture is swallowed in silence. Distance was never the lever anyway.
    cap = re.search(r"const WHEEL_MAX_TRAVEL = ([\d.]+);", html)
    assert cap, "the travel cap must be one named constant to walk back"
    assert float(cap.group(1)) <= 0.5, (
        f"a {cap.group(1)} cap puts the wheel swipe's endpoint off the screen"
    )


def test_swipes_and_wheel_flicks_echo_what_was_sent():
    # A drag's only feedback was a dot at its START point after release, and the
    # wheel flick had none at all — flushWheel fetches /api/swipe raw, so it
    # never even earned postGesture's red failure dot. Both now draw the vector
    # at SEND time, INSIDE the gates, so the echo can never claim a gesture that
    # inputEnabled/phoneBusy refused.
    import re

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    assert 'id="swipe-echo"' in html
    assert "const SWIPE_ECHO_MS = 700;" in html, (
        "the fade must be one named constant — a one-line revert"
    )
    up = re.search(r"screen\.addEventListener\('pointerup'.*?\n\}\);", html, re.S)
    assert up and "showSwipeEcho(" in up.group(0)
    body = up.group(0)
    # The echo sits in the swipe branch, past both gates, never before them.
    assert body.index("if (!inputEnabled) return;") < body.index("showSwipeEcho(")
    assert body.index("phoneBusy()") < body.index("showSwipeEcho(")
    wheel = re.search(r"async function flushWheel\(\) \{(.*?)\n\}", html, re.S)
    assert wheel and "showSwipeEcho(" in wheel.group(1)
    # THE REGRESSION THAT MATTERS: the trail-following-the-pointer form was cut
    # because it requires reworking the `!drag.timer` guard that makes
    # drag.moveT stamp exactly once — the timing the flick/drag split rides on.
    move = re.search(r"screen\.addEventListener\('pointermove'.*?\n\}\);", html, re.S)
    assert move and "showSwipeEcho(" not in move.group(0), (
        "the echo must not be drawn from pointermove: that handler's early "
        "return is what makes drag.moveT fire once"
    )
    # postGesture stays untouched — its red showDot already marks a refused
    # gesture, and recolouring the echo would edit the line viewer-feat-1 owns.
    pg = re.search(r"async function postGesture\(.*?\n\}", html, re.S)
    assert pg and "showSwipeEcho(" not in pg.group(0)


def test_dropped_keystrokes_and_wheel_flicks_are_reported_not_swallowed():
    # The three phone-driving POSTs that awaited a response and never looked at
    # it: flushWheel (/api/swipe), flushKeys (/api/type), sendArrowKey
    # (/api/key). All three can come back 409 (another holder of _ACTION_LOCK —
    # /api/clipboard, /api/screen-text's 3.0-5.7s Home Screen /source, a second
    # viewer tab, or the chained flush whose phoneBusy() guard ran before this
    # tab's busy label existed) or 502 (WDAError), and the bare fetch can reject
    # outright when the viewer is gone. Every one of those lost the whole batch
    # in silence, which is the same freeze-looking silence viewer.py's PhoneBusy
    # branch exists to break.
    import re

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    for name in ("flushWheel", "flushKeys"):
        block = re.search(rf"async function {name}\(\) \{{(.*?)\n\}}", html, re.S)
        assert block, f"{name} changed shape"
        assert "reportNotSent(" in block.group(1), (
            f"{name} discards the response: a 409 or 502 loses the batch in silence"
        )
        assert "catch (e)" in block.group(1), (
            f"{name} must survive a rejected fetch — keydown never awaits it, so a "
            "dead viewer socket is an unhandled rejection and a lost buffer"
        )
    arrow = re.search(r"async function sendArrowKey\(key\) \{(.*?)\n\}", html, re.S)
    assert arrow, "sendArrowKey changed shape"
    assert "reportNotSent(" in arrow.group(1)
    assert "arrowBuf.length = 0" in arrow.group(1), (
        "a refused arrow must not be followed by the rest of the drain: "
        "half-applied caret navigation is worse than none"
    )
    rep = re.search(
        r"async function reportNotSent\(resp, what\) \{(.*?)\n\}", html, re.S
    )
    assert rep, "reportNotSent gone — the three flushers report through it"
    body = rep.group(1)
    # gesturePost opens with `if (phoneBusy()) return;`, which is why this is a
    # separate reporter: that guard here would swallow a buffered keyBuf
    # whenever this tab shows a busy label, the exact shape of the 2026-08-12
    # bug test_no_gesture_post_nested_inside_with_busy exists to catch.
    assert "phoneBusy(" not in body
    # Never replay. A re-send lands on whatever screen the long action left,
    # which is the 2026-08-11 stale-gesture burst.
    assert "fetch(" not in body
    # A count, never the characters: typed text is recorded nowhere, and a
    # passcode gets typed into this viewer too.
    assert "text" not in body


def test_drag_swipe_times_from_the_first_movement():
    # `seconds` used to run from pointerdown, so press-hesitate-flick was sent
    # as a 0.4s (or clamped 0.5s) drag with no inertia — and the 400ms
    # long-press ring encourages exactly that deliberate press. The clock now
    # starts where the drag does: the 8px crossing, which fires exactly once.
    import re

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    move = re.search(r"screen\.addEventListener\('pointermove'.*?\n\}\);", html, re.S)
    assert move, "the pointermove handler changed shape"
    assert "drag.moveT = Date.now()" in move.group(0), (
        "the drag clock must start at the 8px crossing"
    )
    assert ">= 8" in move.group(0), "the 8px tap/swipe threshold must not move"
    up = re.search(r"screen\.addEventListener\('pointerup'.*?\n\}\);", html, re.S)
    assert up, "the pointerup handler changed shape"
    assert "start.moveT || start.t" in up.group(0), (
        "the swipe duration must count from the first movement, not the press"
    )
    assert "0.1), 0.5)" in up.group(0), "the 0.1s floor and 0.5s cap stay"


def test_visibility_restore_does_not_race_the_stream():
    # loadStatus is async, so `loadStatus(); poll();` ran poll() while
    # `streaming` was still false: it fetched a /api/screenshot still frame
    # (0.22s of WDA time, one request at a time) that startStream() was about
    # to make pointless — in the exact instant the human is about to click.
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    assert "loadStatus().finally(poll); loadPhone();" in html, (
        "the still-frame poll must wait for loadStatus to decide about the stream"
    )
    assert "loadStatus(); loadPhone(); poll();" not in html


def _read_response(sock):
    """Read one HTTP response off a live socket (status line, headers, body)."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            return buf, b""
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n")[1:]:
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":")[1])
    while len(rest) < length:
        chunk = sock.recv(4096)
        if not chunk:
            break
        rest += chunk
    return head, rest


def test_keep_alive_serves_two_requests_on_one_connection(base_url):
    # HTTP/1.0 made every /api/* call reconnect. Replicated locally over 300
    # requests: 0.54-0.60ms median against 0.12-0.29ms on a kept-alive
    # connection. Every response path already sets Content-Length, which is
    # what HTTP/1.1 needs or the browser hangs waiting for a body that ended.
    import socket

    port = int(base_url.rsplit(":", 1)[1])
    req = f"GET /api/stop HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(req)
        head1, body1 = _read_response(s)
        s.sendall(req)
        head2, body2 = _read_response(s)
    assert head1.startswith(b"HTTP/1.1 200"), head1
    assert b'"stopped"' in body1
    assert head2.startswith(b"HTTP/1.1 200"), (
        f"the connection did not survive the first response: {head2!r}"
    )
    assert b'"stopped"' in body2


def test_rejected_post_cannot_smuggle_a_second_request(base_url):
    # Keep-alive turns an unread request body into the next request on the
    # wire. The two guards at the top of do_POST answer 403 WITHOUT reading
    # the body, so a cross-origin page could hide a well-formed, Host-correct
    # POST /api/tap inside a text/plain body and drive the phone with it —
    # the origin guard's whole job, undone by a transport change. Those paths
    # close the connection instead, exactly as HTTP/1.0 did.
    import socket

    port = int(base_url.rsplit(":", 1)[1])
    smuggled = (
        f"POST /api/tap HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        "Content-Type: application/json\r\nContent-Length: 15\r\n\r\n"
        '{"x":10,"y":10}'
    ).encode()
    attack = (
        f"POST /api/tap HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Content-Type: text/plain\r\nContent-Length: {len(smuggled)}\r\n\r\n"
    ).encode() + smuggled
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(attack)
        head, _body = _read_response(s)
        rest = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            rest += chunk
    assert head.startswith(b"HTTP/1.1 403"), head
    assert rest == b"", f"the smuggled request was answered: {rest!r}"
    assert not [c for c in viewer.Handler.client.calls if c[0] == "tap"]


def test_an_idle_keepalive_connection_is_closed_instead_of_parking_a_thread(
    base_url, monkeypatch
):
    # Keep-alive is what bought the 0.12-0.29ms round trip, but it also means a
    # tab that goes away (closed, slept, crashed) leaves a ThreadingHTTPServer
    # thread blocked in rfile.readline() with NO deadline. Handler.timeout is
    # the socket read timeout, which BaseHTTPRequestHandler turns into
    # close_connection, so the thread finishes instead of leaking.
    import socket

    assert viewer.Handler.timeout == 60, "the idle keep-alive reaper must ship"
    # socketserver applies it in setup(), i.e. when the connection opens, so it
    # is shortened before connecting — the 60s wait itself is not what is tested.
    monkeypatch.setattr(viewer.Handler, "timeout", 0.5)
    port = int(base_url.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(
            f"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode()
        )
        head, _body = _read_response(s)
        assert head.startswith(b"HTTP/1.1 200"), head
        assert s.recv(4096) == b"", "the idle connection was parked forever"


def test_a_slow_handler_is_not_cut_by_the_idle_timeout(base_url, monkeypatch):
    # The timeout applies to SOCKET READS, not to handler execution — /api/unlock
    # runs up to ~45s on a 45s client and /api/read-thread 10-20s, and cutting
    # either would be a far worse bug than a leaked thread. Proven, not reasoned:
    # a 2s handler under a 1s timeout still answers 200.
    monkeypatch.setattr(viewer.Handler, "timeout", 1)
    client = viewer.Handler.client
    client.tap = lambda _x, _y: time.sleep(2)
    try:
        r = requests.post(base_url + "/api/tap", json={"x": 1, "y": 2}, timeout=10)
    finally:
        del client.tap
    assert r.status_code == 200, r.status_code
    assert r.json() == {"ok": True}


def test_human_gestures_run_with_the_idle_wait_off(base_url):
    # WDA_IDLE_WAIT=2 is what makes a bare swipe ~0.7s instead of ~0.35s, and
    # it buys a settled tree nobody reads here: the human watches the MJPEG
    # stream. _enter_passcode already proved the win (six pad taps 4.94s ->
    # 2.8s). The setting rides the SHARED session, so it is restored in a
    # finally, inside the same _ACTION_LOCK the gesture holds.
    client = viewer.Handler.client
    for path, payload in (
        ("/api/tap", {"x": 10, "y": 20}),
        ("/api/swipe", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}),
        ("/api/long_press", {"x": 10, "y": 20}),
        ("/api/key", {"key": "a"}),
        # The real keystroke path: flushKeys and the paste box both POST here,
        # and nothing reads the tree after it. /api/text is NOT here — it runs
        # send_message, which reads the field back before it taps Send.
        ("/api/type", {"text": "hi"}),
    ):
        client.idle_waits = []
        assert requests.post(base_url + path, json=payload, timeout=5).ok
        assert client.idle_waits == [0, viewer.config.WDA_IDLE_WAIT], (
            f"{path} must drop the idle wait and restore it: {client.idle_waits}"
        )


def test_tree_reading_endpoints_keep_the_idle_wait(base_url):
    # NOT for anything whose helpers read the tree afterwards: goto_home_page's
    # "first read after swipe() returns is correct 6/6" and
    # find_on_home_screen's per-page read both depend on that settle, so a 0
    # here pushes them into a corrective second pass and ends up slower.
    client = viewer.Handler.client
    client.idle_waits = []
    assert requests.post(base_url + "/api/home", json={}, timeout=5).ok
    assert client.idle_waits == []


def test_gesture_survives_a_settings_call_that_fails(base_url):
    # The idle wait is an optimisation; the gesture is what the human asked
    # for. A settings POST that fails must not swallow the tap, and the
    # gesture's own error must still surface the way it does today.
    client = viewer.Handler.client

    def boom(_seconds):
        raise viewer.WDAError("settings refused")

    client.set_wait_for_idle = boom
    try:
        assert requests.post(base_url + "/api/tap", json={"x": 5, "y": 6}, timeout=5).ok
    finally:
        del client.set_wait_for_idle
    assert ("tap", 5.0, 6.0) in client.calls


def test_a_failed_idle_wait_set_is_not_restored(base_url):
    # Nothing was changed, so there is nothing to restore — and on a WEDGED
    # link every one of these calls costs the client's full 10s timeout inside
    # the _ACTION_LOCK, where every other gesture is 409-dropped. Three of them
    # around one human tap is 30s of a dark viewer.
    client = viewer.Handler.client
    tries = []

    def boom(seconds):
        tries.append(seconds)
        raise viewer.WDAError("WDA did not answer")

    client.set_wait_for_idle = boom
    try:
        assert requests.post(base_url + "/api/tap", json={"x": 7, "y": 8}, timeout=5).ok
    finally:
        del client.set_wait_for_idle
    assert tries == [0], f"the restore ran after the set failed: {tries}"


def test_human_gesture_restores_the_idle_wait_when_the_gesture_raises(base_url):
    # The setting rides the ONE shared WDA session across processes, so a
    # gesture that raises inside the window would leave waitForIdleTimeout at 0
    # for the MCP agent too, until the next viewer gesture happened to restore
    # it. _enter_passcode has the same test for the same reason.
    client = viewer.Handler.client
    client.idle_waits = []

    def boom(_x, _y):
        raise RuntimeError("gesture blew up")

    client.tap = boom
    try:
        r = requests.post(base_url + "/api/tap", json={"x": 1, "y": 2}, timeout=5)
    finally:
        del client.tap
    assert r.status_code == 500, r.status_code
    assert client.idle_waits == [0, viewer.config.WDA_IDLE_WAIT], (
        f"the idle wait was left at 0 for every other process: {client.idle_waits}"
    )


def test_cross_origin_post_cannot_smuggle_a_second_request(base_url):
    # The sibling test above trips the Content-Type guard. A REAL cross-origin
    # fetch is well formed and is rejected by _allowed() instead, so that guard
    # is the one that matters — and it answers without reading the body too.
    import socket

    port = int(base_url.rsplit(":", 1)[1])
    smuggled = (
        f"POST /api/tap HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        "Content-Type: application/json\r\nContent-Length: 15\r\n\r\n"
        '{"x":10,"y":10}'
    ).encode()
    attack = (
        f"POST /api/tap HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        "Origin: https://evil.example\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(smuggled)}\r\n\r\n"
    ).encode() + smuggled
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(attack)
        head, _body = _read_response(s)
        rest = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            rest += chunk
    assert head.startswith(b"HTTP/1.1 403"), head
    assert rest == b"", f"the smuggled request was answered: {rest!r}"
    assert not [c for c in viewer.Handler.client.calls if c[0] == "tap"]


def test_a_chunked_post_cannot_smuggle_a_second_request(base_url):
    # A chunked body carries no Content-Length, so the handler's read skips it
    # and leaves it on the wire as the next request. Reproduced before the
    # guard: a 500 with no Connection: close, immediately followed by
    # "HTTP/1.1 400 Bad request syntax" — the leftover body, parsed.
    import socket

    port = int(base_url.rsplit(":", 1)[1])
    attack = (
        f"POST /api/tap HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
        "f\r\n"
        '{"x":10,"y":10}\r\n'
        "0\r\n\r\n"
    ).encode()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(attack)
        head, _body = _read_response(s)
        rest = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            rest += chunk
    assert head.startswith(b"HTTP/1.1 403"), head
    assert rest == b"", f"the leftover body was parsed as a request: {rest!r}"
    assert not [c for c in viewer.Handler.client.calls if c[0] == "tap"]


def test_phone_glance_refreshes_after_actions_but_not_on_typing():
    # VIEWER_PHONE_POLL_SECONDS ships at 0 (docs/VIEWER_PHONE_POLL.md: the 10s
    # /api/phone poll could trigger the WDA heavy-tail wedge with nobody
    # driving), so the glance it paints — battery, lock state, front app, and
    # the "Go to page" chips, which are an ACTION control, not passive info —
    # was painted once at page load and never again. The refresh is
    # event-driven instead, and every assertion here is one of the conditions
    # that makes that safe to re-add.
    import re

    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")

    m = re.search(r"PHONE_REFRESH_DEBOUNCE_MS\s*=\s*(\d+)", html)
    assert m, "no PHONE_REFRESH_DEBOUNCE_MS constant in viewer.html"
    assert int(m.group(1)) >= 1000, (
        "the debounce is what keeps a 30-flick scroll at one /api/phone read "
        "instead of thirty; a short value re-creates a per-gesture read rate "
        "on exactly the screens where the wedge was measured"
    )

    sched = _js_function_body(html, "function schedulePhoneRefresh() {")
    assert "busyLabel" in sched, (
        "/api/phone serves its cached _LAST_PHONE while _ACTION_LOCK is held, "
        "so a refresh that lands mid-action renders pre-action state and never "
        "retries — it has to re-arm while the pane is busy"
    )

    for signature in (
        "async function postGesture(",
        "async function gesturePost(",
        "document.getElementById('btn-unlock').onclick",
        "document.getElementById('btn-lock').onclick",
    ):
        body = _js_function_body(html, signature)
        assert "schedulePhoneRefresh(" in body, (
            f"{signature} changes what the glance shows but never refreshes it"
        )

    keys = _js_function_body(html, "async function flushKeys() {")
    assert "schedulePhoneRefresh(" not in keys, (
        "typing is the highest-frequency path in the file and cannot change "
        "battery, lock state, front app or page — hooking it is the read rate "
        "the debounce exists to avoid"
    )

    assert html.count("setInterval(loadPhone") == 2, (
        "exactly two setInterval(loadPhone) sites are expected (the "
        "config-gated one and the /api/config-failure fallback); an idle "
        "periodic poll must not come back alongside the event-driven refresh"
    )


def test_hotkeys_sit_above_the_focus_guard_and_skip_ctrl_v():
    # Ctrl+Shift+S copies the screen PNG to the PC clipboard — the one thing
    # the page could not do at all (btn-shot only downloads a file).
    html = (Path(viewer.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    assert "ClipboardItem" in html and "'image/png'" in html
    # Firefox has no clipboard.write for images, so the failure path falls back
    # to the download button that already exists rather than throwing.
    assert "btn-shot').click()" in html

    start = html.index("window.addEventListener('keydown'")
    body = html[start : html.index("const JSON_HDR", start)]
    hotkeys = body.index("ev.shiftKey")
    # Only the four GESTURES buttons blur themselves, so every other button
    # keeps focus after a mouse click and a map placed BELOW the focus guard is
    # silently dead until you click the phone — which sends a TAP. The map also
    # sits above `if (!inputEnabled) return;` because /api/screenshot falls back
    # to `ios screenshot` and works with the link down.
    assert hotkeys < body.index("input,textarea,button,select,[tabindex]")
    assert hotkeys < body.index("if (!inputEnabled) return;")
    map_body = body[hotkeys : body.index("input,textarea,button,select,[tabindex]")]
    # The two phone-driving hotkeys re-check inputEnabled themselves, since the
    # map now sits above the global guard.
    assert map_body.count("if (inputEnabled)") == 2
    # A held combo auto-repeats keydown. Ctrl+Shift+B reaches g-back -> an edge
    # swipe, which never sets busyLabel, so phoneBusy() cannot swallow repeats
    # and a held key would send several real Back swipes to the phone.
    assert "ev.repeat" in map_body
    # g-back's "(Ctrl+Shift+B)" hint is the only place the combo is advertised,
    # and updateActionAvail rewrites that title on every 5s status tick, so it
    # must restore the markup's own tooltip instead of blanking it.
    assert "(Ctrl+Shift+B)" in html
    assert "el.title = tip || el.dataset.tip" in html

    # Ctrl+V is NOT in the map: the window paste listener already handles it
    # with the same guards, and a keydown preventDefault there would suppress
    # the paste event and kill the working path.
    assert "'v'" not in map_body and "'V'" not in map_body
    assert "window.addEventListener('paste'" in html
    # Not Ctrl+Shift+C either: that is DevTools inspect-element in Chrome/Edge
    # and a page cannot cancel it.
    assert "'c'" not in map_body and "'C'" not in map_body
    # Ctrl+C keeps meaning "copy my selection" inside the paste box, so its
    # special case must stay BELOW the focus guard.
    assert body.index("btn-clip-from-phone") > body.index(
        "input,textarea,button,select,[tabindex]"
    )


# ---- Pull photos job ---------------------------------------------------------


def test_pull_photos_endpoint_runs_in_background(base_url, monkeypatch):
    """POST starts the pull off the request thread and GET polls it; the job
    must never take _ACTION_LOCK (it is AFC over USB, not a gesture), so a
    slow pull cannot 409-drop the human's taps."""
    from phone_harness import photos

    release = threading.Event()

    def slow_pull(dest=None, progress=photos._noop):
        progress("100APPLE/IMG_0001.JPG")
        release.wait(timeout=10)
        return {"ok": True, "pulled": 2, "skipped": 5, "dest": "X", "errors": []}

    monkeypatch.setattr(photos, "pull_photos", slow_pull)
    with viewer._PHOTO_LOCK:
        viewer._PHOTO_JOB.update(
            running=False, message="", ok=None, pulled=0, skipped=0, dest=""
        )

    j = requests.post(base_url + "/api/pull-photos", json={}, timeout=5).json()
    assert j["running"] is True
    # The POST returned while the pull is still going — that IS the feature.
    # A human gesture lands fine in the middle of it.
    assert not viewer._ACTION_LOCK.locked()
    j = requests.get(base_url + "/api/pull-photos", timeout=5).json()
    assert j["running"] is True and "IMG_0001" in j["message"]
    release.set()
    for _ in range(100):
        j = requests.get(base_url + "/api/pull-photos", timeout=5).json()
        if not j["running"]:
            break
        time.sleep(0.05)
    assert j["running"] is False and j["ok"] is True
    assert j["pulled"] == 2 and j["skipped"] == 5 and j["dest"] == "X"


def test_pull_photos_second_post_joins_the_running_job(base_url, monkeypatch):
    from phone_harness import photos

    release = threading.Event()
    starts = []

    def slow_pull(dest=None, progress=photos._noop):
        starts.append(1)
        release.wait(timeout=10)
        return {"ok": True, "pulled": 0, "skipped": 0, "dest": "X", "errors": []}

    monkeypatch.setattr(photos, "pull_photos", slow_pull)
    with viewer._PHOTO_LOCK:
        viewer._PHOTO_JOB.update(
            running=False, message="", ok=None, pulled=0, skipped=0, dest=""
        )
    requests.post(base_url + "/api/pull-photos", json={}, timeout=5)
    requests.post(base_url + "/api/pull-photos", json={}, timeout=5)
    release.set()
    for _ in range(100):
        if not requests.get(base_url + "/api/pull-photos", timeout=5).json()["running"]:
            break
        time.sleep(0.05)
    assert len(starts) == 1


def test_photos_worker_never_stays_running_on_a_crash(monkeypatch):
    """Same liveness rule as _fix_input_worker: running=False is the page's
    only signal, so an uncaught exception must still flip it."""
    from phone_harness import photos

    def explode(dest=None, progress=None):
        raise RuntimeError("usbmux fell over")

    monkeypatch.setattr(photos, "pull_photos", explode)
    with viewer._PHOTO_LOCK:
        viewer._PHOTO_JOB.update(running=True, message="", ok=None)
    viewer._photos_worker()
    with viewer._PHOTO_LOCK:
        job = dict(viewer._PHOTO_JOB)
    assert job["running"] is False and job["ok"] is False
    assert "usbmux fell over" in job["message"]
