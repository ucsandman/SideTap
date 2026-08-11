"""The send-approval handshake between the agent process and the viewer.
Filesystem only, no phone and no HTTP server needed."""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import approval, config  # noqa: E402


# Autouse: every test in this file needs .state relocated off the real repo.
# vulture flags autouse fixtures as dead code; it cannot see pytest calling them.
@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)


def _answer_with(decision, seen=None):
    """Play the viewer: wait for the card, then click it."""

    def watcher():
        for _ in range(500):
            rec = approval.pending()
            if rec:
                if seen is not None:
                    seen.update(rec)
                approval.decide(rec["id"], decision)
                return
            time.sleep(0.01)

    thread = threading.Thread(target=watcher)
    thread.start()
    return thread


def test_request_times_out_and_denies_by_default():
    assert approval.request("Mom", "hi", [], "screen", timeout=0) == "timeout"


def test_request_leaves_no_state_behind():
    approval.request("Mom", "hi", [], "screen", timeout=0)
    assert not approval.pending_file().exists()
    assert not approval.decision_file().exists()


def test_pending_shows_what_the_human_must_judge():
    seen = {}
    thread = _answer_with("approve", seen)
    result = approval.request(
        "Mom", "on my way", ["instruction override"], "read_messages", timeout=5
    )
    thread.join()
    assert result == "approve"
    assert seen["contact"] == "Mom"
    assert seen["text"] == "on my way"
    assert seen["flags"] == ["instruction override"]
    assert seen["taint_source"] == "read_messages"


def test_deny_is_reported():
    thread = _answer_with("deny")
    assert approval.request("Mom", "hi", [], "screen", timeout=5) == "deny"
    thread.join()


def test_anything_that_is_not_approve_denies():
    """A malformed decision must never be read as consent."""
    thread = _answer_with("maybe")
    assert approval.request("Mom", "hi", [], "screen", timeout=5) == "deny"
    thread.join()


def test_a_second_request_while_one_is_pending_is_busy():
    approval.pending_file().parent.mkdir(exist_ok=True)
    approval.pending_file().write_text('{"id": "other"}', encoding="utf-8")
    assert approval.request("Mom", "hi", [], "screen", timeout=0) == "busy"
    # the other request's record must survive
    assert approval.pending_file().exists()


def test_decide_ignores_a_stale_id():
    approval.pending_file().parent.mkdir(exist_ok=True)
    approval.pending_file().write_text('{"id": "current"}', encoding="utf-8")
    assert approval.decide("stale", "approve") is False
    assert not approval.decision_file().exists()


def test_a_decision_for_another_request_is_not_accepted():
    """A leftover decision file from an earlier send must not auto-approve."""
    config.STATE_DIR.mkdir(exist_ok=True)
    approval.decision_file().write_text(
        '{"id": "old", "decision": "approve"}', encoding="utf-8"
    )
    assert approval.request("Mom", "hi", [], "screen", timeout=0) == "timeout"
