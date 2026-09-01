"""Pull the iPhone camera roll to a Windows folder over USB.

Uses AFC (the file service every paired iPhone exposes) via pymobiledevice3 —
the dependency signing.py already requires. Nothing here touches WDA: no
session, no gestures, so a pull runs beside the agent and must never take the
viewer's _ACTION_LOCK. The phone must be paired (trusted) and, first time
after a reboot, unlocked once.

Sync is incremental with no state file: DCIM's subfolder structure is mirrored
under the destination, and a file whose name already exists there is skipped.
Photos are immutable once shot, and mirroring the subfolders means iOS's
IMG_XXXX rollover can never collide two different photos onto one name.
Each file lands via a .part temp and a rename, so a pull killed mid-file never
leaves a truncated photo that "exists" and is skipped forever.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Callable

from . import config

DEFAULT_DIR = Path.home() / "Pictures" / "iPhone"

Progress = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def photos_dir() -> Path:
    """Destination folder: PHOTOS_DIR from .env, else ~/Pictures/iPhone."""
    return Path(config.get("PHOTOS_DIR") or DEFAULT_DIR)


async def _aw(value):
    # pymobiledevice3 10.x mixes sync and coroutine methods on AfcService
    # (listdir answered plain, pull is a coroutine — probed live 2026-09-01),
    # so every service call goes through this instead of guessing per method.
    return await value if inspect.isawaitable(value) else value


async def _connect():
    """AFC service for the pinned phone. Import here like signing.py does,
    so the module loads (and tests run) without pymobiledevice3."""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.afc import AfcService

    lockdown = await _aw(create_using_usbmux(serial=config.SIDETAP_UDID))
    return AfcService(lockdown=lockdown)


async def _pull(dest: Path, progress: Progress) -> dict:
    afc = await _connect()
    pulled = skipped = 0
    errors: list[str] = []
    try:
        subs = [d for d in await _aw(afc.listdir("DCIM")) if not d.startswith(".")]
        for sub in sorted(subs):
            try:
                names = await _aw(afc.listdir(f"DCIM/{sub}"))
            except Exception:
                continue  # a stray file or unreadable entry at DCIM top level
            outdir = dest / sub
            for name in sorted(n for n in names if not n.startswith(".")):
                target = outdir / name
                if target.exists():
                    skipped += 1
                    continue
                outdir.mkdir(parents=True, exist_ok=True)
                progress(f"{sub}/{name}")
                part = target.with_name(target.name + ".part")
                try:
                    await _aw(
                        afc.pull(f"DCIM/{sub}/{name}", str(part), progress_bar=False)
                    )
                    part.replace(target)
                    pulled += 1
                except Exception as exc:
                    part.unlink(missing_ok=True)
                    errors.append(f"{sub}/{name}: {exc}")
    finally:
        try:
            await _aw(afc.close())
        except Exception:
            pass
    return {
        "ok": True,
        "pulled": pulled,
        "skipped": skipped,
        "dest": str(dest),
        "errors": errors,
    }


def pull_photos(dest: Path | str | None = None, progress: Progress = _noop) -> dict:
    """Sync the camera roll into `dest` (default photos_dir()).

    Returns {ok, pulled, skipped, dest, errors}. ok is False only when the
    pull as a whole could not run (no pairing, cable out, dependency missing);
    a single unreadable file lands in errors and the rest still sync.
    """
    where = Path(dest) if dest else photos_dir()
    try:
        return asyncio.run(_pull(where, progress))
    except Exception as exc:  # transport/pairing failure, named not swallowed
        return {
            "ok": False,
            "pulled": 0,
            "skipped": 0,
            "dest": str(where),
            "errors": [str(exc)],
        }
