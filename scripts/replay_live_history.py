"""Replay real flights' live histories through the current live layer (#688).

Promoted from the prod-briefings-review skill's ad-hoc replay: each flight's prod ticks
are re-run through the production ``commit_live_update`` at their own tick
times, with the METAR/TAF/SIGMET texts from ``live_history.jsonl`` and,
optionally, what the droplet would have had of the observed layers:

- ``--observed OBS.json``: route radar/lightning per tick (``on_mini.py observed``);
- ``--cells CELLS_DIR``: the cells display files (``on_mini.py cells``), used
  as of each tick: only a file already received by then (its mtime, kept from
  the node) is read, exactly as the droplet's ``DisplayStore`` would.

Also ``score-estimates``: joins every logged storm estimate (closest approach
at current motion) with the same storm's observed positions in later frames
and reports the error by horizon, next to persistence (the storm staying
put). That score decides whether the estimate may leave the detail pop-up.

Usage (from the repo root, repo venv):
    python scripts/replay_live_history.py replay DIR OUT [--observed OBS.json] [--cells CELLS_DIR] [FLIGHT...]
    python scripts/replay_live_history.py score-estimates DIR CELLS_DIR [FLIGHT...]

DIR is a pulled tree of ``DATA_DIR/packs/<user>/<flight>/`` (live files plus
the pack ``briefing.json`` files), see ``.claude/skills/prod-briefings-review/SKILL.md``.
Needs ``AIRPORTS_DB`` for the replay (corridor discovery).
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "tests"), str(REPO / "src")]

WMO_HEADER = re.compile(r"^W[SCV]\w+\s+\w{4}\s+(\d{2})(\d{2})(\d{2})")


def histories(root: Path):
    for p in sorted(root.glob("**/live_history.jsonl")):
        yield p.parent, [json.loads(line) for line in p.open() if line.strip()]


def _header_issue_time(raw: str, valid_from: str | None, fallback: str) -> str:
    """A SIGMET's issue time from its WMO header (what the #683 lookahead can see)."""
    m = WMO_HEADER.match(raw or "")
    if not m:
        return fallback
    day, hh, mm = (int(g) for g in m.groups())
    t = datetime.fromisoformat(valid_from or fallback).replace(day=day, hour=hh, minute=mm, second=0, microsecond=0)
    return t.isoformat() if t <= datetime.fromisoformat(fallback) else fallback


def as_of_store(cells_dir: Path):
    """A ``DisplayStore`` over ``cells_dir`` that only lists the files received
    by its ``now`` (file mtime = when the node wrote it, kept by ``on_mini.py
    cells``)."""
    from weatherbrief.observed.cells_display import DisplayStore

    class AsOfDisplayStore(DisplayStore):
        now: datetime | None = None

        def list(self):
            return [d for d in super().list() if self.now is None or d.received_at <= self.now]

    return AsOfDisplayStore(cells_dir)


def replay(root: Path, out_root: Path, observed_path: Path | None, cells_dir: Path | None,
           only: list[str], airports_db: str) -> None:
    import build_live_scenario as bls
    from live_scenario_replay import observations_at, sigmets_at

    from weatherbrief.models.observed import ObservedConditions
    from weatherbrief.observed.storms import load_cell_frames
    from weatherbrief.tasks.live_layer import commit_live_update, load_live_history

    observed = json.loads(observed_path.read_text()) if observed_path else {}
    store = as_of_store(cells_dir) if cells_dir else None
    for d, _ in histories(root):
        if only and not any(o in d.name for o in only):
            continue
        try:
            inputs = bls.inputs_from_history(d)
            for s in inputs["sigmets"]:
                s["issued_at"] = _header_issue_time(s["report"].get("raw_text"), s["report"].get("valid_from"), s["issued_at"])
            scenario = {"inputs": inputs, "derived": bls.build_derived(inputs, airports_db)}
            real_dirs = sorted(p for p in d.iterdir() if (p / "briefing.json").exists())
            packs = []
            for i, p in enumerate(inputs["packs"]):
                # The real pack's own observed baseline (radar/lightning) — the
                # METAR/SIGMET baselines are rebuilt with the current code.
                real = json.loads(real_dirs[min(i, len(real_dirs) - 1)].joinpath("briefing.json").read_text())
                briefing = {k: real[k] for k in ("route", "departure_time", "days_out", "alternates") if k in real}
                baseline = scenario["derived"]["baselines"][i]
                if baseline is not None:
                    briefing.update(baseline)
                    if real.get("observed_conditions") is not None:
                        briefing["observed_conditions"] = real["observed_conditions"]
                pack_dir = out_root / d.parent.name / d.name / p["timestamp"].replace(":", "-")
                pack_dir.mkdir(parents=True, exist_ok=True)
                (pack_dir / "briefing.json").write_text(json.dumps(briefing))
                packs.append((datetime.fromisoformat(p["active_from"]), p["timestamp"], pack_dir, briefing))
            by_tick = observed.get(d.name, {})
            for at in sorted({datetime.fromisoformat(r["tick_at"]) for r in load_live_history(d) if r.get("tick_at")}):
                active = [p for p in packs if p[0] <= at]
                if not active:
                    continue
                _, ts, pack_dir, briefing = active[-1]
                obs = by_tick.get(at.isoformat())
                cells = None
                if store is not None:
                    store.now = at
                    cells = load_cell_frames(at, store)
                commit_live_update(
                    pack_dir, briefing_data=briefing,
                    observations=observations_at(scenario, at), sigmets=sigmets_at(scenario, at),
                    observed=ObservedConditions.model_validate(obs) if obs else None,
                    started_at=at, pack_timestamp=ts, now=at, cells=cells,
                )
            print("ok", d.name, file=sys.stderr)
        except Exception as e:  # keep going; report per flight
            print("FAIL", d.name, type(e).__name__, e, file=sys.stderr)


# --- Scoring the estimate (#688 addendum) --------------------------------------

#: Horizon buckets (minutes from the frame to the estimated closest approach).
BUCKETS = ((0, 30), (30, 60), (60, 10_000))


def _progress_for(d: Path, recs: list[dict], tick_at: str):
    """The flight's progress (#759) as of the estimate's tick, from the pack
    briefing that tick read."""
    from weatherbrief.models.analysis import RouteConfig
    from weatherbrief.tasks.artifacts import parse_target_time
    from weatherbrief.tasks.live_layer import flight_progress

    packs = [r for r in recs if r["type"] == "pack" and r["tick_at"] <= tick_at]
    if not packs:
        return None
    path = d / packs[-1]["pack_dir_name"] / "briefing.json"
    if not path.exists():
        return None
    briefing = json.loads(path.read_text())
    route = RouteConfig.model_validate(briefing["route"])
    return flight_progress(route, parse_target_time(briefing), datetime.fromisoformat(tick_at))


def _frames(cells_dir: Path) -> list[tuple[datetime, dict[str, dict]]]:
    """Every frame's newest revision: (valid time, cell id -> cell)."""
    from weatherbrief.observed.cells_display import DisplayStore

    store = DisplayStore(cells_dir)
    out = []
    for d in sorted(store.list(), key=lambda d: d.valid_time):
        data = store.read(d.stamp, d.revision)
        if data is not None:
            out.append((d.valid_time, {c["id"]: c for c in data.get("cells", []) if "id" in c}))
    return out


def observed_cpa(est: dict, progress, frames) -> tuple[float, datetime] | None:
    """Closest observed approach of the storm (any of its cell ids) to the
    aircraft position (the flight's progress), over the frames from the estimate's frame up to
    its estimated closest approach + 30 min. None when the storm is no longer
    tracked by the estimated time (lost lineage)."""
    from euro_aip.utils.geometry import haversine_nm

    frame_time = datetime.fromisoformat(est["frame_time"])
    cpa_time = datetime.fromisoformat(est["cpa_time"])
    ids = set(est["cell_ids"])
    best = None
    last_seen = None
    for t, by_id in frames:
        if t < frame_time or t > cpa_time + timedelta(minutes=30):
            continue
        cells = [by_id[i] for i in ids if i in by_id]
        if not cells:
            continue
        last_seen = t
        if t < datetime.fromisoformat(est["tick_at"]):
            continue
        a = progress.position(t)
        d = min(haversine_nm(a[0], a[1], c["lat"], c["lon"]) for c in cells)
        if best is None or d < best[0]:
            best = (d, t)
    if best is None or last_seen is None or last_seen < cpa_time - timedelta(minutes=10):
        return None
    return best


def score_estimates(root: Path, cells_dir: Path, only: list[str]) -> dict:
    from weatherbrief.observed.storms import estimate

    frames = _frames(cells_dir)
    rows: dict[tuple, list[dict]] = {b: [] for b in BUCKETS}
    lost = {b: 0 for b in BUCKETS}
    for d, recs in histories(root):
        if only and not any(o in d.name for o in only):
            continue
        for est in (r for r in recs if r["type"] == "estimate"):
            bucket = next(b for b in BUCKETS if b[0] <= est["horizon_min"] < b[1])
            progress = _progress_for(d, recs, est["tick_at"])
            if progress is None or not progress.timed or progress.total_nm <= 0:
                continue
            obs = observed_cpa(est, progress, frames)
            if obs is None:
                lost[bucket] += 1
                continue
            frame_time = datetime.fromisoformat(est["frame_time"])
            tick = datetime.fromisoformat(est["tick_at"])
            # Persistence: the same storm, unmoved, against the same 4-D track.
            still = estimate(est["lat"], est["lon"], 0.0, 0.0, frame_time, progress, tick, None)
            rows[bucket].append({
                "flight": d.name, "storm": est["storm_id"],
                "cpa_err_nm": abs(est["cpa_nm"] - obs[0]),
                "time_err_min": abs((datetime.fromisoformat(est["cpa_time"]) - obs[1]).total_seconds()) / 60,
                "persist_err_nm": abs(still.cpa_nm - obs[0]) if still else None,
            })
    report = {}
    for b in BUCKETS:
        xs = rows[b]
        label = f"{b[0]}-{b[1]} min" if b[1] < 10_000 else f"{b[0]}+ min"
        persist = [x["persist_err_nm"] for x in xs if x["persist_err_nm"] is not None]
        report[label] = {
            "n": len(xs), "lost": lost[b],
            "median_cpa_err_nm": round(statistics.median(x["cpa_err_nm"] for x in xs), 1) if xs else None,
            "median_time_err_min": round(statistics.median(x["time_err_min"] for x in xs), 1) if xs else None,
            "median_persist_err_nm": round(statistics.median(persist), 1) if persist else None,
        }
    return report


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[0] not in ("replay", "score-estimates"):
        print(__doc__)
        return 2
    cmd, args = argv[0], list(argv[1:])

    def opt(name):
        if name in args:
            i = args.index(name)
            value = Path(args[i + 1])
            del args[i:i + 2]
            return value
        return None

    if cmd == "replay":
        obs, cells = opt("--observed"), opt("--cells")
        db = os.environ.get("AIRPORTS_DB") or str(REPO / "data" / "nav.db")
        replay(Path(args[0]), Path(args[1]), obs, cells, args[2:], db)
    else:
        print(json.dumps(score_estimates(Path(args[0]), Path(args[1]), args[2:]), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
