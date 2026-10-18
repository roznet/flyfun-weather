"""Score the forecast convective risk against the observed archive (#793).

Joins two Parquet archives that were never joined before:

- what the forecast said: ``archive/verification/snapshots`` (one row per
  airport, model, run and forecast hour), and
- what happened: ``archive/observed/<source>`` (radar, lightning and cloud tops
  sampled in discs around the same ~620 airports every 10 min, #575).

Offline and read-only, run with DuckDB (``pip install duckdb``, in the ``dev``
and ``analysis`` extras), never by the server. The answer it gives is a 2x2
table per model and lead day, the dataset that tunes the convective thresholds
(#791). October to March is off-season, so a useful sample needs a summer.

Definitions (all in :class:`ConvectiveScoreParams`, so a variant is one flag):

- **Forecast yes**: the ``risk_source`` verdict at or above ``risk_threshold``
  (default ``moderate``). Rows with no verdict are left out. ``risk_source``
  picks the track (:data:`RISK_SOURCES`): ``thermo`` is
  ``sounding_convective_risk``; ``nwp`` the model-native ``nwp_conv_risk``
  (108), the track the grader reads by default; ``graded`` the native verdict
  where the row has a native assessment and thermo where it has none, which is
  the fallback the grader applies (before its own grade floors).
- **Observed yes** in the hour around the forecast hour (frames valid in
  ``[hour - 30 min, hour + 30 min)``): a radar max at or above ``dbz_threshold``
  (41 dBZ, the live layer's core tier) within ``radar_radius_nm`` (10 NM) on a
  frame with adequate coverage, or any flash within ``lightning_radius_nm``
  (20 NM).
- **Known hours only**: every scored hour needs at least one adequately
  covered radar frame **and** one lightning frame, whatever it observed. An
  hour without both (radar outage, airport outside the MTG disc, archive gap)
  is ``unknown`` and dropped, so an outage never reads as a correct negative
  and an under-covered hour never enters the sample only because it stormed.
- **Lead day** is the calendar-date difference between forecast hour and run,
  the convention every verification table uses (``days_out``).

The per-pair view (:func:`pairs_sql`) also carries the ingredients for
threshold work: both verdicts and every convective and stability column of
the snapshot (:data:`PAIR_COLUMNS`), observed max dBZ, flash count and the
highest cloud top (FL, 10 NM). A column older archive files lack reads NULL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

#: ConvectiveRisk values in order; "at or above" is read off this list.
RISK_ORDER = ("none", "marginal", "low", "moderate", "high", "extreme")

#: The forecast verdict each ``risk_source`` scores. ``nwp_conv_method`` is set
#: exactly when the row has a native assessment, so ``graded`` falls back to
#: thermo only where the grader would; a pre-108 row with a native assessment
#: has no stored native verdict and drops out rather than scoring thermo.
RISK_SOURCES = {
    "thermo": "sounding_convective_risk",
    "nwp": "nwp_conv_risk",
    "graded": (
        "CASE WHEN nwp_conv_method IS NULL THEN sounding_convective_risk "
        "ELSE nwp_conv_risk END"
    ),
}

#: Snapshot columns carried onto every pair, with the DuckDB type a missing
#: one is read as (archive files written before the column existed).
PAIR_COLUMNS = {
    "sounding_convective_risk": "VARCHAR",
    "nwp_conv_risk": "VARCHAR",
    "nwp_conv_method": "VARCHAR",
    "cape_jkg": "DOUBLE",
    "nwp_cape_type": "VARCHAR",
    "nwp_cin_jkg": "DOUBLE",
    "nwp_lifted_index": "DOUBLE",
    "nwp_k_index": "DOUBLE",
    "nwp_total_totals": "DOUBLE",
    "nwp_ml_cape_jkg": "DOUBLE",
    "nwp_ml_cin_jkg": "DOUBLE",
    "nwp_conv_cover_pct": "DOUBLE",
    "nwp_conv_base_ft": "DOUBLE",
    "nwp_conv_top_ft": "DOUBLE",
    "nwp_conv_precip_mm_h": "DOUBLE",
    "sounding_cape_jkg": "DOUBLE",
    "sounding_cin_jkg": "DOUBLE",
    "sounding_lifted_index": "DOUBLE",
    "sounding_lfc_ft": "DOUBLE",
    "sounding_el_ft": "DOUBLE",
}


@dataclass(frozen=True)
class ConvectiveScoreParams:
    risk_threshold: str = "moderate"
    dbz_threshold: float = 41.0
    radar_radius_nm: float = 10.0
    lightning_radius_nm: float = 20.0
    tops_radius_nm: float = 10.0
    risk_source: str = "thermo"
    since: datetime | None = None
    until: datetime | None = None

    def __post_init__(self) -> None:
        if self.risk_source not in RISK_SOURCES:
            raise ValueError(
                f"risk_source={self.risk_source!r} is not one of {sorted(RISK_SOURCES)}"
            )
        # The archive's radius columns are matched exactly (see pairs_sql), so a
        # radius the archiver never writes would silently score nothing.
        from weatherbrief.observed.sampler import DEFAULT_RADII_NM

        for name in ("radar_radius_nm", "lightning_radius_nm", "tops_radius_nm"):
            if getattr(self, name) not in DEFAULT_RADII_NM:
                raise ValueError(
                    f"{name}={getattr(self, name)} is not an archived radius "
                    f"{DEFAULT_RADII_NM}"
                )

    def risks_at_or_above(self) -> list[str]:
        return list(RISK_ORDER[RISK_ORDER.index(self.risk_threshold):])


def _glob(root: Path, sub: str) -> str:
    return str(root / sub / "*.parquet")


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def pairs_sql(
    verification_root: Path, observed_root: Path, params: ConvectiveScoreParams,
    snapshot_columns: Iterable[str] | None = None,
) -> str:
    """One row per forecast hour with a known observed outcome.

    ``snapshot_columns`` is what the snapshot archive actually holds; any
    :data:`PAIR_COLUMNS` entry missing from it reads NULL. ``None`` assumes
    all are present.
    """
    risks = ", ".join(_sql_str(r) for r in params.risks_at_or_above())
    present = set(PAIR_COLUMNS if snapshot_columns is None else snapshot_columns)
    missing = "".join(
        f", NULL::{kind} AS {name}"
        for name, kind in PAIR_COLUMNS.items() if name not in present
    )
    risk = RISK_SOURCES[params.risk_source]
    window = []
    if params.since is not None:
        window.append(f"forecast_hour >= TIMESTAMPTZ {_sql_str(params.since.isoformat())}")
    if params.until is not None:
        window.append(f"forecast_hour < TIMESTAMPTZ {_sql_str(params.until.isoformat())}")
    snap_where = " AND ".join([f"({risk}) IS NOT NULL", *window])

    def hourly(source: str) -> str:
        # Frames in [h - 30 min, h + 30 min) belong to hour h.
        return (
            f"FROM read_parquet({_sql_str(_glob(observed_root, source))}, union_by_name=true)"
        )

    # `radius_nm = x` is an exact match on purpose: the archiver writes the fixed
    # disc radii (observed.sampler.DEFAULT_RADII_NM, 5/10/20), so x must be one.
    hour_of = "date_trunc('hour', frame_valid_time + INTERVAL 30 MINUTE)"
    # Cloud tops are context, not part of the verdict: an archive without
    # CTTH files (it is the one source a missed tick loses) still scores.
    if any((observed_root / "eumetsat_ctth").glob("*.parquet")):
        tops = f"""SELECT icao, {hour_of} AS hour, max(highest_fl) AS max_top_fl
    {hourly('eumetsat_ctth')}
    WHERE radius_nm = {params.tops_radius_nm}
    GROUP BY 1, 2"""
    else:
        tops = "SELECT NULL::VARCHAR AS icao, NULL::TIMESTAMPTZ AS hour, NULL::DOUBLE AS max_top_fl WHERE false"
    return f"""
WITH snap_src AS (
    SELECT *{missing}
    FROM read_parquet({_sql_str(_glob(verification_root, 'snapshots'))}, union_by_name=true)
),
snap AS (
    SELECT icao, model,
           CAST(model_init_time AS TIMESTAMPTZ) AS model_init_time,
           CAST(forecast_hour AS TIMESTAMPTZ) AS forecast_hour,
           {risk} AS risk,
           ({risk}) IN ({risks}) AS forecast_yes,
           {", ".join(PAIR_COLUMNS)}
    FROM snap_src
    WHERE {snap_where}
),
radar AS (
    SELECT icao, {hour_of} AS hour,
           count(*) AS radar_frames,
           max(max_value) AS max_dbz
    {hourly('opera_dbzh')}
    WHERE radius_nm = {params.radar_radius_nm} AND NOT insufficient_coverage
    GROUP BY 1, 2
),
lightning AS (
    SELECT icao, {hour_of} AS hour,
           count(*) AS lightning_frames,
           sum(flash_count) AS flashes
    {hourly('eumetsat_li')}
    WHERE radius_nm = {params.lightning_radius_nm}
    GROUP BY 1, 2
),
tops AS (
    {tops}
),
joined AS (
    SELECT s.*,
           date_diff('day', CAST(s.model_init_time AS DATE), CAST(s.forecast_hour AS DATE)) AS days_out,
           r.max_dbz, coalesce(r.radar_frames, 0) AS radar_frames,
           l.flashes, coalesce(l.lightning_frames, 0) AS lightning_frames,
           t.max_top_fl,
           coalesce(r.max_dbz >= {params.dbz_threshold}, false)
             OR coalesce(l.flashes > 0, false) AS observed_yes
    FROM snap s
    LEFT JOIN radar r ON r.icao = s.icao AND r.hour = s.forecast_hour
    LEFT JOIN lightning l ON l.icao = s.icao AND l.hour = s.forecast_hour
    LEFT JOIN tops t ON t.icao = s.icao AND t.hour = s.forecast_hour
)
-- Both sources must be present whatever the outcome: letting an observed yes
-- through on one source would keep an under-covered hour only when it stormed.
SELECT * FROM joined
WHERE radar_frames > 0 AND lightning_frames > 0
"""


def score_sql(pairs: str) -> str:
    """Hit / miss / false alarm / correct negative per model and lead day."""
    return f"""
WITH pairs AS ({pairs})
SELECT model, days_out,
       count(*) AS n,
       count(*) FILTER (WHERE forecast_yes AND observed_yes) AS hits,
       count(*) FILTER (WHERE NOT forecast_yes AND observed_yes) AS misses,
       count(*) FILTER (WHERE forecast_yes AND NOT observed_yes) AS false_alarms,
       count(*) FILTER (WHERE NOT forecast_yes AND NOT observed_yes) AS correct_negatives
FROM pairs
GROUP BY 1, 2
ORDER BY 1, 2
"""


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def with_skill(row: dict) -> dict:
    """Add POD, FAR, CSI and frequency bias to a :func:`score_sql` row."""
    h, m, f = row["hits"], row["misses"], row["false_alarms"]
    return row | {
        "pod": _ratio(h, h + m),
        "far": _ratio(f, h + f),
        "csi": _ratio(h, h + m + f),
        "bias": _ratio(h + f, h + m),
    }


def score_convective(
    verification_root: Path,
    observed_root: Path,
    params: ConvectiveScoreParams = ConvectiveScoreParams(),
    *,
    pairs_out: Path | None = None,
) -> list[dict]:
    """Run the scoring; optionally write the per-pair rows to ``pairs_out``."""
    import duckdb

    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    snap_glob = _sql_str(_glob(verification_root, "snapshots"))
    columns = [
        r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet({snap_glob}, union_by_name=true)"
        ).fetchall()
    ]
    pairs = pairs_sql(verification_root, observed_root, params, columns)
    if pairs_out is not None:
        con.execute(f"COPY ({pairs}) TO {_sql_str(str(pairs_out))} (FORMAT parquet)")
    cur = con.execute(score_sql(pairs))
    names = [d[0] for d in cur.description]
    return [with_skill(dict(zip(names, r))) for r in cur.fetchall()]
