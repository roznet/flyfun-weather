"""CLI for the observed-cells loop.

    python -m weatherbrief.observed.cells run [--once]
    python -m weatherbrief.observed.cells replay --from 2026-10-03T06:00 --to 2026-10-03T12:00 --out /tmp/replay [--force]
    python -m weatherbrief.observed.cells render --time 2026-10-03T14:05 --out cells.png [--bbox S,W,N,E] [--scale 2]
    python -m weatherbrief.observed.cells status [--hours 24]
    python -m weatherbrief.observed.cells retry-failed
    python -m weatherbrief.observed.cells map [--time latest] --out cells.html [--bbox S,W,N,E] [--open]

Needs ``WB_CELLS_ROOT``.  ``WB_CELLS_SOURCES`` narrows the sources
(default ``opera_dbzh,opera_rate,eumetsat_li``; add ``eumetsat_ctth`` to opt
in), ``WB_CELLS_CATCHUP_HOURS`` sets how far back a sweep reaches (default 6).
"""

from __future__ import annotations

import argparse
import json
import logging
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
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    root = cells_root()
    sources = cells_sources()
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
