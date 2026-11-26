"""Run pymobiledevice3's tunneld with no window, logging to <log dir>/tunneld.log.

    pythonw scripts/tunneld.pyw [<log dir>]     (default: .state/ next to scripts/)

Started by the "SideTap tunneld" scheduled task (admin, at logon) that
scripts/install_tunneld_task.ps1 registers, with a log dir outside the app folder:
a running task holding its log or working directory there would block the
installer from replacing the folder on update.
tunneld keeps one tunnel per iPad over USB or Wi-Fi and lists them on 127.0.0.1:49151.
It runs with --no-wifi --no-mobdev2 --no-usb: neither of its two Wi-Fi monitors runs, nor
its USB network-interface monitor (CDC-NCM, which repeats a Bonjour browse); on Windows USB
tunnels come from its usbmux monitor, which stays on. So it never looks for the iPad on
Wi-Fi by itself, and a Wi-Fi tunnel is built only when an agent asks for one
(phone_harness.link.request_tunnel() calls tunneld's /start-tunnel). With the Wi-Fi monitors
on, tunneld connected to a sleeping iPad every few seconds (remote pairing, and lockdown on
port 62078); each connection woke it for about 3 s, the tunnel died when it slept again, and
the Wi-Fi tunnel adapter on this PC came and went about once a minute.
pythonw has no console, so stdout and stderr go to a log file that is cut back to
its last 2 MB at each start.

STOPGAP until pymobiledevice3 fixes it upstream (doronz88/pymobiledevice3#2051): tunneld runs inside
uvicorn with loop="asyncio", which on Windows always builds a ProactorEventLoop, and
the Bonjour browse that finds the iPad on Wi-Fi gets no answers on that loop
(checked 2026-10-18: found in 0 of 14 browses on the proactor loop, 11 of 16 on the selector).
pymobiledevice3 already sets the selector policy for itself; loop="none" makes uvicorn
use it. Remove this patch once a pymobiledevice3 release passes loop="none" on Windows.
"""

import sys
from pathlib import Path

LOG_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / ".state"
LOG = LOG_DIR / "tunneld.log"
KEEP = 2 * 1024 * 1024

LOG.parent.mkdir(parents=True, exist_ok=True)
if LOG.exists() and LOG.stat().st_size > KEEP:
    LOG.write_bytes(LOG.read_bytes()[-KEEP:])
log = open(LOG, "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log

import uvicorn  # noqa: E402

_run = uvicorn.run


def _run_with_policy_loop(*args, **kwargs):
    if sys.platform == "win32" and kwargs.get("loop") == "asyncio":
        kwargs["loop"] = "none"
    return _run(*args, **kwargs)


uvicorn.run = _run_with_policy_loop

from pymobiledevice3.__main__ import main  # noqa: E402

sys.argv = ["pymobiledevice3", "remote", "tunneld", "--no-wifi", "--no-mobdev2", "--no-usb"]
sys.exit(main())
