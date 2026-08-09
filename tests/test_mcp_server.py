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
