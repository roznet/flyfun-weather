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
import os
from collections.abc import Iterable
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
        model_level_variables: Model-level sounding variables to fetch AND
            decode. Also NOT identical between variants (#530): ICON-D2
            publishes the full ICE3-style hydrometeor set (``qr``/``qs``/``qg``
            on top of ``qc``/``qi``/``qv``), ICON-EU only the cloud species —
            verified live against opendata.dwd.de 2026-07-30. Every variable
            here is REQUIRED at decode: an hour missing one is skipped for ICON
            entirely (#478). Opt-in extras that have no consumer yet (``tke``)
            are added by :func:`icon_model_level_fetch_variables` on the fetch
            side only, deliberately outside this required set.
        explicit_conv_variables: Explicit-convection storm diagnostics (#462) —
            reflectivity, echo top, LPI, updraft, updraft helicity.
            Only convection-permitting variants (D2) publish these; empty for
            EU. Fetched into their OWN per-variable cache blobs (not the shared
            cloud-diag blob) because several are multi-message sub-hourly files
            that need message-level ``stepRange`` selection at decode.
        per_level_cache: True → the model-level sounding is cached one file per
            (variable, level) (``f029_ICON_D2_T_L27.grib2``) rather than one
            whole-column blob per variable (#469 phase 1). This is **partial-write
            safety**: DWD publishes one file per level either way, so the layout
            is volume-neutral, but a whole-column key cannot distinguish a
            complete column from a partial one — if some level downloads fail,
            the concatenated blob is still cached under a key that reports
            "present", and every later briefing silently reads a short column.
            Per level, ``is_cached`` answers per level, so a failed level is
            visibly missing: decode can require the complete set (#478) and the
            next briefing tops up only what is absent. ICON-EU keeps the
            whole-column blob (False): completeness is unverifiable there, which
            is exactly why the heavier, more failure-prone D2 column does not use
            it. Whole-column blobs written by older code (both ``{prefix}_{VAR}``
            and the even-older ``{prefix}_QC_QI_P``) stay valid — the read path
            treats them as a hit for the full range (see the migration note in
            the prefetch/decode loops), so no cache flush is needed.
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
    model_level_variables: tuple[str, ...]
    explicit_conv_variables: tuple[str, ...] = ()
    per_level_cache: bool = False

    @property
    def needs_predecessor_step(self) -> bool:
        """True when the flight window's first hour needs the f(H−1) files too.

        Two independent reasons (#421 / #462), one variant-level flag so the
        prefetch and enrichment loops can't drift apart:
        - an accumulated-since-init field (``rain_con`` on EU) needs the
          previous step to de-accumulate the first hour's value;
        - the D2 hourly echo top is constructed from four 15-min windows, three
          of which live in file f(H−1) (see #462 sub-hourly message structure).
        """
        return (
            "rain_con" in self.cloud_diag_variables
            or bool(self.explicit_conv_variables)
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
# lpi_con_max / cape_con added in #530. Both are parameterized-convection
# products, so they exist on EU and NOT on convection-permitting D2:
# - lpi_con_max — Lightning Potential Index, max over the output interval
#   (J/kg). We already get a lightning signal from D2's lpi_max, but only
#   inside D2's 43.18–58.08 N / −3.94–20.34 E box; this one covers the whole
#   ICON-EU domain, i.e. every corridor D2 cannot see.
# - cape_con — CAPE as the convection scheme itself computed it, complementing
#   the cape_ml we already fetch (a diagnostic mixed-layer parcel). Where the
#   two disagree the scheme's own value is what drove the model's convective
#   tendencies.
# Both are DATA AVAILABILITY only in #530: decoded onto NWPCloudDiagnostics,
# consumed by no grader. Wiring them into convective firing is a separate,
# calibrated decision.
ICON_EU_CLOUD_DIAG_VARIABLES = (
    "ceiling", "hbas_con", "htop_con",
    "clcl", "clcm", "clch", "clct",
    "cape_ml", "cin_ml",
    "rain_con",
    "lpi_con_max", "cape_con",
)

# Single-level diagnostics for ICON-D2 — deliberately SMALLER than ICON-EU's
# list. D2 is convection-permitting: it runs NO deep-convection scheme, so
# (verified live against opendata.dwd.de, 2026-07-21):
# - hbas_con / htop_con → 404. Only shallow-convection hbas_sc/htop_sc exist,
#   and those describe fair-weather cumulus — mapping them into
#   convective_base/top would show a benign low top during a real storm.
# - rain_con → exists but is near-zero even in severe storms (the remaining
#   shallow scheme barely precipitates; explicit-storm rain lands in
#   rain_gsp/prg_gsp). Feeding it into convective_precip_mm_h would make the
#   native convective gate read "quiet" exactly when D2 sees a storm.
# All three therefore stay ABSENT for D2 → downstream fields are None
# (missing-data semantics, which icon_eu_conv_rain_rate_mm_h and the firing
# gate already handle). Issue #462 replaces them with D2's convection-
# permitting diagnostics (dbz_cmax, echotop, lpi, uh_max, prg_gsp).
ICON_D2_CLOUD_DIAG_VARIABLES = (
    "ceiling",
    "clcl", "clcm", "clch", "clct",
    "cape_ml", "cin_ml",
)

# Explicit-convection storm diagnostics for ICON-D2 (#462) — the convection-
# permitting replacement for the parameterized hbas_con/htop_con/rain_con
# track. All verified live against opendata.dwd.de (2026-07-21):
# - dbz_ctmax — column-max simulated reflectivity, max over the previous hour
#   (dBZ, stepType=max, one full-hour message). The firing signal.
# - echotop — LOWEST PRESSURE (= highest altitude) with reflectivity > 18 dBZ
#   (shortName min_pres, Pa, stepType=min, sentinel −999). FOUR 15-min
#   messages per file; the hourly value is CONSTRUCTED at enrichment as the
#   min over the four windows ending in (H−1, H]. Never a cloud top.
# - lpi_max — Lightning Potential Index, max over previous hour (J/kg).
# - w_ctmax — max updraft 0–10 km over previous hour (m/s).
# - uh_max — updraft helicity 2–8 km AGL, SIGNED max amplitude (m²/s²).
#   Narrative/character only in v1 (HRRR 2–5 km thresholds NOT portable).
# grau_gsp (surface graupel precipitation, tgrp/231040) was DROPPED from v1
# (#468): despite the name it is a SURFACE accumulation — snow pellets that
# survive the fall to ground — NOT the column mixed-phase-core property #462
# intended it as. It reads ~always 0 under warm-season route-corridor cores
# (graupel almost never survives to sea level in July) and is nonzero only over
# Alpine terrain, so it can never realistically corroborate a corridor storm.
# dbz_cmax (instantaneous) and tcond10_mx are deliberately NOT fetched in v1
# (optional per #462). tcond10_mx — column condensate above the −10 °C isotherm
# — IS the column mixed-phase quantity grau_gsp was meant to be, and is the
# candidate corroborator replacement (#468), deferred pending calibration and
# corridor validation. These fields are fetched into per-variable cache blobs
# (see icon_explicit_conv_cache_key) and decoded with message-level stepRange
# selection — NOT via the shared cloud-diag cfgrib path, whose blob/decode
# assumes one message per variable.
ICON_D2_EXPLICIT_CONV_VARIABLES = (
    "dbz_ctmax", "echotop", "lpi_max", "w_ctmax", "uh_max",
)

# Model-level sounding variables.
#
# "qv" (specific humidity) is used instead of "relhum" because relhum is only
# published on pressure levels, not model levels, on DWD Open Data.
# "fi" (geopotential) is NOT available on model levels either — geopotential
# height is derived from pressure via the hypsometric equation in the sounding
# analysis instead.
ICON_EU_VARIABLES = ("qc", "qi", "clc", "p", "t", "qv", "u", "v", "w")

# ICON-D2 adds the three PRECIPITATING hydrometeor species (#530), verified
# live 2026-07-30 on the same regular-lat-lon model-level layout as qc:
#   qr — rain water, qs — snow, qg — graupel.
# ICON-EU does NOT publish them (its moisture set is qc/qi/qv only), which is
# exactly why this list is per-variant.
#
# Volume: DWD's own file sizes for run 00z step 003 put qg at 2.4 KB and qr at
# 18.7 KB against qc's 13.6 KB — the three together cost ~2.5x one qc, i.e.
# ~21 MB over 50 levels x 12 forecast hours. Negligible against D2's ~494
# MB/fhour sounding, and cached per (variable, level) like every other D2
# species, so a partial download stays detectable.
#
# Why they matter: qc/qi are CLOUD condensate. Precipitation phase was being
# classified from the cloud partition (a proxy — cloud droplets are not what is
# falling), and supercooled RAIN was indistinguishable from supercooled cloud
# droplets even though the two are physically different icing hazards. See
# meteorology-decisions §24.
ICON_D2_VARIABLES = ICON_EU_VARIABLES + ("qr", "qs", "qg")

# Turbulent kinetic energy (#530) — published on model levels by BOTH variants,
# fetched by NEITHER unless explicitly configured. There is no TKE consumer in
# the analysis today, so every byte is speculative, and ICON-EU's tke is the
# most expensive field on the feed by a wide margin: ~903 KB per level-hour
# (turbulence compresses badly) against qc's 88.7 KB, i.e. ~36 MB per forecast
# hour across 40 levels. D2's is cheap by comparison but still buys nothing yet.
#
# Enable with WB_ICON_TKE — a comma-separated list of variant slugs, or "all":
#   WB_ICON_TKE=icon-d2            → D2 only
#   WB_ICON_TKE=icon-d2,icon-eu    → both
#   WB_ICON_TKE=all                → both
# Unset (the default) fetches it for neither. tke is deliberately NOT part of
# IconVariant.model_level_variables: that tuple is the REQUIRED decode set, and
# a failed optional download must not make the pipeline skip the whole hour.
ICON_TKE_VARIABLE = "tke"
ICON_TKE_ENV_VAR = "WB_ICON_TKE"


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
    model_level_variables=ICON_EU_VARIABLES,
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
    model_level_variables=ICON_D2_VARIABLES,
    explicit_conv_variables=ICON_D2_EXPLICIT_CONV_VARIABLES,
    # D2's column is 50 levels × 9 variables per forecast hour — the most files,
    # so the most exposed to a partial download. Cache per (variable, level) so
    # an incomplete fetch is detectable rather than cached as complete (#478).
    # ICON-EU's 40-level column stays on the whole-column blob.
    per_level_cache=True,
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

def icon_tke_enabled(variant: IconVariant) -> bool:
    """True when ``tke`` is explicitly configured for *variant* (#530).

    Reads :data:`ICON_TKE_ENV_VAR` at call time (not import time) so a test or
    a redeploy can flip it without reimporting the module. Unset → False for
    every variant: TKE has no consumer yet and ICON-EU's is ~36 MB per
    forecast hour.
    """
    raw = os.environ.get(ICON_TKE_ENV_VAR, "").strip().lower()
    if not raw:
        return False
    slugs = {tok.strip() for tok in raw.split(",") if tok.strip()}
    return "all" in slugs or variant.slug in slugs


def icon_model_level_fetch_variables(variant: IconVariant) -> tuple[str, ...]:
    """Model-level variables to DOWNLOAD for *variant*.

    The required decode set (``variant.model_level_variables``) plus any
    opt-in extras. Distinct from the decode set on purpose: an extra is
    groundwork with no consumer, so a missing one must not trigger the #478
    "incomplete column → skip the hour" rule that guards the sounding.
    """
    if icon_tke_enabled(variant):
        return variant.model_level_variables + (ICON_TKE_VARIABLE,)
    return variant.model_level_variables


# Cache-key label for the single-level cloud-diagnostic blob. Bumped to V2 in
# #421 when rain_con was added, and to V3 in #530 when lpi_con_max/cape_con
# were: the blob is cached under ONE key for all variables, so adding a
# variable changes the blob's *content* but not its key. Bumping the label
# forces existing (short-schema) cached blobs to re-fetch — without it a warm
# cache would silently keep serving the previous variable set. Referenced
# everywhere the blob is cached (prefetch, enrichment, precache, standalone
# verification) so the four call sites can never drift apart.
ICON_EU_CLOUD_DIAG_CACHE_KEY = "ICON_EU_CLOUD_DIAG_V3"


def icon_cloud_diag_cache_key(variant: IconVariant = ICON_EU) -> str:
    """Cache-key label for a variant's single-level cloud-diagnostic blob.

    ``{cache_prefix}_CLOUD_DIAG_V3`` — the ``_V3`` suffix matches the ICON-EU
    constant; only the prefix differs so ICON-D2 blobs never masquerade as
    ICON-EU in a cache dir. D2's own variable list is unchanged by #530, so its
    bump is a no-op re-fetch of a 6h-TTL blob (2.7% of a D2 run's volume) —
    cheaper than letting the two variants' schema versions drift apart.

    #462 note: the D2 explicit-convection fields deliberately do NOT join this
    blob (they live in per-variable blobs under
    :func:`icon_explicit_conv_cache_key`), so the cloud-diag blob's content is
    unchanged and the #421-style version bump the issue sketched is not needed
    — warm cloud-diag caches remain schema-correct. Keeping them separate also
    keeps the EU/D2 cloud-diag schema shared and lets the sub-hourly explicit
    files use message-level decode instead of the cfgrib blob path.
    """
    return f"{variant.cache_prefix}_CLOUD_DIAG_V3"


def icon_model_level_var_label(
    variant: IconVariant, variable: str, level: int,
) -> str:
    """Cache-key label for ONE (variable, level) model-level file (#469).

    ``{cache_prefix}_{VAR}_L{level:02d}`` → e.g. ``ICON_D2_T_L27``, which
    :func:`cache_key` turns into ``f029_ICON_D2_T_L27.grib2``. Used only by
    ``per_level_cache`` variants (ICON-D2); ICON-EU keeps the whole-column
    per-variable blob under :func:`icon_model_level_var_legacy_label`.
    """
    return f"{variant.cache_prefix}_{variable.upper()}_L{level:02d}"


def icon_model_level_var_legacy_label(variant: IconVariant, variable: str) -> str:
    """Cache-key label for a whole-column per-variable blob (``{prefix}_{VAR}``).

    This is the layout ICON-EU still writes, and the layout ICON-D2 wrote
    before #469. For a ``per_level_cache`` variant it is treated as a hit for
    the FULL level range only (it contains every level, so any requested subset
    is satisfiable from it) — the read path prefers it and skips the per-level
    files when present.
    """
    return f"{variant.cache_prefix}_{variable.upper()}"


def icon_explicit_conv_cache_key(variable: str, variant: IconVariant) -> str:
    """Cache-key label for one explicit-convection variable's per-fhour blob.

    Per-variable (``ICON_D2_EXPL_DBZ_CTMAX_V1`` …) rather than one combined
    blob: the decoder must know which physical field it is looking at without
    guessing eccodes shortNames (DWD's mappings are quirky — rain_con decodes
    as ``crr``), and several of these files carry multiple sub-hourly messages
    that are selected by ``stepRange`` per field.
    """
    return f"{variant.cache_prefix}_EXPL_{variable.upper()}_V1"

# Parallel download settings.
#
# 16 measured 37.1 MB/s droplet→opendata.dwd.de vs 29.6 at 8 (#469) — ~25% for
# one constant, and the dominant term in D2 refresh wall time is this download.
# It is only the DEFAULT for a single fetch call: the cold-cache prefetch already
# sizes its inner pools from ``_POOL_MAXSIZE`` (so outer×inner stays within one
# session's connection pool regardless of this value), and every session caps
# concurrent connections at ``_POOL_MAXSIZE``. The value is env-tunable
# (``MAX_DOWNLOAD_WORKERS``) so a sustained-load regression can be dialed back
# in prod without a deploy — the issue's requested concurrency-aware safety
# valve, since the throughput samples were sub-second bursts, not sustained load.
def _default_download_workers() -> int:
    raw = os.environ.get("MAX_DOWNLOAD_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.warning("Invalid MAX_DOWNLOAD_WORKERS=%r, defaulting to 16", raw)
    return 16


MAX_DOWNLOAD_WORKERS = _default_download_workers()
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


def icon_eu_snap_forecast_hours(
    hours: Iterable[float],
    variant: IconVariant = ICON_EU,
) -> list[int]:
    """Snap arbitrary forecast-hour offsets onto *variant*'s publication grid.

    ICON publishes hourly to ``variant.hourly_to_h`` and every
    ``variant.coarse_step_h`` hours beyond it, so an offset like 80 h has no
    file on opendata.dwd.de at all. Callers that derive forecast hours from
    wall-clock targets (rather than from the grid itself) must pass them
    through here first, or every off-grid hour becomes a guaranteed 404.

    Snapping also deduplicates: 79/80 both land on 78/81, so a contiguous
    hourly span collapses to the coarse steps that actually exist.
    """
    return sorted({_snap_to_icon_eu_grid(h, variant) for h in hours})


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
        # All-or-nothing (#478). A whole-column blob is cached under a key that
        # says nothing about how many levels are inside it, so returning a
        # partial one would have it served as complete for the rest of the run's
        # TTL — the exact ambiguity the per-level layout exists to close. Drop it
        # instead and let the next call refetch the variable; correctness is
        # worth more here than the re-download, and the warning names the
        # shortfall so a persistently missing level is visible rather than
        # silently baked into the cache.
        if downloaded == len(levels):
            result[var] = bytes(buf)
            logger.info(
                "%s f%03d %s: downloaded %d/%d levels (%.1f KB)%s",
                variant.slug, forecast_hour, var, downloaded, len(levels),
                len(buf) / 1024, _format_failure_summary(failures),
            )
        elif buf:
            logger.warning(
                "%s f%03d %s: incomplete column (%d/%d levels) — discarding, "
                "not caching a partial blob%s",
                variant.slug, forecast_hour, var, downloaded, len(levels),
                _format_failure_summary(failures),
            )
        else:
            logger.warning(
                "%s f%03d %s: all %d files failed%s",
                variant.slug, forecast_hour, var, total_failed,
                _format_failure_summary(failures),
            )

    return result


def fetch_icon_eu_per_level(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    levels: list[int],
    variables: list[str],
    session: requests.Session | None = None,
    max_workers: int = MAX_DOWNLOAD_WORKERS,
    variant: IconVariant = ICON_EU,
) -> dict[tuple[str, int], bytes]:
    """Download ICON model-level files individually, keyed by (variable, level).

    Unlike :func:`fetch_icon_eu_per_variable` (which concatenates all levels of
    a variable into ONE blob), this keeps every level separate so the caller can
    cache each ``(variable, level)`` under its own key — the per-level cache
    layout (#469 phase 1), which keeps a partial download *detectable* instead
    of cached as complete. All requested files download on a single pool,
    exactly like the per-variable path.

    Returns:
        ``{(variable, level): decompressed_grib2_bytes}`` for each file that
        downloaded successfully; failures are logged and omitted (the caller
        top-ups the missing levels on the next pass, same as a per-variable
        failure leaves that variable uncached).
    """
    sess = session or requests.Session()
    targets = [(var, level) for var in variables for level in levels]

    result: dict[tuple[str, int], bytes] = {}
    failures: dict[int | str, int] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _download_one_file,
                icon_eu_file_url(
                    init_date, init_hour, forecast_hour, level, var, variant,
                ),
                sess,
            ): (var, level)
            for (var, level) in targets
        }
        for future in as_completed(futures):
            var, level = futures[future]
            data, status = future.result()
            if data is not None:
                result[(var, level)] = data
            else:
                failures[status] = failures.get(status, 0) + 1

    total_failed = sum(failures.values())
    total_bytes = sum(len(b) for b in result.values())
    if result:
        logger.info(
            "%s f%03d per-level: downloaded %d/%d files (%.1f KB)%s",
            variant.slug, forecast_hour, len(result), len(result) + total_failed,
            total_bytes / 1024, _format_failure_summary(failures),
        )
    elif total_failed:
        logger.warning(
            "%s f%03d per-level: all %d files failed%s",
            variant.slug, forecast_hour, total_failed,
            _format_failure_summary(failures),
        )

    return result
