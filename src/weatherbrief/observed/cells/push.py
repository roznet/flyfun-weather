"""Push display files to the droplet's cells inbox (#656).

The home node rsyncs each new ``cells/display/<stamp>.json.gz`` into a
directory on the droplet (``WB_CELLS_PUSH_TARGET``: an rsync destination,
``user@host:/path/`` or a local path for a dev server).  The droplet only
reads what lands there — the same shape as the forecast offload's
``HOST_SNAPSHOT_INBOX``.

**Off by default**: unset, nothing is pushed, so a MacBook dev loop never
uploads unless asked.  A failed push is logged and retried on the next tick
(the file stays pending); it never raises and never blocks analysis.

Only files inside the tick's lookback are candidates, and the set already
pushed is remembered in ``state.json`` — the home node keeps every display
file, and re-listing or re-sending the whole directory each minute would grow
with the archive.  The droplet purges its own copy at 24 h, so an
rsync of the directory would also re-send every purged file.

Plain ``rsync -t`` with explicit files: no ``--mkpath``/``--chmod`` (macOS
ships an old rsync or openrsync).  The files are written 0644 on purpose.

**Revisions (#666).**  ``pushed`` holds display *keys* (``<stamp>`` for
revision 0, ``<stamp>.r<n>`` after), and only a frame's newest revision is a
candidate, so a frame whose lightning was amended is pushed again under its
new name.  A pre-#666 state file (bare stamps) reads as "revision 0 sent".

**Two call sites.**  The runner pushes each live frame the moment its
display file is written (``prune=False``: one slot, leave the rest of the
sent set alone), and again at the end of every tick over the whole lookback,
which is the retry path.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from ..frames import frame_stamp
from .catalogue import latest_display

logger = logging.getLogger(__name__)

PUSH_TARGET_ENV = "WB_CELLS_PUSH_TARGET"
PUSH_TIMEOUT_SECONDS = 120
# rsync's own I/O timeout: a hung ssh connection must not wedge the loop.
RSYNC_IO_TIMEOUT_SECONDS = 60


def push_target() -> str | None:
    raw = os.environ.get(PUSH_TARGET_ENV, "").strip()
    if not raw:
        return None
    # A trailing slash makes rsync treat the target as a directory.
    return raw if raw.endswith("/") else raw + "/"


def push_pending(
    root: Path,
    state: dict,
    slots: Iterable[datetime],
    *,
    target: str | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    prune: bool = True,
) -> int:
    """Push the newest display revision of every slot not pushed yet.  Returns the count.

    Updates ``state`` in place: ``pushed`` (display keys already sent),
    ``last_push`` / ``push_error``.  With ``prune`` (the end-of-tick call),
    ``pushed`` is trimmed to ``slots``; without it (one live frame), the rest
    of the set is left as it was.  Never raises.
    """
    target = target if target is not None else push_target()
    if not target:
        return 0
    if not target.endswith("/"):
        target += "/"
    slots = list(slots)
    if prune:
        _warn_on_outage(state, slots)
    window = {frame_stamp(t) for t in slots}
    pushed = set(state.get("pushed", []))
    if prune:
        pushed = {k for k in pushed if k.split(".")[0] in window}
    files: list[Path] = []
    for t in slots:
        latest = latest_display(root, t)
        if latest is None:
            continue
        path, _revision = latest
        if _key(path) not in pushed:
            files.append(path)
    state["pushed"] = sorted(pushed)
    if not files:
        return 0
    cmd = ["rsync", "-t", f"--timeout={RSYNC_IO_TIMEOUT_SECONDS}", *map(str, files), target]
    try:
        run(cmd, check=True, capture_output=True, text=True, timeout=PUSH_TIMEOUT_SECONDS)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or "").strip().splitlines()[-1:] or [""]
        return _failed(state, f"rsync exit {exc.returncode}: {tail[0]}", len(files))
    except subprocess.TimeoutExpired:
        return _failed(state, f"rsync timed out after {PUSH_TIMEOUT_SECONDS}s", len(files))
    except Exception as exc:  # rsync missing, permissions, anything: never escape the tick
        return _failed(state, f"{type(exc).__name__}: {exc}", len(files))
    state["pushed"] = sorted(pushed | {_key(p) for p in files})
    state["last_push"] = datetime.now(timezone.utc).isoformat()
    state.pop("push_error", None)
    logger.info("cells push: %d display file(s) sent (%s … %s)", len(files),
                _key(files[0]), _key(files[-1]))
    return len(files)


def _key(path: Path) -> str:
    """``<stamp>`` or ``<stamp>.r<n>`` — the file name without ``.json.gz``."""
    return path.name.removesuffix(".json.gz")


def _warn_on_outage(state: dict, slots: list[datetime]) -> None:
    """Say so (once) when pushes stopped for longer than the lookback: frames
    from before the window are never pushed. Harmless for the map (they would
    be past the droplet's stale cut-off), but it should not be silent."""
    last = state.get("last_push")
    if not last or not slots:
        return
    try:
        last_t = datetime.fromisoformat(last)
    except ValueError:
        return
    if last_t < slots[0] and state.get("push_gap_warned") != last:
        logger.warning("cells push: nothing pushed since %s, longer than the lookback; display "
                       "files from before %s will not be pushed", last, slots[0].isoformat())
        state["push_gap_warned"] = last


def _failed(state: dict, why: str, count: int) -> int:
    # The target is not logged: it names a deployment host.
    logger.warning("cells push failed (%d file(s) pending, retried next tick): %s", count, why)
    state["push_error"] = why
    return 0
