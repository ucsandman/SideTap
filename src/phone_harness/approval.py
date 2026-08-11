"""Human approval for a send the agent asked for after reading the phone.

The agent runs in the MCP or CLI process, the human clicks in the viewer
process. They meet through .state/, the same handshake STOP and wda_session
already use. Fail closed: anything other than an explicit approve means the
message does not go out.

No HTTP and no WDA knowledge here — request() returns a decision string and
the caller decides what to raise.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from . import config

POLL = 0.25

# always  - every send after a read waits for a click (the safe default)
# flagged - only ask when the scanner actually found something. Trades real
#           safety for quiet: a payload written to dodge the heuristics gets
#           through, because this promotes the flags from a hint to a verdict.
# off     - never ask. STOP and the activity feed are all that is left.
MODES = ("always", "flagged", "off")


def mode_file() -> Path:
    return config.STATE_DIR / "send_approval"


def mode() -> str:
    """The gate setting in force right now.

    The viewer's toggle (.state/send_approval) beats the .env default, and is
    read at send time, because the agent process is long-lived and the human
    can flip it mid-session. Anything unrecognized, from a typo or a truncated
    file, falls back to "always": a setting that cannot be read must never be
    the one that disables the gate.

    Deliberately not writable by any agent tool. See set_mode.
    """
    for value in (_read_text(mode_file()), config.SEND_APPROVAL):
        if value and value.strip().lower() in MODES:
            return value.strip().lower()
    return "always"


def set_mode(value: str) -> str:
    """Change the gate setting. Called by the viewer toggle and nothing else.

    Never registered as an MCP tool and never added to helpers.__all__: an
    injected instruction that can switch the gate off has defeated it, so the
    only way to reach this is a human clicking in the viewer or editing .env.
    """
    value = (value or "").strip().lower()
    if value not in MODES:
        raise ValueError(f"send approval mode must be one of {MODES}, got {value!r}")
    config.STATE_DIR.mkdir(exist_ok=True)
    mode_file().write_text(value, encoding="utf-8")
    return value


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def pending_file() -> Path:
    """Read dynamically so tests can relocate STATE_DIR."""
    return config.STATE_DIR / "pending_send.json"


def decision_file() -> Path:
    return config.STATE_DIR / "send_decision.json"


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pending() -> dict | None:
    """The send waiting on a human, or None. Read by the viewer."""
    return _read(pending_file())


def decide(request_id: str, decision: str) -> bool:
    """Answer the pending send. Written by the viewer. False if the id is stale."""
    rec = pending()
    if not rec or rec.get("id") != request_id:
        return False
    verdict = "approve" if decision == "approve" else "deny"
    config.STATE_DIR.mkdir(exist_ok=True)
    decision_file().write_text(
        json.dumps({"id": request_id, "decision": verdict}), encoding="utf-8"
    )
    return True


def request(
    contact: str,
    text: str,
    flags: list[str],
    taint_source: str,
    timeout: float | None = None,
) -> str:
    """Block until the human answers in the viewer.

    Returns "approve", "deny", "timeout", or "busy" (another send already
    waiting — one card at a time, so it is always clear which text was just
    approved). Only "approve" may send.
    """
    timeout = config.SEND_APPROVAL_TIMEOUT if timeout is None else timeout
    config.STATE_DIR.mkdir(exist_ok=True)
    if pending_file().exists():
        return "busy"
    request_id = uuid.uuid4().hex[:12]
    # A decision left over from an earlier send must never approve this one.
    decision_file().unlink(missing_ok=True)
    pending_file().write_text(
        json.dumps(
            {
                "id": request_id,
                "contact": contact,
                "text": text,
                "flags": list(flags),
                "taint_source": taint_source,
                "created": time.time(),
            }
        ),
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + timeout
        while True:
            answer = _read(decision_file())
            if answer and answer.get("id") == request_id:
                return "approve" if answer.get("decision") == "approve" else "deny"
            if time.monotonic() >= deadline:
                return "timeout"
            time.sleep(POLL)
    finally:
        pending_file().unlink(missing_ok=True)
        decision_file().unlink(missing_ok=True)
