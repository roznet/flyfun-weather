"""Custom SQLAlchemy column types shared by weatherbrief models.

Home of :class:`TZDateTime` (issue #520). The decorator conceptually belongs
in ``flyfun-common`` so its own models can adopt it too; it lives here until a
flyfun-common release ships it, at which point this module should re-export
the upstream class and the local definition can be deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.dialects.mysql import DATETIME as MYSQL_DATETIME
from sqlalchemy.types import TypeDecorator


class TZDateTime(TypeDecorator):
    """DATETIME that is always UTC-aware in Python, naive-UTC in the DB.

    MySQL's DATETIME stores no offset and SQLAlchemy's ``timezone=True`` is a
    no-op there, so what a read hands back depends on dialect and driver.
    Before this type, every call site compensated by hand — 66
    ``replace(tzinfo=...)`` / ``astimezone(...)`` fixups with contradictory
    conventions (see issue #520). This normalises explicitly on both sides
    instead:

    * **bind**: aware datetimes (any zone) are converted to UTC and stored
      naive. Naive datetimes raise ``ValueError`` — deliberately, so "which
      convention does this module use?" becomes an immediate, local error
      instead of a wrong number somewhere downstream. This applies to query
      bind parameters too: comparing a ``TZDateTime`` column against a naive
      datetime raises.
    * **result**: values come back with ``tzinfo=timezone.utc`` on every
      dialect.

    The stored representation (naive UTC) is unchanged from
    ``DateTime(timezone=True)``, so switching a column over needs no
    migration.

    ``fsp`` (fractional-second precision) is opt-in: plain ``TZDateTime()``
    renders the same DDL as before so adopted columns don't drift from prod
    schema, while ``TZDateTime(fsp=6)`` renders MySQL ``DATETIME(6)`` — use it
    for any new column that serves as a natural key, uniqueness component, or
    equality predicate (plain DATETIME truncates to whole seconds on MySQL
    while SQLite keeps microseconds; that skew caused the migration-015 bug).
    """

    impl = DateTime
    cache_ok = True

    def __init__(self, fsp: int | None = None):
        super().__init__()
        self.fsp = fsp

    def load_dialect_impl(self, dialect):
        if dialect.name == "mysql" and self.fsp:
            return dialect.type_descriptor(MYSQL_DATETIME(fsp=self.fsp))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime written to a TZDateTime column "
                f"({value.isoformat()}); pass an aware datetime"
            )
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            # Some driver/dialect combinations hand back aware values (with
            # timezone(timedelta(0)) rather than timezone.utc); convert rather
            # than blindly re-stamp so a non-UTC offset can't be mislabelled.
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=timezone.utc)


def utc_now() -> datetime:
    """Aware-UTC now, for ``default=utc_now`` on TZDateTime columns."""
    return datetime.now(timezone.utc)
