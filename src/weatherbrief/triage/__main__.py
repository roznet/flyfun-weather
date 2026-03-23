"""CLI entry-point: ``python -m weatherbrief.triage``."""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv
from sqlalchemy import func

from flyfun_common.db import SessionLocal, get_engine
from flyfun_common.db.models import UserRow
from weatherbrief.db.models import FeedbackRow
from weatherbrief.triage.process import process


def _cmd_process(args: argparse.Namespace) -> None:
    if args.log_file:
        print(f"Streaming claude output to {args.log_file}  (tail -f {args.log_file})")
    count = process(
        n=args.n,
        timeout=args.timeout,
        dry_run=args.dry_run,
        feedback_id=args.id,
        log_file=args.log_file,
    )
    label = "[dry-run] Would process" if args.dry_run else "Processed"
    print(f"{label} {count} item(s)")


def _cmd_status(args: argparse.Namespace) -> None:
    get_engine()
    db = SessionLocal()
    try:
        rows = (
            db.query(FeedbackRow.status, func.count(FeedbackRow.id))
            .group_by(FeedbackRow.status)
            .order_by(FeedbackRow.status)
            .all()
        )

        total = sum(cnt for _, cnt in rows)
        print(f"Feedback: {total} total")
        for status, cnt in rows:
            print(f"  {status:15s} {cnt}")

        if args.verbose:
            print()
            recent = (
                db.query(FeedbackRow)
                .order_by(FeedbackRow.created_at.desc())
                .limit(10)
                .all()
            )
            if recent:
                print("Recent items:")
                for fb in recent:
                    print(
                        f"  #{fb.id:<4d}  {fb.category:<10s}  "
                        f"{fb.status:<12s}  {fb.classification or '-'}"
                    )
    finally:
        db.close()


def _cmd_show(args: argparse.Namespace) -> None:
    get_engine()
    db = SessionLocal()
    try:
        fb = db.query(FeedbackRow).filter(FeedbackRow.id == args.id).first()
        if fb is None:
            print(f"Feedback #{args.id} not found", file=sys.stderr)
            sys.exit(1)

        user = db.query(UserRow).filter(UserRow.id == fb.user_id).first()
        user_name = user.display_name if user else "?"
        user_email = user.email if user else "?"

        print(f"=== Feedback #{fb.id} ===")
        print(f"Status          : {fb.status}")
        print(f"Classification  : {fb.classification or '-'}")
        print(f"Confidence      : {fb.confidence or '-'}")
        print(f"Category        : {fb.category}")
        print(f"User            : {user_name} ({user_email})")
        print(f"Flight          : {fb.flight_id or '-'}")
        print(f"Submitted       : {fb.created_at}")
        print(f"Processed       : {fb.processed_at or '-'}")
        print(f"Replied         : {fb.replied_at or '-'}")
        print()
        print("--- Comment ---")
        print(fb.comment)
        if fb.ai_analysis:
            print()
            print("--- AI Analysis ---")
            print(fb.ai_analysis)
        if fb.admin_reply:
            print()
            print("--- Reply ---")
            print(fb.admin_reply)
        if fb.admin_notes:
            print()
            print("--- Admin Notes ---")
            print(fb.admin_notes)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="python -m weatherbrief.triage",
        description="Feedback triage agent",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="verbose logging"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # process
    p_process = sub.add_parser("process", help="Process pending feedback with Claude")
    p_process.add_argument("-n", type=int, default=1, help="Number of items to process")
    p_process.add_argument("--id", type=int, default=None, metavar="FEEDBACK_ID",
                           help="Process a specific feedback ID instead of oldest pending")
    p_process.add_argument("--timeout", type=int, default=300, help="Timeout per item (seconds)")
    p_process.add_argument("--log-file", type=str, default=None, metavar="PATH",
                           help="Stream claude output to this file (use tail -f to watch)")
    p_process.add_argument("--dry-run", action="store_true")

    # status
    p_status = sub.add_parser("status", help="Show feedback status summary")
    p_status.add_argument("-v", "--verbose", action="store_true")

    # show
    p_show = sub.add_parser("show", help="Show details for a feedback item")
    p_show.add_argument("id", type=int, help="Feedback ID")

    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    handlers = {
        "process": _cmd_process,
        "status": _cmd_status,
        "show": _cmd_show,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
