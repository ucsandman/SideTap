"""Pure-function tests: tree walking and .env parsing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness.config import _load_env  # noqa: E402
from phone_harness.helpers import (  # noqa: E402
    _ambiguous_hits,
    _thread_title,
    collect_texts,
)

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


def test_load_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text('A=1\n# comment\nB = "two"\nbroken line\n', encoding="utf-8")
    assert _load_env(env) == {"A": "1", "B": "two"}


def test_load_env_missing_file(tmp_path):
    assert _load_env(tmp_path / "nope.env") == {}
