#!/usr/bin/env python3
"""Export recent prod digests for a model A/B replay (#717). Runs on the droplet.

Picks briefing packs that (a) received a digest thumb since ``--thumbs-since``
and/or (b) were built in the last ``--last-hours``, and copies only what a
replay needs — the byte-faithful ``digest_context.txt`` and the stored
``digest.json`` — under an anonymous id, plus a manifest with each pack's prod
rating, guidance preset, the user's locale and its thumb. No flight, route
owner or user id leaves the box.

Pick ``--thumbs-since`` no earlier than the current briefer prompt's go-live,
so the stored digest was written by the prompt you compare against.

    scp scripts/export_digest_replay.py brice@weather.flyfun.aero:/tmp/
    ssh brice@weather.flyfun.aero 'docker cp /tmp/export_digest_replay.py weatherbrief:/tmp/ \
        && docker exec weatherbrief python /tmp/export_digest_replay.py --thumbs-since 2026-08-04 \
        && docker cp weatherbrief:/tmp/digest_replay.tgz /tmp/'
    scp brice@weather.flyfun.aero:/tmp/digest_replay.tgz . && tar xzf digest_replay.tgz

then replay locally with ``scripts/replay_prod_digests.py``.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import shutil
import tarfile
from datetime import datetime, timedelta, timezone

from flyfun_common.db import SessionLocal, get_engine
from sqlalchemy import text

from weatherbrief.api.preferences import load_user_locale

PACKS_ROOT = "/app/data/packs"


def _pack_dir(artifact_path: str | None) -> str | None:
    if not artifact_path:
        return None
    return artifact_path if artifact_path.startswith("/") else os.path.join(PACKS_ROOT, artifact_path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--thumbs-since", help="Include packs thumbed since this date (YYYY-MM-DD)")
    ap.add_argument("--last-hours", type=int, default=24, help="Include packs built in the last N hours (0 = none)")
    ap.add_argument("--out", default="/tmp/digest_replay")
    args = ap.parse_args()

    get_engine()
    db = SessionLocal()
    try:
        cand: dict = {}

        def add(fl, ts, ass, ap_, days, uid, tag):
            c = cand.setdefault((fl, ts), {"ass": ass, "ap": ap_, "days": days, "uid": uid, "src": set()})
            c["src"].add(tag)

        if args.thumbs_since:
            since = datetime.fromisoformat(args.thumbs_since).replace(tzinfo=timezone.utc)
            for s, ts, fl, ass, ap_, days, uid in db.execute(text("""
                SELECT f.sentiment, bp.fetch_timestamp, bp.flight_id, bp.assessment,
                       bp.artifact_path, bp.days_out, fl.user_id
                FROM feedback f
                JOIN briefing_packs bp ON bp.flight_id = f.flight_id AND bp.fetch_timestamp = f.pack_timestamp
                JOIN flights fl ON fl.id = bp.flight_id
                WHERE f.target = 'digest' AND bp.assessment IS NOT NULL AND bp.fetch_timestamp >= :s
            """), {"s": since}):
                add(fl, ts, ass, ap_, days, uid, f"thumb_{s}")
        if args.last_hours:
            since = datetime.now(timezone.utc) - timedelta(hours=args.last_hours)
            for ts, fl, ass, ap_, days, uid in db.execute(text("""
                SELECT bp.fetch_timestamp, bp.flight_id, bp.assessment, bp.artifact_path,
                       bp.days_out, fl.user_id
                FROM briefing_packs bp JOIN flights fl ON fl.id = bp.flight_id
                WHERE bp.fetch_timestamp >= :s AND bp.has_digest = 1 AND bp.assessment IS NOT NULL
            """), {"s": since}):
                add(fl, ts, ass, ap_, days, uid, f"last{args.last_hours}h")

        shutil.rmtree(args.out, ignore_errors=True)
        os.makedirs(args.out)
        locales: dict = {}
        manifest, skipped = [], 0
        for (fl, ts), c in cand.items():
            d = _pack_dir(c["ap"])
            if not d or not all(os.path.exists(os.path.join(d, f)) for f in ("digest_context.txt", "digest.json")):
                skipped += 1
                continue
            digest = json.load(open(os.path.join(d, "digest.json")))
            if c["uid"] not in locales:
                locales[c["uid"]] = load_user_locale(db, c["uid"]) or "en"
            aid = hashlib.sha1(f"{fl}|{ts.isoformat()}".encode()).hexdigest()[:10]
            os.makedirs(os.path.join(args.out, aid))
            for f in ("digest_context.txt", "digest.json"):
                shutil.copy(os.path.join(d, f), os.path.join(args.out, aid))
            manifest.append({
                "id": aid, "sources": sorted(c["src"]), "prod_assessment": c["ass"],
                "guidance": digest.get("digest_guidance") or "balanced",
                "locale": locales[c["uid"]], "days_out": c["days"],
                "fetch_date": ts.date().isoformat(),
                "user": hashlib.sha1(c["uid"].encode()).hexdigest()[:6],
            })
        with open(os.path.join(args.out, "manifest.json"), "w") as fh:
            json.dump(manifest, fh, indent=1)
        tgz = args.out + ".tgz"
        with tarfile.open(tgz, "w:gz") as t:
            t.add(args.out, arcname=os.path.basename(args.out))
    finally:
        db.close()

    print(f"exported {len(manifest)} (skipped {skipped} without context) -> {tgz}")
    print("sources", dict(collections.Counter(s for m in manifest for s in m["sources"])))
    print("ratings", dict(collections.Counter(m["prod_assessment"] for m in manifest)))
    print("locales", dict(collections.Counter(m["locale"] for m in manifest)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
