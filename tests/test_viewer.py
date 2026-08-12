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

    def window_size(self):  # noqa: vulture  (duck-typed stand-in for WDAClient)
        if self.window is None:
            raise viewer.WDAError("no phone in tests")
        return self.window

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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        viewer.Handler.client = original
        helpers._client = original_helpers_client
        helpers._invalidate_tree()


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
    }


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


def test_parse_console_accepts_literal_call():
    name, args, kwargs = viewer._parse_console('tap_text("General", exact=True)')
    assert (name, args, kwargs) == ("tap_text", ["General"], {"exact": True})
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
    assert heal(100.0, wda_up=False, lockdown_ok=True, stopped=False)
    assert not heal(100.0, wda_up=True, lockdown_ok=True, stopped=False)
    assert not heal(
        100.0, wda_up=False, lockdown_ok=False, stopped=False
    )  # still asleep
    assert not heal(100.0, wda_up=False, lockdown_ok=True, stopped=True)  # kill switch


def test_should_heal_honors_cooldown(monkeypatch):
    monkeypatch.setitem(viewer._HEAL, "cooldown_until", 500.0)
    assert not viewer._should_heal(499.0, wda_up=False, lockdown_ok=True, stopped=False)
    assert viewer._should_heal(500.0, wda_up=False, lockdown_ok=True, stopped=False)


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
