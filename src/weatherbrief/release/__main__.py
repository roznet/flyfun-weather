"""CLI entry-point: ``python -m weatherbrief.release``.

Programmatic management of the What's New / release stream (the
``system_messages`` table). Built so the deploy skill can draft an entry from
the PRs in a deploy, the user validates it, and it's pushed with one command —
and so an initial backfill can be bulk-imported from a JSON file.

Subcommands
-----------
list
    Show existing entries (id, date, highlight flag, category, title).
add
    Create one entry. Body comes from --body, --body-file PATH, or
    --body-file - (stdin, so markdown survives SSH without shell-escaping).
import
    Bulk-insert from a JSON file (list of {date, title, body, category,
    highlight}). Dedupes on (date, title) so re-runs are idempotent.
    Use --force-no-highlight for a historical backfill so existing users
    don't get a wall of notification dots for past work.
    Pass ``-`` to read the JSON from stdin: ``release-notes/`` is not in the
    container image, so publishing a drafted entry on the server means piping
    it in over SSH rather than naming a path the container can't see.

Examples
--------
    python -m weatherbrief.release list
    python -m weatherbrief.release add --title "New cloud display" \
        --category feature --highlight --body-file - < notes.md
    python -m weatherbrief.release import release-notes/backfill-grouped.json \
        --force-no-highlight
    ssh user@host "docker exec -i weatherbrief python -m weatherbrief.release \
        import -" < release-notes/ios-1.5.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as date_t
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from flyfun_common.db import SessionLocal, get_engine
from weatherbrief.db.models import SystemMessageRow

VALID_CATEGORIES = ("feature", "change", "fix", "app_release")


def _read_body(args: argparse.Namespace) -> str:
    if args.body is not None:
        return args.body
    if args.body_file is not None:
        if args.body_file == "-":
            return sys.stdin.read()
        return Path(args.body_file).read_text(encoding="utf-8")
    raise SystemExit("error: provide --body or --body-file (use - for stdin)")


def _validate_entry(date: str, title: str, body: str, category: str) -> None:
    if not date or not title or not body:
        raise SystemExit("error: date, title, and body are all required")
    if category not in VALID_CATEGORIES:
        raise SystemExit(f"error: category must be one of {VALID_CATEGORIES}, got {category!r}")


def _cmd_list(args: argparse.Namespace) -> None:
    get_engine()
    db = SessionLocal()
    try:
        rows = (
            db.query(SystemMessageRow)
            .order_by(SystemMessageRow.date.desc(), SystemMessageRow.id.desc())
            .all()
        )
        if not rows:
            print("No release entries yet.")
            return
        print(f"{len(rows)} entries (newest first):\n")
        for r in rows:
            dot = "★" if r.highlight else " "
            print(f"  {dot} #{r.id:<4d} {r.date}  [{r.category:<7s}] {r.title}")
    finally:
        db.close()


def _cmd_add(args: argparse.Namespace) -> None:
    body = _read_body(args)
    date = args.date or date_t.today().isoformat()
    _validate_entry(date, args.title, body, args.category)

    get_engine()
    db = SessionLocal()
    try:
        row = SystemMessageRow(
            date=date,
            title=args.title,
            body=body,
            category=args.category,
            highlight=args.highlight,
        )
        db.add(row)
        db.commit()
        dot = " (highlighted — lights the dot)" if args.highlight else ""
        print(f"Created #{row.id}: {date} [{args.category}] {args.title}{dot}")
    finally:
        db.close()


def _cmd_import(args: argparse.Namespace) -> None:
    # "-" reads stdin so a drafted entry can be piped straight into the
    # container, which has no copy of `release-notes/`. Same reasoning as
    # `add --body-file -`.
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    data: Any = json.loads(raw)
    if not isinstance(data, list):
        raise SystemExit("error: import file must be a JSON list of entries")

    get_engine()
    db = SessionLocal()
    created = 0
    skipped = 0
    try:
        # Build a set of existing (date, title) pairs for dedupe.
        existing = {
            (r.date, r.title)
            for r in db.query(SystemMessageRow.date, SystemMessageRow.title).all()
        }
        for i, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise SystemExit(f"error: entry {i} is not an object")
            date = str(entry.get("date", "")).strip()
            title = str(entry.get("title", "")).strip()
            body = str(entry.get("body", "")).strip()
            category = str(entry.get("category", "feature")).strip()
            highlight = bool(entry.get("highlight", False))
            if args.force_no_highlight:
                highlight = False
            _validate_entry(date, title, body, category)

            if (date, title) in existing:
                skipped += 1
                continue

            db.add(SystemMessageRow(
                date=date, title=title, body=body,
                category=category, highlight=highlight,
            ))
            existing.add((date, title))
            created += 1

        if args.dry_run:
            db.rollback()
            print(f"[dry-run] would create {created}, skip {skipped} (already present)")
        else:
            db.commit()
            print(f"Imported {created} new entries, skipped {skipped} (already present)")
            if args.force_no_highlight and created:
                print("  (--force-no-highlight: all imported entries are non-highlighted)")
    finally:
        db.close()


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m weatherbrief.release")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List existing release entries")
    p_list.set_defaults(func=_cmd_list)

    p_add = sub.add_parser("add", help="Create a single release entry")
    p_add.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--category", default="feature", choices=VALID_CATEGORIES)
    p_add.add_argument("--highlight", action="store_true",
                       help="Light the notification dot for this entry")
    p_add.add_argument("--body", help="Body markdown as a string")
    p_add.add_argument("--body-file", dest="body_file",
                       help="Read body markdown from a file; use - for stdin")
    p_add.set_defaults(func=_cmd_add)

    p_imp = sub.add_parser("import", help="Bulk-import entries from a JSON file")
    p_imp.add_argument("file", help="Path to JSON file (list of entries); - for stdin")
    p_imp.add_argument("--force-no-highlight", action="store_true",
                       help="Force highlight=false on all imported entries "
                            "(use for historical backfill)")
    p_imp.add_argument("--dry-run", action="store_true",
                       help="Report what would be imported without writing")
    p_imp.set_defaults(func=_cmd_import)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
