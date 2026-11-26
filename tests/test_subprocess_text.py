"""Every text-mode subprocess call must tolerate undecodable bytes. No phone needed.

Windows console tools print in the OEM code page and go-ios prints UTF-8, while
text=True decodes with the ANSI code page. On a non-English Windows a single
unmapped byte raises UnicodeDecodeError out of subprocess.run, so text-mode
calls pass errors="replace" (syslog.py set the precedent).
"""

import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "phone_harness"


def _kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def test_text_mode_subprocess_calls_replace_decode_errors():
    offenders = []
    for path in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "Popen", "check_output")
                and (_kwarg(node, "text") is True or _kwarg(node, "universal_newlines") is True)
                and _kwarg(node, "errors") is None
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, "text=True without errors=: " + ", ".join(offenders)
