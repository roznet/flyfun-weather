"""CLI entry-point: ``python -m weatherbrief.triage``."""

from __future__ import annotations

import argparse
import logging
import sys
import textwrap

from dotenv import load_dotenv

from weatherbrief.triage.db import connect
from weatherbrief.triage.ingest import ingest
from weatherbrief.triage.process import process


def _cmd_ingest(args: argparse.Namespace) -> None:
    count = ingest(dry_run=args.dry_run)
    label = "[dry-run] Would ingest" if args.dry_run else "Ingested"
    print(f"{label} {count} item(s)")


def _cmd_process(args: argparse.Namespace) -> None:
    if args.log_file:
        print(f"Streaming claude output to {args.log_file}  (tail -f {args.log_file})")
    count = process(
        n=args.n,
        timeout=args.timeout,
        dry_run=args.dry_run,
        app_feedback_id=args.id,
        log_file=args.log_file,
    )
    label = "[dry-run] Would process" if args.dry_run else "Processed"
    print(f"{label} {count} item(s)")


def _cmd_status(args: argparse.Namespace) -> None:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM queue GROUP BY status ORDER BY status"
        ).fetchall()

        total = sum(r["cnt"] for r in rows)
        print(f"Queue: {total} total")
        for r in rows:
            print(f"  {r['status']:15s} {r['cnt']}")

        if args.verbose:
            print()
            # Recent items
            recent = conn.execute(
                "SELECT id, app_feedback_id, category, status, classification, "
                "ingested_at, processed_at FROM queue ORDER BY id DESC LIMIT 10"
            ).fetchall()
            if recent:
                print("Recent items:")
                fmt = "  #{id:<4d}  fb={app_feedback_id:<4d}  {category:<10s}  {status:<12s}  {classification}"
                for r in recent:
                    print(
                        fmt.format(
                            **{
                                **dict(r),
                                "classification": r["classification"] or "-",
                            }
                        )
                    )

            print()
            # Rate info
            flagged = conn.execute(
                "SELECT * FROM user_rate WHERE flagged = 1"
            ).fetchall()
            if flagged:
                print("Flagged users:")
                for r in flagged:
                    print(f"  {r['user_id']}  count={r['feedback_count']}")
            else:
                print("No flagged users")
    finally:
        conn.close()


def _cmd_show(args: argparse.Namespace) -> None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM queue WHERE id = ?", (args.id,)
        ).fetchone()
        if row is None:
            print(f"Item {args.id} not found", file=sys.stderr)
            sys.exit(1)

        d = dict(row)
        print(f"=== Triage Item #{d['id']} ===")
        print(f"App Feedback ID : {d['app_feedback_id']}")
        print(f"Status          : {d['status']}")
        print(f"Classification  : {d['classification'] or '-'}")
        print(f"Confidence      : {d['confidence'] or '-'}")
        print(f"Category        : {d['category']}")
        print(f"User            : {d['user_name']} ({d['user_email']})")
        print(f"Flight          : {d['flight_id']}")
        print(f"Submitted       : {d['feedback_created_at']}")
        print(f"Ingested        : {d['ingested_at']}")
        print(f"Processed       : {d['processed_at'] or '-'}")
        print()
        print("--- Comment ---")
        print(d["comment"])
        if d["analysis"]:
            print()
            print("--- Analysis ---")
            print(d["analysis"])
        if d["suggested_response"]:
            print()
            print("--- Suggested Response ---")
            print(d["suggested_response"])
        if d["error_message"]:
            print()
            print("--- Error ---")
            print(d["error_message"])

        # Cost / performance
        if d["claude_cost_usd"] is not None:
            print()
            print("--- Processing Stats ---")
            print(f"  Cost         : ${d['claude_cost_usd']:.4f}")
            print(f"  Duration     : {d['claude_duration_ms']}ms")
            print(f"  Turns        : {d['claude_num_turns']}")
            print(f"  Wall time    : {d['processing_time_s']:.1f}s")
            print(f"  Session      : {d['claude_session_id'] or '-'}")

        if args.with_log:
            print()
            print("--- Processing Log ---")
            logs = conn.execute(
                "SELECT * FROM processing_log WHERE queue_id = ? ORDER BY timestamp",
                (args.id,),
            ).fetchall()
            for log in logs:
                detail = f"  {log['detail']}" if log["detail"] else ""
                print(f"  {log['timestamp']}  {log['action']}{detail}")
    finally:
        conn.close()


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

    # ingest
    p_ingest = sub.add_parser("ingest", help="Import new feedback into triage queue")
    p_ingest.add_argument("--dry-run", action="store_true")

    # process
    p_process = sub.add_parser("process", help="Process pending triage items with Claude")
    p_process.add_argument("-n", type=int, default=1, help="Number of items to process")
    p_process.add_argument("--id", type=int, default=None, metavar="FEEDBACK_ID",
                           help="Process a specific app_feedback_id instead of oldest pending")
    p_process.add_argument("--timeout", type=int, default=300, help="Timeout per item (seconds)")
    p_process.add_argument("--log-file", type=str, default=None, metavar="PATH",
                           help="Stream claude output to this file (use tail -f to watch)")
    p_process.add_argument("--dry-run", action="store_true")

    # status
    p_status = sub.add_parser("status", help="Show queue summary")
    p_status.add_argument("-v", "--verbose", action="store_true")

    # show
    p_show = sub.add_parser("show", help="Show details for a triage item")
    p_show.add_argument("id", type=int, help="Queue item ID")
    p_show.add_argument("--with-log", action="store_true", help="Include processing log")

    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    handlers = {
        "ingest": _cmd_ingest,
        "process": _cmd_process,
        "status": _cmd_status,
        "show": _cmd_show,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
