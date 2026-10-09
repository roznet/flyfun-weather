#!/usr/bin/env python3
"""Pipeline health and digest review for prod-briefings-review.

Container side (copy in and `docker exec $SERVER_CONTAINER python /tmp/briefings.py ...`;
needs the prod DB and the pack files):

    health        SINCE UNTIL                      refresh jobs, briefings, models, cost
    digest-check  SINCE UNTIL                      mechanical checks on every digest
    sample        SINCE UNTIL [--n 12] [--seen F]  ranked review sample, per day -> JSON
    export        PICKS.json OUT.tgz               the picked packs' review files

Local side (stdlib only):

    timing   APP.log [BASELINE.log]                llm_digest / fetch / total from "Pipeline timing"
    seen     RECORD [--days 7]                     flights reviewed recently -> JSON list
    record   RECORD FINDINGS.json --server-head SHA  append reviewed packs to the record
    tally    RECORD [--since YYYY-MM-DD]           findings by day and weakness

SINCE / UNTIL are UTC dates (YYYY-MM-DD), UNTIL exclusive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

PACKS_ROOT = "/app/data/packs"
ORDER = {"green": 0, "amber": 1, "red": 2}

# Mechanical-check patterns. Kept here (not in the skill text) so the record's
# counts stay comparable run to run.
FIRST_PERSON = re.compile(r"\b(?i:our|we|unser\w*|wir)\b|\bI\b")
CONSENSUS = re.compile(
    r"all (two |three |four |five )?models|across (the )?models|the models (all )?show|"
    r"every model|modellübergreifend|alle Modelle", re.I)
COORDS = re.compile(r"\b\d{1,2}(\.\d+)?\s?°\s?[NSEW]\b|\b\d{1,2}\.\d[NSEW]\b")
# What a minority-view advisory is about, so a consensus phrase only counts when
# its sentence talks about that subject ("all three models agree on a tailwind"
# over a GFS-only LIFR is real agreement about something else).
SUBJECT = {
    "flight_category": r"LIFR|IFR|MVFR|ceiling|Decke|Wolkenuntergrenze|cloud|Wolk|overcast|OVC|BKN",
    "ifr_feasibility": r"LIFR|IFR|ceiling|Decke|minim|cloud|Wolk",
    "approach_feasibility": r"LIFR|IFR|ceiling|Decke|minim|approach|Anflug",
    "vfr_feasibility": r"VFR|IMC|VMC|cloud|Wolk|overcast|OVC|BKN|ceiling|Decke",
    "vmc_cruise": r"IMC|VMC|cloud|Wolk|overcast|OVC|BKN",
    "cloud_top": r"cloud|Wolk|tops|Obergrenze",
    "icing_escape": r"icing|Vereisung|freezing|Nullgrad",
    "fiki_icing": r"icing|Vereisung|freezing|Nullgrad",
    "convective": r"convect|Konvekt|CB|TCU|thunder|Gewitter|shower|Schauer|CAPE",
    "convective_character": r"convect|Konvekt|CB|TCU|thunder|Gewitter|shower|Schauer",
    "airport_wind": r"gust|Böe|crosswind|Seitenwind|runway|RW",
    "llws": r"shear|Scherung",
    "headwind": r"headwind|Gegenwind",
    "turbulence": r"turbul",
    "mountain_wind": r"mountain|wave|Föhn|foehn|rotor",
}
DIGEST_FIELDS = ("assessment_reason", "synoptic", "specific_concerns", "trend", "watch_items")


# ---------------------------------------------------------------------------
# Container side
# ---------------------------------------------------------------------------

def _db():
    from flyfun_common.db import SessionLocal, get_engine
    get_engine()
    return SessionLocal()


def _window(since: str, until: str):
    return datetime.fromisoformat(since), datetime.fromisoformat(until)


def _pack_dir(artifact_path: str) -> str:
    return artifact_path if artifact_path.startswith("/") else os.path.join(PACKS_ROOT, artifact_path)


def cmd_health(a):
    from sqlalchemy import text
    db = _db()
    s, e = _window(a.since, a.until)
    w = {"s": s, "e": e}
    jobs = db.execute(text("""
        SELECT status, triggered_by, COUNT(*) FROM briefing_refresh_jobs
        WHERE created_at >= :s AND created_at < :e GROUP BY status, triggered_by"""), w).fetchall()
    print(f"refresh jobs {a.since}..{a.until}: {sum(r[2] for r in jobs)}")
    for st, trig, n in sorted(jobs):
        print(f"  {st:10} {trig:10} {n}")
    for fid, st, stage, err in db.execute(text("""
        SELECT flight_id, status, stage, last_error FROM briefing_refresh_jobs
        WHERE created_at >= :s AND created_at < :e AND status IN ('failed', 'abandoned')"""), w):
        print(f"  !! {st} {fid} stage={stage} {(err or '')[:160]}")
    packs = db.execute(text("""
        SELECT COUNT(*), SUM(has_digest), SUM(llm_digest_requested),
               SUM(CASE WHEN llm_digest_requested = 1 AND has_digest = 0 THEN 1 ELSE 0 END),
               COUNT(DISTINCT flight_id)
        FROM briefing_packs WHERE fetch_timestamp >= :s AND fetch_timestamp < :e"""), w).fetchone()
    print(f"packs {packs[0]} (flights {packs[4]}), digest {packs[1]} of {packs[2]} requested, "
          f"MISSING {packs[3]}")
    grades = db.execute(text("""
        SELECT COALESCE(assessment, 'longrange'), COUNT(*) FROM briefing_packs
        WHERE fetch_timestamp >= :s AND fetch_timestamp < :e GROUP BY 1"""), w).fetchall()
    print("grades", dict(grades))
    models = db.execute(text("""
        SELECT llm_model, COUNT(*), COUNT(DISTINCT user_id) FROM briefing_usage
        WHERE timestamp >= :s AND timestamp < :e AND llm_digest = 1 GROUP BY llm_model"""), w).fetchall()
    for m, n, u in models:
        print(f"  model {m}: {n} digests, {u} users")
    costs = [json.loads(d).get("token_cost_usd", 0) for (d,) in db.execute(text("""
        SELECT detail_json FROM cost_ledger WHERE service = 'flyfun-weather' AND action = 'briefing'
          AND created_at >= :s AND created_at < :e AND detail_json IS NOT NULL"""), w)]
    if costs:
        print(f"token cost per briefing: median ${statistics.median(costs):.4f}, "
              f"max ${max(costs):.4f}, total ${sum(costs):.2f} over {len(costs)}")
    db.close()


def _digest_rows(db, s, e):
    from sqlalchemy import text
    return db.execute(text("""
        SELECT bp.id, bp.flight_id, bp.fetch_timestamp, bp.assessment, bp.days_out,
               bp.artifact_path, fl.user_id
        FROM briefing_packs bp JOIN flights fl ON fl.id = bp.flight_id
        WHERE bp.fetch_timestamp >= :s AND bp.fetch_timestamp < :e AND bp.has_digest = 1
        ORDER BY bp.fetch_timestamp"""), {"s": s, "e": e}).fetchall()


def _dwd_duplicated(ctx: str) -> bool:
    m = re.search(r"=== TEXT FORECASTS \(DWD[^\n]*===\n(.*?)(?=\n=== [A-Z]|\Z)", ctx, re.S)
    if not m:
        return False
    body = m.group(1)
    labels = len(re.findall(r"^--- .+ ---$", body, re.M))
    embedded = len(re.findall(r"=== [A-ZÄÖÜa-zäöü]+ \(\d{4}-\d{2}-\d{2}\) ===", body))
    # The split fallback pastes the whole translation (with its own day
    # headers) under every label.
    return labels > 1 and embedded >= labels


def _advisory_flags(adv: dict):
    nongreen, minority, outlier = {}, [], []
    for a in adv.get("advisories", []):
        st = (a.get("aggregate_status") or "green").lower()
        pm = a.get("per_model") or []
        agree = [m for m in pm if (m.get("status") or "").lower() == st]
        if st in ("amber", "red"):
            nongreen[a["advisory_id"]] = st
            if agree and len(pm) - len(agree) > len(agree):
                minority.append(a["advisory_id"])
            elif len(agree) < len(pm):
                outlier.append(a["advisory_id"])
    return nongreen, minority, outlier


def _consensus_over_minority(txt: str, minority: list[str]) -> str | None:
    """The first sentence that claims agreement about a minority view's subject."""
    subjects = [SUBJECT[a] for a in minority if a in SUBJECT]
    if not subjects:
        return None
    subj = re.compile("|".join(subjects), re.I)
    for sent in re.split(r"(?<=[.;!?])\s+", txt):
        if CONSENSUS.search(sent) and subj.search(sent):
            return sent.strip()
    return None


def cmd_digest_check(a):
    db = _db()
    s, e = _window(a.since, a.until)
    counts, examples, n = Counter(), defaultdict(list), 0
    for pid, fl, ts, ass, days, ap, uid in _digest_rows(db, s, e):
        d = _pack_dir(ap)
        try:
            ctx = open(os.path.join(d, "digest_context.txt")).read()
            dj = json.load(open(os.path.join(d, "digest.json")))
            adv = json.load(open(os.path.join(d, "route_advisories.json")))
        except OSError:
            counts["unreadable"] += 1
            continue
        n += 1
        txt = " ".join(str(dj.get(k) or "") for k in DIGEST_FIELDS)
        _, minority, _ = _advisory_flags(adv)
        hits = {
            "dwd_duplicated": _dwd_duplicated(ctx),
            "first_person": bool(FIRST_PERSON.search(txt)),
            "consensus_over_minority": _consensus_over_minority(txt, minority) is not None,
            "raw_coordinates": bool(COORDS.search(txt)),
            "longrange": ass is None,
        }
        for k, v in hits.items():
            if v:
                counts[k] += 1
                if k != "longrange" and len(examples[k]) < 3:
                    m = {"first_person": FIRST_PERSON, "raw_coordinates": COORDS}.get(k)
                    snip = ""
                    if k == "consensus_over_minority":
                        snip = _consensus_over_minority(txt, minority)[:200]
                    elif m:
                        mm = m.search(txt)
                        snip = txt[max(0, mm.start() - 60): mm.end() + 60].replace("\n", " ")
                    examples[k].append(f"{pid} {fl[:30]} {snip}")
    db.close()
    print(f"digests checked {n} ({a.since}..{a.until})")
    for k in ("dwd_duplicated", "first_person", "consensus_over_minority", "raw_coordinates",
              "longrange", "unreadable"):
        print(f"  {k:24} {counts[k]}")
        for ex in examples[k]:
            print(f"      {ex}")


def _lead(d: int) -> str:
    return "D-0" if d <= 0 else "D-1/2" if d <= 2 else "D-3+"


def _load_day(db, day: date):
    from sqlalchemy import text
    from weatherbrief.api.preferences import load_user_locale
    s = datetime.combine(day, datetime.min.time())
    e = s + timedelta(days=1)
    thumbs = {(fl, ts): st for fl, ts, st in db.execute(text("""
        SELECT flight_id, pack_timestamp, sentiment FROM feedback
        WHERE target = 'digest' AND pack_timestamp >= :s AND pack_timestamp < :e"""),
        {"s": s, "e": e})}
    usage = defaultdict(list)
    for fl, ts, model in db.execute(text("""
        SELECT flight_id, timestamp, llm_model FROM briefing_usage
        WHERE timestamp >= :s AND timestamp < :e AND llm_digest = 1"""),
            {"s": s, "e": e + timedelta(hours=1)}):
        usage[fl].append((ts, model))
    locales, packs = {}, []
    for pid, fl, ts, ass, days, ap, uid in _digest_rows(db, s, e):
        if ass is None:  # long-range outlook: different prompt, not sampled here
            continue
        d = _pack_dir(ap)
        try:
            ctx = open(os.path.join(d, "digest_context.txt")).read()
            adv = json.load(open(os.path.join(d, "route_advisories.json")))
        except OSError:
            continue
        if uid not in locales:
            locales[uid] = load_user_locale(db, uid) or "en"
        nongreen, minority, outlier = _advisory_flags(adv)
        after = [m for t, m in sorted(usage[fl]) if t >= ts]
        packs.append({
            "id": pid, "flight": fl, "ts": ts.isoformat(), "user": uid, "dir": d,
            "grade": ass.lower(), "lead": _lead(days), "days_out": days,
            "locale": locales[uid],
            "pilot": "vfr_only" if "PILOT CAPABILITY: VFR only" in ctx else "ifr",
            "dwd": "extract" if "large-scale extract" in ctx else
                   "translate" if "translated from German" in ctx else "none",
            "nongreen": nongreen, "minority": minority, "outlier": outlier,
            "worst": max((ORDER[v] for v in nongreen.values()), default=0),
            "obs_sig": ctx.count("[SIGNIFICANT]"),
            "thumb": thumbs.get((fl, ts)),
            "llm_model": after[0] if after else None,
        })
    return packs


def _features(p):
    f = {("grade", p["grade"]): 2, ("lead", p["lead"]): 2, ("locale", p["locale"]): 1,
         ("pilot", p["pilot"]): 1, ("dwd", p["dwd"]): 1}
    for aid, st in p["nongreen"].items():
        f[("adv", f"{aid}={st[0].upper()}")] = 1
    for aid in p["minority"]:
        f[("minority", aid)] = 1
    return f


# Fixed-rule picks, in priority order. Each takes at most one pack (thumbs-down
# takes all), together capped at half the sample.
RULES = [
    ("thumbs-down", lambda p: p["thumb"] == "down"),
    ("softer than its worst advisory", lambda p: p["worst"] == 2 and ORDER[p["grade"]] < 2),
    ("harsher than its advisories", lambda p: ORDER[p["grade"]] > p["worst"]),
    ("minority-view advisory", lambda p: bool(p["minority"])),
    ("D-0 obs differ from models", lambda p: p["lead"] == "D-0" and p["obs_sig"] > 0),
]
MIN_GAIN = 2       # a core diversity pick must add at least this many new features
SEEN_PENALTY = 2   # a flight reviewed in the last days pays this much gain


def rank_day(packs, n, day: str, seen):
    """Rank up to n packs. Core = worth a full review; tail = light pass."""
    latest = {}
    for p in sorted(packs, key=lambda p: p["ts"]):
        latest[p["flight"]] = p
    per_user, pool = Counter(), []
    for p in sorted(latest.values(), key=lambda p: p["ts"], reverse=True):
        if per_user[p["user"]] < 2:
            pool.append(p)
            per_user[p["user"]] += 1

    def tie(p):
        return hashlib.sha1(f"{day}|{p['id']}".encode()).hexdigest()

    ranked, covered = [], set()

    def take(p, tier, why, gain=None):
        ranked.append({**p, "rank": len(ranked) + 1, "tier": tier, "reason": why, "gain": gain})
        covered.update(_features(p))

    def left():
        taken = {r["id"] for r in ranked}
        return [p for p in pool if p["id"] not in taken]

    cap = max(1, n // 2)
    for why, rule in RULES:
        hits = sorted((p for p in left() if rule(p)), key=lambda p: (p["flight"] in seen, tie(p)))
        for p in (hits if why == "thumbs-down" else hits[:1]):
            if len(ranked) < cap:
                take(p, "core", why)

    def score(p):
        g = sum(w for k, w in _features(p).items() if k not in covered)
        return g - (SEEN_PENALTY if p["flight"] in seen else 0)

    while len(ranked) < n and left():
        best = max(left(), key=lambda p: (score(p), tie(p)))
        if score(best) >= MIN_GAIN:
            new = [f"{k[0]}={k[1]}" for k in _features(best) if k not in covered]
            take(best, "core", "diversity: " + ", ".join(new), score(best))
            continue
        # Below the bar: a pack carrying an advisory type no pick has yet is
        # still core — a lone carrier is exactly what a review misses.
        have = {k[1].split("=")[0] for k in covered if k[0] == "adv"}
        lone = [p for p in left() if any(x not in have for x in p["nongreen"])]
        if lone:
            best = max(lone, key=lambda p: (len([x for x in p["nongreen"] if x not in have]),
                                            p["flight"] not in seen, tie(p)))
            new = sorted(x for x in best["nongreen"] if x not in have)
            take(best, "core", "lone carrier: " + ", ".join(new), score(best))
            continue
        # Tail: fill the ceiling in gain order; these get the light pass.
        take(best, "tail", "fill", score(best))
    return pool, ranked


def cmd_sample(a):
    seen = set(json.load(open(a.seen))) if a.seen else set()
    db = _db()
    s, e = _window(a.since, a.until)
    out, day = [], s.date()
    while day < e.date():
        packs = _load_day(db, day)
        pool, ranked = rank_day(packs, a.n, day.isoformat(), seen)
        seen |= {r["flight"] for r in ranked}
        core = [r for r in ranked if r["tier"] == "core"]
        pool_adv = {x for p in pool for x in p["nongreen"]}
        got_adv = {x for r in ranked for x in r["nongreen"]}
        print(f"== {day}: {len(packs)} digests, pool {len(pool)}, core {len(core)}, "
              f"tail {len(ranked) - len(core)}, advisory types {len(got_adv)}/{len(pool_adv)}")
        for r in ranked:
            ng = ",".join(f"{k}{'*' if k in r['minority'] else ''}={v[0].upper()}"
                          for k, v in sorted(r["nongreen"].items()))
            print(f"  {r['rank']:>2} {r['tier']:4} {r['id']:>6} {r['grade'].upper():5} {r['lead']:5} "
                  f"{r['locale']} {r['pilot']:8} {r['flight'][:32]:32} | {r['reason'][:70]}")
            print(f"       non-green: {ng or '-'}")
        for r in ranked:
            out.append({**r, "day": day.isoformat()})
        day += timedelta(days=1)
    db.close()
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"-> {a.out} ({len(out)} packs)")


def cmd_export(a):
    import tarfile
    picks = json.load(open(a.picks))
    with tarfile.open(a.out, "w:gz") as t:
        for p in picks:
            for f in ("digest_context.txt", "digest.json", "digest.md", "route_advisories.json"):
                path = os.path.join(p["dir"], f)
                if os.path.exists(path):
                    t.add(path, arcname=f"{p['id']}/{f}")
            card = {k: p[k] for k in ("id", "day", "rank", "tier", "reason", "flight", "grade",
                                       "lead", "locale", "pilot", "dwd", "nongreen", "minority",
                                       "outlier", "llm_model", "thumb")}
            data = json.dumps(card, indent=1, default=str).encode()
            info = tarfile.TarInfo(f"{p['id']}/card.json")
            info.size = len(data)
            import io
            t.addfile(info, io.BytesIO(data))
    print(f"exported {len(picks)} packs -> {a.out}")


# ---------------------------------------------------------------------------
# Local side
# ---------------------------------------------------------------------------

def cmd_timing(a):
    def parse(path):
        rows = []
        for line in open(path):
            if "Pipeline timing" in line:
                rows.append({k: float(v) for k, v in re.findall(r"(\w+)=([\d.]+)s", line)})
        return rows

    def q(xs):
        if not xs:
            return "n=0"
        xs = sorted(xs)
        return (f"n={len(xs)} p50={statistics.median(xs):.1f}s "
                f"p90={xs[max(0, int(len(xs) * .9) - 1)]:.1f}s max={xs[-1]:.1f}s")

    for label, path in (("window", a.log), ("baseline", a.baseline)):
        if not path:
            continue
        rows = parse(path)
        print(f"{label}:")
        for stage in ("llm_digest", "fetch", "total"):
            print(f"  {stage:10} {q([r[stage] for r in rows if stage in r])}")


def _read_record(path):
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def cmd_seen(a):
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=a.days)).isoformat()
    flights = sorted({r["flight"] for r in _read_record(a.record) if r.get("day", "") >= cutoff})
    json.dump(flights, sys.stdout)
    print(file=sys.stderr)
    print(f"{len(flights)} flights reviewed since {cutoff}", file=sys.stderr)


REQUIRED = {"day", "pack_id", "flight", "tier", "review", "grade", "findings"}


def cmd_record(a):
    entries = json.load(open(a.findings))
    for e in entries:
        missing = REQUIRED - set(e)
        if missing:
            raise SystemExit(f"pack {e.get('pack_id')}: missing {sorted(missing)}")
        for f in e["findings"]:
            if f.get("severity") not in ("major", "minor") or "weakness" not in f:
                raise SystemExit(f"pack {e['pack_id']}: finding needs weakness + major/minor: {f}")
    os.makedirs(os.path.dirname(os.path.abspath(a.record)), exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(a.record, "a") as fh:
        for e in entries:
            fh.write(json.dumps({**e, "server_head": a.server_head, "recorded_at": stamp},
                                ensure_ascii=False) + "\n")
    print(f"recorded {len(entries)} packs -> {a.record}")


def cmd_tally(a):
    rows = [r for r in _read_record(a.record) if r.get("day", "") >= (a.since or "")]
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r)
    for day, rs in sorted(by_day.items()):
        full = [r for r in rs if r["review"] == "full"]
        majors = Counter(f["weakness"] for r in rs for f in r["findings"] if f["severity"] == "major")
        packs_major = sum(any(f["severity"] == "major" for f in r["findings"]) for r in rs)
        promoted = sum(r["tier"] == "tail" and r["review"] == "full" for r in rs)
        heads = sorted({r.get("server_head", "?") for r in rs})
        print(f"{day}: {len(rs)} packs ({len(full)} full, {promoted} promoted from tail), "
              f"{packs_major} with a major | majors by weakness {dict(majors)} | head {','.join(heads)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("health", "digest-check", "sample"):
        p = sub.add_parser(name)
        p.add_argument("since")
        p.add_argument("until")
        if name == "sample":
            p.add_argument("--n", type=int, default=12, help="ceiling per day")
            p.add_argument("--seen", help="JSON list of flights reviewed recently")
            p.add_argument("--out", default="/tmp/briefings_sample.json")
    p = sub.add_parser("export")
    p.add_argument("picks")
    p.add_argument("out")
    p = sub.add_parser("timing")
    p.add_argument("log")
    p.add_argument("baseline", nargs="?")
    p = sub.add_parser("seen")
    p.add_argument("record")
    p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("record")
    p.add_argument("record")
    p.add_argument("findings")
    p.add_argument("--server-head", required=True, help="prod SHA the digests were written by")
    p = sub.add_parser("tally")
    p.add_argument("record")
    p.add_argument("--since")
    a = ap.parse_args()
    {"health": cmd_health, "digest-check": cmd_digest_check, "sample": cmd_sample,
     "export": cmd_export, "timing": cmd_timing, "seen": cmd_seen, "record": cmd_record,
     "tally": cmd_tally}[a.cmd](a)


if __name__ == "__main__":
    main()
