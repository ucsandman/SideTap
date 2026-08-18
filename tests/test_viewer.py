"""Viewer HTTP origin-guard tests. No phone; loopback only."""

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

    def orientation(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        # Raises on a dead link exactly like the real session request, which is
        # the whole point of it: /api/status serves a memoised window size and
        # this is the only thing left that can still notice WDA is gone.
        self.orientation_calls += 1
        if self.window is None:
            raise viewer.WDAError("no phone in tests")
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
    # window_size() is cached per session id (T2b); every test gets a fresh
    # StubClient whose session_id defaults to the same "stub-session" string,
    # so a stale cache from a previous test would otherwise be served here
    # without ever calling window_size() again.
    viewer._WINDOW_SESSION = None  # noqa: vulture  (viewer.py reads these)
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
        viewer._WINDOW_SESSION = None  # noqa: vulture  (viewer.py reads these)
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


def test_window_size_is_cached_per_session(base_url, monkeypatch):
    # window_size() is a 201ms device constant; /api/status polls every 5s and
    # must not re-fetch it unless the session actually changed.
    monkeypatch.setattr(viewer, "_WINDOW_SESSION", None)
    monkeypatch.setattr(viewer, "_WINDOW_SIZE", None)
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
    assert client.window_size_calls == 2  # session change forced a refetch


def test_status_reports_input_down_after_wda_dies_behind_the_cache(
    base_url, monkeypatch
):
    # The memo removed the ONLY WDA request /api/status made, so a cache hit
    # answered "input": True over a dead link forever. Deep sleep kills WDA
    # ~15min after the screen darkens, and viewer.html HIDES btn-up ("Restart
    # link") and btn-fix while input is true — so the lie also removed the two
    # buttons that fix it. orientation() is the 7.7ms liveness probe that keeps
    # the cached path honest.
    monkeypatch.setattr(viewer, "_WINDOW_SESSION", None)
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
    monkeypatch.setattr(viewer, "_WINDOW_SESSION", None)
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


def _run_heal_loop(monkeypatch, *, answers, clock):
    """Drive _heal_loop over scripted (is_up, monotonic) values. Returns the
    number of admin.up() calls; the loop ends when the fakes run out."""
    monkeypatch.setitem(viewer._HEAL, "down_since", 0.0)
    monkeypatch.setitem(viewer._HEAL, "cooldown_until", 0.0)
    monkeypatch.setattr(viewer.time, "sleep", lambda _s: None)
    monkeypatch.setattr(viewer.device, "lockdown_ready", lambda: True)
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
