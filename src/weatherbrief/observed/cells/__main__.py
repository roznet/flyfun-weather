"""CLI for the observed-cells loop.

    python -m weatherbrief.observed.cells run [--once]
    python -m weatherbrief.observed.cells replay --from 2026-10-03T06:00 --to 2026-10-03T12:00 --out /tmp/replay [--force]
    python -m weatherbrief.observed.cells render --time 2026-10-03T14:05 --out cells.png [--bbox S,W,N,E] [--scale 2]
    python -m weatherbrief.observed.cells status [--hours 24]
    python -m weatherbrief.observed.cells retry-failed
    python -m weatherbrief.observed.cells map [--time latest] --out cells.html [--bbox S,W,N,E] [--open]
    python -m weatherbrief.observed.cells archive pending | pack --day D | verify --day D --remote-sums F
                                                  | prune [--execute] | nas-plan … | restore …

``archive`` is the nightly job's toolbox (#658, see ``archive.py``): ``nas-plan``
and ``restore`` work without ``WB_CELLS_ROOT``.

Needs ``WB_CELLS_ROOT``.  ``WB_CELLS_SOURCES`` narrows the sources
(default ``opera_dbzh,opera_rate,eumetsat_li``; add ``eumetsat_ctth`` to opt
in), ``WB_CELLS_CATCHUP_HOURS`` sets how far back a sweep reaches (default 6).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .policy import DEFAULT_POLICY
from .runner import (
    FrameCache,
    Workspace,
    catchup_lookback,
    cells_root,
    cells_sources,
    coverage_report,
    replay,
    retry_failed,
    run_forever,
    run_tick,
)


def _utc(text: str) -> datetime:
    t = datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _duration(text: str) -> timedelta:
    """``48h`` / ``90d`` / ``30m``."""
    units = {"m": "minutes", "h": "hours", "d": "days"}
    if len(text) < 2 or text[-1] not in units:
        raise argparse.ArgumentTypeError(f"duration like 48h or 90d, got {text!r}")
    return timedelta(**{units[text[-1]]: float(text[:-1])})


def _add_archive(sub) -> None:
    from .archive import DEFAULT_KEEP_CELLS, DEFAULT_KEEP_FRAMES, DEFAULT_NAS_KEEP_DAYS, parse_day

    arc = sub.add_parser("archive", help="nightly archive to the NAS and retention (#658)")
    asub = arc.add_subparsers(dest="archive_command", required=True)
    asub.add_parser("pending", help="complete days with hot data and no verified marker")
    pk = asub.add_parser("pack", help="pack one complete UTC day into the staging tree")
    pk.add_argument("--day", required=True, type=parse_day)
    pk.add_argument("--out", type=Path, help="staging tree (default <root>/cells/archive/staging)")
    vf = asub.add_parser("verify", help="compare the manifest with sha256sums of the NAS copy")
    vf.add_argument("--day", required=True, type=parse_day)
    vf.add_argument("--remote-sums", required=True, type=Path, help="sha256sum output computed on the NAS")
    vf.add_argument("--out", type=Path, help="staging tree holding the manifest")
    pr = asub.add_parser("prune", help="delete hot data of verified days past retention (dry run by default)")
    pr.add_argument("--keep-frames", type=_duration, default=DEFAULT_KEEP_FRAMES)
    pr.add_argument("--keep-cells", type=_duration, default=DEFAULT_KEEP_CELLS)
    pr.add_argument("--out", type=Path, help="staging tree to clear for verified days")
    pr.add_argument("--execute", action="store_true", help="actually delete")
    npl = asub.add_parser("nas-plan", help="raw-frame tars on the NAS past retention (prints paths)")
    npl.add_argument("--manifests", required=True, type=Path)
    npl.add_argument("--keep-days", required=True, type=Path, help="keep-days.json (must exist)")
    npl.add_argument("--today", type=parse_day, help="default: today UTC")
    npl.add_argument("--keep", type=_duration, default=timedelta(days=DEFAULT_NAS_KEEP_DAYS))
    npl.add_argument("--present", type=Path, help="tar paths on the NAS, one per line (relative)")
    rs = asub.add_parser("restore", help="unpack a day into a scratch root")
    rs.add_argument("--day", required=True, type=parse_day)
    rs.add_argument("--from", dest="src", required=True, type=Path)
    rs.add_argument("--to", required=True, type=Path)


def _archive(args, sources) -> int:
    from . import archive
    from .runner import cells_root, code_revision

    now = datetime.now(timezone.utc)
    cmd = args.archive_command
    if cmd == "nas-plan":
        today = args.today or now.date()
        plan = archive.nas_plan(
            args.manifests, archive.read_keep_days(args.keep_days), today,
            keep_days_count=int(args.keep.total_seconds() // 86400),
            present=({line.strip().removeprefix("./") for line in args.present.read_text().splitlines()
                      if line.strip()} if args.present else None),
        )
        for path in plan["delete"]:
            print(path)
        print(json.dumps({"cutoff": plan["cutoff"], "days": plan["days"],
                          "tars": len(plan["delete"])}), file=sys.stderr)
        return 0
    if cmd == "restore":
        live = os.environ.get("WB_CELLS_ROOT", "").strip()
        if live and Path(live).expanduser().resolve() == args.to.resolve():
            raise SystemExit("restore into a scratch root, never the live WB_CELLS_ROOT")
        print(json.dumps(archive.restore_day(args.day, args.src, args.to), indent=2))
        return 0
    root = cells_root()
    staging = getattr(args, "out", None) or archive.default_staging(root)
    if cmd == "pending":
        for day in archive.pending_days(root, now):
            print(archive.day_str(day))
        return 0
    if cmd == "pack":
        manifest = archive.pack_day(root, args.day, staging, sources=sources, now=now,
                                    code_revision=code_revision())
        summary = {k: manifest[k] for k in ("day", "tars", "frames", "catalogues", "failed_markers",
                                             "policy_versions", "code_revisions", "event")}
        print(json.dumps(summary, indent=2))
        return 0
    if cmd == "verify":
        result = archive.verify_day(root, args.day, staging, args.remote_sums, now=now)
        print(json.dumps(result, indent=2))
        return 0 if result["verified"] else 1
    if cmd == "prune":
        result = archive.prune(root, now=now, keep_frames=args.keep_frames, keep_cells=args.keep_cells,
                               staging=staging, execute=args.execute)
        for rel in result["deleted"]:
            print(("deleted " if args.execute else "would delete ") + rel)
        for rel in result["kept_unarchived"]:
            print("kept (not in the verified archive) " + rel)
        for line in result["skipped"]:
            print("skipped " + line)
        print(json.dumps({"execute": args.execute, "files": len(result["deleted"]),
                          "freed_bytes": result["freed_bytes"],
                          "kept_unarchived": len(result["kept_unarchived"])}), file=sys.stderr)
        return 0
    raise AssertionError(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m weatherbrief.observed.cells")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="collect and analyse continuously")
    run.add_argument("--once", action="store_true", help="one tick, then exit")
    rep = sub.add_parser("replay", help="re-run the analysis over archived frames")
    rep.add_argument("--from", dest="start", required=True, type=_utc)
    rep.add_argument("--to", dest="end", required=True, type=_utc)
    rep.add_argument("--out", required=True, type=Path)
    rep.add_argument("--force", action="store_true")
    ren = sub.add_parser("render", help="review PNG of one frame")
    ren.add_argument("--time", required=True, type=_utc)
    ren.add_argument("--out", required=True, type=Path)
    ren.add_argument("--bbox", help="south,west,north,east in degrees")
    ren.add_argument("--scale", type=int, default=1)
    ren.add_argument("--root", type=Path, help="read catalogues from here (e.g. a replay)")
    st = sub.add_parser("status", help="frame coverage and catalogue count")
    st.add_argument("--hours", type=float, default=24.0)
    sub.add_parser("retry-failed", help="clear failure markers so the loop retries those frames")
    mp = sub.add_parser("map", help="interactive HTML map of one frame (radar + cells)")
    mp.add_argument("--time", help="frame time (default: newest catalogue)")
    mp.add_argument("--out", required=True, type=Path)
    mp.add_argument("--bbox", help="south,west,north,east in degrees (default: radar extent)")
    mp.add_argument("--open", action="store_true", help="open it in the default browser")
    _add_archive(sub)
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    sources = cells_sources()
    if args.command == "archive":
        return _archive(args, sources)
    root = cells_root()
    lookback = catchup_lookback()

    if args.command == "run":
        ws = Workspace(root)
        if args.once:
            run_tick(ws, DEFAULT_POLICY, FrameCache(ws.frames), sources, lookback)
        else:
            run_forever(ws, DEFAULT_POLICY, sources=sources, lookback=lookback)
    elif args.command == "replay":
        done = replay(root, args.out, args.start, args.end, DEFAULT_POLICY,
                      force=args.force, sources=sources)
        print(f"replayed {done} frames into {args.out}")
    elif args.command == "render":
        from .render import render_frame

        ws = Workspace(args.root or root, frames_root=root)
        bbox = tuple(float(v) for v in args.bbox.split(",")) if args.bbox else None
        print(render_frame(ws, args.time, args.out, bbox=bbox, scale=args.scale))
    elif args.command == "map":
        from .webmap import render_map

        ws = Workspace(root)
        if args.time:
            when = _utc(args.time)
        else:
            newest = sorted((root / "cells" / "catalogues").glob("*/*.json.gz"))
            if not newest:
                raise SystemExit("no catalogues yet — run the loop first")
            from ..frames import parse_frame_stamp

            when = parse_frame_stamp(newest[-1].name.split(".")[0])
        bbox = tuple(float(v) for v in args.bbox.split(",")) if args.bbox else None
        out = render_map(ws, when, args.out, bbox=bbox)
        print(out)
        if args.open:
            import webbrowser

            webbrowser.open(out.resolve().as_uri())
    elif args.command == "retry-failed":
        print(f"cleared {retry_failed(Workspace(root))} failure marker(s)")
    elif args.command == "status":
        end = datetime.now(timezone.utc)
        report = coverage_report(Workspace(root), end - timedelta(hours=args.hours), end, sources)
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
