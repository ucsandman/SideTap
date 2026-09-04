"""Start the SideTap MCP server from a plain ``python <this file>`` command.

This is ``phone-harness.cmd mcp`` for launchers that cannot run a ``.cmd``:
Node's ``child_process.spawn`` without a shell (which is how declick starts a
stdio MCP server) resolves ``.exe`` files but not ``.cmd`` ones, and the repo
is deliberately not pip-installable, so ``python -m phone_harness`` needs
``src`` on ``sys.path`` first. Register it with

    declick add "mcp:python C:/path/to/sidetap/scripts/phone_mcp.py" --name phone

and every helper becomes a shell verb (``declick run phone ocr --fields text,x,y``),
which is what lets a subagent with no MCP tools drive the phone.
"""

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.argv = ["phone_harness", "mcp"]  # noqa: vulture -- read by run.main() inside the module below
runpy.run_module("phone_harness", run_name="__main__", alter_sys=True)
