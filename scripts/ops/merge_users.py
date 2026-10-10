#!/usr/bin/env python3
"""Merge one user account into another (admin, on request). Dry-run by default.

    venv/bin/python scripts/ops/merge_users.py --prod EMAIL            # plan
    venv/bin/python scripts/ops/merge_users.py --prod EMAIL --apply
    venv/bin/python scripts/ops/merge_users.py --prod --from ID --into ID [--apply]

Without ``--prod`` it runs against this checkout's dev DB and DATA_DIR.

Why: before flyfun-common 0.6.10, a Google/Apple sign-in never joined the
account that already held the same email, so one pilot could end up with two
accounts and see different flights per device. Merging is admin-only, done
when the pilot confirms the accounts are theirs; matching by display name is
not proof (anyone can set a name).

**EMAIL mode** finds the accounts holding that address (case-insensitive) and,
when there are exactly two, picks the survivor: a Google/Apple account over an
email (magic-link) one, since an email sign-in is found again by its address
while a Google/Apple sign-in is only found by its provider id; between two
of the same kind, the one with more flights, then the older. ``--into``
overrides the pick. **--from/--into** names both accounts by id (an Apple
"Hide My Email" account the pilot has confirmed is theirs).

What a merge does, in one DB transaction:
- every table with a ``user_id`` column (found by reflection, so tables of
  other apps on the shared DB are included) is re-pointed to the survivor;
- unique keys that include ``user_id`` keep the survivor's row on a clash.
  ``user_preferences`` is filled instead: survivor values win, empty ones
  take the absorbed account's (GRAMET credentials, app prefs per key);
- a subscription the survivor now holds to its own flight is dropped;
- pack directories move from ``packs/{from}/`` to ``packs/{into}/`` and the
  stored ``artifact_path`` / ``pack_path`` values follow;
- the absorbed user row is deleted, so its sessions get 401 and those
  devices sign in again. Its Google/Apple sign-in then lands on a new empty
  account unless flyfun-common's email linking applies (gmail/Workspace for
  Google, iCloud for Apple): tell the pilot which sign-in to use.

Dry-run performs all DB changes and rolls them back, so its counts and any
constraint error are the real ones; directories are only listed. Avoid
running ``--apply`` while one of the pilot's briefings is refreshing.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

OAUTH_PROVIDERS = ("google", "apple")
PATH_COLUMNS = (("briefing_packs", "artifact_path"), ("briefing_refresh_jobs", "pack_path"))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


# --- Plan ---------------------------------------------------------------------


def accounts_for_email(db, email: str) -> list[dict]:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT u.id, u.provider, u.email, u.display_name, u.created_at, "
            "u.last_login_at, (SELECT COUNT(*) FROM flights f WHERE f.user_id = u.id) "
            "AS flights FROM users u WHERE LOWER(u.email) = :e ORDER BY u.created_at"
        ),
        {"e": email.strip().lower()},
    ).mappings().all()
    # The DB collation may fold accents; keep case-only matches.
    return [dict(r) for r in rows if r["email"].lower() == email.strip().lower()]


def account(db, user_id: str) -> dict | None:
    from sqlalchemy import text

    row = db.execute(
        text(
            "SELECT u.id, u.provider, u.email, u.display_name, u.created_at, "
            "u.last_login_at, (SELECT COUNT(*) FROM flights f WHERE f.user_id = u.id) "
            "AS flights FROM users u WHERE u.id = :id"
        ),
        {"id": user_id},
    ).mappings().first()
    return dict(row) if row else None


def pick_survivor(accounts: list[dict]) -> dict:
    """Google/Apple over email; then more flights; then older."""
    return min(
        accounts,
        key=lambda a: (
            a["provider"] not in OAUTH_PROVIDERS,
            -a["flights"],
            str(a["created_at"]),
        ),
    )


# --- Merge --------------------------------------------------------------------


def _empty(value) -> bool:
    return value in (None, "", "{}", "[]", False, 0)


def _merge_preferences(db, meta, src: str, dst: str, report: list[str]) -> None:
    """Fill the survivor's empty preference fields from the absorbed row."""
    from sqlalchemy import select, update

    table = meta.tables.get("user_preferences")
    if table is None:
        return
    a = db.execute(select(table).where(table.c.user_id == src)).mappings().first()
    b = db.execute(select(table).where(table.c.user_id == dst)).mappings().first()
    if a is None or b is None:
        return  # no clash: the plain re-point moves (or skips) it
    fill = {}
    for col, value in a.items():
        if col == "user_id" or _empty(value):
            continue
        if col == "app_prefs_json":
            try:
                merged = {**json.loads(value or "{}"), **json.loads(b[col] or "{}")}
            except ValueError:
                continue
            if merged != json.loads(b[col] or "{}"):
                fill[col] = json.dumps(merged)
        elif _empty(b[col]):
            fill[col] = value
    if fill:
        db.execute(update(table).where(table.c.user_id == dst).values(**fill))
        report.append(f"  user_preferences: filled {sorted(fill)} from the absorbed account")


def merge(db, src: str, dst: str, data_dir: Path | None, *, apply: bool) -> list[str]:
    """Merge user ``src`` into ``dst``. Commits only when ``apply``."""
    from sqlalchemy import MetaData, and_, delete, inspect, select, true, update

    if src == dst:
        raise SystemExit("from and into are the same account")
    for uid in (src, dst):
        if account(db, uid) is None:
            raise SystemExit(f"no user {uid}")

    report: list[str] = []
    conn = db.connection()
    insp = inspect(conn)
    tables = [
        t for t in insp.get_table_names()
        if t != "users" and any(c["name"] == "user_id" for c in insp.get_columns(t))
    ]
    meta = MetaData()
    meta.reflect(bind=conn, only=tables + [t for t, _ in PATH_COLUMNS if insp.has_table(t)])

    _merge_preferences(db, meta, src, dst, report)

    for name in sorted(tables):
        table = meta.tables[name]
        keys = []
        pk = [c.name for c in table.primary_key.columns]
        if "user_id" in pk:
            keys.append(pk)
        keys += [u["column_names"] for u in insp.get_unique_constraints(name)
                 if "user_id" in u["column_names"]]
        keys += [i["column_names"] for i in insp.get_indexes(name)
                 if i.get("unique") and "user_id" in i["column_names"]]
        dropped = 0
        for cols in {tuple(k) for k in keys}:
            # In Python, not a self-referencing DELETE: MySQL refuses those
            # (error 1093) and these tables are small.
            others = [c for c in cols if c != "user_id"]
            if not others:  # one row per user: the survivor's wins
                if db.execute(select(table.c.user_id).where(table.c.user_id == dst)).first():
                    dropped += db.execute(
                        delete(table).where(table.c.user_id == src)
                    ).rowcount
                continue
            taken = {
                tuple(r) for r in db.execute(
                    select(*(table.c[c] for c in others)).where(table.c.user_id == dst)
                ).all()
            }
            for row in db.execute(
                select(*(table.c[c] for c in others)).where(table.c.user_id == src)
            ).all():
                if tuple(row) in taken:
                    dropped += db.execute(
                        delete(table).where(table.c.user_id == src).where(
                            and_(true(), *(table.c[c] == v for c, v in zip(others, row)))
                        )
                    ).rowcount
        moved = db.execute(
            update(table).where(table.c.user_id == src).values(user_id=dst)
        ).rowcount
        if moved or dropped:
            report.append(
                f"  {name}: {moved} moved"
                + (f", {dropped} dropped (survivor already has that key)" if dropped else "")
            )

    subs, flights = meta.tables.get("flight_subscriptions"), meta.tables.get("flights")
    if subs is not None and flights is not None:
        own = db.execute(
            delete(subs).where(subs.c.user_id == dst).where(
                subs.c.flight_id.in_(select(flights.c.id).where(flights.c.user_id == dst))
            )
        ).rowcount
        if own:
            report.append(f"  flight_subscriptions: {own} dropped (now the survivor's own flight)")

    for name, col in PATH_COLUMNS:
        table = meta.tables.get(name)
        if table is None:
            continue
        old, new = f"/packs/{src}/", f"/packs/{dst}/"
        rows = db.execute(
            select(table.c.id, table.c[col]).where(table.c[col].contains(old))
        ).all()
        for row_id, path in rows:
            db.execute(
                update(table).where(table.c.id == row_id)
                .values({col: path.replace(old, new, 1)})
            )
        if rows:
            report.append(f"  {name}.{col}: {len(rows)} paths rewritten")

    users = MetaData()
    users.reflect(bind=conn, only=["users"])
    db.execute(delete(users.tables["users"]).where(users.tables["users"].c.id == src))
    report.append(f"  users: deleted {src}")

    moves = []
    src_dir = data_dir / "packs" / src if data_dir else None
    if src_dir is not None and src_dir.is_dir():
        for child in sorted(src_dir.iterdir()):
            target = data_dir / "packs" / dst / child.name
            if target.exists():
                db.rollback()
                raise SystemExit(f"refusing: {target} already exists (nothing changed)")
            moves.append((child, target))
    if moves:
        report.append(f"  pack dirs: {len(moves)} to move {src_dir} -> {data_dir / 'packs' / dst}")

    if not apply:
        db.rollback()
        report.append("DRY RUN: rolled back, no directory moved. Re-run with --apply.")
        return report

    done = []
    try:
        for child, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            child.rename(target)
            done.append((child, target))
        db.commit()
    except BaseException:
        db.rollback()
        for child, target in reversed(done):
            target.rename(child)
        raise
    if src_dir is not None and src_dir.is_dir() and not any(src_dir.iterdir()):
        src_dir.rmdir()
    report.append("APPLIED.")
    return report


# --- Entry points ---------------------------------------------------------------


def _fmt(a: dict) -> str:
    return (f"{a['id']}  {a['provider']:<6} {a['email']}  created {str(a['created_at'])[:10]}"
            f"  last login {str(a['last_login_at'])[:10]}  {a['flights']} flights")


def run(args, db, data_dir: Path | None) -> int:
    if args.email:
        accounts = accounts_for_email(db, args.email)
        for a in accounts:
            print("   ", _fmt(a))
        if len(accounts) < 2:
            print("nothing to merge: fewer than two accounts with that email")
            return 1
        if args.into:
            into = next((a for a in accounts if a["id"] == args.into), None)
            if into is None:
                raise SystemExit("--into is not one of these accounts")
        elif len(accounts) == 2:
            into = pick_survivor(accounts)
        else:
            raise SystemExit("more than two accounts: name the survivor with --into")
        absorbed = [a for a in accounts if a["id"] != into["id"]]
    else:
        into, absorbed = account(db, args.into), [account(db, args.from_)]
        if into is None or absorbed[0] is None:
            raise SystemExit("unknown user id")
        print("    into:", _fmt(into))
        print("    from:", _fmt(absorbed[0]))

    for a in absorbed:
        print(f"\nmerge {a['id']} ({a['provider']}) -> {into['id']} ({into['provider']})")
        for line in merge(db, a["id"], into["id"], data_dir, apply=args.apply):
            print(line)
        if a["provider"] in OAUTH_PROVIDERS:
            print(f"  NOTE: their {a['provider']} sign-in no longer has an account; it "
                  f"re-links only if flyfun-common's email rule applies, otherwise it "
                  f"creates a new empty one. Tell them to sign in with "
                  f"{into['provider'] if into['provider'] != 'email' else 'the email link'}.")
    return 0


def parse(argv: list[str]):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("email", nargs="?", help="merge the accounts holding this email")
    p.add_argument("--from", dest="from_", help="absorbed user id (with --into)")
    p.add_argument("--into", help="surviving user id")
    p.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    p.add_argument("--prod", action="store_true", help="run inside the prod container")
    args = p.parse_args(argv)
    if not args.email and not (args.from_ and args.into):
        p.error("give an EMAIL, or both --from and --into")
    return args


def main(argv: list[str]) -> int:
    args = parse(argv)
    if args.prod:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import hosts

        ssh = hosts.server_values("SERVER_SSH")["SERVER_SSH"]
        inner = [a for a in argv if a != "--prod"]
        r = subprocess.run(
            [*SSH, ssh, "docker exec -i weatherbrief python - " + shlex.join(inner)],
            input=Path(__file__).read_text(), text=True, timeout=300,
        )
        return r.returncode

    from weatherbrief.db import SessionLocal, init_shared_db
    from weatherbrief.storage.flights import _data_dir

    init_shared_db()
    db = SessionLocal()
    try:
        return run(args, db, _data_dir())
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
