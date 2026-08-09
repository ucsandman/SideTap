"""Pure-function tests: tree walking and .env parsing. No phone needed."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import config, helpers  # noqa: E402
from phone_harness.config import _load_env  # noqa: E402
from phone_harness.helpers import (  # noqa: E402
    _ambiguous_hits,
    _passcode_pad_visible,
    _thread_title,
    collect_texts,
)
from phone_harness.wda_client import WDAError  # noqa: E402

SAMPLE_TREE = {
    "type": "Application",
    "label": "",
    "rect": {"x": 0, "y": 0, "width": 390, "height": 844},
    "children": [
        {
            "type": "Button",
            "label": "General",
            "isVisible": "1",
            "rect": {"x": 20, "y": 100, "width": 350, "height": 44},
            "children": [],
        },
        {
            "type": "Button",
            "label": "Hidden Thing",
            "isVisible": "0",
            "rect": {"x": 20, "y": 200, "width": 350, "height": 44},
        },
        {
            "type": "StaticText",
            "label": "",
            "name": "Bluetooth",
            "isVisible": "1",
            "rect": {"x": 20, "y": 300, "width": 350, "height": 44},
        },
        {
            "type": "Other",
            "label": "Zero Size",
            "isVisible": "1",
            "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
        },
        {
            "type": "Cell",
            "label": "",
            "isVisible": "1",
            "rect": {"x": 0, "y": 400, "width": 390, "height": 60},
            "children": [
                {
                    "type": "StaticText",
                    "value": "Nested Value",
                    "isVisible": "1",
                    "rect": {"x": 30, "y": 410, "width": 200, "height": 20},
                },
            ],
        },
    ],
}


def test_collect_texts_finds_visible_text_with_centers():
    hits = collect_texts(SAMPLE_TREE)
    texts = [h["text"] for h in hits]
    assert texts == ["General", "Bluetooth", "Nested Value"]
    general = hits[0]
    assert general["x"] == 20 + 350 / 2
    assert general["y"] == 100 + 44 / 2


def test_collect_texts_skips_invisible_and_zero_size():
    texts = [h["text"] for h in collect_texts(SAMPLE_TREE)]
    assert "Hidden Thing" not in texts
    assert "Zero Size" not in texts


def test_ambiguous_hits_exact_match_is_never_ambiguous():
    # The Messages list has cells whose labels contain "Mom" plus a StaticText
    # that is exactly "Mom" (the 1:1 thread). The exact one wins, not ambiguous.
    hits = [
        {"text": "Mom, Alex & Sam, Unread, Summary"},
        {"text": "Mom"},
        {"text": "Mom laughed at a message"},
    ]
    assert _ambiguous_hits(hits, "Mom") == []


def test_ambiguous_hits_flags_multiple_fuzzy_no_exact():
    hits = [
        {"text": "Mommy dearest"},
        {"text": "Mom, Alex & Sam"},
    ]
    result = _ambiguous_hits(hits, "Mom")
    assert len(result) == 2


def test_ambiguous_hits_single_hit_is_fine():
    assert _ambiguous_hits([{"text": "Mom, Anytown OH"}], "Mom") == []


def test_thread_title_reads_contact_photo_button():
    tree = {
        "type": "Application",
        "rect": {"x": 0, "y": 0, "width": 440, "height": 956},
        "children": [
            {
                "type": "Button",
                "label": "Contact photo for Mom",
                "isVisible": "1",
                "rect": {"x": 200, "y": 90, "width": 40, "height": 40},
            },
        ],
    }
    assert _thread_title(tree) == "Mom"


def test_thread_title_none_when_no_header_button():
    # A group thread / unknown layout has no "Contact photo for X" button.
    assert _thread_title(SAMPLE_TREE) is None


def _buttons_tree(labels):
    return {
        "type": "Application",
        "rect": {"x": 0, "y": 0, "width": 390, "height": 844},
        "children": [
            {
                "type": "Button",
                "label": label,
                "isVisible": "1",
                "rect": {"x": 10, "y": 100 + i * 90, "width": 100, "height": 80},
            }
            for i, label in enumerate(labels)
        ],
    }


def test_passcode_pad_visible_with_digit_buttons():
    assert _passcode_pad_visible(_buttons_tree(list("1234567890")))


def test_passcode_pad_visible_with_passcode_text():
    tree = _buttons_tree(["Emergency"])
    tree["children"].append(
        {
            "type": "StaticText",
            "label": "Enter Passcode",
            "isVisible": "1",
            "rect": {"x": 100, "y": 200, "width": 200, "height": 30},
        }
    )
    assert _passcode_pad_visible(tree)


def test_passcode_pad_not_visible_on_ordinary_screen():
    assert not _passcode_pad_visible(SAMPLE_TREE)


class StubPhone:
    """Stands in for WDAClient in unlock() tests. Locked until typed at."""

    def __init__(
        self,
        tree,
        type_error=None,
        unlock_error=None,
        frame=None,
        app="com.apple.springboard",
        wrong_pin=False,
    ):
        self.tree = tree
        self.typed = []
        self.pressed = []
        self.swipes = 0
        self.source_calls = 0
        self.type_error = type_error
        self.unlock_error = unlock_error
        self.app = app
        self.wrong_pin = wrong_pin
        # A lit screen compresses to a big PNG; a dark one to almost nothing.
        self.frame = frame if frame is not None else b"\0" * 200_000

    def active_app(self):
        return {"bundleId": self.app}

    def screenshot(self):
        return self.frame

    def unlock(self):
        if self.unlock_error:
            raise self.unlock_error

    def press_button(self, name):
        self.pressed.append(name)

    def window_size(self):
        return (390.0, 844.0)

    def swipe(self, *_args):
        self.swipes += 1

    def source(self):
        self.source_calls += 1
        return self.tree

    def type_text(self, text):
        if self.type_error:
            raise self.type_error
        self.typed.append(text)
        if not self.wrong_pin:
            self.tree = SAMPLE_TREE  # accepted: pad dismissed, home screen


@pytest.fixture()
def fast(monkeypatch):
    monkeypatch.setattr(helpers.time, "sleep", lambda _s: None)
    monkeypatch.setattr(config, "PHONE_PASSCODE", "246810")

    def use(stub):
        monkeypatch.setattr(helpers, "_client", stub)
        return stub

    return use


def test_unlock_types_nothing_when_no_pad_appears(fast):
    """Wake + swipe lands somewhere without a pad (phone was just asleep):
    unlock() is done — and must never type the passcode blind."""
    stub = fast(StubPhone(SAMPLE_TREE))
    helpers.unlock()
    assert stub.pressed == ["home"]  # woke the screen
    assert stub.typed == []


def test_unlock_types_when_pad_is_visible(fast):
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    helpers.unlock()
    assert stub.typed == ["246810"]


def test_unlock_never_consults_wda_locked(fast):
    """/wda/locked lies (returned False with the pad on screen, live
    2026-08-09). unlock() must decide from the screen, never that endpoint."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    stub.is_locked = None  # noqa: vulture  (poison: any call raises TypeError)
    helpers.unlock()
    assert stub.typed == ["246810"]


def test_unlock_locked_without_passcode_raises(fast, monkeypatch):
    """Pad on screen but no PHONE_PASSCODE configured: clear error, no typing."""
    monkeypatch.setattr(config, "PHONE_PASSCODE", None)
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    with pytest.raises(WDAError, match="PHONE_PASSCODE"):
        helpers.unlock()
    assert stub.typed == []


def test_unlock_leaves_foreground_app_alone(fast):
    """An app is frontmost -> the phone is unlocked and in use. The edge swipe
    would yank the user out of the app; unlock() must not gesture at all."""
    stub = fast(StubPhone(SAMPLE_TREE, app="com.apple.mobilesafari"))
    helpers.unlock()
    assert stub.pressed == []
    assert stub.swipes == 0
    assert stub.typed == []


def test_unlock_wrong_pin_raises_and_never_retries(fast):
    """Pad still up after typing = wrong PIN (or lost keys). One attempt only —
    iOS lockout escalates on repeated wrong passcodes."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890")), wrong_pin=True))
    with pytest.raises(WDAError, match="still on screen"):
        helpers.unlock()
    assert stub.typed == ["246810"]  # exactly one attempt


def test_unlock_resummons_pad_when_screen_slept(fast):
    """If the screen went dark during the (slow) pad check, unlock() must wake
    and swipe again before typing — keys on a dark screen go nowhere."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890")), frame=b"tiny"))
    helpers.unlock()
    assert stub.pressed == ["home", "home"]  # woke twice
    assert stub.typed == ["246810"]


def test_unlock_uses_the_client_it_is_given(fast):
    """The viewer passes its own client (WDA holds one session; a second
    client steals it mid-sequence). unlock(c) must not touch the singleton."""
    singleton = fast(StubPhone(_buttons_tree(list("1234567890"))))
    mine = StubPhone(_buttons_tree(list("1234567890")))
    helpers.unlock(mine)
    assert mine.typed == ["246810"]
    assert singleton.typed == []


def test_unlock_scrubs_passcode_from_errors(fast):
    err = WDAError("POST /wda/keys: could not type '246810'")
    fast(StubPhone(_buttons_tree(list("1234567890")), type_error=err))
    with pytest.raises(WDAError) as exc_info:
        helpers.unlock()
    assert "246810" not in str(exc_info.value)


class CountingClient:
    """WDAClient stand-in that counts /source fetches for the cache tests."""

    def __init__(self, tree=SAMPLE_TREE):
        self.tree = tree
        self.source_calls = 0

    def source(self):
        self.source_calls += 1
        return self.tree

    def tap(self, x, y):
        pass

    def type_text(self, text):
        pass


def _fresh_counting_client(monkeypatch):
    helpers._invalidate_tree()  # cache is module state; start every test clean
    stub = CountingClient()
    monkeypatch.setattr(helpers, "_client", stub)
    return stub


def test_ui_tree_cached_for_consecutive_reads(monkeypatch):
    stub = _fresh_counting_client(monkeypatch)
    assert helpers.find_text("general")  # fetches
    assert helpers.find_text("bluetooth")  # cache hit, no second fetch
    assert stub.source_calls == 1


def test_actions_invalidate_tree_cache(monkeypatch):
    stub = _fresh_counting_client(monkeypatch)
    helpers.ui_tree()
    helpers.tap(10, 20)  # the screen may now differ; cache must not be reused
    helpers.ui_tree()
    assert stub.source_calls == 2


def test_type_text_invalidates_tree_cache(monkeypatch):
    stub = _fresh_counting_client(monkeypatch)
    helpers.ui_tree()
    helpers.type_text("hi")
    helpers.ui_tree()
    assert stub.source_calls == 2


def test_unlock_invalidates_tree_cache(fast):
    stub = fast(StubPhone(SAMPLE_TREE))
    helpers._invalidate_tree()  # cache is module state; start the test clean
    helpers.ui_tree()  # 1: cache the pre-unlock screen
    helpers.unlock()  # 2: unlock's own pad check; screen changed
    helpers.ui_tree()  # must refetch (3), not reuse the pre-unlock tree
    assert stub.source_calls == 3


def test_tree_cache_expires_by_ttl(monkeypatch):
    stub = _fresh_counting_client(monkeypatch)
    helpers.ui_tree()
    monkeypatch.setattr(helpers.time, "monotonic", lambda: helpers.time.time() + 3600)
    helpers.ui_tree()  # the screen can change on its own; a stale tree is unsafe
    assert stub.source_calls == 2


def test_load_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text('A=1\n# comment\nB = "two"\nbroken line\n', encoding="utf-8")
    assert _load_env(env) == {"A": "1", "B": "two"}


def test_load_env_missing_file(tmp_path):
    assert _load_env(tmp_path / "nope.env") == {}
