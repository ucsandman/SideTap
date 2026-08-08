"""One command to run everything: bring the phone link up, then open the viewer.

python launch.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from phone_harness import admin, viewer  # noqa: E402

if __name__ == "__main__":
    code = admin.up()
    if code != 0:
        print(
            "\nLink is not up. Opening the viewer anyway — its panel shows what to fix."
        )
    sys.exit(viewer.serve())
