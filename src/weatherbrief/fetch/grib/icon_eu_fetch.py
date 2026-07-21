"""DWD ICON GRIB2 download from opendata.dwd.de.

Serves two DWD ICON variants that share this entire download/decode machinery
(same server layout, same variable names, same regular-lat-lon grid type),
differing only in a handful of config values captured by :class:`IconVariant`
(issue #456):

- **ICON-EU** — ~6.5 km, all of Europe, hourly to 78h then 3-hourly to 120h.
  The default variant; every helper here keeps ``variant=ICON_EU`` so existing
  callers are unchanged.
- **ICON-D2** — ~2.2 km convection-permitting, central Europe only, hourly to
  48h. Selected in place of ICON-EU for the ``icon`` slot when the whole route
  fits the D2 domain and the flight window is within a D2 run's 48h horizon.

Both provide cloud liquid water (QC) and ice mixing ratio (QI) on model levels.

Data structure differs from GFS:
- Individual bz2-compressed files per variable/level/timestep
- Model levels (not pressure levels) — need P field for vertical interpolation
- Separate files per level (no .idx companion files)

The historical ``icon_eu_*`` names are retained (many callers + tests import
them) even though they now dispatch on ``variant``; think of the prefix as
"DWD ICON GRIB", not "ICON-EU specifically".
"""

from __future__ import annotations

import bz2
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

# DWD Open Data base URL (ICON-EU). ICON-D2 lives under a sibling path — see
# the ``base_url`` field on each :class:`IconVariant` below.
DWD_BASE_URL = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"

@dataclass(frozen=True)
class IconVariant:
    """Config for one DWD ICON GRIB variant (ICON-EU or ICON-D2).

    Pure data, no behaviour — every ``icon_eu_*`` helper below reads these
    fields so the same download/decode path serves both variants (issue #456).

    Attributes:
        slug: Cache-dir model key and filename model token ("icon-eu"/"icon-d2").
        source_key: Freshness ``SOURCE_REGISTRY`` key ("icon_eu:dwd"/"icon_d2:dwd").
        cache_prefix: Prefix for per-run cache-key variable labels
            ("ICON_EU"/"ICON_D2") so the two variants never collide in a cache dir.
        base_url: DWD opendata grib base for this model.
        grid_label: Filename region token ("europe"/"germany").
        lat_min/lat_max/lon_min/lon_max: All-or-nothing domain bbox.
        cycles: UTC run hours, freshest-first (run-finder order).
        publish_delay_hours: Hours after init before a run is expected published.
        level_min/level_max: Inclusive model-level slice (aviation altitude band).
        var_suffix_upper: True → uppercase variable suffix in filenames (EU);
            False → lowercase (D2). Governs both model- and single-level names.
        single_level_2d: True → single-level filenames carry a "_2d_" segment (D2).
        main_cycles: Cycles reaching ``horizon_main_h`` (others reach the short one).
        horizon_main_h/horizon_short_h: Per-cycle model-level forecast horizon.
        hourly_to_h: Last hourly forecast step; steps above it are ``coarse_step_h``.
        coarse_step_h: Step size in the post-hourly region (EU 3h; D2 has none → 1).
        cloud_diag_variables: Single-level diagnostic variables published for
            this variant. NOT identical between variants: ICON-D2 has no deep-
            convection parameterization, so ``hbas_con``/``htop_con``/``rain_con``
            don't exist (or are meaningless) on its feed — see the D2 tuple below.
        cloud_diag_cache_version: Version suffix of the single-level
            cloud-diagnostic blob cache key. Bump when the variable list (blob
            content) changes so warm caches re-fetch (#421 precedent).
    """

    slug: str
    source_key: str
    cache_prefix: str
    base_url: str
    grid_label: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    cycles: tuple[int, ...]
    publish_delay_hours: float
    level_min: int
    level_max: int
    var_suffix_upper: bool
    single_level_2d: bool
    main_cycles: frozenset[int]
    horizon_main_h: int
    horizon_short_h: int
    hourly_to_h: int
    coarse_step_h: int
    cloud_diag_variables: tuple[str, ...]
    cloud_diag_cache_version: str = "V2"

    @property
    def needs_predecessor_step(self) -> bool:
        """True when the single-level fetch needs one leading step prepended.

        Any variable whose hourly value is derived against the previous step
        requires the lead file: accumulated fields de-accumulated per hour
        (``rain_con`` on EU, ``grau_gsp`` on D2) and sub-hourly windowed fields
        whose hourly product spans messages from two adjacent files (D2
        ``echotop`` quarters — #462).
        """
        return bool(
            {"rain_con", "grau_gsp", "echotop"}.intersection(self.cloud_diag_variables)
        )


# Single-level cloud diagnostic variables (ICON-EU).
# cape_ml / cin_ml (mixed-layer CAPE/CIN, instantaneous) added for the
# native convective track (#283 Phase 2). rain_con (convective rain,
# accumulated since init) added in #421 so the convective firing gate and
# native corroboration can evaluate ICON towers — the rate is de-accumulated
# in the enrichment loop, not here.
# SNOW_CON (convective snow, also accumulated) is intentionally NOT fetched
# (#421 scope decision): it would double the single-level file count for the
# rare convective-snow-shower case, and omitting it is safe by construction —
# the firing gate holds down only on positive dry evidence, so a winter ICON
# tower with rain_con ~0 but real convective snow simply keeps its tier (no
# regression, just an unclosed corner). If added, sum the two de-accumulated
# rates before assigning convective_precip_mm_h.
ICON_EU_CLOUD_DIAG_VARIABLES = (
    "ceiling", "hbas_con", "htop_con",
    "clcl", "clcm", "clch", "clct",
    "cape_ml", "cin_ml",
    "rain_con",
)

# Single-level diagnostics for ICON-D2. D2 is convection-permitting: it runs
# NO deep-convection scheme, so (verified live against opendata.dwd.de,
# 2026-07-21):
# - hbas_con / htop_con → 404. Only shallow-convection hbas_sc/htop_sc exist,
#   and those describe fair-weather cumulus — mapping them into
#   convective_base/top would show a benign low top during a real storm.
# - rain_con → exists but is near-zero even in severe storms (the remaining
#   shallow scheme barely precipitates; explicit-storm rain lands in
#   rain_gsp/prg_gsp). Feeding it into convective_precip_mm_h would make the
#   native convective gate read "quiet" exactly when D2 sees a storm.
# All three therefore stay ABSENT for D2 → downstream parameterized fields are
# None (missing-data semantics). In their place (#462), D2's convection-
# permitting storm fields (all verified against the live feed 2026-07-21 —
# eccodes per-message inspection of 00z f012 files):
# - dbz_ctmax — column-max simulated reflectivity, dBZ, stepType=max over the
#   previous hour (single full-hour message per file). The firing signal.
# - echotop — LOWEST PRESSURE (= highest altitude) with reflectivity ≥ 18 dBZ,
#   delivered as shortName `min_pres` in Pa, stepType=min, FOUR 15-min
#   messages per file; −999 Pa = "no echo this quarter" (valid data, not
#   missing). The hourly product is CONSTRUCTED as the min over the four
#   quarter windows ending in (H−1, H] — three messages from file f(H−1) plus
#   the first of file f(H). Depth/character only — NEVER a cloud top.
# - lpi_max — Lightning Potential Index (Lynn & Yair 2010), J/kg, hour max.
# - w_ctmax — max updraft in the 0–10 km column, m/s, hour max.
# - uh_max_med — updraft helicity over the 2–5 km AGL layer, m²/s², hour max
#   of the SIGNED amplitude. Chosen over uh_max (2–8 km) deliberately: 2–5 km
#   is the literature/HRRR-calibrated layer, so rotation notes can reference
#   portable thresholds.
# - grau_gsp — grid-scale graupel, kg/m² ≡ mm, ACCUMULATED since init; the
#   file carries one on-the-hour message (stepUnits=hours) plus three quarter-
#   hour accums — de-accumulation uses the on-the-hour messages only.
# Deferred: dbz_cmax (instant — optional sub-hourly detail), tcond10_mx
# (column condensate above −10 °C — overlaps the dbz signal; revisit with
# calibration). Not usable: hbas_con/htop_con (404), rain_con (≈0), prr_con
# (404), echotopinm (404 — only the pressure form is delivered).
ICON_D2_CLOUD_DIAG_VARIABLES = (
    "ceiling",
    "clcl", "clcm", "clch", "clct",
    "cape_ml", "cin_ml",
    "dbz_ctmax", "echotop", "lpi_max", "w_ctmax", "uh_max_med", "grau_gsp",
)


# ICON-EU model-level horizon depends on the run cycle (verified empirically
# against opendata.dwd.de directory listings):
# - Main runs (00z, 06z, 12z, 18z): hourly to 78h, 3-hourly to 120h
# - Short runs (03z, 09z, 15z, 21z): hourly to 30h, then 6-hourly to 48h
# We cap short-run usage at 30h so the run-picker falls back to the prior main
# run for any flight extending past +30h — keeps every briefing on a uniform
# hourly grid and avoids 404s on f031–f047 which are not published.
ICON_EU = IconVariant(
    slug="icon-eu",
    source_key="icon_eu:dwd",
    cache_prefix="ICON_EU",
    base_url="https://opendata.dwd.de/weather/nwp/icon-eu/grib",
    grid_label="europe",
    lat_min=29.5,
    lat_max=70.5,
    lon_min=-23.5,
    lon_max=62.5,
    cycles=(21, 18, 15, 12, 9, 6, 3, 0),
    publish_delay_hours=3.0,
    level_min=35,  # levels 35-74 ≈ 300-1000 hPa (surface to ~FL280)
    level_max=74,
    var_suffix_upper=True,
    single_level_2d=False,
    main_cycles=frozenset({0, 6, 12, 18}),
    horizon_main_h=120,
    horizon_short_h=30,
    hourly_to_h=78,
    coarse_step_h=3,
    cloud_diag_variables=ICON_EU_CLOUD_DIAG_VARIABLES,
)

# ICON-D2: 2.2 km convection-permitting, central-Europe domain, 8 runs/day,
# hourly to 48h (no coarse tail). Filename quirks vs EU (issue #456): lowercase
# variable suffix (..._60_t.grib2.bz2), single-level files carry a "_2d_"
# segment (..._006_2d_ceiling.grib2.bz2), region token "germany", model token
# "icon-d2". Domain corners + 65-level count from DWD's ICON-D2 regular-lat-lon
# product (1215×746 ≈ 906k points ≈ ICON-EU's ~905k).
#
# level_min=16 validated against DWD's HHL (half-level height) field decoded
# from the live feed (2026-07-21): D2 level 16 ≈ 9,460 m ≈ FL310, matching
# ICON-EU's level-35 top (~300 hPa). D2 level numbering is NOT comparable to
# EU's (65 levels vs 74, both numbered top-down with bottom = surface): level
# 25 — the original guess — sits at only ~6,300 m ≈ FL207 and would truncate
# every D2 sounding at ~20,000 ft. 16–65 = 50 levels, surface→~FL310.
# Publication delay ~1–2h — 2h with margin; the run-finder HEAD-probes anyway
# so a small miss just walks back one cycle.
ICON_D2 = IconVariant(
    slug="icon-d2",
    source_key="icon_d2:dwd",
    cache_prefix="ICON_D2",
    base_url="https://opendata.dwd.de/weather/nwp/icon-d2/grib",
    grid_label="germany",
    lat_min=43.18,
    lat_max=58.08,
    lon_min=-3.94,
    lon_max=20.34,
    cycles=(21, 18, 15, 12, 9, 6, 3, 0),
    publish_delay_hours=2.0,
    level_min=16,
    level_max=65,
    var_suffix_upper=False,
    single_level_2d=True,
    main_cycles=frozenset({0, 3, 6, 9, 12, 15, 18, 21}),
    horizon_main_h=48,
    horizon_short_h=48,
    hourly_to_h=48,
    coarse_step_h=1,
    cloud_diag_variables=ICON_D2_CLOUD_DIAG_VARIABLES,
    # V2 → V3 in #462: the explicit-convection storm fields (dbz_ctmax,
    # echotop, lpi_max, w_ctmax, uh_max_med, grau_gsp) joined the blob, so
    # pre-#462 cached blobs must re-fetch.
    cloud_diag_cache_version="V3",
)

# Back-compat module constants (ICON-EU). The variant above is the single
# source of truth; these aliases keep existing imports/tests working.
ICON_EU_MAIN_CYCLES = set(ICON_EU.main_cycles)
ICON_EU_MODEL_LEVEL_MAX_HOUR_MAIN = ICON_EU.horizon_main_h
ICON_EU_MODEL_LEVEL_MAX_HOUR_SHORT = ICON_EU.horizon_short_h
ICON_EU_LAT_MIN = ICON_EU.lat_min
ICON_EU_LAT_MAX = ICON_EU.lat_max
ICON_EU_LON_MIN = ICON_EU.lon_min
ICON_EU_LON_MAX = ICON_EU.lon_max
ICON_EU_CYCLES = list(ICON_EU.cycles)
ICON_EU_PUBLISH_DELAY_HOURS = ICON_EU.publish_delay_hours
ICON_EU_MODEL_LEVEL_MIN = ICON_EU.level_min
ICON_EU_MODEL_LEVEL_MAX = ICON_EU.level_max


def icon_eu_model_level_max_hour(
    init_hour: int, variant: IconVariant = ICON_EU,
) -> int:
    """Return the model-level forecast horizon for the given cycle + variant."""
    if init_hour in variant.main_cycles:
        return variant.horizon_main_h
    return variant.horizon_short_h


def icon_eu_window_out_of_range(
    target_time: datetime,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
    variant: IconVariant = ICON_EU,
) -> bool:
    """True when no publishable run of *variant* has enough horizon for the flight.

    Deterministic (no network): walks the same cycles and publication-delay
    logic as :func:`find_latest_icon_eu_run_with_response` and checks whether
    any run available as-of *as_of_time* reaches ``target_time +
    flight_duration_hours``. ICON-EU's model-level horizon is only 120h (5
    days) and ICON-D2's only 48h, so a flight beyond that is out of range.

    Used to distinguish the *expected* "flight beyond horizon" skip (an
    info-level condition) from a genuine upstream/probe failure — the
    run-finder returns ``None`` for both.
    """
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = target_time + timedelta(hours=flight_duration_hours)
    for days_back in range(2):
        check_date = reference_time - timedelta(days=days_back)
        for cycle in variant.cycles:
            init_time = check_date.replace(
                hour=cycle, minute=0, second=0, microsecond=0,
            )
            if init_time > reference_time:
                continue
            hours_since_init = (reference_time - init_time).total_seconds() / 3600
            if hours_since_init < variant.publish_delay_hours:
                continue
            horizon = init_time + timedelta(
                hours=icon_eu_model_level_max_hour(cycle, variant),
            )
            if horizon >= need_until:
                # At least one publishable run reaches the flight window.
                return False
    return True

# Variables to fetch: sounding + cloud microphysics + pressure for vertical interpolation.
# Note: "qv" (specific humidity) is used instead of "relhum" because relhum is only
# available on pressure levels, not model levels on DWD Open Data.
# "fi" (geopotential) is NOT available on model levels — only on pressure levels.
# Geopotential height is instead derived from pressure via the hypsometric equation
# in the sounding analysis (or omitted — not critical for core analysis).
ICON_EU_VARIABLES = ("qc", "qi", "clc", "p", "t", "qv", "u", "v", "w")

# Cache-key label for the single-level cloud-diagnostic blob. Bumped to V2 in
# #421 when rain_con was added: the blob is cached under ONE key for all
# variables, so adding a variable changes the blob's *content* but not its key.
# Bumping the label forces existing (rain_con-less) cached blobs to re-fetch —
# without it a warm cache would silently keep the pre-#421 gate-less behaviour.
# Referenced everywhere the blob is cached (prefetch, enrichment, precache,
# standalone verification) so the four call sites can never drift apart.
ICON_EU_CLOUD_DIAG_CACHE_KEY = "ICON_EU_CLOUD_DIAG_V2"


def icon_cloud_diag_cache_key(variant: IconVariant = ICON_EU) -> str:
    """Cache-key label for a variant's single-level cloud-diagnostic blob.

    ``{cache_prefix}_CLOUD_DIAG_{version}`` — the prefix keeps the two variants
    from colliding in a cache dir; the version is bumped per variant whenever
    its variable list (blob content) changes, so warm caches re-fetch (#421
    precedent). ICON-D2 moved to ``_V3`` in #462 when the explicit-convection
    storm fields were added to its blob; ICON-EU stays at ``_V2``.
    """
    return f"{variant.cache_prefix}_CLOUD_DIAG_{variant.cloud_diag_cache_version}"

# Parallel download settings
MAX_DOWNLOAD_WORKERS = 8
REQUEST_TIMEOUT = 30  # seconds per file


def route_in_icon_eu_domain(
    route_points: list, variant: IconVariant = ICON_EU,
) -> bool:
    """Check if all route points fall within *variant*'s domain.

    All-or-nothing: returns False if any point is outside.
    """
    for rp in route_points:
        if not (variant.lat_min <= rp.lat <= variant.lat_max):
            return False
        if not (variant.lon_min <= rp.lon <= variant.lon_max):
            return False
    return True


def _icon_var_suffix(variable: str, variant: IconVariant) -> str:
    """Filename variable suffix, cased per variant (EU upper, D2 lower)."""
    return variable.upper() if variant.var_suffix_upper else variable.lower()


def icon_eu_file_url(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    level: int,
    variable: str,
    variant: IconVariant = ICON_EU,
) -> str:
    """Build URL for a single DWD ICON model-level GRIB2 bz2 file.

    Examples:
        ICON-EU: .../icon-eu/grib/00/qc/
          icon-eu_europe_regular-lat-lon_model-level_2026022100_000_35_QC.grib2.bz2
        ICON-D2: .../icon-d2/grib/00/t/
          icon-d2_germany_regular-lat-lon_model-level_2026072000_006_60_t.grib2.bz2
    """
    return (
        f"{variant.base_url}/{init_hour:02d}/{variable.lower()}/"
        f"{variant.slug}_{variant.grid_label}_regular-lat-lon_model-level_"
        f"{init_date}{init_hour:02d}_{forecast_hour:03d}_{level:02d}_"
        f"{_icon_var_suffix(variable, variant)}.grib2.bz2"
    )


def icon_eu_single_level_url(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    variable: str,
    variant: IconVariant = ICON_EU,
) -> str:
    """Build URL for a single DWD ICON single-level GRIB2 bz2 file (no level).

    ICON-D2 single-level files carry a ``_2d_`` segment between the forecast
    hour and the variable; ICON-EU does not.

    Examples:
        ICON-EU: .../icon-eu/grib/00/ceiling/
          icon-eu_europe_regular-lat-lon_single-level_2026022100_006_CEILING.grib2.bz2
        ICON-D2: .../icon-d2/grib/00/ceiling/
          icon-d2_germany_regular-lat-lon_single-level_2026072000_006_2d_ceiling.grib2.bz2
    """
    segment = "_2d_" if variant.single_level_2d else "_"
    return (
        f"{variant.base_url}/{init_hour:02d}/{variable.lower()}/"
        f"{variant.slug}_{variant.grid_label}_regular-lat-lon_single-level_"
        f"{init_date}{init_hour:02d}_{forecast_hour:03d}{segment}"
        f"{_icon_var_suffix(variable, variant)}.grib2.bz2"
    )


def fetch_icon_eu_single_level(
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    variables: list[str] | None = None,
    session: requests.Session | None = None,
    max_workers: int = MAX_DOWNLOAD_WORKERS,
    variant: IconVariant = ICON_EU,
) -> dict[int, bytes]:
    """Download ICON single-level GRIB2 fields and return concatenated bytes per fhour.

    Downloads single-level cloud diagnostic files (no level dimension)
    using the same parallel pattern as model-level fetch.

    Args:
        init_date: YYYYMMDD format.
        init_hour: Cycle hour (0, 3, 6, ..., 21).
        forecast_hours: Forecast hours to download.
        variables: Variable names (defaults to the variant's
            ``cloud_diag_variables`` — NOT the same list for EU and D2).
        session: Optional requests session.
        max_workers: Per-call download thread count. Callers that already
            run several fetches concurrently pass a smaller value so the
            total connection count stays bounded.
        variant: ICON variant (EU/D2) whose URL conventions to use.

    Returns:
        Dict of {forecast_hour: concatenated decompressed GRIB2 bytes}.
    """
    if variables is None:
        variables = list(variant.cloud_diag_variables)

    sess = session or requests.Session()
    result: dict[int, bytes] = {}

    for fhour in forecast_hours:
        urls = [
            icon_eu_single_level_url(init_date, init_hour, fhour, var, variant)
            for var in variables
        ]

        buf = bytearray()
        downloaded = 0
        failures: dict[int | str, int] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_download_one_file, url, sess): url
                for url in urls
            }
            for future in as_completed(futures):
                data, status = future.result()
                if data is not None:
                    buf.extend(data)
                    downloaded += 1
                else:
                    failures[status] = failures.get(status, 0) + 1

        total_failed = sum(failures.values())
        if buf:
            result[fhour] = bytes(buf)
            logger.info(
                "%s single-level f%03d: downloaded %d/%d files (%.1f KB)%s",
                variant.slug, fhour, downloaded, downloaded + total_failed,
                len(buf) / 1024, _format_failure_summary(failures),
            )
        elif total_failed:
            logger.warning(
                "%s single-level f%03d: all %d files failed%s",
                variant.slug, fhour, total_failed, _format_failure_summary(failures),
            )

    return result


def find_latest_icon_eu_run(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
    cover_until: datetime | None = None,
    variant: IconVariant = ICON_EU,
) -> tuple[str, int] | None:
    """Find the latest available ICON model run whose horizon covers the flight.

    Thin wrapper around :func:`find_latest_icon_eu_run_with_response` for
    callers that don't need the HEAD response.
    """
    found = find_latest_icon_eu_run_with_response(
        target_time, session, as_of_time, cover_until, variant,
    )
    return (found[0], found[1]) if found is not None else None


def find_latest_icon_eu_run_with_response(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
    cover_until: datetime | None = None,
    variant: IconVariant = ICON_EU,
) -> tuple[str, int, requests.Response] | None:
    """Find the latest ICON run + return the matching probe HEAD response.

    Tries cycles in reverse chronological order, checking that enough time
    has passed for publication and that the run's model-level horizon
    reaches ``cover_until``.  If ``cover_until`` is None, any run that
    covers ``target_time`` is accepted.

    The response is returned so the freshness dispatch can read its
    ``Last-Modified`` header without re-issuing the same HEAD upstream.

    Args:
        target_time: Flight departure time.
        session: Optional requests session.
        as_of_time: If set, only consider runs initialized before this time
            (for historical "as-of" briefings). Uses ``now`` if None.
        cover_until: Latest time the run must cover (typically departure +
            flight duration).  Runs whose model-level horizon falls short
            are skipped in favour of an older main run with longer range.

    Returns:
        ``(init_date_YYYYMMDD, init_hour, head_response)`` or ``None``.
    """
    sess = session or requests.Session()
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = cover_until or target_time

    for days_back in range(2):
        check_date = reference_time - timedelta(days=days_back)
        date_str = check_date.strftime("%Y%m%d")
        for cycle in variant.cycles:
            init_time = check_date.replace(
                hour=cycle, minute=0, second=0, microsecond=0,
            )
            if init_time > reference_time:
                continue
            hours_since_init = (reference_time - init_time).total_seconds() / 3600
            if hours_since_init < variant.publish_delay_hours:
                continue

            # Check if this run's horizon covers the flight
            max_hour = icon_eu_model_level_max_hour(cycle, variant)
            horizon = init_time + timedelta(hours=max_hour)
            if horizon < need_until:
                logger.debug(
                    "%s %s %02dz: horizon %dh doesn't reach flight end, skipping",
                    variant.slug, date_str, cycle, max_hour,
                )
                continue

            # Probe: check if the bottom-level P file for forecast hour 000 exists
            probe_url = icon_eu_file_url(
                date_str, cycle, 0, variant.level_max, "p", variant,
            )
            try:
                resp = sess.head(probe_url, timeout=10)
                if resp.status_code == 200:
                    logger.info(
                        "Found %s run: %s %02dz (horizon %dh)",
                        variant.slug, date_str, cycle, max_hour,
                    )
                    return date_str, cycle, resp
            except requests.RequestException:
                continue

    return None


def bracket_icon_eu_forecast_hours(
    init_date: str,
    init_hour: int,
    target_time: datetime,
    variant: IconVariant = ICON_EU,
) -> tuple[int, int]:
    """Find the two forecast hours that bracket the target time.

    ICON-EU is hourly for 0–78h then 3-hourly to 120h; ICON-D2 is hourly to 48h.

    Returns:
        (f_prev, f_next) bracketing the target.
    """
    init_dt = datetime.strptime(
        f"{init_date}{init_hour:02d}", "%Y%m%d%H",
    ).replace(tzinfo=timezone.utc)
    delta_hours = (target_time - init_dt).total_seconds() / 3600
    delta_hours = max(0, delta_hours)

    if delta_hours <= variant.hourly_to_h:
        # Hourly region
        f_prev = int(delta_hours)
        f_next = f_prev + 1
    else:
        # Coarse (post-hourly) region
        step = variant.coarse_step_h
        base = int((delta_hours - variant.hourly_to_h) / step)
        f_prev = variant.hourly_to_h + base * step
        f_next = f_prev + step

    # Clamp to max forecast hour
    f_prev = min(f_prev, variant.horizon_main_h)
    f_next = min(f_next, variant.horizon_main_h)

    return f_prev, f_next


def _snap_to_icon_eu_grid(fhour: float, variant: IconVariant = ICON_EU) -> int:
    """Snap a fractional forecast hour to the nearest grid point for *variant*.

    ICON-EU: 1-hourly for 0–78h, 3-hourly for 78–120h. ICON-D2: 1-hourly to 48h.
    """
    if fhour <= variant.hourly_to_h:
        return round(fhour)
    step = variant.coarse_step_h
    base = variant.hourly_to_h + round((fhour - variant.hourly_to_h) / step) * step
    return min(base, variant.horizon_main_h)


def _snap_to_icon_eu_grid_floor(fhour: float, variant: IconVariant = ICON_EU) -> int:
    """Snap DOWN to nearest grid point (floor, not round) for *variant*."""
    if fhour <= variant.hourly_to_h:
        return int(fhour)
    step = variant.coarse_step_h
    base = variant.hourly_to_h + int((fhour - variant.hourly_to_h) / step) * step
    return min(base, variant.horizon_main_h)


def icon_eu_previous_step(fhour: int, variant: IconVariant = ICON_EU) -> int | None:
    """Return the forecast hour immediately preceding *fhour* for *variant*.

    Respects the temporal grid — 1-hourly at or below ``hourly_to_h`` (step −1),
    coarse above it (step −``coarse_step_h``) — mirroring
    :func:`_snap_to_icon_eu_grid`. Returns ``None`` for ``fhour == 0``:
    accumulation is 0 at init by definition, so there is no earlier step to
    difference an accumulated field against.

    Used to prepend one leading single-level step to the on-demand cloud-diag
    fetch so the first flight-window hour has a predecessor to de-accumulate
    ``rain_con`` against. ICON is downloaded exactly on the window hours (no
    ±margin like the locally-mirrored ECMWF run), so without this the first
    hour would have no rate (#421).
    """
    if fhour <= 0:
        return None
    if fhour <= variant.hourly_to_h:
        return fhour - 1
    return fhour - variant.coarse_step_h


def icon_eu_conv_rain_rate_mm_h(
    rain_con: float | None,
    prev_rain_con: float | None,
    window_h: float | None,
) -> float | None:
    """De-accumulate ICON ``rain_con`` into a convective-precip rate (mm/h).

    ``rain_con`` is accumulated since init in kg/m² ≡ mm — **already mm**, so
    there is NO ×1000 (that conversion is only for ECMWF ``cp``, which is m
    water equivalent). Clamped at 0 so a decreasing accumulation (new run /
    GRIB glitch) yields 0 rather than a negative rate.

    Returns ``None`` — not ``0.0`` — when any input is missing (no predecessor
    step, uncovered point, or non-positive window). ``None`` = unknown, which
    the firing gate treats as missing-data-safe; ``0.0`` would actively hold a
    tower down. The two are **not** interchangeable (#421).
    """
    if rain_con is None or prev_rain_con is None or window_h is None or window_h <= 0:
        return None
    return max(0.0, (rain_con - prev_rain_con) / window_h)


def compute_icon_eu_flight_window_hours(
    init_date: str,
    init_hour: int,
    departure_time: datetime,
    flight_duration_hours: float,
    variant: IconVariant = ICON_EU,
) -> list[int]:
    """Compute ICON forecast hours covering a flight window.

    Same logic as GFS but snapped to the variant's temporal grid.
    """
    init_dt = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    dep_dt = departure_time

    extra = 1 if dep_dt.minute > 0 else 0
    n_hours = max(1, math.ceil(flight_duration_hours) + 1 + extra)
    fhours: set[int] = set()
    for h in range(n_hours):
        utc = dep_dt + timedelta(hours=h)
        delta = (utc - init_dt).total_seconds() / 3600
        delta = max(0.0, delta)
        fhours.add(_snap_to_icon_eu_grid(delta, variant))

    # Include the floor hour so non-round departure times get coverage
    if dep_dt.minute > 0:
        floor_utc = dep_dt.replace(minute=0, second=0, microsecond=0)
        floor_delta = (floor_utc - init_dt).total_seconds() / 3600
        if floor_delta >= 0:
            fhours.add(_snap_to_icon_eu_grid(floor_delta, variant))

    # Include the floor native hour before departure for forward-fill coverage.
    # In a coarse (post-hourly) region, rounding may skip the preceding native
    # hour, leaving interpolated hours without GRIB diagnostics.
    dep_delta = (dep_dt - init_dt).total_seconds() / 3600
    if dep_delta > 0:
        fhours.add(_snap_to_icon_eu_grid_floor(dep_delta, variant))

    return sorted(fhours)


def _format_failure_summary(failures: dict[int | str, int]) -> str:
    """Format a per-status failure summary for log lines, e.g. '404=40'."""
    if not failures:
        return ""
    parts = [f"{k}={v}" for k, v in sorted(failures.items(), key=lambda kv: str(kv[0]))]
    return " (" + ",".join(parts) + ")"


def _download_one_file(
    url: str,
    session: requests.Session,
) -> tuple[bytes | None, int | str]:
    """Download and decompress a single bz2-compressed GRIB2 file.

    Returns ``(bytes_or_None, status_or_error_label)`` so callers can
    aggregate failure modes (HTTP 404 vs network errors).
    """
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            logger.debug("HTTP %d for %s", resp.status_code, url.split("/")[-1])
            return None, resp.status_code
        return bz2.decompress(resp.content), 200
    except requests.RequestException as e:
        logger.debug("Download failed %s: %s", url.split("/")[-1], e)
        return None, "network"
    except OSError as e:
        logger.debug("Decompress failed %s: %s", url.split("/")[-1], e)
        return None, "decompress"


def fetch_icon_eu_fields(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    levels: list[int],
    variables: list[str],
    session: requests.Session | None = None,
    variant: IconVariant = ICON_EU,
) -> bytes:
    """Download ICON model-level GRIB2 fields in parallel and concat bytes.

    Downloads individual bz2-compressed files for each variable/level
    combination using a thread pool, decompresses, and concatenates into
    a single GRIB2 byte stream.

    Args:
        init_date: YYYYMMDD format.
        init_hour: Cycle hour (0, 3, 6, ..., 21).
        forecast_hour: Forecast hour.
        levels: Model level numbers to download.
        variables: Variable names (e.g. ["qc", "qi", "p"]).
        session: Optional requests session.
        variant: ICON variant (EU/D2) whose URL conventions to use.

    Returns:
        Concatenated decompressed GRIB2 bytes.
    """
    sess = session or requests.Session()
    urls: list[str] = []
    for var in variables:
        for level in levels:
            urls.append(
                icon_eu_file_url(init_date, init_hour, forecast_hour, level, var, variant),
            )

    result = bytearray()
    downloaded = 0
    failures: dict[int | str, int] = {}

    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = {
            pool.submit(_download_one_file, url, sess): url
            for url in urls
        }
        for future in as_completed(futures):
            data, status = future.result()
            if data is not None:
                result.extend(data)
                downloaded += 1
            else:
                failures[status] = failures.get(status, 0) + 1

    total_failed = sum(failures.values())
    logger.info(
        "%s f%03d: downloaded %d/%d files (%.1f KB)%s",
        variant.slug, forecast_hour, downloaded, downloaded + total_failed,
        len(result) / 1024, _format_failure_summary(failures),
    )
    return bytes(result)


def fetch_icon_eu_per_variable(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    levels: list[int],
    variables: list[str],
    session: requests.Session | None = None,
    max_workers: int = MAX_DOWNLOAD_WORKERS,
    variant: IconVariant = ICON_EU,
) -> dict[str, bytes]:
    """Download ICON model-level GRIB2 fields per variable for chunked decode.

    Same as fetch_icon_eu_fields but returns separate bytes per variable,
    so callers can decode one variable at a time and free memory between.

    ``max_workers`` bounds this call's download threads; callers running
    several per-variable fetches concurrently pass a smaller value so the
    total connection count stays bounded.

    Returns:
        {variable_name: concatenated_decompressed_grib2_bytes}.
    """
    sess = session or requests.Session()
    result: dict[str, bytes] = {}

    for var in variables:
        urls = [
            icon_eu_file_url(init_date, init_hour, forecast_hour, level, var, variant)
            for level in levels
        ]

        buf = bytearray()
        downloaded = 0
        failures: dict[int | str, int] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_download_one_file, url, sess): url
                for url in urls
            }
            for future in as_completed(futures):
                data, status = future.result()
                if data is not None:
                    buf.extend(data)
                    downloaded += 1
                else:
                    failures[status] = failures.get(status, 0) + 1

        total_failed = sum(failures.values())
        if buf:
            result[var] = bytes(buf)
            logger.info(
                "%s f%03d %s: downloaded %d/%d levels (%.1f KB)%s",
                variant.slug, forecast_hour, var, downloaded,
                downloaded + total_failed, len(buf) / 1024,
                _format_failure_summary(failures),
            )
        else:
            logger.warning(
                "%s f%03d %s: all %d files failed%s",
                variant.slug, forecast_hour, var, total_failed,
                _format_failure_summary(failures),
            )

    return result
