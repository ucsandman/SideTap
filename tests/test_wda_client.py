"""WDA client tests against a mocked WebDriverAgent HTTP server."""

import base64
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import config  # noqa: E402
from phone_harness import wda_client  # noqa: E402
from phone_harness.wda_client import (  # noqa: E402
    WDAClient,
    WDAError,
    _activity_summary,
    activity_file,
)

FAKE_PNG = b"\x89PNG\r\n\x1a\nfakedata"


class FakeWDA(BaseHTTPRequestHandler):
    """Minimal WDA imitation. Counts requests; can kill sessions."""

    requests_seen = []
    kill_next_session = False
    session_counter = 0
    last_settings = None
    valid_sessions = set()
    infinity_sessions = set()  # sessions whose /actions fail with INFINITY
    hanging_sessions = set()  # sessions whose /actions hang past the timeout
    hang_seconds = 1.0

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
            sid = f"sess-{FakeWDA.session_counter}"
            FakeWDA.valid_sessions = {sid}  # real WDA: a new session evicts the old
            self._reply({"sessionId": sid})
        elif self.path.endswith("/actions") or self.path.endswith("/wda/keys"):
            if self._session_dead():
                return
            sid = self.path.split("/")[2]
            if sid in FakeWDA.hanging_sessions:
                # XCTest's snapshot timeout: the real one blocks ~16s before
                # it answers at all (measured on device 2026-08-14).
                time.sleep(FakeWDA.hang_seconds)
            if sid in FakeWDA.infinity_sessions:
                self._reply(
                    {
                        "error": "unknown error",
                        "message": "Invalid parameter not satisfying: "
                        "point.x != INFINITY && point.y != INFINITY",
                    },
                    500,
                )
                return
            self._reply(None)
        elif self.path.endswith("/wda/apps/launch"):
            self._reply(None)
        elif self.path.endswith("/wda/setPasteboard"):
            if self._session_dead():
                return
            FakeWDA.pasteboard = self.payload.get("content", "")
            self._reply(None)
        elif self.path.endswith("/wda/getPasteboard"):
            if self._session_dead():
                return
            self._reply(getattr(FakeWDA, "pasteboard", ""))
        elif self.path.endswith("/appium/settings"):
            FakeWDA.last_settings = self.payload.get("settings")
            self._reply(None)
        elif self.path in ("/wda/homescreen", "/wda/lock"):
            self._reply(None)
        else:
            self._reply({"error": "unknown command", "message": self.path}, 404)

    def _session_dead(self):
        if FakeWDA.kill_next_session:
            FakeWDA.kill_next_session = False
            self._reply({"error": "invalid session id", "message": "session gone"}, 404)
            return True
        sid = self.path.split("/")[2] if self.path.startswith("/session/") else None
        if sid and sid not in FakeWDA.valid_sessions:
            self._reply({"error": "invalid session id", "message": "session gone"}, 404)
            return True
        return False


@pytest.fixture()
def wda(tmp_path, monkeypatch):
    # Point .state at tmp so activity/STOP writes never touch the real repo.
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWDA)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeWDA.requests_seen = []
    FakeWDA.kill_next_session = False
    FakeWDA.session_counter = 0
    FakeWDA.valid_sessions = set()
    FakeWDA.infinity_sessions = set()
    FakeWDA.hanging_sessions = set()
    client = WDAClient(base_url=f"http://127.0.0.1:{server.server_port}", timeout=5)
    yield client
    server.shutdown()


def test_status(wda):
    assert wda.status()["ready"] is True
    assert wda.is_up()


def test_screenshot_decodes_base64(wda):
    assert wda.screenshot() == FAKE_PNG


def test_session_created_once_and_reused(wda):
    # Back-to-back gestures share one session: WDA holds exactly one, and
    # minting per call would evict whoever else is driving the phone. The
    # deliberate exception is a gesture that follows a long idle gap — see
    # test_first_gesture_after_an_idle_gap_mints_before_acting.
    wda.tap(1, 1)
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


# WDA holds exactly ONE session: a client creating its own steals it from
# whoever had it (viewer click garbles an agent mid-send_message). All clients
# therefore share the active session id through .state/wda_session.


def test_second_client_adopts_shared_session(wda):
    wda.window_size()  # creates sess-1, publishes it
    other = WDAClient(base_url=wda.base_url, timeout=5)
    other.window_size()
    assert other.session_id == wda.session_id
    assert FakeWDA.session_counter == 1  # adopted, not stolen


def test_stale_shared_session_file_recovers(wda):
    (config.STATE_DIR / "wda_session").write_text("sess-gone", encoding="utf-8")
    assert wda.window_size() == (390.0, 844.0)  # invalid -> creates fresh
    assert FakeWDA.session_counter == 1
    assert (config.STATE_DIR / "wda_session").read_text(
        encoding="utf-8"
    ).strip() == wda.session_id


def test_infinity_frame_error_heals_like_dead_session(wda):
    # Seen live 2026-08-09: a session that crossed a screen lock keeps
    # answering perception GETs but fails every /actions with "point.x !=
    # INFINITY" — alive but unusable, forever. The shared-session model
    # preserves such a session faithfully, so recovery must treat the
    # INFINITY error exactly like a dead session: recreate and retry once.
    wda.window_size()  # sess-1
    FakeWDA.infinity_sessions = {"sess-1"}
    wda.tap(10, 20)  # must heal to sess-2 and succeed, not raise
    assert wda.session_id == "sess-2"
    assert FakeWDA.session_counter == 2


def test_actions_timeout_replaces_the_session_but_never_replays(wda):
    # The heal above only fires once the INFINITY error ARRIVES, and the real
    # poisoned session hangs ~16s inside XCTest's snapshot timeout before it
    # answers. Any client with a shorter timeout — the viewer's is 10s
    # (viewer.py Handler.client) — gives up first, so it never sees the error
    # that identifies the session and reuses the same dead id forever:
    # reproduced against a hanging fake WDA, every later gesture timed out
    # again at 10s (2026-08-14).
    #
    # The gesture itself is NEVER retried here. A timeout does not cancel it:
    # the swipe may still land on the phone, and replaying it double-taps.
    wda.tap(1, 1)  # sess-1, and stamps the activity log
    FakeWDA.hanging_sessions = {"sess-1"}
    wda.timeout = 0.3
    FakeWDA.requests_seen = []
    with pytest.raises(WDAError):
        wda.tap(10, 20)
    posts = [p for m, p in FakeWDA.requests_seen if m == "POST"]
    assert posts.count("/session/sess-1/actions") == 1, (
        "a timed-out gesture must never be replayed"
    )

    # The phone recovers; the NEXT gesture must not go to the suspect session.
    FakeWDA.hanging_sessions = set()
    wda.timeout = 5
    FakeWDA.requests_seen = []
    wda.tap(10, 20)
    posts = [p for m, p in FakeWDA.requests_seen if m == "POST"]
    assert "/session" in posts, "a session that timed out mid-gesture is suspect"
    assert not any(p.startswith("/session/sess-1/") for p in posts)
    assert wda.session_id == "sess-2"


def test_a_session_replaced_after_the_timeout_is_adopted_not_evicted(wda):
    # unlock() mints a fresh session of its own, and the everyday sequence is
    # gesture times out -> human clicks Unlock -> human taps again. Minting a
    # SECOND session there would evict the one unlock just made (and retune the
    # viewer's stream) for nothing, so prefer the published id like every other
    # recovery in this class does.
    wda.tap(1, 1)  # sess-1, and stamps the activity log
    FakeWDA.hanging_sessions = {"sess-1"}
    wda.timeout = 0.3
    with pytest.raises(WDAError):
        wda.tap(10, 20)
    # sess-1 stays VALID and still hanging: reusing it would succeed eventually
    # (that is the 16s on the phone), so only the timing and the adopted id
    # tell the two behaviours apart.
    wda.timeout = 5
    (config.STATE_DIR / "wda_session").write_text("sess-9", encoding="utf-8")
    FakeWDA.valid_sessions = {"sess-1", "sess-9"}  # someone else replaced it
    FakeWDA.requests_seen = []
    start = time.perf_counter()
    wda.tap(10, 20)
    assert time.perf_counter() - start < FakeWDA.hang_seconds, (
        "the suspect session was used again — on the phone that is the 16s hang"
    )
    assert wda.session_id == "sess-9"
    assert "/session" not in [p for m, p in FakeWDA.requests_seen if m == "POST"]


def test_first_gesture_after_an_idle_gap_mints_before_acting(wda):
    # Measured on device 2026-08-14: after 16 minutes asleep, the first
    # /actions on the pre-sleep session hung 16.25s before failing point.x !=
    # INFINITY, while a fresh session took 0.01s and its swipe 1.18s. Nothing
    # cheap distinguishes the two, so a gesture that follows an idle gap long
    # enough for the display to have slept mints first instead of paying it.
    wda.tap(1, 1)  # sess-1, and stamps the gesture clock
    # A REAL gap, not one derived from the threshold — 2 minutes is the gap in
    # the live incident (11:01:59 lock, 11:02:20 the 17.58s swipe). Deriving it
    # from _SLEEP_SUSPECT_SECONDS would make this test move with the constant
    # and pass at any value, including "never".
    wda._last_gesture_ok -= 120  # nothing has landed since
    FakeWDA.requests_seen = []
    wda.tap(2, 2)
    assert wda.session_id == "sess-2"
    posts = [p for m, p in FakeWDA.requests_seen if m == "POST"]
    assert posts[0] == "/session", "the mint must come BEFORE the gesture"
    assert "/session/sess-2/actions" in posts


def test_a_gesture_while_the_phone_is_being_driven_does_not_mint(wda):
    # The phone cannot sleep while it is being driven, so back-to-back gestures
    # must not churn the one session WDA allows.
    wda.tap(1, 1)  # sess-1
    FakeWDA.requests_seen = []
    wda.tap(2, 2)
    wda.tap(3, 3)
    assert wda.session_id == "sess-1"
    assert "/session" not in [p for m, p in FakeWDA.requests_seen if m == "POST"]


def test_a_wake_press_does_not_count_as_a_landed_gesture(wda):
    # The call that WAKES the display is a POST too, and keying the clock on
    # "any action" let that wake refresh it — so the rule never fired on the
    # one sequence it exists for. Measured live 2026-08-14: press_button("home")
    # answered in 0.47s on the very session whose next gesture then hung 16.25s,
    # and the fix sat there doing nothing because the wake had reset the clock.
    wda.tap(1, 1)  # sess-1
    wda._last_gesture_ok -= 120  # ...and then the phone slept
    wda.home()  # the wake: a POST, but not a gesture
    FakeWDA.requests_seen = []
    wda.tap(2, 2)
    assert wda.session_id == "sess-2", "a wake must not vouch for the session"
    assert "/session" in [p for m, p in FakeWDA.requests_seen if m == "POST"]


def test_a_read_after_an_idle_gap_does_not_mint(wda):
    # Reads work fine on a poisoned session; re-minting for them would evict
    # whoever holds the session every time a poll goes quiet.
    wda.tap(1, 1)  # sess-1
    wda._last_gesture_ok -= 120
    assert wda.window_size() == (390.0, 844.0)
    assert wda.session_id == "sess-1"


def test_perception_survives_an_actions_timeout(wda):
    # Only /actions is suspect. A poisoned session still answers GETs, and
    # re-minting on every read would churn the one session WDA allows (and
    # retune the viewer's stream) for a screen read that works fine.
    wda.tap(1, 1)  # sess-1, and stamps the activity log
    FakeWDA.hanging_sessions = {"sess-1"}
    wda.timeout = 0.3
    with pytest.raises(WDAError):
        wda.tap(10, 20)
    FakeWDA.hanging_sessions = set()
    wda.timeout = 5
    assert wda.window_size() == (390.0, 844.0)
    assert wda.session_id == "sess-1", "a read must not evict the session"


def test_both_current_and_shared_dead_creates_fresh(wda):
    # Seen live: a process on older code churned sessions, so the in-memory id
    # AND the published id were both dead. Recovery must still reach a fresh
    # session instead of raising.
    wda.window_size()  # sess-1
    FakeWDA.valid_sessions = set()  # everything dead
    (config.STATE_DIR / "wda_session").write_text("sess-ghost", encoding="utf-8")
    assert wda.window_size() == (390.0, 844.0)
    assert wda.session_id == "sess-2"
    assert FakeWDA.session_counter == 2


def test_invalid_session_adopts_replacement_from_file(wda):
    import requests as _requests

    wda.window_size()  # sess-1
    # Another process replaces the session (sess-2 evicts sess-1) and
    # publishes the new id — exactly what its own recovery path would do.
    r = _requests.post(f"{wda.base_url}/session", json={}, timeout=5)
    new_sid = r.json()["value"]["sessionId"]
    (config.STATE_DIR / "wda_session").write_text(new_sid, encoding="utf-8")
    assert wda.window_size() == (390.0, 844.0)
    assert wda.session_id == new_sid  # adopted the replacement
    assert FakeWDA.session_counter == 2  # did not mint a third


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

    # The client owns a persistent Session, so patch the method it actually
    # calls; patching the module-level requests.request no longer intercepts it.
    monkeypatch.setattr(requests.Session, "request", slow)
    client = WDAClient(base_url="http://127.0.0.1:1", timeout=1)
    with pytest.raises(WDAError, match="did not answer"):
        client.status()
    assert client.is_up() is False


def test_link_state_tells_wedged_from_down(monkeypatch, wda):
    # Three states, three repairs. A WEDGED WDA accepts the socket and never
    # answers (the app in front is not answering accessibility requests) and is
    # cleared by putting the Home Screen back in front; a DOWN one refuses the
    # connection and needs a restart. Restarting a wedged one fails with XCTest
    # error 103, which reads as an expired signature (issue #2).
    import requests

    assert wda.link_state() == "up"

    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("hang")),
    )
    assert wda.link_state() == "wedged"
    assert wda.is_up() is False

    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda *a, **k: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("no")
        ),
    )
    assert wda.link_state() == "down"
    assert wda.is_up() is False


def test_timeout_message_names_the_foreground_app(monkeypatch):
    # The old text ("busy or wedged; try again") sent a reporter into a full
    # Sideloadly re-sign. Name the cause and the repair that actually works.
    import requests

    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("hang")),
    )
    client = WDAClient(base_url="http://127.0.0.1:1", timeout=1)
    with pytest.raises(WDAError) as exc:
        client.status()
    assert "accessibility" in str(exc.value)
    assert "phone-harness up" in str(exc.value)


def test_lock_posts_wda_lock(wda):
    wda.lock()
    assert ("POST", "/wda/lock") in FakeWDA.requests_seen


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


def test_new_session_applies_standard_action_waits(wda):
    # Measured on device: animationCoolOffTimeout=2 (WDA default) is what made
    # every swipe cost 2-5s under scroll momentum; with it at 0, idle waits of
    # 0/1/2s all measure ~0.7s per swipe. idle=2 keeps settle protection for
    # agents on genuinely busy screens at no measured cost. The session CREATOR
    # applies the settings; adopters inherit them with the session.
    wda.window_size()
    assert FakeWDA.last_settings == {
        "waitForIdleTimeout": config.WDA_IDLE_WAIT,
        "animationCoolOffTimeout": config.WDA_ANIM_COOLOFF,
        "accessibilityDeadline": config.WDA_ACCESSIBILITY_DEADLINE,
    }
    assert config.WDA_IDLE_WAIT == 2.0
    assert config.WDA_ANIM_COOLOFF == 0.0
    assert config.WDA_ACCESSIBILITY_DEADLINE == 2.0


def test_adopting_client_does_not_retune(wda):
    wda.window_size()  # creator tunes
    FakeWDA.last_settings = None
    other = WDAClient(base_url=wda.base_url, timeout=5)
    other.window_size()  # adopts the shared session
    assert FakeWDA.last_settings is None


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


# ---- activity feed ----------------------------------------------------------


def _feed_lines(tmp_path):
    text = (tmp_path / "agent_activity.log").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_actions_land_in_activity_feed(wda, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    wda.tap(10, 20)
    wda.type_text("secret")
    actions = [r["action"] for r in _feed_lines(tmp_path)]
    assert actions == ["tap (10, 20)", "type (6 chars)"]


def test_activity_feed_says_one_char_not_one_chars(wda, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    wda.type_text("x")
    assert [r["action"] for r in _feed_lines(tmp_path)] == ["type (1 char)"]


def test_activity_feed_never_records_typed_text(wda, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    wda.type_text("hunter2")
    assert "hunter2" not in activity_file().read_text(encoding="utf-8")


def test_blocked_actions_stay_out_of_the_feed(wda, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    (tmp_path / "STOP").touch()
    with pytest.raises(WDAError):
        wda.tap(1, 1)
    assert not activity_file().exists()


def test_redact_actions_hides_gesture_coordinates(wda, tmp_path, monkeypatch):
    """Pad-tap coordinates spell out the passcode digit by digit — inside
    redact_actions the feed shows the label, never the tap itself. Redaction
    must also END with the block: a leaked flag would blind the whole feed."""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    with wda_client.redact_actions("passcode entry"):
        wda.tap(123, 456)
    wda.tap(10, 20)
    # Scan every field EXCEPT "ts": the epoch is ~10 digits of clock, and it
    # contains "123" or "456" often enough to fail this test at random (it did,
    # 2026-08-14). Dropping only the timestamp keeps a future new field covered.
    records = _feed_lines(tmp_path)
    leaked = json.dumps([{k: v for k, v in r.items() if k != "ts"} for r in records])
    assert "123" not in leaked and "456" not in leaked
    actions = [r["action"] for r in records]
    assert actions == ["passcode entry", "tap (10, 20)"]


def test_activity_summary_swipe_and_long_press():
    def gesture(steps):
        return {
            "actions": [
                {"type": "pointer", "id": "finger1", "actions": steps},
            ]
        }

    swipe = gesture(
        [
            {"type": "pointerMove", "duration": 0, "x": 100, "y": 800},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": 40},
            {"type": "pointerMove", "duration": 300, "x": 100, "y": 200},
            {"type": "pointerUp", "button": 0},
        ]
    )
    assert _activity_summary("/session/s/actions", swipe) == (
        "swipe (100, 800) → (100, 200)"
    )
    press = gesture(
        [
            {"type": "pointerMove", "duration": 0, "x": 50, "y": 60},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": 1000},
            {"type": "pointerUp", "button": 0},
        ]
    )
    assert _activity_summary("/session/s/actions", press) == "long-press (50, 60)"


def test_activity_summary_skips_stream_tuning():
    assert _activity_summary("/session/s/appium/settings", {"settings": {}}) is None


def test_activity_feed_stays_bounded(wda, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(wda_client, "_ACTIVITY_MAX_BYTES", 500)
    monkeypatch.setattr(wda_client, "_ACTIVITY_KEEP_LINES", 5)
    for _ in range(50):
        wda.tap(1, 2)
    assert len(_feed_lines(tmp_path)) <= 6  # 5 kept + at most 1 fresh append
    assert activity_file().stat().st_size < 1000


def test_client_does_not_build_a_session_per_request(wda, monkeypatch):
    # requests.request() builds a throwaway Session (and connection pool) per
    # call, so no TCP keep-alive to WDA ever survives. Every helper funnels
    # through _request, so this is paid on every tap, read and screenshot.
    import requests

    built = []
    real_init = requests.Session.__init__

    def counting_init(self, *a, **kw):
        built.append(self)
        return real_init(self, *a, **kw)

    monkeypatch.setattr(requests.Session, "__init__", counting_init)
    client = WDAClient(base_url=wda.base_url, timeout=5)
    client.status()
    client.status()
    client.status()

    assert len(built) == 1, (
        f"3 calls built {len(built)} Sessions; one per call means the "
        "connection to WDA is never reused"
    )


def test_clipboard_set_and_get(wda):
    wda.set_clipboard("Hello from clipboard test!")
    assert wda.get_clipboard() == "Hello from clipboard test!"


def test_clipboard_empty(wda):
    FakeWDA.pasteboard = ""
    assert wda.get_clipboard() == ""


def test_activity_summary_clipboard():
    summary = _activity_summary(
        "/session/s/wda/setPasteboard",
        {"content": "SGVsbG8=", "contentType": "plaintext"},
    )
    assert summary == "set clipboard (8 b64 chars)"
    assert "Hello" not in summary


def test_activity_summary_key_press():
    summary = _activity_summary(
        "/session/s/actions",
        {
            "actions": [
                {
                    "type": "key",
                    "id": "keyboard1",
                    "actions": [
                        {"type": "keyDown", "value": "\ue017"},
                        {"type": "keyUp", "value": "\ue017"},
                    ],
                }
            ]
        },
    )
    assert summary == "key press"
