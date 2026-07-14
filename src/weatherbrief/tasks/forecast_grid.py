"""Forecast-map horizon policy — how far out we sample, and at which hours.

Single source of truth for the (day, hour, model) grid the forecast map is
built on. The cycle that writes snapshots, the cache builder, and the API that
serves them all read from here so the three cannot drift apart.

The horizon is set by **ECMWF**, not by the model that reaches furthest. GFS
runs to 384 h, but a map drawn from GFS alone is a single model with no
cross-check — the point of the map is the spread between models, so the limit
is the last day on which at least two of them can still speak.

Delivered ECMWF steps (00Z/12Z runs) are hourly to 90 h, 3-hourly to 144 h,
then 6-hourly at 150/156/162/168, and stop dead at 168 h. With
``step = 24 * day + hour``, that puts the walls almost exactly on day
boundaries:

===== =============== ===== ====== =========================
Day   Steps (00Z run) GFS   ICON   ECMWF
===== =============== ===== ====== =========================
0-3   6 - 90          yes   yes    yes (hourly)
4     102 - 114       yes   yes    yes (3-hourly)
5     126 - 138       yes   no     yes (3-hourly)
6     150 - 162       yes   no     only 06/12/18Z land
7     174+            yes   no     past the 168 h wall
===== =============== ===== ====== =========================

ICON-EU's cloud-diag GRIB stops at 120 h. D+4's last sample is 114 h and D+5's
first is 126 h, so ICON falls away on a clean day boundary rather than leaving
a ragged half-day — which is why D+5 and D+6 are simply two-model days.

Past 144 h ECMWF is 6-hourly, and 06/12/18Z land exactly on those steps while
09/15Z never do. So D+6 uses a coarser three-slot grid; sampling it at five
slots would leave two of them permanently ECMWF-less.
"""

from __future__ import annotations

# ECMWF operational delivery (00Z/12Z runs). These two numbers are the facts;
# everything below is derived from them, so that a change to the contract moves
# the grid rather than silently leaving it sampling steps nobody delivers.
ECMWF_MAX_STEP_H = 168        # hard wall — nothing beyond this exists
ECMWF_3H_MAX_STEP_H = 144     # hourly/3-hourly up to here, 6-hourly past it

# Sample hours for a normal day, and for days past ECMWF's 3-hourly region.
# The coarse set is exactly the hours that land on 6-hourly steps: from a 00Z or
# 12Z init, 06/12/18Z are multiples of 6 and 09/15Z never are.
FINE_SAMPLE_HOURS = (6, 9, 12, 15, 18)
COARSE_SAMPLE_HOURS = (6, 12, 18)


def _first_coarse_day() -> int:
    """First day whose fine-grid hours would reach past the 3-hourly region."""
    day = 0
    while 24 * day + max(FINE_SAMPLE_HOURS) <= ECMWF_3H_MAX_STEP_H:
        day += 1
    return day


def _last_offered_day() -> int:
    """Last day with at least one delivered sample step (its earliest hour)."""
    day = 0
    while 24 * (day + 1) + min(FINE_SAMPLE_HOURS) <= ECMWF_MAX_STEP_H:
        day += 1
    return day


# Both happen to be 6 today, but for different reasons — one is the 168 h wall,
# the other is where the cadence thins to 6-hourly. Deriving them separately
# keeps that a fact rather than a coincidence waiting to be mis-copied.
COARSE_FROM_DAY = _first_coarse_day()
MAX_FORECAST_DAY = _last_offered_day()

# How far each model is fetched for the map. ICON stops at 4 because its
# cloud-diag GRIB horizon is 120 h; fetching it further would store rows with
# no ceiling, which reads as agreement on the map when it is really absence.
# Kept distinct from ``standalone_verification.MODEL_FORECAST_DAYS``, which is
# the per-flight alternates horizon and has no reason to move with the map's.
MAP_FORECAST_DAYS = {
    "gfs": 6,
    "icon": 4,
    "ecmwf": 6,
}


def sample_hours_for_day(day: int) -> tuple[int, ...]:
    """UTC sample hours offered for relative day ``day`` (0 = today)."""
    return COARSE_SAMPLE_HOURS if day >= COARSE_FROM_DAY else FINE_SAMPLE_HOURS


def forecast_days() -> tuple[int, ...]:
    """Relative days the map covers, D+0 .. MAX_FORECAST_DAY inclusive."""
    return tuple(range(MAX_FORECAST_DAY + 1))


def all_sample_hours() -> tuple[int, ...]:
    """Every hour that can appear on any day — the superset.

    The per-day grid is a subset of this, so it is the correct parse-time
    filter to hand Open-Meteo: it bounds how much of the response is
    materialised (#236) without pre-empting the per-day refinement.
    """
    return FINE_SAMPLE_HOURS


def day_hour_pairs() -> list[tuple[int, int]]:
    """Every (day, hour) slot the map can hold, in display order."""
    return [(d, h) for d in forecast_days() for h in sample_hours_for_day(d)]
