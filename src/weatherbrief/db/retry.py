"""Deadlock / lock-wait retry helper for hot write paths.

With several writer roles hitting MySQL concurrently (web endpoints, the
scheduler refresh loop, boot resume, METAR ingest, verification, retention
DDL, analytics rollup), InnoDB transiently fails writes with:

- errno 1213 ("Deadlock found ...") — this transaction was chosen as the
  deadlock victim and rolled back by the server;
- errno 1205 ("Lock wait timeout exceeded ...") — gave up waiting for a lock
  after ``innodb_lock_wait_timeout``.

Both are safe to retry from the start of the failed transaction. This helper
wraps the smallest critical section of a write path::

    for attempt in db_retry(session):
        with attempt:
            ...  # the statements that may deadlock

Each loop iteration is one attempt. A retryable ``OperationalError`` rolls
the session back, sleeps a jittered exponential backoff, and re-enters the
block; the error propagates unchanged after ``max_attempts``, or immediately
when it isn't a lock conflict. Non-``OperationalError`` exceptions
(``IntegrityError`` and friends) are never retried. On SQLite — or any run
without an injected lock error — the loop is a pass-through: one iteration,
no rollback, no sleep.

The loop body must be exactly the ``with attempt:`` block: a suppressed
failure re-enters the loop, so any statement after the ``with`` would run
between attempts.

Why an attempt-guard iterator rather than a plain context manager or
decorator: a ``with`` block cannot re-run its own body, and a decorator
would have to fish the session out of arbitrary call signatures. This is
the shape tenacity uses (``for attempt in Retrying(): with attempt:``) —
the session stays explicit and the retried block stays inline.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from types import TracebackType

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: InnoDB error numbers worth retrying: deadlock victim, lock wait timeout.
RETRYABLE_ERRNOS = frozenset((1213, 1205))

#: Message fragments matched as a fallback for DBAPIs that don't expose the
#: server errno as ``orig.args[0]`` the way PyMySQL does.
_RETRYABLE_FRAGMENTS = ("Deadlock found", "Lock wait timeout")


def is_retryable_lock_error(exc: OperationalError) -> bool:
    """True when ``exc`` wraps an InnoDB deadlock (1213) / lock timeout (1205)."""
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ())
    if args and args[0] in RETRYABLE_ERRNOS:
        return True
    text = f"{exc}{orig}"
    return any(fragment in text for fragment in _RETRYABLE_FRAGMENTS)


def db_retry(
    session: Session, max_attempts: int = 3, base_delay_s: float = 0.05
) -> Iterator["_AttemptGuard"]:
    """Yield one attempt guard per try at a deadlock-prone critical section.

    See the module docstring for the usage pattern. ``max_attempts`` counts
    the initial try, so the default allows two retries; ``base_delay_s`` is
    the backoff unit — the sleep before attempt N is
    ``base × 2^(N-2) × (0.5 + random())``.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    return _RetryLoop(session, max_attempts, base_delay_s)


class _RetryLoop:
    """Iterator state for one ``db_retry`` loop."""

    def __init__(self, session: Session, max_attempts: int, base_delay_s: float):
        self._session = session
        self._max_attempts = max_attempts
        self._base_delay_s = base_delay_s
        self._attempt = 0
        self._succeeded = False

    def __iter__(self) -> "_RetryLoop":
        return self

    def __next__(self) -> "_AttemptGuard":
        # The body completed cleanly — the loop is done. (Reached only after
        # success: failures either propagate out of the guard or are
        # suppressed with attempts remaining, see _retry_or_raise.)
        if self._succeeded:
            raise StopIteration
        self._attempt += 1
        return _AttemptGuard(self)

    def _retry_or_raise(self, exc: OperationalError) -> bool:
        """Decide a failed attempt's fate. True suppresses the error so the
        ``for`` loop takes another iteration; False lets it propagate."""
        if not is_retryable_lock_error(exc) or self._attempt >= self._max_attempts:
            return False
        # After a 1213 the server has already rolled the transaction back;
        # after a 1205 only the statement failed. Either way the session must
        # be reset to a clean transaction before the next attempt.
        self._session.rollback()
        delay = (
            self._base_delay_s
            * (2 ** (self._attempt - 1))
            * (0.5 + random.random())
        )
        logger.warning(
            "InnoDB lock conflict on write (attempt %d/%d) — retrying in %.3fs: %s",
            self._attempt,
            self._max_attempts,
            delay,
            exc,
        )
        time.sleep(delay)
        return True


class _AttemptGuard:
    """Context manager around one attempt's critical section."""

    def __init__(self, loop: _RetryLoop):
        self._loop = loop

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc_type is None:
            self._loop._succeeded = True
            return False
        if isinstance(exc, OperationalError):
            return self._loop._retry_or_raise(exc)
        return False
