#!/usr/bin/env python3
"""Seed a demo account with a copy of an existing flight + its briefing pack.

Ad-hoc admin tool (informal by design — only ever run by the admin). It copies a
flight *and its frozen weather pack* into a target user account so the demo login
sees a real, browsable briefing without re-fetching. This matters for old /
historical flights: their forecast can't be regenerated, so the interesting
weather only exists in the saved pack — we copy the files, we do not re-brief.

Two source modes
----------------
1. Eval-set corpus / staging (recommended for historical flights):
       python scripts/seed_demo_flight.py --from-corpus <corpus_id> --to-user demo@flyfun.aero
       python scripts/seed_demo_flight.py --from-staging <corpus_id> --to-user <user_id>

2. An existing flight in the DB this script is connected to (a "flight link"):
       python scripts/seed_demo_flight.py --from-flight <flight_id|URL> --to-user demo@flyfun.aero
       python scripts/seed_demo_flight.py --from-share <share_code>     --to-user demo@flyfun.aero
   `--from-flight` accepts a bare id, a `/briefing.html?flight=<id>` URL, or a
   `/s/<code>` short link.

   IMPORTANT: this reads from whatever DATABASE_URL + DATA_DIR the environment
   points at. So a *production* flight link works only when the flight row AND
   its pack files are reachable here — i.e. run it on prod (or against prod
   DATABASE_URL + prod DATA_DIR), or first pull the pack locally
   (scripts/pull_prod_complete_packs.py) or add it to the corpus and use
   --from-corpus.

Why it can't collide with the source
-------------------------------------
The flight id is `sha256({...,"user":user_id})[:4]` suffixed onto route+date, so
the same flight under the demo user gets a *different*, non-colliding id. The
only unique column that must NOT be carried over is `share_code` — we drop it and
let save_flight mint a fresh one. `profile_id`/`aircraft_id` are also dropped
(they FK the source user's rows).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select  # noqa: E402

from flyfun_common.db import UserRow  # noqa: E402
from weatherbrief.api.flights import _compute_flight_id  # noqa: E402
from weatherbrief.db import SessionLocal, init_shared_db  # noqa: E402
from weatherbrief.db.models import FlightRow  # noqa: E402
from weatherbrief.models import BriefingPackMeta, Flight  # noqa: E402
from weatherbrief.storage.flights import (  # noqa: E402
    _resolve_artifact_path,
    delete_flight,
    list_packs,
    load_flight,
    lookup_flight_id_by_share_code,
    pack_dir_for,
    save_flight,
    save_pack_meta,
)

# corpus_meta.json etc. are eval-set bookkeeping, not part of a real pack dir.
_CORPUS_ONLY_FILES = ("corpus_meta.json", "label.json", "_source.json", "debrief.json")


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _resolve_target_user(db, ref: str) -> UserRow:
    """Resolve the demo account by user id or email. Must already exist."""
    row = db.get(UserRow, ref)
    if row is None:
        row = db.execute(select(UserRow).where(UserRow.email == ref)).scalar_one_or_none()
    if row is None:
        _die(
            f"no user matches {ref!r} (by id or email). "
            "Create the demo account first (sign in once as it), then re-run."
        )
    return row


def _resolve_source_flight_id(db, *, flight_ref: str | None, share: str | None) -> str:
    """Turn a flight id / URL / share code into a concrete flight_id."""
    if share:
        fid = lookup_flight_id_by_share_code(db, share)
        if not fid:
            _die(f"no flight for share code {share!r}")
        return fid
    ref = flight_ref or ""
    if "://" in ref or ref.startswith("/"):
        parsed = urlparse(ref)
        q = parse_qs(parsed.query)
        if q.get("flight"):
            return q["flight"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "s":
            fid = lookup_flight_id_by_share_code(db, parts[1])
            if not fid:
                _die(f"no flight for share code {parts[1]!r} (from {ref!r})")
            return fid
        _die(f"could not extract a flight id from URL {ref!r}")
    return ref


def _source_from_corpus(corpus_id: str, area: str) -> tuple[Flight, list[tuple[BriefingPackMeta, Path]]]:
    from weatherbrief.eval_workbench.corpus import corpus_exists, load_corpus_meta
    from weatherbrief.eval_workbench.resolver import synthesize_flight, synthesize_pack_meta

    if not corpus_exists(corpus_id, area):
        _die(f"no {area} pack: {corpus_id!r}")
    meta = load_corpus_meta(corpus_id, area)
    flight = synthesize_flight(meta)
    pack_meta = synthesize_pack_meta(meta, area)
    src_dir = Path(pack_meta.artifact_path)
    return flight, [(pack_meta, src_dir)]


def _source_from_flight(db, flight_id: str) -> tuple[Flight, list[tuple[BriefingPackMeta, Path]]]:
    try:
        flight = load_flight(db, flight_id)
    except KeyError:
        _die(f"flight not found in this DB: {flight_id!r}")
    metas = list_packs(db, flight_id)  # newest first
    if not metas:
        _die(f"flight {flight_id!r} has no briefing packs to copy")
    packs = [(m, Path(_resolve_artifact_path(m.artifact_path))) for m in metas]
    return flight, packs


def _copy_pack_dir(src: Path, dest: Path, *, is_corpus: bool) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(*_CORPUS_ONLY_FILES) if is_corpus else None
    shutil.copytree(src, dest, ignore=ignore)


def seed(
    db,
    *,
    source_flight: Flight,
    source_packs: list[tuple[BriefingPackMeta, Path]],
    target_user_id: str,
    is_corpus: bool,
    which_packs: str,
    keep_private: bool,
    overwrite: bool,
    dry_run: bool,
) -> None:
    new_id = _compute_flight_id(
        route_name=source_flight.route_name,
        departure_time=source_flight.departure_time,
        cruise_altitude_ft=source_flight.cruise_altitude_ft,
        flight_ceiling_ft=source_flight.flight_ceiling_ft,
        flight_duration_hours=source_flight.flight_duration_hours,
        user_id=target_user_id,
    )
    packs = source_packs if which_packs == "all" else source_packs[:1]

    print(f"  source route : {source_flight.route_name} ({source_flight.target_date})")
    print(f"  source id    : {source_flight.id}")
    print(f"  -> new id     : {new_id}  (owner {target_user_id})")
    print(f"  packs to copy: {len(packs)} of {len(source_packs)}")
    for meta, src in packs:
        dest = pack_dir_for(target_user_id, new_id, meta.fetch_timestamp)
        exists = "OK" if src.exists() else "MISSING"
        print(f"      {meta.fetch_timestamp.isoformat()}  [{exists}]")
        print(f"        {src}")
        print(f"        -> {dest}")

    existing = db.get(FlightRow, new_id)
    if existing is not None and not overwrite:
        _die(f"demo already has flight {new_id!r}; pass --overwrite to replace it")

    if dry_run:
        print("  (dry-run: nothing written)")
        return

    if existing is not None:
        delete_flight(db, new_id)  # removes old packs + dirs

    demo = source_flight.model_copy(
        update={
            "id": new_id,
            "user_id": target_user_id,
            "share_code": None,  # let save_flight mint a fresh unique one
            "profile_id": None,  # FK to the source user's rows — drop
            "aircraft_id": None,
            "auto_refresh": False,  # don't auto-refresh seeded (often historical) flights
            "auto_refresh_hour": None,
            "last_auto_refresh_at": None,
            "private": source_flight.private if keep_private else False,
            "created_at": datetime.now(timezone.utc),
        }
    )
    save_flight(db, demo, target_user_id)

    for meta, src in packs:
        if not src.exists():
            _die(f"pack source dir missing, aborting: {src}")
        dest = pack_dir_for(target_user_id, new_id, meta.fetch_timestamp)
        _copy_pack_dir(src, dest, is_corpus=is_corpus)
        new_meta = meta.model_copy(
            update={"id": None, "flight_id": new_id, "artifact_path": str(dest)}
        )
        save_pack_meta(db, new_meta)  # recomputes integrity HMAC over new id + path

    db.commit()
    print(f"  seeded: /briefing.html?flight={new_id}   (share: /s/{demo.share_code})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-corpus", metavar="CORPUS_ID", help="copy from the committed eval corpus")
    src.add_argument("--from-staging", metavar="CORPUS_ID", help="copy from the eval staging area")
    src.add_argument("--from-flight", metavar="ID|URL", help="copy a flight in this DB (id, ?flight= URL, or /s/ link)")
    src.add_argument("--from-share", metavar="CODE", help="copy the flight behind a share code in this DB")
    ap.add_argument("--to-user", required=True, metavar="ID|EMAIL", help="target demo account (must exist)")
    ap.add_argument("--packs", choices=("latest", "all"), default="latest",
                    help="which packs to copy for a live flight (default: latest; corpus always has one)")
    ap.add_argument("--keep-private", action="store_true", help="preserve the source flight's private flag (default: make public)")
    ap.add_argument("--overwrite", action="store_true", help="replace an already-seeded flight of the same id")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    args = ap.parse_args()

    init_shared_db()
    db = SessionLocal()
    try:
        user = _resolve_target_user(db, args.to_user)
        print(f"target user: {user.id}  <{user.email}>")

        is_corpus = bool(args.from_corpus or args.from_staging)
        if args.from_corpus:
            flight, packs = _source_from_corpus(args.from_corpus, "corpus")
        elif args.from_staging:
            flight, packs = _source_from_corpus(args.from_staging, "staging")
        else:
            fid = _resolve_source_flight_id(db, flight_ref=args.from_flight, share=args.from_share)
            flight, packs = _source_from_flight(db, fid)

        seed(
            db,
            source_flight=flight,
            source_packs=packs,
            target_user_id=user.id,
            is_corpus=is_corpus,
            which_packs=args.packs,
            keep_private=args.keep_private,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
