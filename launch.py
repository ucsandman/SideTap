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
    # --no-browser: a fleet launcher (SideTap Pro) opens one dashboard instead
    # of a tab per instance; humans running `python launch.py` still get theirs.
    threading.Thread(target=admin.up, daemon=True).start()
    sys.exit(viewer.serve(open_browser="--no-browser" not in sys.argv[1:]))
