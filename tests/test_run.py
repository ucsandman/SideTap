"""CLI entry-point tests. No phone needed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import run  # noqa: E402


class _FakeStream:
    """Stands in for a piped stdout/stderr that defaults to cp1252 on Windows."""

    encoding = "cp1252"

    def __init__(self):
        self.reconfigured = []
        self.written = []

    def reconfigure(self, **kw):
        self.reconfigured.append(kw)
        if "encoding" in kw:
            self.encoding = kw["encoding"]

    def write(self, s):
        # Reject anything cp1252 cannot carry, exactly like the real stream.
        s.encode(self.encoding)
        self.written.append(s)
        return len(s)

    def flush(self):  # noqa: vulture
        pass


def test_main_reconfigures_both_streams_to_utf8(monkeypatch):
    # A piped stream on Windows defaults to cp1252. Phone text routinely carries
    # U+202F and emoji, so a post-action print() would raise UnicodeEncodeError
    # and erase the confirmation that the action already happened.
    out, err = _FakeStream(), _FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    run.main(["help"])

    assert out.reconfigured, "main() never reconfigured stdout"
    assert err.reconfigured, "main() never reconfigured stderr"
    assert out.reconfigured[0]["encoding"] == "utf-8"
    assert err.reconfigured[0]["encoding"] == "utf-8"


def test_main_survives_printing_narrow_no_break_space(monkeypatch):
    # U+202F appears in iOS clock strings; it is not encodable in cp1252.
    out, err = _FakeStream(), _FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    run.main(["help"])
    out.write("9:41 AM")  # would raise before the reconfigure

    assert "9:41 AM" in out.written


def test_main_reconfigure_is_tolerant_of_odd_streams(monkeypatch):
    # pytest's capture objects and some shells hand back streams with no
    # reconfigure(); the CLI must not die on its first line because of it.
    class NoReconfigure:
        encoding = "utf-8"

        def write(self, s):
            return len(s)

        def flush(self):  # noqa: vulture
            pass

    monkeypatch.setattr(sys, "stdout", NoReconfigure())
    monkeypatch.setattr(sys, "stderr", NoReconfigure())

    assert run.main(["help"]) == 0
