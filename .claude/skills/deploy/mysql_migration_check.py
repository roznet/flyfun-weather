#!/usr/bin/env python3
"""Rehearse pending Alembic migrations against a real MySQL before deploying.

Dev is SQLite and production is MySQL, so a migration can pass every local test
and still be impossible to run in production. Migration 094 shipped that way: it
gave a TEXT column a ``server_default``, which MySQL rejects outright (error
1101). Because the code deployed alongside it already expected the new columns,
every ``briefing_packs`` query failed until the schema was applied by hand.
Nothing local could have caught it -- SQLite accepts that DDL happily.

This closes that gap. It builds a throwaway MySQL database, brings it to the
revision production is *currently* at, seeds a row into each table the pending
migrations touch, then runs the pending migrations for real. So both the "MySQL
rejects this DDL" class and the "cannot add a NOT NULL column to a table that
already has rows" class fail here rather than in production.

Credentials are read at run time from ``~/.my.cnf`` and never written anywhere.
If MySQL or that file is absent the check SKIPS (exit 0): it sharpens a deploy,
it is not a gate that should block one on a machine with no local MySQL.

Usage:
    python mysql_migration_check.py --from-rev 093
    python mysql_migration_check.py --from-rev 093 --to-rev 094

Exit codes: 0 = passed or skipped, 1 = a pending migration failed on MySQL.
"""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import os
import pathlib
import re
import subprocess
import sys
import urllib.parse


def _skip(msg: str) -> None:
    print(f"SKIP: {msg}")
    sys.exit(0)


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def read_my_cnf() -> dict:
    """Read ``[client]`` from ~/.my.cnf.

    Values are often quote-wrapped in that file, and configparser keeps the
    quotes while the mysql CLI strips them -- so a naive read authenticates with
    a password two characters too long and gets a baffling 1045 "access denied"
    even though the CLI works fine. Strip them.
    """
    path = pathlib.Path.home() / ".my.cnf"
    if not path.exists():
        _skip("~/.my.cnf not found - no local MySQL credentials to rehearse with")

    cp = configparser.ConfigParser()
    cp.read(path)
    if not cp.has_section("client"):
        _skip("~/.my.cnf has no [client] section")

    def clean(value: str) -> str:
        return (value or "").strip().strip('"').strip("'")

    sec = cp["client"]
    return {
        "user": clean(sec.get("user", "root")),
        "password": clean(sec.get("password", "")),
        "host": clean(sec.get("host", "127.0.0.1")) or "127.0.0.1",
    }


def scratch_url(creds: dict, db_name: str) -> str:
    user = urllib.parse.quote_plus(creds["user"])
    pw = urllib.parse.quote_plus(creds["password"])
    return f"mysql+pymysql://{user}:{pw}@{creds['host']}/{db_name}"


def server_url(creds: dict) -> str:
    """Same credentials, no database selected -- for CREATE/DROP DATABASE."""
    user = urllib.parse.quote_plus(creds["user"])
    pw = urllib.parse.quote_plus(creds["password"])
    return f"mysql+pymysql://{user}:{pw}@{creds['host']}"


def _migration_files() -> dict:
    """Map revision id -> (path, down_revision) for every migration on disk."""
    rev_re = re.compile(r"""^revision(?::\s*str)?\s*=\s*["']([^"']+)["']""", re.M)
    down_re = re.compile(r"""^down_revision(?::[^=]*)?\s*=\s*(?:["']([^"']+)["']|None)""", re.M)
    out = {}
    for path in pathlib.Path("alembic/versions").glob("*.py"):
        text = path.read_text()
        rev = rev_re.search(text)
        if not rev:
            continue
        down = down_re.search(text)
        out[rev.group(1)] = (path, down.group(1) if down and down.group(1) else None)
    return out


def pending_files(from_rev: str, to_rev: str) -> list:
    """Migration files strictly after ``from_rev`` up to and including ``to_rev``.

    Walks ``down_revision`` back from the target, which keeps this honest about
    the real chain rather than guessing from filenames or git ranges -- the
    alembic revision ids and the git history are different things.
    """
    files = _migration_files()
    if not files:
        return []

    if to_rev in (None, "head", "heads"):
        parents = {d for _, (_, d) in files.items() if d}
        tips = [r for r in files if r not in parents]
        if len(tips) != 1:
            return []
        cursor = tips[0]
    else:
        cursor = to_rev

    chain = []
    seen = set()
    while cursor and cursor != from_rev and cursor in files and cursor not in seen:
        seen.add(cursor)
        path, down = files[cursor]
        chain.append(path)
        cursor = down
    return list(reversed(chain))


def tables_touched(from_rev: str, to_rev: str) -> set:
    """Tables named by the migrations in the pending range.

    Parsed from the migration files, so we seed exactly the tables about to be
    altered -- that is what makes a NOT NULL add against a populated table fail
    here instead of in production.
    """
    pattern = re.compile(r"""batch_alter_table\(\s*["']([A-Za-z0-9_]+)["']""")
    tables = set()
    for path in pending_files(from_rev, to_rev):
        tables.update(pattern.findall(path.read_text()))
    return tables


def seed_row(conn, sa, table: str) -> str:
    """Insert one row into ``table``, filling whatever MySQL strict mode demands.

    Foreign keys are switched off for the insert: the point is to have a row
    present when the ALTER runs, not to build referentially valid data.
    """
    cols = list(conn.execute(sa.text(f"SHOW COLUMNS FROM {table}")))
    values = {}
    for field, coltype, nullable, _key, default, extra in cols:
        if nullable == "YES" or default is not None or "auto_increment" in (extra or ""):
            continue
        t = coltype.lower()
        if "int" in t:
            values[field] = 1
        elif any(k in t for k in ("float", "double", "decimal")):
            values[field] = 1.0
        elif "datetime" in t or "timestamp" in t:
            values[field] = dt.datetime(2026, 1, 1, 0, 0, 0)
        elif "date" in t:
            values[field] = dt.date(2026, 1, 1)
        else:
            values[field] = "x"

    if not values:
        values = {cols[1][0]: "x"}

    names = ", ".join(values)
    holes = ", ".join(f":{k}" for k in values)
    conn.execute(sa.text("SET FOREIGN_KEY_CHECKS=0"))
    conn.execute(sa.text(f"INSERT INTO {table} ({names}) VALUES ({holes})"), values)
    conn.execute(sa.text("SET FOREIGN_KEY_CHECKS=1"))
    return f"{table} ({len(values)} required cols)"


def alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, DATABASE_URL=url)
    return subprocess.run(
        ["alembic", *args], capture_output=True, text=True, env=env,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-rev", required=True,
                    help="revision production is currently at (its alembic current)")
    ap.add_argument("--to-rev", default="head", help="revision to upgrade to (default: head)")
    ap.add_argument("--keep", action="store_true", help="leave the scratch DB behind for inspection")
    args = ap.parse_args()

    try:
        import sqlalchemy as sa  # noqa: F401
        import pymysql  # noqa: F401
    except ImportError as exc:
        _skip(f"{exc.name} not importable - activate the venv first")

    import sqlalchemy as sa

    creds = read_my_cnf()
    db_name = f"wb_migcheck_{os.getpid()}"

    try:
        admin = sa.create_engine(server_url(creds), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(sa.text(f"DROP DATABASE IF EXISTS {db_name}"))
            c.execute(sa.text(f"CREATE DATABASE {db_name} CHARACTER SET utf8mb4"))
    except Exception as exc:  # noqa: BLE001 - any connection problem means "skip"
        _skip(f"cannot reach local MySQL ({type(exc).__name__}) - is it running?")

    url = scratch_url(creds, db_name)
    print(f"scratch database: {db_name} (dropped when done)")

    try:
        print(f"building schema to {args.from_rev} (what production runs now)...")
        res = alembic(url, "upgrade", args.from_rev)
        if res.returncode != 0:
            _fail(f"could not build the baseline schema on MySQL:\n{res.stderr[-2000:]}")

        touched = tables_touched(args.from_rev, args.to_rev)
        engine = sa.create_engine(url)
        seeded = []
        for table in sorted(touched):
            try:
                with engine.begin() as c:
                    seeded.append(seed_row(c, sa, table))
            except Exception as exc:  # noqa: BLE001
                print(f"  note: could not seed {table} ({type(exc).__name__}) - "
                      f"the DDL is still exercised, just against an empty table")
        if seeded:
            print("seeded: " + "; ".join(seeded))
        elif touched:
            print(f"tables touched but none seeded: {', '.join(sorted(touched))}")
        else:
            print("no batch_alter_table targets found in the pending range")

        print(f"running pending migrations {args.from_rev} -> {args.to_rev} on MySQL...")
        res = alembic(url, "upgrade", args.to_rev)
        if res.returncode != 0:
            tail = (res.stderr or res.stdout)[-2000:]
            _fail(
                "a pending migration cannot run on MySQL. This WILL break the deploy "
                f"if shipped:\n{tail}"
            )

        print(f"reversing {args.to_rev} -> {args.from_rev} to check downgrade()...")
        res = alembic(url, "downgrade", args.from_rev)
        if res.returncode != 0:
            print(f"WARNING: downgrade failed on MySQL - rollback would need manual DDL:\n"
                  f"{(res.stderr or res.stdout)[-1200:]}")
        else:
            res = alembic(url, "upgrade", args.to_rev)
            if res.returncode != 0:
                print("WARNING: re-upgrade after downgrade failed - the migration is not "
                      "cleanly repeatable")

        print("PASS: pending migrations run on real MySQL")
    finally:
        if args.keep:
            print(f"kept scratch database {db_name}")
        else:
            try:
                with admin.connect() as c:
                    c.execute(sa.text(f"DROP DATABASE IF EXISTS {db_name}"))
            except Exception:  # noqa: BLE001
                print(f"note: could not drop scratch database {db_name} - drop it by hand")


if __name__ == "__main__":
    main()
