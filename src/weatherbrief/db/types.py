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

    ``fsp`` (fractional-second precision) is opt-in. Plain ``TZDateTime()``
    renders the same DDL as ``DateTime(timezone=True)``, which is exactly what
    lets an existing column be converted without a migration; ``fsp=6`` renders
    MySQL ``DATETIME(6)``.

    Reach for ``fsp=6`` on a **new** column that serves as a natural key,
    uniqueness component, or equality predicate, unless its values are known to
    be coarse — plain DATETIME truncates to whole seconds on MySQL while SQLite
    keeps microseconds, and that dialect skew caused the migration-015 bug.

    Converting an **existing** column is different: adding ``fsp`` there is a
    DDL change and needs its own migration, so it is a deliberate act rather
    than part of adopting this type. The columns converted in #520 are on plain
    ``TZDateTime()`` even where they participate in a ``UniqueConstraint``,
    because they hold METAR observation times and NWP cycle times — whole
    minutes and whole hours, with no sub-second component to truncate.
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
