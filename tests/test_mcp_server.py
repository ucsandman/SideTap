"""MCP tool registration. No phone needed; skipped if `mcp` is not installed."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

# Skip on missing OR incompatible mcp (2.0 dropped mcp.server.fastmcp).
pytest.importorskip("mcp.server.fastmcp")

from phone_harness import mcp_server  # noqa: E402


def _tools():
    return asyncio.run(mcp_server.server.list_tools())


def test_helper_surface_is_registered():
    names = {t.name for t in _tools()}
    assert {
        "ocr",
        "tap",
        "tap_text",
        "type_text",
        "swipe",
        "open_app",
        "send_message",
        "read_messages",
        "wait_for_text",
        "screenshot",
        "unlock",
    } <= names


def test_tool_schemas_carry_real_signatures():
    tools = {t.name: t for t in _tools()}
    assert "text" in tools["type_text"].inputSchema["properties"]
    assert {"x", "y"} <= set(tools["tap"].inputSchema["properties"])
    # Docstrings become tool descriptions the model reads.
    assert "conversation" in tools["send_message"].description


def test_wrapped_tools_hide_internal_params():
    tools = {t.name: t for t in _tools()}
    # unlock(c=...) takes an internal WDA client; the MCP surface must not.
    assert tools["unlock"].inputSchema.get("properties", {}) == {}


# act(): several helper calls in one MCP round trip (launch-thread feedback:
# eight taps should be one call, not eight schema-heavy round trips).


def test_act_runs_steps_in_order(monkeypatch):
    calls = []
    monkeypatch.setitem(mcp_server._ACT_TOOLS, "tap", lambda x, y: calls.append(("tap", x, y)))
    monkeypatch.setitem(mcp_server._ACT_TOOLS, "type_text", lambda text: calls.append(("type", text)))
    out = mcp_server.act(
        [{"tool": "tap", "args": {"x": 1, "y": 2}}, {"tool": "type_text", "args": {"text": "hi"}}]
    )
    assert calls == [("tap", 1, 2), ("type", "hi")]
    assert [s["ok"] for s in out] == [True, True]


def test_act_stops_at_first_failure(monkeypatch):
    calls = []

    def boom():
        raise RuntimeError("nope")

    monkeypatch.setitem(mcp_server._ACT_TOOLS, "press_home", boom)
    monkeypatch.setitem(mcp_server._ACT_TOOLS, "tap", lambda x, y: calls.append("tap"))
    out = mcp_server.act(
        [{"tool": "press_home", "args": {}}, {"tool": "tap", "args": {"x": 1, "y": 2}}]
    )
    assert not calls  # the tap after the failure never ran
    assert out[-1]["ok"] is False
    assert "nope" in out[-1]["error"]


def test_act_rejects_unknown_tool():
    out = mcp_server.act([{"tool": "screenshot", "args": {}}])
    assert out[-1]["ok"] is False  # bytes-returning tools are not batchable
    out = mcp_server.act([{"tool": "rm_rf", "args": {}}])
    assert out[-1]["ok"] is False


def test_act_is_registered_with_schema():
    tools = {t.name: t for t in _tools()}
    assert "steps" in tools["act"].inputSchema["properties"]
    assert "one round trip" in tools["act"].description
