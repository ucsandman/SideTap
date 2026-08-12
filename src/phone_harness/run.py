"""phone-harness CLI.

Usage:
  phone-harness <<'PY'          # pipe Python; helpers are pre-imported
  tap_text("General")
  PY
  phone-harness doctor          # diagnose the whole chain (alias: --doctor)
  phone-harness up              # start tunnel + WDA + port forwards
  phone-harness down            # stop them
  phone-harness view            # live viewer web page (click = tap)
  phone-harness mcp             # MCP server over stdio, e.g.:
                                #   claude mcp add sidetap \
                                #     --env PYTHONPATH=<repo>/src -- python -m phone_harness mcp
  phone-harness fix-input [profile.mobileprovision]
                                # sign WDA so touch input works (free Apple ID)
  phone-harness notify-expiry [--install | --uninstall]
                                # toast when the 7-day signature is <36h from
                                # expiry; --install adds a daily 10:00 check
"""

from __future__ import annotations

import sys
import traceback

from . import config, helpers, trust


def _agent_namespace() -> dict:
    ns = {name: getattr(helpers, name) for name in helpers.__all__}
    workspace = config.REPO_ROOT / "agent-workspace" / "agent_helpers.py"
    if workspace.exists():
        try:
            exec(
                compile(workspace.read_text(encoding="utf-8"), str(workspace), "exec"),
                ns,
            )
        except Exception:
            print(f"warning: {workspace} failed to load:", file=sys.stderr)
            traceback.print_exc()
    return ns


def _exec_stdin() -> int:
    code = sys.stdin.read()
    if not code.strip():
        print(__doc__)
        return 0
    try:
        exec(compile(code, "<agent-script>", "exec"), _agent_namespace())
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        # The MCP surface wraps screen reads in an envelope; this one prints
        # the same warning, so an agent driving the phone through stdin is
        # told what it just read is data, not instructions.
        if trust.tainted():
            print(f"\n[SideTap] {trust.WARNING}", file=sys.stderr)


def _force_utf8_streams() -> None:
    """Make stdout/stderr carry anything the phone can show.

    A piped stream on Windows defaults to cp1252. Phone text routinely holds
    U+202F (iOS clock strings) and emoji, so a print() of a result AFTER a real
    gesture already landed raised UnicodeEncodeError: the agent saw a traceback
    and no confirmation, and could rerun the script — a duplicate send.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # captured or wrapped streams may not support it


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else None
    if cmd in ("doctor", "--doctor"):
        from . import admin

        return admin.doctor()
    if cmd == "up":
        from . import admin

        return admin.up()
    if cmd == "down":
        from . import admin

        return admin.down()
    if cmd == "view":
        from . import viewer

        return viewer.serve()
    if cmd == "mcp":
        try:
            from . import mcp_server
        except ImportError:
            print(
                "MCP support needs the `mcp` package: pip install mcp",
                file=sys.stderr,
            )
            return 1
        return mcp_server.main()
    if cmd == "fix-input":
        from pathlib import Path

        from . import signing

        profile = Path(args[1]) if len(args) > 1 else None
        result = signing.fix_input(
            profile=profile, progress=lambda s, m: print(f"[{s}] {m}")
        )
        print(("OK: " if result["ok"] else "FAILED: ") + result["message"])
        return 0 if result["ok"] else 1
    if cmd == "notify-expiry":
        from . import admin

        if "--install" in args[1:]:
            return admin.reminder_install()
        if "--uninstall" in args[1:]:
            return admin.reminder_uninstall()
        return admin.notify_expiry()
    if cmd in ("help", "--help", "-h"):
        print(__doc__)
        return 0
    if cmd in (None, "-"):
        return _exec_stdin()
    print(f"Unknown command: {cmd}\n{__doc__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
