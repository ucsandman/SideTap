"""MCP server: the agent helpers as native typed tools for any MCP client.

`claude mcp add sidetap -- phone-harness mcp` (or the equivalent in Claude
Desktop) lets a session drive the phone with typed tool calls instead of
piping Python through stdin. stdio transport; needs the `mcp` package.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Image

from . import helpers

server = FastMCP(
    "sidetap",
    instructions=(
        "Drive the user's iPhone over USB. Coordinates are points (the same "
        "units ocr() returns), origin top-left. Call ocr() to read the screen, "
        "screenshot() for pixels. A .state/STOP file blocks every action; the "
        "user controls it from the viewer."
    ),
)

# Registered with their real signatures and docstrings from helpers.py, so the
# MCP schema stays in lockstep with the Python API. screenshot and unlock get
# wrappers below (bytes return / internal client parameter).
_TOOLS = [
    helpers.ocr,
    helpers.screen_info,
    helpers.tap,
    helpers.tap_text,
    helpers.long_press,
    helpers.swipe,
    helpers.scroll,
    helpers.type_text,
    helpers.press_home,
    helpers.open_app,
    helpers.current_app,
    helpers.wait_for_app,
    helpers.send_message,
    helpers.read_messages,
    helpers.find_text,
    helpers.wait_for_text,
    helpers.wait_stable,
]
for _fn in _TOOLS:
    server.tool()(_fn)


@server.tool()
def screenshot() -> Image:
    """Current phone screen as a PNG image."""
    return Image(data=helpers.screenshot(), format="png")


@server.tool()
def unlock() -> str:
    """Wake and unlock the phone. Types PHONE_PASSCODE from .env only if the
    passcode pad is actually on screen; one attempt only (iOS lockout)."""
    helpers.unlock()
    return "unlocked"


def main() -> int:
    server.run()  # stdio transport; blocks until the client disconnects
    return 0
