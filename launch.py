"""One command to run everything: open the viewer, bring the phone link up.

python launch.py

The viewer opens immediately; the link comes up in the background (its doctor
panel shows live progress instead of a blank terminal for up to a minute).
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from phone_harness import admin, viewer  # noqa: E402

if __name__ == "__main__":
    threading.Thread(target=admin.up, daemon=True).start()
    sys.exit(viewer.serve())
