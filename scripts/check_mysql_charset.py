#!/usr/bin/env python3
"""Verify every table in a MySQL database is InnoDB / utf8mb4.

No migration in the 84-revision history declares charset, collation, or
engine — every table inherits server defaults while the app code assumes
utf8mb4 (e.g. the 191-char index caps). The mysql-review PR deliberately
does not rebuild tables (that needs prod verification + a maintenance
window); this script is the pre/post check for when that happens, and a
guard against new non-utf8mb4 tables landing in the meantime.

Read-only. Needs only SQLAlchemy — no app code.

Usage:
    python scripts/check_mysql_charset.py                       # uses $DATABASE_URL
    python scripts/check_mysql_charset.py --url mysql+pymysql://user:pass@host/db

Exit codes: 0 = all tables InnoDB + utf8mb4-family (or not a MySQL URL),
1 = violations found, 2 = no URL given.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text

_TABLES_SQL = """
SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, TABLE_ROWS
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME
"""

# sys.schema_unused_indexes only exists when the sys schema is installed, and
# its stats reset on server restart — hence the "verify with uptime" caveat.
_UNUSED_INDEXES_SQL = """
SELECT object_name AS table_name, index_name
FROM sys.schema_unused_indexes
WHERE object_schema = DATABASE()
ORDER BY 1, 2
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Exit 0 when clean or not MySQL; exit 1 on violations.",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DATABASE_URL"),
        help="SQLAlchemy database URL (default: $DATABASE_URL)",
    )
    args = parser.parse_args()

    if not args.url:
        print("error: no database URL — pass --url or set DATABASE_URL", file=sys.stderr)
        return 2

    engine = create_engine(args.url)
    if engine.dialect.name != "mysql":
        print(f"not MySQL (dialect: {engine.dialect.name}) — nothing to check")
        return 0

    with engine.connect() as conn:
        rows = conn.execute(text(_TABLES_SQL)).all()

        print(f"{'TABLE_NAME':<40} {'ENGINE':<10} {'TABLE_COLLATION':<24} ~ROWS")
        bad: list[str] = []
        for name, eng, collation, approx_rows in rows:
            print(f"{name:<40} {eng or '-':<10} {collation or '-':<24} {approx_rows}")
            if eng != "InnoDB" or not (collation or "").startswith("utf8mb4"):
                bad.append(name)

        if bad:
            print(f"\nFAIL: {len(bad)} table(s) not InnoDB/utf8mb4: {', '.join(bad)}")
            rc = 1
        else:
            print(f"\nOK: {len(rows)} table(s), all InnoDB with utf8mb4-family collation")
            rc = 0

        try:
            unused = conn.execute(text(_UNUSED_INDEXES_SQL)).all()
        except Exception as exc:
            print(f"\nunused-index check skipped ({type(exc).__name__}: {exc})")
        else:
            if unused:
                print("\npossibly unused indexes (verify with uptime):")
                for table_name, index_name in unused:
                    print(f"  {table_name} / {index_name}")
            else:
                print("\nno unused indexes reported by sys.schema_unused_indexes")

    return rc


if __name__ == "__main__":
    sys.exit(main())
