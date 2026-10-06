"""Live-layer review helpers (run locally, with the repo venv). See ../SKILL.md.

  summarize     DIR                  per flight: event counts, every alert with the raw report behind
                                     it, and regression flags for the #682/#683/§39 fixes
  briefing-list DIR                  pack briefing.json paths to pull for the replay (stdin for tar)
  observed-jobs DIR JOBS.json        route + prod tick times per flight, for on_mini.py observed
  replay        DIR OUT [--observed OBS.json] [FLIGHT_SUBSTR...]
                                     re-run each flight's prod ticks through the current classifier
  compare       OLD_DIR NEW_DIR [FLIGHT_SUBSTR...]   old (prod) vs new (replay) events
  metar-points  DIR POINTS.json      every METAR in the histories, classified (observed CB/TCU,
                                     trend-only, none), with airport position, for on_mini.py airport-radar
  radar-summary RADAR.json [ICAO...] radar/lightning around airports by METAR class (+ per-station rows)

DIR is a pulled tree of DATA_DIR/packs/<user>/<flight>/ (live_history.jsonl, live_meta.json,
live.json, and the pack briefing.json files for replay).
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(os.environ.get("WB_REPO") or Path(__file__).resolve().parents[4])
AIRPORTS_DB = os.environ.get("AIRPORTS_DB") or str(REPO / "data" / "nav.db")

TREND = re.compile(r"\s(TEMPO|BECMG|NOSIG|PROB\d{2}|RMK)\b")
CLOUD = re.compile(r"(?:\b(?:FEW|SCT|BKN|OVC|VV)(?:\d{3}|///)|(?<![A-Z0-9])///)(CB|TCU)\b")
TWO_VIS = re.compile(r"\s(\d{4})\s(\d{4})(?:[NSEW]{1,2})?\s")
WMO_HEADER = re.compile(r"^W[SCV]\w+\s+\w{4}\s+(\d{2})(\d{2})(\d{2})")


def histories(root: Path):
    for p in sorted(root.glob("**/live_history.jsonl")):
        yield p.parent, [json.loads(line) for line in p.open() if line.strip()]


def split_trend(raw: str) -> tuple[str, str]:
    m = TREND.search(raw)
    return (raw[: m.start()], raw[m.start():]) if m else (raw, "")


CEILING = re.compile(r"\b(?:BKN|OVC|VV)(\d{3})")
RANK = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}


def prevailing_category(raw: str) -> str | None:
    """Flight category from the FIRST visibility group (prevailing) and the ceiling
    in the observed body; None when the visibility can't be read simply."""
    body, _ = split_trend(raw)
    if "CAVOK" in body:
        return "VFR"
    m = re.search(r"\s(\d{4})(?:NDV)?\s", body)
    if not m:
        return None
    vis_sm = int(m.group(1)) / 1609.34
    ceils = [int(h) * 100 for h in CEILING.findall(body)]
    ceil = min(ceils) if ceils else 99999
    if vis_sm < 1 or ceil < 500:
        return "LIFR"
    if vis_sm < 3 or ceil < 1000:
        return "IFR"
    if vis_sm <= 5 or ceil <= 3000:
        return "MVFR"
    return "VFR"


def last_report(recs, icao, kind, at):
    m = [r for r in recs if r["type"] == "report" and r["kind"] == kind and r.get("icao") == icao and r["tick_at"] <= at]
    return m[-1]["raw"] if m else None


# --- summarize ---------------------------------------------------------------

def summarize(root: Path) -> None:
    for d, recs in histories(root):
        ev = [r for r in recs if r["type"] == "event"]
        ticks = sorted({r["tick_at"] for r in recs if r.get("tick_at")})
        tiers = collections.Counter((r["event"][:3], r["change"]["tier"]) for r in ev)
        appeared = collections.Counter(
            (r["change"]["key"], r["change"].get("direction"), r["change"].get("to_value"))
            for r in ev if r["event"] == "appeared"
        )
        reappear = sum(n - 1 for n in appeared.values() if n > 1)
        print(f"\n== {d.name}  {ticks[0][5:16]}..{ticks[-1][11:16]}  ticks={len(ticks)}  {dict(tiers)}  reappear={reappear}")
        for r in ev:
            c, at = r["change"], r["tick_at"]
            if r["event"] != "appeared":
                continue
            flags, raw = [], None
            if c["kind"].startswith("metar") or c["kind"].startswith("taf"):
                raw = last_report(recs, c.get("icao"), "taf" if c["kind"].startswith("taf") else "metar", at)
            if c["kind"] == "metar_convective" and raw and c["direction"] == "worse":
                body, trend = split_trend(raw)
                if not CLOUD.search(body) and "TS" not in body[20:] and CLOUD.search(trend):
                    flags.append("REGRESSION#682: CB/TCU only in trend")
                if c["role"] not in ("departure", "destination", "alternate") and c["tier"] == "alert" and c.get("to_value") in ("CB", "TCU"):
                    flags.append("REGRESSION§39: en-route CB/TCU alert")
            if c["kind"] == "metar_category" and raw and TWO_VIS.search(raw):
                want = prevailing_category(raw)
                if want and c.get("to_value") in RANK and RANK[c["to_value"]] > RANK[want]:
                    flags.append(f"REGRESSION#682: {c['to_value']} but prevailing visibility + ceiling give {want} (sector minimum used?)")
            if c["kind"] in ("radar", "lightning") and c.get("to_value") not in (None, "none", "heavy", "present"):
                if re.search(r"\d", str(c.get("to_value"))):
                    flags.append("REGRESSION#682: numeric radar/lightning identity")
            show = c["tier"] == "alert" or flags or c["kind"].startswith(("sigmet", "radar", "lightning", "metar_conv"))
            if show:
                print(f"   {at[11:16]} {c['tier'][:5]:5} {c['role'][:5]:5} {c['kind']:16} new={c.get('new_alert')} | {c['message']}")
                if raw:
                    print(f"         {raw.replace(chr(10), ' | ')[:230]}")
                for f in flags:
                    print(f"         !! {f}")


def briefing_list(root: Path) -> None:
    """The pack briefing.json paths (relative to packs/) the replay needs."""
    for d, recs in histories(root):
        rel = d.relative_to(root)
        for r in recs:
            if r["type"] == "pack":
                print(f"{rel}/{r['pack_dir_name']}/briefing.json")


# --- replay -----------------------------------------------------------------

def observed_jobs(root: Path, out: Path) -> None:
    jobs = {}
    for d, recs in histories(root):
        packs = [r for r in recs if r["type"] == "pack"]
        b = json.loads((d / packs[-1]["pack_dir_name"] / "briefing.json").read_text())
        jobs[d.name] = {"route": b["route"], "ticks": sorted({r["tick_at"] for r in recs if r.get("tick_at")})}
    out.write_text(json.dumps(jobs))
    print(len(jobs), "flights,", sum(len(j["ticks"]) for j in jobs.values()), "ticks")


def _header_issue_time(raw: str, valid_from: str | None, fallback: str) -> str:
    """A SIGMET's issue time from its WMO header (what the #683 lookahead can see)."""
    m = WMO_HEADER.match(raw or "")
    if not m:
        return fallback
    day, hh, mm = (int(g) for g in m.groups())
    t = datetime.fromisoformat(valid_from or fallback).replace(day=day, hour=hh, minute=mm, second=0, microsecond=0)
    return t.isoformat() if t <= datetime.fromisoformat(fallback) else fallback


def replay(root: Path, out_root: Path, observed_path: Path | None, only: list[str]) -> None:
    sys.path[:0] = [str(REPO / "scripts"), str(REPO / "tests"), str(REPO / "src")]
    import build_live_scenario as bls
    from live_scenario_replay import observations_at, sigmets_at

    from weatherbrief.models.observed import ObservedConditions
    from weatherbrief.tasks.live_layer import commit_live_update, load_live_history

    observed = json.loads(observed_path.read_text()) if observed_path else {}
    for d, _ in histories(root):
        if only and not any(o in d.name for o in only):
            continue
        try:
            inputs = bls.inputs_from_history(d)
            for s in inputs["sigmets"]:
                s["issued_at"] = _header_issue_time(s["report"].get("raw_text"), s["report"].get("valid_from"), s["issued_at"])
            scenario = {"inputs": inputs, "derived": bls.build_derived(inputs, AIRPORTS_DB)}
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
                commit_live_update(
                    pack_dir, briefing_data=briefing,
                    observations=observations_at(scenario, at), sigmets=sigmets_at(scenario, at),
                    observed=ObservedConditions.model_validate(obs) if obs else None,
                    started_at=at, pack_timestamp=ts, now=at,
                )
            print("ok", d.name, file=sys.stderr)
        except Exception as e:  # keep going; report per flight
            print("FAIL", d.name, type(e).__name__, e, file=sys.stderr)


def _events(path: Path):
    out = []
    for line in path.open():
        if line.strip():
            r = json.loads(line)
            if r["type"] == "event":
                c = r["change"]
                out.append((r["tick_at"][11:16], r["event"][:3], c["tier"], c["kind"], c["message"], c.get("new_alert")))
    return out


def compare(old_root: Path, new_root: Path, verbose: list[str]) -> None:
    tot = {"old": collections.Counter(), "new": collections.Counter()}
    for new in sorted(new_root.glob("**/live_history.jsonl")):
        fl = new.parent.name
        old = next(old_root.glob(f"**/{fl}/live_history.jsonl"))
        row = []
        for tag, evs in (("old", _events(old)), ("new", _events(new))):
            app = [e for e in evs if e[1] == "app"]
            s = {"alerts": sum(e[2] == "alert" for e in app), "pings": sum(e[5] is True for e in app),
                 "radar/ltg": sum(e[3] in ("radar", "lightning") for e in app), "events": len(evs)}
            tot[tag].update(s)
            row.append(f"{tag} {s}")
        print(f"{fl[:44]:44} {'  '.join(row)}")
        if any(v in fl for v in verbose):
            so = collections.Counter(e[1:3] + (e[4],) for e in _events(old) if e[2] == "alert")
            sn = collections.Counter(e[1:3] + (e[4],) for e in _events(new) if e[2] == "alert")
            print("  alert rows only in OLD:", *[f"\n     {k}" for k in so - sn])
            print("  alert rows only in NEW:", *[f"\n     {k}" for k in sn - so])
    print("\nTOTAL old", dict(tot["old"]), "\nTOTAL new", dict(tot["new"]))


# --- airport radar corroboration -------------------------------------------

def metar_points(root: Path, out: Path, since: str = "") -> None:
    import sqlite3

    db = sqlite3.connect(AIRPORTS_DB)
    seen = {}
    for _, recs in histories(root):
        for r in recs:
            if r["type"] == "report" and r["kind"] == "metar" and r["observed_at"] >= since:
                seen[(r["icao"], r["observed_at"])] = r["raw"]
    rows = []
    for (icao, t), raw in seen.items():
        pos = db.execute("select latitude_deg, longitude_deg from airports where icao_code=?", (icao,)).fetchone()
        if not pos:
            continue
        body, trend = split_trend(raw)
        obs = sorted(set(CLOUD.findall(body)))
        cls = "observed" if obs else ("trend_only" if CLOUD.search(trend) else "none")
        rows.append(dict(icao=icao, t=t, lat=pos[0], lon=pos[1], cls=cls, obs=obs, auto=" AUTO " in raw, raw=raw))
    out.write_text(json.dumps(rows))
    print(len(rows), collections.Counter(r["cls"] for r in rows))


def radar_summary(path: Path, icaos: list[str]) -> None:
    d = [o for o in json.loads(path.read_text()) if o["radar_frames"] > 0]

    def grp(o):
        if o["cls"] == "observed":
            return f"obs {'CB' if 'CB' in o['obs'] else 'TCU'} {'AUTO' if o['auto'] else 'manned'}"
        return o["cls"]

    groups = collections.defaultdict(list)
    for o in d:
        groups[grp(o)].append(o)
    pct = lambda xs, f: f"{100 * sum(map(f, xs)) / len(xs):3.0f}%"  # noqa: E731
    dbz = lambda o, r: o["dbz"].get(r) or -99  # noqa: E731
    print(f"{'group':16} {'n':>5} {'no echo<=10NM':>13} {'>=30dBZ 5NM':>12} {'>=41dBZ 10NM':>13} {'flash<=10NM':>12}")
    for k in ("obs CB AUTO", "obs CB manned", "obs TCU AUTO", "obs TCU manned", "trend_only", "none"):
        xs = groups.get(k)
        if xs:
            print(f"{k:16} {len(xs):5} {pct(xs, lambda o: dbz(o, '10.0') < 0):>13} {pct(xs, lambda o: dbz(o, '5.0') >= 30):>12} "
                  f"{pct(xs, lambda o: dbz(o, '10.0') >= 41):>13} {pct(xs, lambda o: (o['flashes'].get('10.0') or 0) > 0):>12}")
    for o in sorted(d, key=lambda o: (o["icao"], o["t"])):
        if o["icao"] in icaos:
            print(f"{o['icao']} {o['t'][5:16]} {o['cls']:10} dBZ 3/5/10={o['dbz'].get('3.0')}/{o['dbz'].get('5.0')}/{o['dbz'].get('10.0')} "
                  f"flashes 10NM={o['flashes'].get('10.0')} | {o['raw'][:90]}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "summarize":
        summarize(Path(args[0]))
    elif cmd == "briefing-list":
        briefing_list(Path(args[0]))
    elif cmd == "observed-jobs":
        observed_jobs(Path(args[0]), Path(args[1]))
    elif cmd == "replay":
        obs = None
        if "--observed" in args:
            i = args.index("--observed")
            obs = Path(args[i + 1])
            args = args[:i] + args[i + 2:]
        replay(Path(args[0]), Path(args[1]), obs, args[2:])
    elif cmd == "compare":
        compare(Path(args[0]), Path(args[1]), args[2:])
    elif cmd == "metar-points":
        metar_points(Path(args[0]), Path(args[1]), args[2] if len(args) > 2 else "")
    elif cmd == "radar-summary":
        radar_summary(Path(args[0]), args[1:])
    else:
        sys.exit(__doc__)
