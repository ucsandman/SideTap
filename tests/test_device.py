"""ios_path fallback for truncated-PATH launches (shortcut/Startup). No phone needed."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import device  # noqa: E402


def test_ios_path_prefers_path_lookup(monkeypatch):
    monkeypatch.setattr(device.shutil, "which", lambda _: r"C:\somewhere\ios.exe")
    assert device.ios_path() == r"C:\somewhere\ios.exe"


def test_ios_path_falls_back_to_npm_global_dir(monkeypatch, tmp_path):
    # Windows truncates a registry PATH past ~4095 chars when it builds the
    # logon environment, so shortcut/Startup launches can miss the npm dir
    # even though terminals see it (bit the Startup shortcut live 2026-08-10).
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    exe = tmp_path / "npm" / "ios.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert device.ios_path() == str(exe)


def test_ios_path_falls_back_to_installer_bin_dir(monkeypatch, tmp_path):
    # The sidetap.io one-line installer puts ios.exe in %LOCALAPPDATA%\SideTap\bin
    # (no Node/npm on the machine at all), so PATH lookup and the npm dir both miss.
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    exe = tmp_path / "SideTap" / "bin" / "ios.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert device.ios_path() == str(exe)


def test_ios_path_none_when_missing_everywhere(monkeypatch, tmp_path):
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert device.ios_path() is None


# ---- WDA bundle cache: deep sleep empties `ios apps --list` (seen live
# 2026-08-10) while the app is still installed. The cache keeps up() working
# so the link can heal the moment the phone wakes.


def test_wda_bundle_cached_on_live_detection(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    monkeypatch.setattr(
        device,
        "list_apps",
        lambda: [{"bundle_id": "com.x.WebDriverAgentRunner.xctrunner", "name": "WDA"}],
    )
    assert device.detect_wda_bundle() == "com.x.WebDriverAgentRunner.xctrunner"
    cached = (tmp_path / "wda_bundle").read_text(encoding="utf-8").strip()
    assert cached == "com.x.WebDriverAgentRunner.xctrunner"


def test_wda_bundle_falls_back_to_cache_when_list_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    (tmp_path / "wda_bundle").write_text("com.x.cached.xctrunner", encoding="utf-8")
    monkeypatch.setattr(device, "list_apps", lambda: [])
    assert device.detect_wda_bundle() == "com.x.cached.xctrunner"


def test_wda_bundle_none_when_empty_list_and_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    monkeypatch.setattr(device, "list_apps", lambda: [])
    assert device.detect_wda_bundle() is None


# ---- Developer Disk Image: an iOS update silently unmounts it (bit live
# 2026-08-10, the 26.6 update) — runwda then dies in dtx channel timeouts.
# The image mounter is asked through the tunnel (link.py).


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_ddi_mounted_asks_the_link(monkeypatch):
    monkeypatch.setattr(device.link, "ddi_mounted", lambda: True)
    assert device.ddi_mounted()
    monkeypatch.setattr(device.link, "ddi_mounted", lambda: False)
    assert not device.ddi_mounted()


def test_ddi_mounted_false_when_phone_unreachable(monkeypatch):
    def unreachable():
        raise device.link.LinkError("no tunnel to the phone")

    monkeypatch.setattr(device.link, "ddi_mounted", unreachable)
    assert not device.ddi_mounted()


def test_mount_ddi_names_locked_phone(monkeypatch):
    # The one mount failure a human can fix on the spot must be named, not
    # buried in a log tail: DeviceLocked -> "unlock it".
    def locked():
        raise device.link.LinkError("PyMobileDevice3Exception: {'Error': 'DeviceLocked'}")

    monkeypatch.setattr(device.link, "mount_ddi", locked)
    monkeypatch.setattr(device, "ddi_mounted", lambda: False)
    ok, msg = device.mount_ddi()
    assert not ok
    assert "unlock" in msg.lower()


def test_mount_ddi_verifies_by_reprobe(monkeypatch):
    monkeypatch.setattr(device.link, "mount_ddi", lambda: None)
    monkeypatch.setattr(device, "ddi_mounted", lambda: True)
    ok, msg = device.mount_ddi()
    assert ok
    assert "mounted" in msg


def test_wda_bundle_no_cache_fallback_when_app_really_absent(monkeypatch, tmp_path):
    # A non-empty list without WDA means genuinely uninstalled: the stale
    # cache must NOT resurrect it.
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    (tmp_path / "wda_bundle").write_text("com.x.cached.xctrunner", encoding="utf-8")
    monkeypatch.setattr(
        device,
        "list_apps",
        lambda: [{"bundle_id": "com.apple.mobilesafari", "name": ""}],
    )
    assert device.detect_wda_bundle() is None


# ---- list_apps() persistent cache: same deep-sleep-empties-the-list failure
# mode as detect_wda_bundle's own cache, but now lives INSIDE list_apps()
# itself so every caller (helpers.open_app, admin._check_wda_installed,
# detect_wda_bundle) benefits without touching helpers.py.


def _ios_apps_proc(bundle_ids):
    """Stand-in for link.list_apps returning these bundle ids."""
    return lambda: [{"bundle_id": bid, "name": bid} for bid in bundle_ids]


def test_list_apps_caches_nonempty_result_to_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.link, "list_apps", _ios_apps_proc(["com.a.App"]))
    apps = device.list_apps()
    assert apps == [{"bundle_id": "com.a.App", "name": "com.a.App"}]
    cached = json.loads((tmp_path / "apps_cache.json").read_text(encoding="utf-8"))
    assert cached == apps


def test_list_apps_falls_back_to_cache_when_live_list_empty(monkeypatch, tmp_path):
    # Deep sleep empties `ios apps --list` while apps stay installed
    # (docs/ERRORS.md) — an empty live result must never overwrite, or shadow,
    # a previously cached good list.
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    (tmp_path / "apps_cache.json").write_text(
        json.dumps([{"bundle_id": "com.a.App", "name": "com.a.App"}]), encoding="utf-8"
    )
    monkeypatch.setattr(device.link, "list_apps", _ios_apps_proc([]))
    assert device.list_apps() == [{"bundle_id": "com.a.App", "name": "com.a.App"}]


def test_list_apps_empty_with_no_cache_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.link, "list_apps", _ios_apps_proc([]))
    assert device.list_apps() == []


def test_list_apps_fresh_nonempty_overwrites_stale_cache(monkeypatch, tmp_path):
    # A newly installed app must still be findable — staleness must not stick.
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    (tmp_path / "apps_cache.json").write_text(
        json.dumps([{"bundle_id": "com.old.App", "name": "com.old.App"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(device.link, "list_apps", _ios_apps_proc(["com.new.App"]))
    assert device.list_apps() == [{"bundle_id": "com.new.App", "name": "com.new.App"}]
    cached = json.loads((tmp_path / "apps_cache.json").read_text(encoding="utf-8"))
    assert cached == [{"bundle_id": "com.new.App", "name": "com.new.App"}]


# ---- memoized_run(): scopes ios/netstat subprocess results to ONE caller-
# defined run (e.g. one doctor pass). Must never leak across two separate
# runs — "a stale check is a lying check" (project rule).


def test_list_devices_memoized_within_one_run(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    calls = []

    def fake_list():
        calls.append("list")
        return ["00008150-X"]

    monkeypatch.setattr(device.link, "list_devices", fake_list)
    with device.memoized_run():
        assert device.list_devices() == ["00008150-X"]
        assert device.list_devices() == ["00008150-X"]
    assert len(calls) == 1  # spawned once, not twice, inside the block
    device.list_devices()
    assert len(calls) == 2  # outside the block, a fresh call always re-spawns


def test_list_apps_memoized_within_one_run(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    calls = []

    def fake_apps():
        calls.append("apps")
        return [{"bundle_id": "com.a.App", "name": "com.a.App"}]

    monkeypatch.setattr(device.link, "list_apps", fake_apps)
    with device.memoized_run():
        device.list_apps()
        device.list_apps()
    assert len(calls) == 1
    with device.memoized_run():  # a separate, later run always re-spawns
        device.list_apps()
    assert len(calls) == 2


def test_netstat_memoized_within_one_run(monkeypatch):
    calls = []

    def fake_subprocess_run(cmd, **kw):
        calls.append(cmd)
        return _Proc("")

    monkeypatch.setattr(device.sys, "platform", "win32")
    monkeypatch.setattr(device.subprocess, "run", fake_subprocess_run)
    with device.memoized_run():
        device.port_exposed_to_lan(8100)
        device.port_exposed_to_lan(9100)  # different port, same netstat output
    assert len(calls) == 1
    device.port_exposed_to_lan(8100)
    assert len(calls) == 2  # outside the block: fresh spawn again


def test_memoized_run_thread_isolated(monkeypatch, tmp_path):
    """A module-global cache lets two overlapping memoized_run() blocks on
    different threads stomp each other's restore: A enters (previous=None),
    B enters while A is still open (previous=A's dict), A exits (global=None),
    B exits (global=A's now-orphaned, populated dict) — every block has
    exited, but the global is a non-None dict nothing ever clears, so a later
    call outside any block returns that frozen data instead of spawning.
    threading.local() gives each thread an independent cache, so neither
    thread's exit can touch the other's, and nothing survives past either
    block. Reproduces the exact interleaving via real threads + events, not a
    simulation, and checks purely through spawn counts (portable against the
    pre-fix module-global implementation too)."""
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    calls = []
    lock = threading.Lock()

    def fake_list():
        with lock:
            calls.append("list")
        return ["00008150-X"]

    monkeypatch.setattr(device.link, "list_devices", fake_list)

    a_cached = threading.Event()
    b_cached = threading.Event()
    a_exited = threading.Event()
    errors = []

    def thread_a():
        try:
            with device.memoized_run():
                device.list_devices()  # call #1, cached inside A's own block
                a_cached.set()
                assert b_cached.wait(timeout=5), "B never entered its block"
        except Exception as exc:  # don't let a thread swallow its own failure
            errors.append(exc)
        finally:
            a_exited.set()

    def thread_b():
        try:
            assert a_cached.wait(timeout=5), "A never entered its block"
            with device.memoized_run():  # nested-in-time while A is still open
                device.list_devices()  # call #2, B's own block, not A's cache
                b_cached.set()
                assert a_exited.wait(timeout=5), "A never exited its block"
        except Exception as exc:
            errors.append(exc)

    ta = threading.Thread(target=thread_a)
    tb = threading.Thread(target=thread_b)
    ta.start()
    tb.start()
    ta.join(timeout=5)
    tb.join(timeout=5)

    assert not errors
    assert not ta.is_alive() and not tb.is_alive()
    assert len(calls) == 2  # each thread's own block spawned once

    device.list_devices()  # both blocks are closed now: must re-spawn, never
    assert len(calls) == 3  # return whichever thread's cache leaked through


def test_safe_kill_can_spare_the_process_tree(monkeypatch):
    # The tunnel and the forwards are CHILDREN of whatever launched them, so a
    # tree kill aimed at a stale viewer takes the phone link down with it.
    # Measured 2026-08-12: 11 green checks, then a second SideTap launch left
    # the tunnel and WDA dead.
    cmds = []
    monkeypatch.setattr(device.sys, "platform", "win32")
    monkeypatch.setattr(device, "_pid_image", lambda pid: "python.exe")
    monkeypatch.setattr(
        device.subprocess, "run", lambda cmd, **kw: cmds.append((cmd, kw))
    )
    assert device._safe_kill(4242, "python", tree=False) is True
    assert cmds[0][0] == ["taskkill", "/PID", "4242", "/F"]
    assert device._safe_kill(4242, "python") is True  # default still kills the tree
    assert "/T" in cmds[1][0]


# ---- start_tunnel: tunneld runs as its own admin task; start_tunnel starts
# that task only when tunneld is not answering, then waits for a tunnel, and
# names which of the two is missing when it gives up.


def _tunnel_env(monkeypatch, listed_on_call, tunneld_up=True):
    clock = [0.0]
    probes = []
    started = []
    monkeypatch.setattr(device.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(device.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(device.link, "tunneld_up", lambda timeout=3.0: tunneld_up)
    monkeypatch.setattr(
        device.subprocess, "run", lambda cmd, **kw: started.append(cmd) or _Proc("")
    )

    def fake_running(timeout=10.0):
        probes.append(clock[0])
        return len(probes) >= listed_on_call

    monkeypatch.setattr(device, "tunnel_running", fake_running)

    def fake_tunnel_for(udid=None):
        if len(probes) >= listed_on_call:
            return ("fd00::1", 5000, "usb")
        raise device.link.LinkError("no tunnel to the phone: it is not on USB, or not on the same Wi-Fi")

    monkeypatch.setattr(device.link, "tunnel_for", fake_tunnel_for)
    return clock, probes, started


def test_start_tunnel_returns_as_soon_as_a_tunnel_is_listed(monkeypatch):
    clock, probes, started = _tunnel_env(monkeypatch, listed_on_call=2)
    assert device.start_tunnel() is None
    assert len(probes) == 2
    assert clock[0] < device._TUNNEL_READY_TIMEOUT
    assert started == []  # tunneld was up: its task is not touched


def test_start_tunnel_starts_the_tunneld_task_when_tunneld_is_down(monkeypatch):
    _clock, _probes, started = _tunnel_env(monkeypatch, listed_on_call=1, tunneld_up=False)
    device.start_tunnel()
    started = [c for c in started if "/Query" not in c]  # the task's status is read first
    assert started and started[0][:3] == ["schtasks", "/Run", "/TN"]
    assert started[0][3] == device.config.TUNNELD_TASK


def test_start_tunnel_names_tunneld_when_it_never_comes_up(monkeypatch):
    _tunnel_env(monkeypatch, listed_on_call=10_000, tunneld_up=False)
    with pytest.raises(device.DeviceError) as exc:
        device.start_tunnel()
    assert "tunneld is not answering" in str(exc.value)


def test_start_tunnel_names_the_phone_when_tunneld_has_no_tunnel(monkeypatch):
    clock, _probes, _started = _tunnel_env(monkeypatch, listed_on_call=10_000)
    with pytest.raises(device.DeviceError) as exc:
        device.start_tunnel()
    assert "no tunnel to the phone" in str(exc.value)
    assert clock[0] >= device._TUNNEL_READY_TIMEOUT


def test_list_apps_unreachable_with_no_cache_raises_the_reason(monkeypatch, tmp_path):
    # An empty list would read as "no such app"; the agent needs the real cause.
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)

    def down():
        raise device.link.LinkError("tunneld is not answering at http://127.0.0.1:49151")

    monkeypatch.setattr(device.link, "list_apps", down)
    with pytest.raises(device.DeviceError, match="tunneld is not answering"):
        device.list_apps()


def test_list_apps_unreachable_uses_the_saved_list(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    saved = [{"bundle_id": "com.tencent.xin", "name": "WeChat"}]
    (tmp_path / "apps.json").write_text(json.dumps(saved), encoding="utf-8")
    monkeypatch.setattr(device, "_apps_cache_file", lambda: tmp_path / "apps.json")

    def down():
        raise device.link.LinkError("no tunnel to the phone")

    monkeypatch.setattr(device.link, "list_apps", down)
    assert device.list_apps() == saved


def test_reused_pid_is_not_our_relay(monkeypatch, tmp_path):
    # The pid in relay.pid now belongs to some other python job (pid reuse after
    # a reboot): the relay is not running, and stop_all must not kill that job.
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    (tmp_path / "relay.pid").write_text("424242", encoding="utf-8")
    monkeypatch.setattr(device, "_pid_image", lambda pid: "python.exe")
    monkeypatch.setattr(device, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(device, "_pid_cmdline", lambda pid: "python.exe -m some.other.job")
    killed = []
    monkeypatch.setattr(device.subprocess, "run", lambda *a, **k: killed.append(a))
    assert device.proc_status("relay") == "dead"
    assert device.stop_all(("relay",)) == []
    assert killed == []


def test_our_relay_pid_is_recognised(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    (tmp_path / "relay.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(device, "_pid_cmdline", lambda pid: "pythonw.exe -m phone_harness.relay")
    assert device.proc_status("relay") == "running"


def test_down_stops_a_go_ios_runwda_left_from_before_the_tunneld_link(monkeypatch, tmp_path):
    # Its WDA was started without USE_IP and listens on every address: dropping
    # the pid file and leaving it running would keep it reachable from the LAN.
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    (tmp_path / "runwda.pid").write_text("5150", encoding="utf-8")
    monkeypatch.setattr(device, "ios_path", lambda: "C:/npm/ios.exe")
    monkeypatch.setattr(device, "_pid_image", lambda pid: "ios.exe")
    monkeypatch.setattr(device, "_pid_cmdline", lambda pid: "ios.exe runwda --bundleid x")
    killed = []
    monkeypatch.setattr(device.subprocess, "run", lambda cmd, **k: killed.append(cmd))
    assert device.stop_all(("runwda",)) == ["runwda"]
    assert killed and killed[0][:3] == ["taskkill", "/PID", "5150"]


def test_start_tunnel_passes_on_why_the_tunnel_request_failed(monkeypatch):
    monkeypatch.setattr(device.link, "tunneld_up", lambda timeout=3.0: True)
    monkeypatch.setattr(device, "tunnel_running", lambda timeout=10.0: False)
    monkeypatch.setattr(device, "_TUNNEL_READY_TIMEOUT", 0.0)

    def failed(udid=None):
        raise device.link.LinkError("no tunnel to the phone (tunneld's tunnel request failed: HTTP 500 PairingError)")

    monkeypatch.setattr(device.link, "tunnel_for", failed)
    with pytest.raises(device.DeviceError, match="HTTP 500 PairingError"):
        device.start_tunnel()


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill tree semantics")
def test_stopping_the_relay_spares_the_wda_it_started(tmp_path, monkeypatch):
    """Real processes: launcher -> interpreter (both the relay) -> WDA runner.
    The relay's self-heal starts WDA as its child; a relay restart must not
    take WDA down with it (2026-10-18 23:36 ET)."""
    import subprocess
    import time

    import psutil

    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    # the base interpreter: a venv's python.exe would add a launcher per level
    exe = getattr(sys, "_base_executable", sys.executable)
    marker = "phone_harness.relay"
    sleeper = "import time; time.sleep(120)"
    spawn = "import subprocess, time; subprocess.Popen([{exe!r}, '-c', {code!r}, {tag!r}]); time.sleep(120)"
    interpreter = spawn.format(exe=exe, code=sleeper, tag="xcuitest")  # starts the "runner"
    launcher = spawn.format(exe=exe, code=interpreter, tag=marker)
    top = subprocess.Popen([exe, "-c", launcher, marker])
    tree = []
    try:
        deadline = time.time() + 20
        while time.time() < deadline and len(tree) < 2:
            time.sleep(0.2)
            tree = psutil.Process(top.pid).children(recursive=True)
        runner = [p for p in tree if p.cmdline()[-1] == "xcuitest"]
        relay_interpreter = [p for p in tree if p not in runner]
        assert len(runner) == 1 and len(relay_interpreter) == 1
        (tmp_path / "relay.pid").write_text(str(top.pid), encoding="utf-8")
        assert device.stop_all(("relay",)) == ["relay"]
        time.sleep(1)
        assert top.poll() is not None
        assert not relay_interpreter[0].is_running()
        assert runner[0].is_running()
    finally:
        for p in [*tree]:
            try:
                p.kill()
            except psutil.Error:
                pass
        top.kill()


def test_spawn_keeps_the_previous_runs_log(tmp_path, monkeypatch):
    """A restart must not wipe the log that says how the last run ended."""
    import subprocess as sp

    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device, "_pythonw", lambda: sys.executable)
    (tmp_path / "relay.log").write_text("last words of the old relay\n", encoding="utf-8")
    pid = device._spawn_py("relay", ["-c", "print('new run')"])
    for _ in range(100):
        if "new run" in (tmp_path / "relay.log").read_text(encoding="utf-8"):
            break
        time.sleep(0.05)
    text = (tmp_path / "relay.log").read_text(encoding="utf-8")
    assert "last words of the old relay" in text and "new run" in text
    assert text.index("last words") < text.index("started") < text.index("new run")


def test_concurrent_relay_starts_spawn_one(tmp_path, monkeypatch):
    """Two agents finding the relay missing at once must not start two (review 2026-10-20)."""
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device, "_free_port", lambda port: None)
    spawned = []

    def spawn(name, args):
        time.sleep(0.3)  # the gap in which a second caller would also see no relay
        spawned.append(name)
        device._pid_file(name).write_text("1", encoding="utf-8")
        return 1

    monkeypatch.setattr(device, "_spawn_py", spawn)
    monkeypatch.setattr(device, "proc_status", lambda name: "running" if spawned else "not started")
    ts = [threading.Thread(target=device.start_forwards) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert spawned == ["relay"]


def test_log_is_not_cut_while_an_earlier_run_writes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device, "_LOG_KEEP", 10)
    (tmp_path / "relay.log").write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(device, "proc_status", lambda name: "running")
    device._open_log("relay").close()
    assert (tmp_path / "relay.log").read_text(encoding="utf-8").startswith("x" * 100)
    monkeypatch.setattr(device, "proc_status", lambda name: "not started")
    device._open_log("relay").close()
    assert not (tmp_path / "relay.log").read_text(encoding="utf-8").startswith("x" * 100)


def test_free_port_kills_only_a_go_ios_forwarder(monkeypatch):
    """An ios.exe on the port that is not `ios forward <port>` is left alone."""
    monkeypatch.setattr(device.sys, "platform", "win32")
    killed = []

    class R:
        def __init__(self, out=""):
            self.stdout = out

    def run(cmd, **kw):
        if cmd[0] == "netstat":
            return R("  TCP    127.0.0.1:9100   0.0.0.0:0   LISTENING   4242\n")
        if cmd[0] == "tasklist":
            return R("ios.exe   4242 Console 1 10,000 K")
        killed.append(cmd)
        return R()

    monkeypatch.setattr(device.subprocess, "run", run)
    monkeypatch.setattr(device, "_pid_cmdline", lambda pid: "ios.exe tunnel start --userspace")
    device._free_port(9100)
    assert killed == []
    monkeypatch.setattr(device, "_pid_cmdline", lambda pid: "ios.exe forward 9100 9100 --udid x")
    device._free_port(9100)
    assert killed and killed[0][:2] == ["taskkill", "/F"]
