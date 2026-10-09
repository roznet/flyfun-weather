"""Clear auto-derived trip names, so an unnamed trip is stored unnamed (#728).

Revision ID: 098
Revises: 097
Create Date: 2026-10-09

Until #728, ``create_trip`` persisted the derived label ("EGTF → LSGS → EGTF,
20–22 Feb", or "New trip" for an empty one) into ``flight_trips.name``. That
made "has the pilot named this trip?" unanswerable — a client titling the group
with the name printed the chain twice — and froze the label at creation while
the legs moved on. The API now derives ``display_name`` on every read and
``name`` holds only what the pilot typed.

Data only, no ``batch_alter_table``. A name is cleared only when it is *wholly*
in the derived shape: a pilot name that merely contains a chain ("Summer tour
EGTF → LFAT → EGTF, 3–5 Aug") is the pilot's and is kept. A pilot who typed
exactly the derived shape by hand loses nothing visible — ``display_name``
derives the same text back.
"""
from __future__ import annotations

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "098"
down_revision: Union[str, None] = "097"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# What ``api/trips.py::derive_trip_name`` produced: an upper-case ident chain
# (or "Trip" when no leg had airports), a comma, then "20 Feb", "20–22 Feb" or
# "28 Feb–2 Mar".
_DAY = r"\d{1,2} [A-Z][a-z]{2}"
DERIVED_NAME_RE = re.compile(
    r"^(?:Trip|[A-Z0-9]+(?: → [A-Z0-9]+)*), "
    rf"(?:\d{{1,2}}(?: [A-Z][a-z]{{2}})?–)?{_DAY}$"
)


def is_derived_name(name: str) -> bool:
    return name == "New trip" or bool(DERIVED_NAME_RE.match(name))


def upgrade() -> None:
    bind = op.get_bind()
    trips = sa.table(
        "flight_trips",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
    )
    rows = bind.execute(sa.select(trips.c.id, trips.c.name)).fetchall()
    for trip_id, name in rows:
        if not name or not is_derived_name(name):
            continue
        bind.execute(
            sa.update(trips)
            # Conditional on the name read above: the new app is already
            # serving while this runs, and a rename landing between the SELECT
            # and here is the pilot's and must survive.
            .where(trips.c.id == trip_id, trips.c.name == name)
            .values(name="")
        )


def downgrade() -> None:
    # Nothing to restore: the derived text was never the pilot's, and the
    # pre-#728 clients already fall back to the chain for an empty name.
    pass
