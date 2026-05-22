"""Queries for the climatology / patterns views (issue #155).

Read paths for the 7 new frontend views built on top of the airport
observation climatology tables. All views share the same MTD-vs-completed
month dispatch:

- **Completed month** (month < current UTC month): read directly from
  ``airport_monthly_summary`` (one row per airport).
- **Month-to-date** (month == current UTC month): SUM rows from
  ``airport_daily_summary`` for the dates in that month so far.

Percentile fields are not derivable from daily sums; views that consume
them (climatology card, etc.) must hide those columns when ``is_mtd`` is
True. The window resolver surfaces that flag explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    AirportDailySummaryRow,
    AirportMonthlySummaryRow,
)
from weatherbrief.tasks.map_queries import _get_coords


# ---------------------------------------------------------------------------
# Month window resolution (shared by all month-backed views)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthWindow:
    """Resolved month, with the dispatch decision baked in.

    ``month`` is always the first UTC day of the requested calendar month.
    ``is_mtd`` is True when that month == current UTC month, meaning the
    consumer should SUM ``airport_daily_summary`` rows whose ``date`` falls
    in [month, as_of_date]. ``as_of_date`` is the last completed UTC day
    included in the MTD aggregation (yesterday-UTC); None for completed
    months.
    """

    month: date
    is_mtd: bool
    as_of_date: date | None


def resolve_month_window(
    month_str: str,
    now_utc: datetime | None = None,
) -> MonthWindow:
    """Parse an ``'YYYY-MM'`` string into a MonthWindow.

    Raises ValueError for malformed input or for future months (which have
    no data on either table). Past months are accepted unconditionally —
    queries return an empty list if no rows exist.
    """
    now = now_utc or datetime.now(timezone.utc)
    current_month_start = date(now.year, now.month, 1)

    try:
        year_s, month_s = month_str.split("-", 1)
        year, month = int(year_s), int(month_s)
        month_start = date(year, month, 1)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"month must be 'YYYY-MM', got {month_str!r}") from exc

    if month_start > current_month_start:
        raise ValueError(f"month {month_str} is in the future")

    if month_start == current_month_start:
        # MTD: aggregate completed UTC days only (yesterday-UTC and earlier).
        today_utc = now.date()
        as_of: date | None = today_utc - timedelta(days=1)
        if as_of < month_start:
            # First UTC day of the month — no completed days yet.
            as_of = None
        return MonthWindow(month=month_start, is_mtd=True, as_of_date=as_of)

    return MonthWindow(month=month_start, is_mtd=False, as_of_date=None)


# ---------------------------------------------------------------------------
# View #1 — Climatology map (VFR/MVFR/IFR/LIFR % per airport)
# ---------------------------------------------------------------------------


# Fields read for the category map. Both daily and monthly summaries have
# all five; daily rows are SUM-ed for MTD, monthly rows are read as-is.
_CATEGORY_FIELDS = ("n_obs", "n_vfr", "n_mvfr", "n_ifr", "n_lifr")

# AUTO stations that never had a ceilometer/visibility sensor (e.g. LGPA)
# report ``////`` slashes, so the parser stores the obs but leaves
# ``flight_category`` NULL. Such rows count toward ``n_obs`` but not toward
# any of the four category counts. When the NULL share exceeds this
# threshold, the four category percentages (computed over the categorizable
# subset) rest on a small, potentially biased sample — flag the airport
# low-confidence so the map can grey-tint it and the leaderboard can omit
# it. See issue #173.
_LOW_CONFIDENCE_NULL_SHARE_PCT = 25.0


def get_category_climatology(
    db: Session,
    window: MonthWindow,
    airports_db_path: str,
) -> dict[str, Any]:
    """Return per-airport flight-category counts and percentages.

    The four ``pct_*`` values are computed over the *categorizable* subset
    (``n_categorized = n_vfr + n_mvfr + n_ifr + n_lifr``), so they always sum
    to ~100% even when an airport's METARs frequently omit visibility/cloud
    (sensor-not-available AUTO stations leave ``flight_category`` NULL). The
    data-completeness gap is surfaced separately via ``n_no_category`` /
    ``pct_no_category`` (measured against raw ``n_obs``), and ``low_confidence``
    is set when that gap exceeds ``_LOW_CONFIDENCE_NULL_SHARE_PCT``.

    Response shape (one entry per watchlist airport with a coord; airports
    with no rows still plot with zero counts so the frontend can render them
    grey using the watchlist as the source of truth):

        {
          "month": "2026-05",
          "is_mtd": True,
          "as_of_date": "2026-05-18",  # only when is_mtd=True
          "airports": [
            {"icao": "EGKK", "lat": 51.1, "lon": 0.2,
             "n_obs": 421, "n_vfr": 320, "n_mvfr": 60, "n_ifr": 30, "n_lifr": 11,
             "n_categorized": 421, "n_no_category": 0, "pct_no_category": 0.0,
             "low_confidence": False,
             "pct_vfr": 76.0, "pct_mvfr": 14.3, "pct_ifr": 7.1, "pct_lifr": 2.6},
            ...
          ]
        }

    ``pct_*`` keys are absent when ``n_categorized == 0`` (an airport whose
    obs are all uncategorizable) — those plot grey, same as no-data airports.
    """
    rows = _read_counts_by_icao(db, window, _CATEGORY_FIELDS)
    coords = _get_coords(airports_db_path)

    # Enumerate the full watchlist so airports without data still plot
    # (rendered grey by the frontend). Issue #155 DoD: "all 830 airports
    # plot correctly; airports with no data are styled distinctly".
    airports: list[dict[str, Any]] = []
    for icao in sorted(coords):
        lat, lon = coords[icao]
        counts = rows.get(icao)
        entry: dict[str, Any] = {"icao": icao, "lat": lat, "lon": lon}
        if counts is None:
            entry.update({f: 0 for f in _CATEGORY_FIELDS})
            entry.update({
                "n_categorized": 0, "n_no_category": 0,
                "pct_no_category": 0.0, "low_confidence": False,
            })
        else:
            entry.update(counts)
            n_obs = counts["n_obs"] or 0
            n_categorized = (
                counts["n_vfr"] + counts["n_mvfr"]
                + counts["n_ifr"] + counts["n_lifr"]
            )
            n_no_category = max(n_obs - n_categorized, 0)
            pct_no_category = round(100.0 * n_no_category / n_obs, 1) if n_obs > 0 else 0.0
            entry["n_categorized"] = n_categorized
            entry["n_no_category"] = n_no_category
            entry["pct_no_category"] = pct_no_category
            entry["low_confidence"] = (
                n_obs > 0 and pct_no_category > _LOW_CONFIDENCE_NULL_SHARE_PCT
            )
            if n_categorized > 0:
                entry["pct_vfr"] = round(100.0 * counts["n_vfr"] / n_categorized, 1)
                entry["pct_mvfr"] = round(100.0 * counts["n_mvfr"] / n_categorized, 1)
                entry["pct_ifr"] = round(100.0 * counts["n_ifr"] / n_categorized, 1)
                entry["pct_lifr"] = round(100.0 * counts["n_lifr"] / n_categorized, 1)
        airports.append(entry)

    response: dict[str, Any] = {
        "month": window.month.strftime("%Y-%m"),
        "is_mtd": window.is_mtd,
        "airports": airports,
    }
    if window.is_mtd and window.as_of_date is not None:
        response["as_of_date"] = window.as_of_date.isoformat()
    return response


# ---------------------------------------------------------------------------
# Shared read helper (count fields only — percentile fields go through a
# separate path because they can't be SUM-ed across daily rows)
# ---------------------------------------------------------------------------


def _read_counts_by_icao(
    db: Session,
    window: MonthWindow,
    fields: tuple[str, ...],
) -> dict[str, dict[str, int]]:
    """Return ``{icao: {field: count, ...}}`` for the resolved window.

    For completed months reads ``airport_monthly_summary`` directly.
    For MTD sums ``airport_daily_summary`` rows in the window. Both
    paths return identical key shapes; the caller stays agnostic.
    """
    if window.is_mtd:
        if window.as_of_date is None:
            return {}
        cols = [
            AirportDailySummaryRow.icao,
            *[func.sum(getattr(AirportDailySummaryRow, f)).label(f) for f in fields],
        ]
        stmt = (
            select(*cols)
            .where(AirportDailySummaryRow.date >= window.month)
            .where(AirportDailySummaryRow.date <= window.as_of_date)
            .group_by(AirportDailySummaryRow.icao)
        )
    else:
        cols = [
            AirportMonthlySummaryRow.icao,
            *[getattr(AirportMonthlySummaryRow, f).label(f) for f in fields],
        ]
        # month is stored as a DATETIME at midnight UTC; compare on the date.
        stmt = select(*cols).where(
            func.date(AirportMonthlySummaryRow.month) == window.month
        )

    out: dict[str, dict[str, int]] = {}
    for row in db.execute(stmt).all():
        icao = row[0]
        out[icao] = {field: int(getattr(row, field) or 0) for field in fields}
    return out


# ---------------------------------------------------------------------------
# View #2 — Phenomena map (TS / fog)
# ---------------------------------------------------------------------------

_PHENOMENA_FIELDS = ("n_obs", "n_ts", "n_fg")


def get_phenomena_climatology(
    db: Session,
    window: MonthWindow,
    airports_db_path: str,
    kind: str,
) -> dict[str, Any]:
    """Per-airport TS or fog frequency (``n_<kind> / n_obs``).

    ``kind``: ``'ts'`` or ``'fog'``. The response shape is metric-agnostic
    so the frontend can render all four datasets through one pipeline:

        {
          "month": "2026-04", "is_mtd": False,
          "dataset": "phenomena", "metric": "ts", "unit": "%",
          "airports": [
            {"icao": "EGKK", "lat": ..., "lon": ...,
             "value": 2.3,          # color driver (% TS)
             "n_obs": 720, "n_ts": 17, "n_fg": 4},
            ...
          ]
        }

    ``value`` is the metric being color-coded; the extra count fields are
    included for the popup. Airports with ``n_obs == 0`` get ``value = null``.
    """
    if kind not in ("ts", "fog"):
        raise ValueError("kind must be 'ts' or 'fog'")
    field = "n_ts" if kind == "ts" else "n_fg"

    rows = _read_counts_by_icao(db, window, _PHENOMENA_FIELDS)
    coords = _get_coords(airports_db_path)

    airports: list[dict[str, Any]] = []
    for icao in sorted(coords):
        lat, lon = coords[icao]
        entry: dict[str, Any] = {
            "icao": icao, "lat": lat, "lon": lon,
            "n_obs": 0, "n_ts": 0, "n_fg": 0, "value": None,
        }
        counts = rows.get(icao)
        if counts is not None:
            entry.update(counts)
            n_obs = counts["n_obs"] or 0
            if n_obs > 0:
                entry["value"] = round(100.0 * counts[field] / n_obs, 2)
        airports.append(entry)

    return _envelope(window, dataset="phenomena", metric=kind, unit="%",
                     airports=airports)


# ---------------------------------------------------------------------------
# View #3 — High-wind frequency map
# ---------------------------------------------------------------------------

WIND_METRICS = ("over25", "p95", "gust")
# Metrics available in MTD mode (daily summary doesn't carry the others).
WIND_METRICS_MTD = ("gust",)


def get_wind_climatology(
    db: Session,
    window: MonthWindow,
    airports_db_path: str,
    metric: str,
) -> dict[str, Any]:
    """Per-airport wind metric.

    Metrics:
      - ``over25``: ``n_wind_over_25kt / n_obs`` (%). Completed months only —
        daily summary doesn't carry the per-row hour count, so MTD cannot
        reconstruct it without joining raw observations.
      - ``p95``: wind_p95_kt (kt). Completed months only — percentiles
        can't be SUM-ed across daily rows.
      - ``gust``: gust_max_kt (kt). Both modes; MTD uses ``MAX(gust_max_kt)``
        across daily rows.

    400 if the metric is requested in MTD when unavailable.
    """
    if metric not in WIND_METRICS:
        raise ValueError(f"metric must be one of {WIND_METRICS}")
    if window.is_mtd and metric not in WIND_METRICS_MTD:
        raise ValueError(
            f"metric '{metric}' is not available month-to-date "
            f"(available MTD: {WIND_METRICS_MTD})"
        )

    coords = _get_coords(airports_db_path)
    if window.is_mtd:
        # MTD: only 'gust' lands here. Read MAX(gust_max_kt) per airport.
        if window.as_of_date is None:
            values: dict[str, int | None] = {}
        else:
            stmt = (
                select(
                    AirportDailySummaryRow.icao,
                    func.max(AirportDailySummaryRow.gust_max_kt).label("gust"),
                )
                .where(AirportDailySummaryRow.date >= window.month)
                .where(AirportDailySummaryRow.date <= window.as_of_date)
                .group_by(AirportDailySummaryRow.icao)
            )
            values = {row[0]: row[1] for row in db.execute(stmt).all()}
    else:
        # Completed month: straight read from monthly summary.
        select_col = {
            "over25": AirportMonthlySummaryRow.n_wind_over_25kt,
            "p95":    AirportMonthlySummaryRow.wind_p95_kt,
            "gust":   AirportMonthlySummaryRow.gust_max_kt,
        }[metric]
        # We also fetch n_obs so we can compute n_wind_over_25kt as a percentage.
        stmt = select(
            AirportMonthlySummaryRow.icao,
            select_col.label("raw"),
            AirportMonthlySummaryRow.n_obs.label("n_obs"),
        ).where(func.date(AirportMonthlySummaryRow.month) == window.month)
        rows = {r[0]: (r[1], r[2]) for r in db.execute(stmt).all()}
        values = {icao: tup[0] for icao, tup in rows.items()}
        n_obs_by_icao = {icao: tup[1] for icao, tup in rows.items()}

    # MTD path didn't collect n_obs yet — pull it now so the leaderboard
    # can apply its min-obs gate.
    if window.is_mtd and window.as_of_date is not None:
        n_stmt = (
            select(
                AirportDailySummaryRow.icao,
                func.sum(AirportDailySummaryRow.n_obs).label("n_obs"),
            )
            .where(AirportDailySummaryRow.date >= window.month)
            .where(AirportDailySummaryRow.date <= window.as_of_date)
            .group_by(AirportDailySummaryRow.icao)
        )
        n_obs_by_icao = {r[0]: r[1] for r in db.execute(n_stmt).all()}
    elif window.is_mtd:
        n_obs_by_icao = {}

    airports: list[dict[str, Any]] = []
    unit = "%" if metric == "over25" else "kt"
    for icao in sorted(coords):
        lat, lon = coords[icao]
        raw = values.get(icao)
        n_obs = n_obs_by_icao.get(icao, 0) or 0
        if raw is None:
            value: float | None = None
        elif metric == "over25":
            value = round(100.0 * raw / n_obs, 2) if n_obs > 0 else None
        else:
            value = round(float(raw), 1)
        airports.append({
            "icao": icao, "lat": lat, "lon": lon,
            "value": value, "raw": raw, "n_obs": n_obs,
        })

    return _envelope(window, dataset="wind", metric=metric, unit=unit,
                     airports=airports)


# ---------------------------------------------------------------------------
# View #4 — Volatility map + leaderboard
# ---------------------------------------------------------------------------

_VOLATILITY_FIELDS = ("n_obs", "n_category_changes")
# Monthly summary stores the SUM of within-day transitions as `category_changes`.
_VOLATILITY_FIELDS_MONTHLY = ("n_obs", "category_changes")

# Minimum observation count an airport needs before it can rank on the
# leaderboard — keeps sparse-data outliers (a handful of obs with one
# flip) from dominating the top of the list.
_LEADERBOARD_MIN_OBS_COMPLETED = 100
_LEADERBOARD_MIN_OBS_MTD = 50


def get_volatility_climatology(
    db: Session,
    window: MonthWindow,
    airports_db_path: str,
) -> dict[str, Any]:
    """Per-airport ``category_changes / n_obs`` (transitions per observation).

    Stored as ``n_category_changes`` on daily and ``category_changes`` on
    monthly — the column rename is hidden inside this function.
    """
    if window.is_mtd:
        rows = _read_counts_by_icao(db, window, _VOLATILITY_FIELDS)
        changes_field = "n_category_changes"
    else:
        rows = _read_counts_by_icao(db, window, _VOLATILITY_FIELDS_MONTHLY)
        changes_field = "category_changes"

    coords = _get_coords(airports_db_path)
    airports: list[dict[str, Any]] = []
    for icao in sorted(coords):
        lat, lon = coords[icao]
        entry: dict[str, Any] = {
            "icao": icao, "lat": lat, "lon": lon,
            "n_obs": 0, "n_changes": 0, "value": None,
        }
        counts = rows.get(icao)
        if counts is not None:
            n_obs = counts["n_obs"] or 0
            n_changes = counts[changes_field] or 0
            entry["n_obs"] = n_obs
            entry["n_changes"] = n_changes
            if n_obs > 0:
                entry["value"] = round(n_changes / n_obs, 3)
        airports.append(entry)

    return _envelope(window, dataset="volatility", metric="changes_per_obs",
                     unit="ratio", airports=airports)


def get_leaderboard(
    db: Session,
    window: MonthWindow,
    airports_db_path: str,
    dataset: str,
    sub: str | None,
    limit: int = 20,
) -> dict[str, Any]:
    """Top ``limit`` airports for any (dataset, sub) combination.

    Reuses the same per-airport computations as the map endpoints, then
    applies a min-obs gate and sorts by value descending. Min-obs uses
    the same defaults as the original volatility leaderboard:
    ``_LEADERBOARD_MIN_OBS_COMPLETED`` for completed months and
    ``_LEADERBOARD_MIN_OBS_MTD`` for MTD windows.

    Sort direction is always ``DESC`` — for VFR% the leaderboard reads as
    "most consistently VFR"; for unfavorable metrics (IFR/MVFR/LIFR/TS/Fog/
    wind/volatility) it reads as "most extreme". One consistent direction
    avoids an off-by-one UX trap.
    """
    min_obs = _LEADERBOARD_MIN_OBS_MTD if window.is_mtd else _LEADERBOARD_MIN_OBS_COMPLETED

    # Dispatch: get the per-airport map response, then collapse into
    # leaderboard rows (icao, value, n_obs, …) ranked by value desc.
    if dataset == "category":
        if sub not in ("vfr", "mvfr", "ifr", "lifr"):
            raise ValueError(f"category sub must be vfr/mvfr/ifr/lifr, got {sub!r}")
        data = get_category_climatology(db, window, airports_db_path)
        value_key = f"pct_{sub}"
        # Gate on the categorizable obs count (the sample the % is computed
        # over), not raw n_obs, and omit low-confidence airports (high NULL
        # share) so sensor-starved AUTO stations like LGPA can't top the
        # ranking on a small biased sample. See issue #173.
        rows = [
            {"icao": a["icao"], "value": a[value_key],
             "n_obs": a["n_categorized"],
             "extra": {"n_vfr": a["n_vfr"], "n_mvfr": a["n_mvfr"],
                       "n_ifr": a["n_ifr"], "n_lifr": a["n_lifr"],
                       "n_no_category": a["n_no_category"]}}
            for a in data["airports"]
            if value_key in a
            and not a["low_confidence"]
            and a["n_categorized"] >= min_obs
        ]
        unit = "%"
        label = f"{sub.upper()} %"
    elif dataset == "phenomena":
        if sub not in ("ts", "fog"):
            raise ValueError(f"phenomena sub must be 'ts' or 'fog', got {sub!r}")
        data = get_phenomena_climatology(db, window, airports_db_path, sub)
        key = "n_ts" if sub == "ts" else "n_fg"
        rows = [
            {"icao": a["icao"], "value": a["value"],
             "n_obs": a["n_obs"], "extra": {key: a[key]}}
            for a in data["airports"]
            if a["value"] is not None and a["n_obs"] >= min_obs
        ]
        unit = "%"
        label = "TS %" if sub == "ts" else "Fog %"
    elif dataset == "wind":
        if sub not in WIND_METRICS:
            raise ValueError(f"wind sub must be one of {WIND_METRICS}, got {sub!r}")
        data = get_wind_climatology(db, window, airports_db_path, sub)
        unit = "%" if sub == "over25" else "kt"
        rows = [
            {"icao": a["icao"], "value": a["value"],
             "n_obs": a.get("n_obs", 0), "extra": {"raw": a.get("raw")}}
            for a in data["airports"]
            if a["value"] is not None and a.get("n_obs", 0) >= min_obs
        ]
        label = {"over25": "% hrs > 25 kt", "p95": "Wind p95 (kt)",
                 "gust": "Peak gust (kt)"}[sub]
    elif dataset == "volatility":
        data = get_volatility_climatology(db, window, airports_db_path)
        rows = [
            {"icao": a["icao"], "value": a["value"],
             "n_obs": a["n_obs"], "extra": {"n_changes": a["n_changes"]}}
            for a in data["airports"]
            if a["value"] is not None and a["n_obs"] >= min_obs
        ]
        unit = "ratio"
        label = "Changes / obs"
    else:
        raise ValueError(f"unknown dataset {dataset!r}")

    rows.sort(key=lambda r: (-(r["value"] or 0), r["icao"]))
    rows = rows[:limit]

    return {
        "month": window.month.strftime("%Y-%m"),
        "is_mtd": window.is_mtd,
        **({"as_of_date": window.as_of_date.isoformat()}
           if window.is_mtd and window.as_of_date else {}),
        "dataset": dataset,
        "sub": sub,
        "unit": unit,
        "label": label,
        "min_n_obs": min_obs,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Shared response envelope
# ---------------------------------------------------------------------------


def _envelope(
    window: MonthWindow,
    *,
    dataset: str,
    metric: str,
    unit: str,
    airports: list[dict[str, Any]],
) -> dict[str, Any]:
    """Common map-response wrapper used by views #2, #3, #4."""
    response: dict[str, Any] = {
        "month": window.month.strftime("%Y-%m"),
        "is_mtd": window.is_mtd,
        "dataset": dataset,
        "metric": metric,
        "unit": unit,
        "airports": airports,
    }
    if window.is_mtd and window.as_of_date is not None:
        response["as_of_date"] = window.as_of_date.isoformat()
    return response
