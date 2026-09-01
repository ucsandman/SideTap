"""photos.pull_photos against a fake AFC — no phone, no pymobiledevice3."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import photos  # noqa: E402


class FakeAfc:
    """Mimics pymobiledevice3's mixed API: listdir sync, pull a coroutine."""

    def __init__(self, tree, fail=()):
        self.tree = tree  # {"DCIM": {"100APPLE": {"IMG_1.JPG": b"..."}}}
        self.fail = set(fail)
        self.closed = False

    def _node(self, path):
        node = self.tree
        for part in path.split("/"):
            node = node[part]
        return node

    def listdir(self, path):
        return list(self._node(path))

    async def pull(self, src, dst, progress_bar=True):  # noqa: vulture  (duck-typed: photos passes it)
        if src in self.fail:
            raise OSError(f"unreadable: {src}")
        with open(dst, "wb") as f:
            f.write(self._node(src))

    async def close(self):
        self.closed = True


def _wire(monkeypatch, afc):
    async def connect():
        return afc

    monkeypatch.setattr(photos, "_connect", connect)
    return afc


TREE = {
    "DCIM": {
        "100APPLE": {"IMG_0001.JPG": b"one", "IMG_0002.HEIC": b"two"},
        "101APPLE": {"IMG_0001.JPG": b"rollover-twin"},
        ".MISC": {"junk": b"x"},
    }
}


def test_pulls_new_files_mirroring_subfolders(tmp_path, monkeypatch):
    afc = _wire(monkeypatch, FakeAfc(TREE))
    result = photos.pull_photos(tmp_path)
    assert result["ok"] and result["pulled"] == 3 and result["skipped"] == 0
    # Rollover twins (same name, different folder) both survive.
    assert (tmp_path / "100APPLE" / "IMG_0001.JPG").read_bytes() == b"one"
    assert (tmp_path / "101APPLE" / "IMG_0001.JPG").read_bytes() == b"rollover-twin"
    # Hidden folders never sync, and the service is closed.
    assert not (tmp_path / ".MISC").exists()
    assert afc.closed


def test_second_run_skips_everything(tmp_path, monkeypatch):
    _wire(monkeypatch, FakeAfc(TREE))
    photos.pull_photos(tmp_path)
    _wire(monkeypatch, FakeAfc(TREE))
    again = photos.pull_photos(tmp_path)
    assert again["pulled"] == 0 and again["skipped"] == 3


def test_one_bad_file_does_not_stop_the_rest(tmp_path, monkeypatch):
    _wire(monkeypatch, FakeAfc(TREE, fail={"DCIM/100APPLE/IMG_0001.JPG"}))
    result = photos.pull_photos(tmp_path)
    assert result["ok"] and result["pulled"] == 2
    assert result["errors"] and "IMG_0001" in result["errors"][0]
    # The failed file left no .part behind and stays pullable next run.
    assert not list((tmp_path / "100APPLE").glob("*.part"))
    _wire(monkeypatch, FakeAfc(TREE))
    assert photos.pull_photos(tmp_path)["pulled"] == 1


def test_connection_failure_is_ok_false(tmp_path, monkeypatch):
    async def connect():
        raise OSError("no usbmux")

    monkeypatch.setattr(photos, "_connect", connect)
    result = photos.pull_photos(tmp_path)
    assert result["ok"] is False and "no usbmux" in result["errors"][0]


def test_partial_file_is_not_mistaken_for_synced(tmp_path, monkeypatch):
    # A truncated .part from a killed pull must not shadow the real file.
    (tmp_path / "100APPLE").mkdir(parents=True)
    (tmp_path / "100APPLE" / "IMG_0001.JPG.part").write_bytes(b"trunc")
    _wire(monkeypatch, FakeAfc(TREE))
    result = photos.pull_photos(tmp_path)
    assert result["pulled"] == 3
    assert (tmp_path / "100APPLE" / "IMG_0001.JPG").read_bytes() == b"one"


def test_photos_dir_env_override(monkeypatch):
    monkeypatch.setattr(photos.config, "get", lambda k, d=None: r"C:\pix")
    assert str(photos.photos_dir()) == r"C:\pix"
    monkeypatch.setattr(photos.config, "get", lambda k, d=None: None)
    assert photos.photos_dir() == photos.DEFAULT_DIR


def test_aw_handles_both_shapes():
    async def coro():
        return 1

    assert asyncio.run(photos._aw(coro())) == 1
    assert asyncio.run(photos._aw(2)) == 2
