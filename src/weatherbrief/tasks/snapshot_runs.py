"""Which forecast-snapshot rows make up a model's newest map cycle (#936).

A map cycle used to write one ``model_init_time`` per model, so readers kept
the rows of the newest init. ICON no longer does: rows inside the ICON-D2
domain carry the D2 run's own init, usually 3 h newer than the ICON-EU init
of the rest of the cycle (``standalone_verification.icon_d2_run``). Readers
that pick "the newest run" therefore keep, per model, every run within the
model's cycle spread of the newest one, and per airport-hour the freshest of
those. Models with one init per cycle keep exactly the newest init, as before.

The spread is shorter than the gap between two map cycles (07Z and 19Z, so
ICON-EU inits 12 h apart): an airport's row from the previous cycle can only
come through where the newest cycle has no row for it, and never shadows a
newer one.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TypeVar

# Per model, how far before its newest run a run of the same cycle can be.
# Matches standalone_verification._D2_RUN_MAX_LEAD (a D2 run is chosen
# strictly less than 12 h after the ICON-EU init).
CYCLE_RUN_SPREAD: dict[str, timedelta] = {"icon": timedelta(hours=12)}

T = TypeVar("T")


def cycle_floor(model: str, newest: datetime) -> datetime | None:
    """Runs of *model* strictly after this belong to *newest*'s cycle.

    None for a model with one init per cycle: only *newest* itself does.
    """
    spread = CYCLE_RUN_SPREAD.get(model)
    return newest - spread if spread is not None else None


def in_newest_cycle(model: str, init: datetime, newest: datetime) -> bool:
    """True when a run of *model* initialised at *init* is in *newest*'s cycle."""
    floor = cycle_floor(model, newest)
    if floor is None:
        return init == newest
    return floor < init <= newest


def newest_cycle_rows(
    rows: Iterable[T], newest: dict[str, datetime] | None = None,
) -> list[T]:
    """Per ``(model, icao, forecast_hour)``, the freshest row of the newest cycle.

    *rows* are snapshot dicts or ORM rows. *newest* is each model's newest
    init; computed from *rows* when not given. Row order is kept.
    """
    get = _get
    rows = list(rows)
    if newest is None:
        newest = {}
        for r in rows:
            m, init = get(r, "model"), get(r, "model_init_time")
            if m not in newest or init > newest[m]:
                newest[m] = init
    best: dict[tuple, T] = {}
    for r in rows:
        m, init = get(r, "model"), get(r, "model_init_time")
        if m not in newest or not in_newest_cycle(m, init, newest[m]):
            continue
        key = (m, get(r, "icao"), get(r, "forecast_hour"))
        kept = best.get(key)
        if kept is None or init > get(kept, "model_init_time"):
            best[key] = r
    chosen = {id(r) for r in best.values()}
    return [r for r in rows if id(r) in chosen]


def _get(row, column: str):
    return row[column] if isinstance(row, dict) else getattr(row, column)
