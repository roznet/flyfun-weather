"""Nightly archive and retention for an observed-cells root (#658).

The cells loop never deletes and never talks to the NAS: a slow or offline NAS
must not stall live analysis.  A separate nightly job (private ops repo) calls
the functions here, which are pure over a root and a UTC day:

``pack``     one *complete* day → one plain tar per source, one ``cells`` tar.gz
             and a manifest, into a staging tree laid out exactly like the NAS.
``verify``   manifest sha256 vs the sums the job computed on the NAS copy; on a
             full match, writes ``cells/archive/verified/<day>.json``.  That
             marker is the **only** thing that allows a prune.
``prune``    hot frames older than 48 h and analysis older than 90 days, for
             verified days only, never today or yesterday, and only files the
             verified manifest lists with the same size.  Also clears the
             verified day's staging copy.
``nas-plan`` from the NAS manifests: raw-frame tars past 12 months that are
             neither an event day nor pinned in ``keep-days.json``.  Plans only;
             the job deletes over ssh.  Cells tars are never listed.
``restore``  unpack a day into a scratch root for ``replay`` / ``render`` / ``map``.

Layout of the staging tree and the NAS copy (``…/weather/observed-archive/``)::

    opera_dbzh/2026/20261004.tar        frames + sidecars, uncompressed (already compressed payloads)
    opera_rate/…  eumetsat_li/…
    cells/2026/20261004.tar.gz          catalogues/<day>/*, scores/<day>.jsonl, runs/<day>.jsonl
    manifest/20261004.json              counts, bytes, sha256, members, gaps, versions, event class
    keep-days.json                      (NAS only) days pinned by hand: {"20260827": "reason"}

Tar member names are relative to the observed root (``opera_dbzh/20261004T0000.h5``,
``cells/catalogues/20261004/…``), so extracting into any root rebuilds the tree.
Tars are deterministic (sorted members, mtime 0, no owner) so re-packing the same
files gives the same sha256.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..frames import CANVAS_SUFFIX, SOURCE_SPECS, parse_frame_stamp
from .catalogue import CELLS_DIR, cells_dir, read_catalogue

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA = "observed-archive-manifest/1"

#: A day is packed only this long after it ends, so the late lightning products
#: and the last frames' attribute wait (15 min) have landed.  The job runs after
#: 00:30Z for the same reason.
PACK_SETTLE = timedelta(minutes=30)

DEFAULT_KEEP_FRAMES = timedelta(hours=48)
DEFAULT_KEEP_CELLS = timedelta(days=90)
DEFAULT_NAS_KEEP_DAYS = 365

_DAY_RE = re.compile(r"^\d{8}$")
_STAMP_LEN = len("20261004T0000")


@dataclass(frozen=True)
class EventPolicy:
    """What makes a day worth keeping its raw frames past 12 months.

    **Provisional** (#658): report the classification for the first weeks
    before relying on it.  The values are written into every manifest, so a
    later change never silently reclassifies days already archived — the
    stored ``event`` answer is what ``nas-plan`` reads.
    """

    # Rule 1: at some frame, at least ``min_cells`` cells of ``tier`` carry
    # ``min_flashes`` or more flashes each.
    tier: str = "core41"
    min_flashes: int = 10
    min_cells: int = 3
    # Rule 2: any cell (any tier) with peak >= ``strong_dbz`` and >= ``strong_flashes``.
    strong_dbz: float = 55.0
    strong_flashes: int = 50
    # Below this share of frames with lightning, "not an event" cannot be
    # concluded: the day is classified unknown (``event: null``) and kept.
    min_lightning_fraction: float = 0.5
    name: str = "events-1"


DEFAULT_EVENT_POLICY = EventPolicy()


# --- Days -----------------------------------------------------------------------


def parse_day(text: str) -> date:
    if not _DAY_RE.match(text):
        raise ValueError(f"day must be YYYYMMDD, got {text!r}")
    return datetime.strptime(text, "%Y%m%d").date()


def day_str(day: date) -> str:
    return day.strftime("%Y%m%d")


def day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def is_complete(day: date, now: datetime) -> bool:
    return now >= day_start(day) + timedelta(days=1) + PACK_SETTLE


def protected_days(now: datetime) -> set[date]:
    """Today and yesterday (UTC): never pruned, whatever the markers say."""
    today = now.astimezone(timezone.utc).date()
    return {today, today - timedelta(days=1)}


# --- Paths ----------------------------------------------------------------------


def archive_dir(root: Path) -> Path:
    return cells_dir(root) / "archive"


def default_staging(root: Path) -> Path:
    return archive_dir(root) / "staging"


def verified_marker(root: Path, day: date) -> Path:
    return archive_dir(root) / "verified" / f"{day_str(day)}.json"


def frames_tar_rel(source: str, day: date) -> str:
    return f"{source}/{day.year:04d}/{day_str(day)}.tar"


def cells_tar_rel(day: date) -> str:
    return f"{CELLS_DIR}/{day.year:04d}/{day_str(day)}.tar.gz"


def manifest_rel(day: date) -> str:
    return f"manifest/{day_str(day)}.json"


def _frame_files(root: Path, source: str, day: date) -> list[Path]:
    """The day's payloads and sidecars for one source (no temp files, no canvases)."""
    directory = Path(root) / source
    if not directory.is_dir():
        return []
    prefix = day_str(day) + "T"
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.name.startswith(prefix) and not p.name.endswith(CANVAS_SUFFIX)
    )


def _cells_files(root: Path, day: date) -> list[Path]:
    cells = cells_dir(root)
    d = day_str(day)
    out: list[Path] = []
    cat_dir = cells / "catalogues" / d
    if cat_dir.is_dir():
        out += sorted(p for p in cat_dir.iterdir() if p.is_file() and not p.name.startswith(".tmp-"))
    for sub in ("scores", "runs"):
        p = cells / sub / f"{d}.jsonl"
        if p.is_file():
            out.append(p)
    return out


def hot_days(root: Path) -> set[date]:
    """Every UTC day with frames or analysis under ``root``."""
    root = Path(root)
    days: set[date] = set()
    for source in SOURCE_SPECS:
        directory = root / source
        if directory.is_dir():
            for p in directory.iterdir():
                if len(p.name) >= _STAMP_LEN and p.name[8] == "T" and p.name[:8].isdigit():
                    days.add(parse_day(p.name[:8]))
    cat_root = cells_dir(root) / "catalogues"
    if cat_root.is_dir():
        days |= {parse_day(p.name) for p in cat_root.iterdir() if p.is_dir() and _DAY_RE.match(p.name)}
    for sub in ("scores", "runs"):
        directory = cells_dir(root) / sub
        if directory.is_dir():
            days |= {parse_day(p.stem) for p in directory.glob("*.jsonl") if _DAY_RE.match(p.stem)}
    return days


def pending_days(root: Path, now: datetime) -> list[date]:
    """Complete days with hot data and no verified marker: what tonight packs."""
    return sorted(d for d in hot_days(root)
                  if is_complete(d, now) and not verified_marker(root, d).exists())


# --- Pack -----------------------------------------------------------------------


def _write_tar(dest: Path, members: list[Path], root: Path, *, gz: bool) -> None:
    """Deterministic tar of ``members`` (names relative to ``root``), atomically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as raw:
            gzf = gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=6) if gz else None
            with tarfile.open(fileobj=gzf or raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for path in sorted(members, key=lambda p: p.relative_to(root).as_posix()):
                    info = tar.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o644
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)
            if gzf is not None:
                gzf.close()
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _frame_coverage(root: Path, source: str, day: date) -> dict:
    """Frame slots present (payload *and* sidecar) vs expected, missing runs as ranges."""
    spec = SOURCE_SPECS[source]
    directory = Path(root) / source
    t = day_start(day)
    end = t + timedelta(days=1)
    expected = present = 0
    gaps: list[list[str]] = []
    run_start = None
    while t < end:
        stamp = t.strftime("%Y%m%dT%H%M")
        expected += 1
        ok = (directory / f"{stamp}.{spec.extension}").exists() and (directory / f"{stamp}.json").exists()
        if ok:
            present += 1
            if run_start is not None:
                gaps.append([run_start, prev])
                run_start = None
        elif run_start is None:
            run_start = stamp
        prev = stamp
        t += spec.interval
    if run_start is not None:
        gaps.append([run_start, prev])
    return {"expected": expected, "present": present, "gaps": gaps}


def classify_event(catalogues: list[dict], policy: EventPolicy = DEFAULT_EVENT_POLICY) -> dict:
    """Event-day classification from one day's catalogues.

    ``event`` is ``True``/``False``, or ``None`` when the day cannot be judged
    (no catalogues, or too few frames with lightning) — ``nas-plan`` keeps it.
    """
    frames = len(catalogues)
    with_lightning = 0
    peak_count, peak_time = 0, None
    strongest = None
    for cat in catalogues:
        unavailable = {u.get("what") for u in cat.get("unavailable") or []}
        if "lightning" not in unavailable:
            with_lightning += 1
        n = 0
        for cell in cat.get("cells") or []:
            flashes = cell.get("flashes")
            if flashes is None:
                continue
            if cell.get("tier") == policy.tier and flashes >= policy.min_flashes:
                n += 1
            key = (flashes, cell.get("peak_dbz") or 0.0)
            if (cell.get("peak_dbz") or 0.0) >= policy.strong_dbz and (
                strongest is None or key > (strongest["flashes"], strongest["peak_dbz"])
            ):
                strongest = {"id": cell.get("id"), "tier": cell.get("tier"),
                             "valid_time": cat.get("valid_time"),
                             "peak_dbz": cell.get("peak_dbz"), "flashes": flashes}
        if n > peak_count:
            peak_count, peak_time = n, cat.get("valid_time")
    reasons = []
    if peak_count >= policy.min_cells:
        reasons.append(f"{peak_count} {policy.tier} cells with >= {policy.min_flashes} flashes at {peak_time}")
    if strongest and strongest["flashes"] >= policy.strong_flashes:
        reasons.append(f"cell {strongest['id']} {strongest['peak_dbz']} dBZ with "
                       f"{strongest['flashes']} flashes at {strongest['valid_time']}")
    fraction = with_lightning / frames if frames else 0.0
    if reasons:
        event = True
    elif frames == 0:
        event, reasons = None, ["no catalogues"]
    elif fraction < policy.min_lightning_fraction:
        event, reasons = None, [f"lightning in only {with_lightning}/{frames} frames"]
    else:
        event = False
    return {
        "event": event,
        "reasons": reasons,
        "policy": asdict(policy),
        "frames": frames,
        "frames_with_lightning": with_lightning,
        "peak_cells": peak_count,
        "peak_time": peak_time,
        "strongest": strongest,
    }


def pack_day(root: Path, day: date, out: Path, *, sources: tuple[str, ...], now: datetime,
             event_policy: EventPolicy = DEFAULT_EVENT_POLICY,
             code_revision: str | None = None) -> dict:
    """Pack one complete UTC day of ``root`` into ``out``; return the manifest.

    ``sources`` are the ones *expected* (gaps are counted against them); any
    other source directory that holds frames for the day is packed too.
    Re-packing the same files overwrites with identical tars.
    """
    root, out = Path(root), Path(out)
    if verified_marker(root, day).exists():
        # Its frames may already be pruned: a re-pack would hold only what is
        # left and, rsynced, overwrite the full tar on the NAS.
        raise ValueError(f"{day_str(day)} is already verified on the NAS; refusing to re-pack")
    if not is_complete(day, now):
        raise ValueError(f"{day_str(day)} is not complete yet (packs from "
                         f"{(day_start(day) + timedelta(days=1) + PACK_SETTLE).isoformat()})")
    tars: list[dict] = []
    members: dict[str, list[list]] = {}
    coverage: dict[str, dict] = {}

    def add(rel: str, files: list[Path], gz: bool) -> None:
        dest = out / rel
        _write_tar(dest, files, root, gz=gz)
        tars.append({"path": rel, "files": len(files),
                     "bytes": sum(p.stat().st_size for p in files),
                     "tar_bytes": dest.stat().st_size, "sha256": sha256_file(dest)})
        members[rel] = [[p.relative_to(root).as_posix(), p.stat().st_size] for p in sorted(files)]

    for source in SOURCE_SPECS:
        files = _frame_files(root, source, day)
        if source in sources:
            coverage[source] = _frame_coverage(root, source, day)
        if files:
            add(frames_tar_rel(source, day), files, gz=False)

    cells_files = _cells_files(root, day)
    catalogues = []
    policies: Counter = Counter()
    revisions: Counter = Counter()
    failed = 0
    for p in cells_files:
        if p.name.endswith(".failed.json"):
            failed += 1
        elif p.name.endswith(".json.gz"):
            cat = read_catalogue(p)
            if cat is None:
                continue
            catalogues.append(cat)
            policies[cat.get("policy_version")] += 1
            revisions[str(cat.get("code_revision"))] += 1
    if cells_files:
        add(cells_tar_rel(day), cells_files, gz=True)

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "day": day_str(day),
        "packed_at": now.isoformat(),
        "packed_by": code_revision,
        "tars": tars,
        "members": members,
        "frames": coverage,
        "catalogues": len(catalogues),
        "failed_markers": failed,
        "policy_versions": dict(sorted(policies.items())),
        "code_revisions": dict(sorted(revisions.items())),
        "event": classify_event(catalogues, event_policy),
    }
    path = out / manifest_rel(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    os.replace(tmp, path)
    return manifest


def read_manifest(path: Path) -> dict | None:
    """A manifest, or ``None`` if missing, unreadable or not a manifest."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA or not isinstance(data.get("tars"), list):
        return None
    return data


# --- Verify ---------------------------------------------------------------------


def read_sums(path: Path) -> dict[str, str]:
    """``sha256sum`` output (``<hex>  <path>``) → {normalised path: hex}."""
    sums: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            continue
        name = parts[1].lstrip("*").strip()
        while name.startswith("./"):
            name = name[2:]
        sums[name] = parts[0].lower()
    return sums


def _lookup(sums: dict[str, str], rel: str) -> str | None:
    if rel in sums:
        return sums[rel]
    hits = [v for k, v in sums.items() if k.endswith("/" + rel)]
    return hits[0] if len(hits) == 1 else None


def verify_day(root: Path, day: date, staging: Path, remote_sums: Path, *,
               now: datetime) -> dict:
    """Compare the staged manifest with the NAS sums; mark the day verified on a full match.

    Every tar in the manifest must be in the sums file with the same sha256.
    A day with nothing to archive (no tars) is not marked: there is nothing
    for a prune to rely on.
    """
    manifest = read_manifest(Path(staging) / manifest_rel(day))
    if manifest is None:
        return {"day": day_str(day), "verified": False, "problems": ["no readable manifest in staging"]}
    sums = read_sums(remote_sums)
    problems = []
    for tar in manifest["tars"]:
        remote = _lookup(sums, tar["path"])
        if remote is None:
            problems.append(f"{tar['path']}: not in remote sums")
        elif remote != tar["sha256"]:
            problems.append(f"{tar['path']}: sha256 mismatch (local {tar['sha256'][:12]}, remote {remote[:12]})")
    if not manifest["tars"]:
        problems.append("manifest lists no tars")
    result = {"day": day_str(day), "verified": not problems, "problems": problems,
              "tars": len(manifest["tars"])}
    if not problems:
        marker = verified_marker(root, day)
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_name(marker.name + ".tmp")
        tmp.write_text(json.dumps({"verified_at": now.isoformat(), "manifest": manifest},
                                  sort_keys=True))
        os.replace(tmp, marker)
    return result


def read_verified(root: Path, day: date) -> dict | None:
    try:
        data = json.loads(verified_marker(root, day).read_text())
        manifest = data["manifest"]
        if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("day") != day_str(day):
            return None
        return manifest
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def verified_days(root: Path) -> list[date]:
    directory = archive_dir(root) / "verified"
    if not directory.is_dir():
        return []
    return sorted(parse_day(p.stem) for p in directory.glob("*.json") if _DAY_RE.match(p.stem))


# --- Prune ----------------------------------------------------------------------


def prune(root: Path, *, now: datetime, keep_frames: timedelta = DEFAULT_KEEP_FRAMES,
          keep_cells: timedelta = DEFAULT_KEEP_CELLS, staging: Path | None = None,
          execute: bool = False) -> dict:
    """Delete hot files of verified days past their retention.

    A file goes only when the verified manifest lists it **with the same
    size**: a frame filled in by a late sweep after packing, or a jsonl that
    grew, is not in the archive and stays (reported as ``kept_unarchived``).
    Frames go by their own stamp (``< now - keep_frames``); the analysis goes
    a whole day at a time once the day ended ``keep_cells`` ago.
    """
    root = Path(root)
    staging = Path(staging) if staging is not None else default_staging(root)
    protect = protected_days(now)
    frame_cutoff = now - keep_frames
    cells_cutoff = now - keep_cells
    deleted: list[str] = []
    unarchived: list[str] = []
    skipped: list[str] = []
    freed = 0

    def drop(path: Path, rel: str) -> None:
        nonlocal freed
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        deleted.append(rel)
        freed += size
        if execute:
            path.unlink()

    for day in verified_days(root):
        manifest = read_verified(root, day)
        if manifest is None:
            skipped.append(f"{day_str(day)}: unreadable verified marker")
            continue
        # The staged copy of a verified day is on the NAS; it has no reason to
        # stay, even for yesterday (the protection is for hot data).
        for rel in [t["path"] for t in manifest["tars"]] + [manifest_rel(day)]:
            p = staging / rel
            if p.exists():
                drop(p, f"staging/{rel}")
        if day in protect:
            continue
        listed = {name: size for rows in manifest["members"].values() for name, size in rows}
        cells_due = day_start(day) + timedelta(days=1) <= cells_cutoff
        for source in SOURCE_SPECS:
            for path in _frame_files(root, source, day):
                if parse_frame_stamp(path.name[:_STAMP_LEN]) >= frame_cutoff:
                    continue
                rel = path.relative_to(root).as_posix()
                if listed.get(rel) == path.stat().st_size:
                    drop(path, rel)
                else:
                    unarchived.append(rel)
        if cells_due:
            for path in _cells_files(root, day):
                rel = path.relative_to(root).as_posix()
                if listed.get(rel) == path.stat().st_size:
                    drop(path, rel)
                else:
                    unarchived.append(rel)
            if execute:
                try:
                    (cells_dir(root) / "catalogues" / day_str(day)).rmdir()
                except OSError:
                    pass  # absent, or something unarchived stayed in it
    return {"execute": execute, "deleted": deleted, "freed_bytes": freed,
            "kept_unarchived": unarchived, "skipped": skipped}


# --- NAS plan -------------------------------------------------------------------


def read_keep_days(path: Path) -> dict[str, str]:
    """``keep-days.json``: {"YYYYMMDD": "reason"}.  Missing or corrupt → raises.

    Failing is deliberate: a NAS mount that is down, or a typo in the file,
    must not turn into deleting the days someone pinned.
    """
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not all(isinstance(k, str) and _DAY_RE.match(k) for k in data):
        raise ValueError(f"{path}: expected an object keyed by YYYYMMDD")
    return {k: str(v) for k, v in data.items()}


def nas_plan(manifests_dir: Path, keep_days: dict[str, str], today: date, *,
             keep_days_count: int = DEFAULT_NAS_KEEP_DAYS, present: set[str] | None = None) -> dict:
    """Raw-frame tars on the NAS that may be deleted.

    Listed only when the day is older than ``keep_days_count`` days, its
    manifest is readable and says ``event: false``, and it is not pinned.  A
    missing or corrupt manifest lists nothing — the tar is kept.  Cells tars
    are never listed.  ``present`` (paths on the NAS) narrows the plan to tars
    still there, so it does not re-list what earlier nights deleted.
    """
    cutoff = today - timedelta(days=keep_days_count)
    delete: list[str] = []
    kept: Counter = Counter()
    for path in sorted(Path(manifests_dir).glob("*.json")):
        if not _DAY_RE.match(path.stem):
            continue
        day = parse_day(path.stem)
        if day >= cutoff:
            kept["recent"] += 1
            continue
        manifest = read_manifest(path)
        if manifest is None or manifest.get("day") != path.stem:
            kept["unreadable manifest"] += 1
            logger.warning("nas-plan: unreadable manifest %s, keeping its tars", path)
            continue
        if path.stem in keep_days:
            kept["keep-days"] += 1
            continue
        event = (manifest.get("event") or {}).get("event", None)
        if event is not False:
            kept["event day" if event else "event unknown"] += 1
            continue
        for tar in manifest["tars"]:
            rel = str(tar.get("path", ""))
            source = rel.split("/", 1)[0]
            if (source not in SOURCE_SPECS or not rel.endswith(f"/{path.stem}.tar")
                    or rel != frames_tar_rel(source, day)):
                continue  # cells tars, or anything unexpected, are never listed
            if present is not None and rel not in present:
                continue
            delete.append(rel)
        kept["planned"] += 1
    return {"delete": delete, "days": dict(kept), "cutoff": day_str(cutoff)}


# --- Restore --------------------------------------------------------------------


def restore_day(day: date, src: Path, dest_root: Path) -> dict:
    """Unpack a day's frame tars and cells tar from ``src`` (staging or a NAS copy)
    into ``dest_root``.  Refuses to overwrite an existing file."""
    src, dest_root = Path(src), Path(dest_root)
    tars = [src / frames_tar_rel(s, day) for s in SOURCE_SPECS] + [src / cells_tar_rel(day)]
    tars = [t for t in tars if t.exists()]
    if not tars:
        raise FileNotFoundError(f"no archive for {day_str(day)} under {src}")
    files = 0
    for tar_path in tars:
        with tarfile.open(tar_path, "r:*") as tar:
            for member in tar.getmembers():
                target = (dest_root / member.name).resolve()
                if not member.isfile() or not target.is_relative_to(dest_root.resolve()):
                    raise ValueError(f"{tar_path}: unexpected member {member.name!r}")
                if target.exists():
                    raise FileExistsError(f"{target} exists; restore into an empty scratch root")
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as handle, target.open("wb") as out:
                    shutil.copyfileobj(handle, out)
                files += 1
    return {"day": day_str(day), "tars": [t.relative_to(src).as_posix() for t in tars], "files": files}


__all__ = [
    "DEFAULT_EVENT_POLICY", "EventPolicy", "MANIFEST_SCHEMA", "classify_event", "nas_plan",
    "pack_day", "pending_days", "prune", "read_keep_days", "read_manifest", "read_sums",
    "restore_day", "verify_day",
]
