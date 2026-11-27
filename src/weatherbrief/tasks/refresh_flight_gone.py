"""Stop a briefing refresh whose flight was moved or deleted while it ran.

``POST /flights/{id}/move`` re-keys a flight (date, route, altitude, ceiling
and duration are all encoded in its ID): it deletes the old row and inserts a
new one. A refresh already running on the old ID keeps going, and before this
module it ran to the end — GRIB fetch, analysis, GRAMET, a paid LLM digest —
only to fail its ``briefing_packs`` insert on the foreign key, record a
``failed`` job, and leave its pack directory on disk where nothing would ever
delete it (retention walks DB rows; this directory never had one). The client
starts a fresh refresh on the new ID right after the move, so the old run was
pure waste. Deleting a flight mid-refresh is the same race.

Two pieces:

* :func:`check_flight_at_stage` — a cheap primary-key lookup at a few stage
  boundaries ahead of expensive work; raises :class:`FlightGoneError` so the
  pipeline stops there.
* :func:`discard_refresh_for_gone_flight` — called from a refresh's failure
  handler; if the flight is gone (whether a gate caught it or the final insert
  hit the foreign key) it removes the run's own pack directory and tells the
  caller to record ``abandoned`` instead of ``failed``.

Why a gate cannot abandon a refresh that would have succeeded
-------------------------------------------------------------
The only condition that abandons is "the flight's row is confirmed absent in
committed state". That is precisely the condition under which the refresh's
final ``briefing_packs`` insert fails its foreign key, so the gate changes when
a doomed refresh stops, not whether it succeeds. Each way the lookup could be
wrong is closed off:

* **DB error / timeout** — treated as "still exists" (fail open). A gate never
  abandons on doubt; the refresh carries on exactly as before.
* **Row not committed yet** — every refresh path loads the flight row before
  it registers, creating endpoints commit before responding
  (``get_db(scope="function")``), and the first gate fires after the GRIB
  fetch, tens of seconds in.
* **Move/delete still in its transaction** — the lookup only sees committed
  state, so the flight still "exists" and the refresh continues; if the move
  then commits, the failure handler classifies it afterwards.
* **Row re-created under the same ID** (move A→B→A, or delete then re-create
  the identical flight, between a gate and the finalize) — the one window in
  which the old behaviour would have finished and attached the pack to the new
  row. Abandoning loses nothing: the move/create that re-created the row is
  followed by the client's own refresh on it, as for any move.

The directory removal is equally narrow: only this run's own timestamped pack
directory (guarded to sit under the flight's directory), only when the flight
is gone — so no ``briefing_packs`` row can reference it — and the flight
directory itself only when that leaves it empty.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from flyfun_common.db import SessionLocal

from weatherbrief.db.models import FlightRow

logger = logging.getLogger(__name__)

#: Stage boundaries that check the flight still exists. Each is emitted at the
#: top level of the pipeline (not inside a ``try`` that would swallow the
#: raise, and not inside the GRIB decode code), in front of real cost:
#: ``route_analysis`` follows the fetch (analysis, advisories, alternates still
#: to come), ``fetch_gramet`` precedes the external GRAMET call, and
#: ``briefing_ready`` comes just before the provisional pack insert and the LLM
#: digest.
GATE_STAGES = frozenset({"route_analysis", "fetch_gramet", "briefing_ready"})

ABANDON_REASON = "flight moved or deleted during the refresh"


class FlightGoneError(RuntimeError):
    """The flight a refresh was building for no longer exists."""

    def __init__(self, flight_id: str, stage: str | None = None) -> None:
        where = f" (at {stage})" if stage else ""
        super().__init__(f"{ABANDON_REASON}{where}: {flight_id}")
        self.flight_id = flight_id
        self.stage = stage


def flight_confirmed_gone(flight_id: str) -> bool:
    """True only when the flight's row is positively absent.

    Uses its own short-lived session so it reads committed state and never
    touches a caller's transaction. Any error answers False — "can't tell" must
    never abandon a refresh.
    """
    try:
        session = SessionLocal()
        try:
            return session.get(FlightRow, flight_id) is None
        finally:
            session.close()
    except Exception:
        logger.warning(
            "refresh: could not check whether flight %s still exists — assuming it does",
            flight_id, exc_info=True,
        )
        return False


def check_flight_at_stage(flight_id: str, stage: str) -> None:
    """Raise :class:`FlightGoneError` at a gate stage if the flight is gone."""
    if stage in GATE_STAGES and flight_confirmed_gone(flight_id):
        raise FlightGoneError(flight_id, stage)


def discard_refresh_for_gone_flight(
    flight_id: str, pack_path: Path | str | None, exc: BaseException,
) -> bool:
    """Classify a failed refresh; clean up and return True if its flight is gone.

    True means the caller should record the job ``abandoned`` (not ``failed``)
    and log at INFO: the pilot moved or deleted the flight, nothing broke.
    False leaves the failure exactly as it was.
    """
    if not isinstance(exc, FlightGoneError) and not flight_confirmed_gone(flight_id):
        return False
    _remove_pack_dir(flight_id, pack_path)
    return True


def close_failed_refresh(
    registry,
    flight_id: str,
    pack_path: Path | str | None,
    exc: BaseException,
    *,
    label: str,
    log: logging.Logger = logger,
) -> bool:
    """Record a failed refresh as ``abandoned`` (flight gone) or ``failed``.

    The one place the abandon-vs-fail split lives for the paths that own their
    registry entry (queued and streaming refreshes): marks the outcome on
    *registry* and logs at INFO or ERROR under *label* (e.g. "Streaming
    refresh"). Returns True when the refresh was abandoned.
    """
    if discard_refresh_for_gone_flight(flight_id, pack_path, exc):
        registry.mark_outcome(flight_id, "abandoned", str(exc))
        log.info("%s abandoned for %s: %s", label, flight_id, exc)
        return True
    registry.mark_outcome(flight_id, "failed", str(exc))
    log.error("%s failed for %s: %s", label, flight_id, exc, exc_info=True)
    return False


def _remove_pack_dir(flight_id: str, pack_path: Path | str | None) -> None:
    if not pack_path:
        return
    from weatherbrief.storage.flights import safe_path_component

    pack_dir = Path(pack_path)
    flight_dir = pack_dir.parent
    if flight_dir.name != safe_path_component(flight_id):
        # Not the layout pack_dir_for produces — never delete what we can't place.
        logger.warning(
            "refresh: not removing %s — not under flight %s's directory",
            pack_dir, flight_id,
        )
        return
    try:
        # No ignore_errors: a half-removed directory is the very orphan this
        # module exists to prevent, so a real failure must reach the warning.
        # Already gone is fine — /move's after-commit cleanup may beat us.
        try:
            shutil.rmtree(pack_dir)
        except FileNotFoundError:
            pass
        if flight_dir.is_dir() and not any(flight_dir.iterdir()):
            flight_dir.rmdir()
    except OSError:
        logger.warning("refresh: could not remove %s", pack_dir, exc_info=True)
