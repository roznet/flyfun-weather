#!/usr/bin/env python3
"""Validate the clutter rule over archived radar frames (#696).

The rule was calibrated on one convective night (2026-10-06 23:35Z →
10-07 00:55Z). Before `WB_CELLS_CLUTTER_SUPPRESS` goes on anywhere, it has to
hold over regimes it has not seen. This is that check, as one command, with
**pass/fail gates** rather than a table to squint at.

Why it replays rather than reading the archive directly: `cells clutter` reads
the `clutter` block off catalogues, and every catalogue written before #696 is
`cells-1`/`cells-2` and has none. So a historical day must be re-analysed under
`cells-3` first. Raw frames are therefore the binding retention — 48 h on the
Mac mini, 12 months of frame tars on the NAS — not the 90 days of catalogues.

Usage
-----
Local frames (a dev checkout's own archive)::

    python scripts/clutter_validate.py \\
        --frames ~/Developer/public/flyfun-weather/main/data/observed-archive \\
        --from 2026-10-03T08:15 --to 2026-10-04T11:20 \\
        --work /tmp/clutter-1003

At scale, from a compute node's 48 h of frames. **Fetch, do not replay there**:
the node runs the live cells loop, and a replay peaks at ~2.3 GB and saturates
a core for ~5 s a frame. Pull the frames and replay on this machine::

    python scripts/clutter_validate.py --fetch mac-mini-m4 \\
        --from 2026-10-05T00:00 --to 2026-10-07T00:00 --work /tmp/clutter-mini

Pool independently replayed spans into one verdict — the only way the cross-day
recurrence below means anything, since two days that share nothing but geography
get replayed separately::

    python scripts/clutter_validate.py --report-only \
        --work /tmp/clutter-1003 --also /tmp/clutter-mini --also /tmp/clutter-1007

Gates
-----
* **FAIL** if any flagged cell carried observed lightning or a cloud top. Those
  are vetoes, so a non-zero count is a leak in the code, not a calibration
  result.
* **FAIL** if the flag rate exceeds `--max-flag-pct` (default 5 %). The night it
  was calibrated on gave 0.74 % / 1.44 %; several times that means the rule is
  doing something other than what we think in this regime.
* **WARN** if a site that flagged on two or more days ever showed lightning, or
  if no site is persistent at all (nothing to corroborate against).

Exit status is 1 on any FAIL, 0 otherwise. A WARN never fails the run — it is
for the human reading the output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from weatherbrief.observed.cells.clutter_eval import rows_all, sites, summarise, write_jsonl  # noqa: E402
from weatherbrief.observed.cells.policy import DEFAULT_POLICY  # noqa: E402
from weatherbrief.observed.cells.runner import replay  # noqa: E402
from weatherbrief.observed.frames import SOURCE_SPECS, frame_stamp  # noqa: E402

#: Default root of the cells archive on a compute node (observed-cells.md).
NODE_ROOT = "~/flyfun-data/observed-archive"
#: Sources a replay needs: radar to analyse, rain rate and lightning so the
#: vetoes and the `weather` label can actually fire. Without lightning the
#: run cannot say anything about genuine-core retention.
SOURCES = ("opera_dbzh", "opera_rate", "eumetsat_li")


def utc(text: str) -> datetime:
    t = datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def node_target(name: str) -> str:
    """``user@host`` for a compute node, from ``scripts/ops/hosts.py``.

    Read rather than hardcoded: the mini is LAN-only and its address has moved
    before, and a stale alias is what silently skipped it at deploy time once.
    """
    out = subprocess.run([sys.executable, str(REPO / "scripts/ops/hosts.py"), "node", name],
                         capture_output=True, text=True, check=True).stdout
    for token in out.replace("(", " ").replace(")", " ").split():
        if "@" in token:
            return token
    raise SystemExit(f"could not read an ssh target for node {name!r} from hosts.py:\n{out}")


def fetch_frames(target: str, remote_root: str, start: datetime, end: datetime,
                 work: Path) -> int:
    """Copy the frames in range from a node into ``work``, as one tar over ssh.

    One stream, not a file-per-frame loop or rsync: `openrsync` on macOS has
    failed on this shape before, and 48 h is ~1.7 GB in ~2,000 small files.
    Only the stamps in range travel, so a wider window costs proportionally.
    """
    names: list[str] = []
    for source in SOURCES:
        spec = SOURCE_SPECS[source]
        step = 5 if source.startswith("opera") else 10
        t = start
        while t <= end:
            stamp = frame_stamp(t)
            names.append(f"{source}/{stamp}.{spec.extension}")
            names.append(f"{source}/{stamp}.json")
            t += timedelta(minutes=step)
    work.mkdir(parents=True, exist_ok=True)
    listing = work / ".fetch-list"
    listing.write_text("\n".join(names) + "\n")
    print(f"fetching {len(names)} candidate paths from {target}:{remote_root} …")
    # `-T -` reads the member list from stdin; `--ignore-failed-read` keeps a
    # gap in the archive (a frame the node never got) from failing the fetch.
    remote = (f"cd {remote_root} && tar -c --ignore-failed-read -T - 2>/dev/null")
    with listing.open("rb") as handle:
        fetch = subprocess.Popen(["ssh", target, remote], stdin=handle, stdout=subprocess.PIPE)
        untar = subprocess.Popen(["tar", "-x", "-C", str(work)], stdin=fetch.stdout)
        fetch.stdout.close()
        untar.communicate()
    got = sum(1 for source in SOURCES
              for _ in (work / source).glob(f"*.{SOURCE_SPECS[source].extension}")
              if (work / source).exists())
    print(f"fetched {got} payload frames into {work}")
    return got


def report(rows: list[dict], precision: float, min_days: int) -> dict:
    summary = summarise(rows)
    summary["sites"] = sites(rows, precision=precision, min_days=min_days)
    return summary


def print_report(summary: dict, *, max_flag_pct: float) -> list[str]:
    """Print the report; return the list of FAIL lines (empty = pass)."""
    fails: list[str] = []
    warns: list[str] = []

    print(f"\n{summary['frames']} frames analysed under {DEFAULT_POLICY.policy_version}")
    print(f"{'tier':<8}{'cells':>8}{'suspect':>9}{'confirmed':>11}{'flagged %':>11}"
          f"{'weather':>9}{'lost':>6}")
    for tier, row in summary["tiers"].items():
        print(f"{tier:<8}{row['cells']:>8}{row['suspect']:>9}{row['confirmed']:>11}"
              f"{(row['flagged_pct'] or 0):>11.2f}{row['label_weather']:>9}{row['weather_lost']:>6}")
        if row["weather_lost"]:
            fails.append(f"{tier}: {row['weather_lost']} flagged cells carried lightning or a "
                         f"cloud top — a veto leak, not a calibration result")
        if (row["flagged_pct"] or 0) > max_flag_pct:
            fails.append(f"{tier}: {row['flagged_pct']:.2f} % flagged, over the "
                         f"{max_flag_pct:.2f} % ceiling")
        if not row["label_weather"]:
            warns.append(f"{tier}: no cell in this span carried lightning or a cloud top, so "
                         f"genuine-core retention is untested here (collect LI frames too)")

    print("\nwhy flagged:", ", ".join(f"{k} {v}" for k, v in summary["reasons"].items()) or "—")
    print("features unknown:", ", ".join(f"{k} {v}" for k, v in summary["unknown_features"].items()) or "—")
    delta = summary["peak_minus_robust_db"]
    print(f"peak - robust peak (dB): p50 {delta['p50']} p90 {delta['p90']} max {delta['max']}")

    sx = summary["sites"]
    print(f"\nfixed sites at {sx['precision_deg']}°: {sx['n_sites']} distinct, "
          f"{sx['n_persistent']} seen on >= {sx['min_days']} days, carrying "
          f"{sx['hits_at_persistent_sites']} of {sx['hits_total']} flags")
    print(f"{'lat':>8}{'lon':>9}{'days':>6}{'frames':>8}{'peak p50':>10}{'flash max':>11}  hours UTC")
    for site in sx["sites"][:20]:
        flash = "—" if site["flashes_max"] is None else str(site["flashes_max"])
        hours = ",".join(f"{h:02d}" for h in site["hours_utc"][:10])
        print(f"{site['lat']:>8.1f}{site['lon']:>9.1f}{site['n_days']:>6}{site['frames']:>8}"
              f"{(site['peak_dbz_p50'] or 0):>10.1f}{flash:>11}  {hours}")
    if sx["sites_with_lightning"]:
        warns.append(f"{len(sx['sites_with_lightning'])} flagged site(s) showed lightning at "
                     f"some point — look at them before trusting the rule there")
    if sx["n_sites"] and not sx["n_persistent"]:
        warns.append("no site flagged on more than one day: this span offers no recurrence "
                     "evidence, so it cannot corroborate the rule")

    for line in warns:
        print(f"\nWARN  {line}")
    for line in fails:
        print(f"\nFAIL  {line}")
    if not fails:
        print("\nPASS  no genuine core lost and the flag rate is within the ceiling")
    return fails


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", type=Path, required=True,
                        help="scratch root for fetched frames and replayed catalogues")
    parser.add_argument("--frames", type=Path,
                        help="read frames from this archive root instead of fetching")
    parser.add_argument("--fetch", metavar="NODE",
                        help="compute node to pull frames from (e.g. mac-mini-m4)")
    parser.add_argument("--remote-root", default=NODE_ROOT, help=f"node's root (default {NODE_ROOT})")
    parser.add_argument("--from", dest="start", type=utc, help="first frame (UTC)")
    parser.add_argument("--to", dest="end", type=utc, help="last frame (UTC)")
    parser.add_argument("--report-only", action="store_true",
                        help="skip fetch and replay; report over --work as it stands")
    parser.add_argument("--also", type=Path, action="append", default=[], metavar="ROOT",
                        help="pool another replayed root into the same verdict (repeatable). "
                             "Cross-day recurrence only means something across spans that "
                             "share nothing but geography, and those get replayed separately.")
    parser.add_argument("--max-flag-pct", type=float, default=5.0)
    parser.add_argument("--precision", type=float, default=0.1, help="site grouping, degrees")
    parser.add_argument("--min-days", type=int, default=2, help="days for a site to count persistent")
    parser.add_argument("--jsonl", type=Path, help="write one row per assessed cell")
    parser.add_argument("--json", type=Path, help="write the report as JSON")
    args = parser.parse_args(argv)

    if not args.report_only and not (args.start and args.end):
        parser.error("--from and --to are required unless --report-only")

    if args.fetch and not args.report_only:
        if not fetch_frames(node_target(args.fetch), args.remote_root,
                            args.start, args.end, args.work):
            raise SystemExit("fetched no frames — check the node's root and the window")
        frames_root = args.work
    else:
        frames_root = args.frames or args.work

    if not args.report_only:
        if frames_root.resolve() == args.work.resolve():
            # replay refuses to share a root with a live loop; a fetched tree is
            # ours alone, so give the catalogues their own directory beside it.
            out_root = args.work / "analysis"
        else:
            out_root = args.work
        print(f"replaying {frames_root} -> {out_root} under {DEFAULT_POLICY.policy_version} …")
        done = replay(frames_root, out_root, args.start, args.end, DEFAULT_POLICY,
                      sources=SOURCES)
        print(f"replayed {done} frames")
    else:
        out_root = args.work / "analysis" if (args.work / "analysis").exists() else args.work

    roots = [out_root, *args.also]
    rows: list[dict] = []
    for root in roots:
        found = rows_all(root, DEFAULT_POLICY)
        print(f"  {root}: {len(found)} assessed cells")
        rows.extend(found)
    if not rows:
        raise SystemExit(f"no assessed cells under {DEFAULT_POLICY.policy_version} in "
                         f"{', '.join(str(r) for r in roots)} — catalogues from an older "
                         f"policy are skipped on purpose")
    if args.jsonl:
        print(f"wrote {write_jsonl(rows, args.jsonl)} rows to {args.jsonl}")

    summary = report(rows, args.precision, args.min_days)
    fails = print_report(summary, max_flag_pct=args.max_flag_pct)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=2, sort_keys=True))
        print(f"\nreport written to {args.json}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
