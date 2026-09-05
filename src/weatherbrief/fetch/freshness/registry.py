"""Per-(model, source) schedule registry.

Pure config + pure functions — no I/O.  Computes when each (model, source)
pair's next run should be expected, and how far the run's forecast horizon
reaches.  Used by both the freshness loop (to decide when to poll) and the
freshness check (to decide whether a newer run actually covers the flight).

Each ``SourceConfig`` is keyed by ``"{model}:{source}"`` (e.g. ``"ecmwf:direct"``,
``"gfs:openmeteo"``).  Cycles are UTC hours of day; ``delivery_offset`` is the
expected wallclock lag from cycle init to data readiness.

``run_horizon`` may differ per cycle (e.g. ECMWF 00/12 reach 168h; the short
cut-off 06/18 cycles reach 144h; ICON-EU main cycles 120h vs intermediate 78h).
Note that a cycle's *horizon* is not where its hourly step cadence ends — for
ECMWF that is init+90h, after which the manifest thins to 3-hourly and then
6-hourly, all still within the same run.

**Two schedule kinds.**  Everything above describes a *cycle* source: a
forecast model that runs at fixed hours of the day and reaches some horizon
into the future.  Observed streams (#574) are neither — OPERA radar publishes
a composite every five minutes and it describes the past, not the future.
A ``cycles`` tuple cannot express "every five minutes" (it is hours-of-day),
so :class:`SourceConfig` carries an optional ``interval`` instead, and the
scheduling functions below branch on which one is set.  An interval source's
``horizon`` is zero by construction: an observation forecasts nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class SourceConfig:
    """Schedule + horizon + descriptive config for one (model, source) pair.

    Schedule fields (``cycles``, ``delivery_offset``, ``horizon``, retry
    knobs, ``readiness_check``) drive the freshness loop.  Descriptive
    fields (``model_label`` through ``description``) feed the public
    ``/api/data-sources`` endpoint and the help-page data-sources table —
    they are static metadata about *what this source is*, separate from
    dynamic per-cycle marker state.

    Attributes:
        key: Stable identifier ``"{model}:{source}"``.
        cycles: UTC hours-of-day at which the source publishes runs.
        delivery_offset: Expected lag from cycle start to data readiness.
            May be a single ``timedelta`` (uniform) or a per-cycle dict.
        horizon: Forecast horizon.  Uniform ``timedelta`` or per-cycle dict.
        retry_interval: Base interval used for the *first* slip retry.
            Subsequent retries grow exponentially (×2 per slip) up to
            :attr:`max_retry_interval` — so we don't keep hammering a
            slow-publishing source every 10 min for 6 h.
        max_retry_interval: Ceiling for the exponential backoff bump.
        max_slip_retries: After this many slip bumps, jump ``next_expected``
            forward to the *next* scheduled cycle's expected delivery time
            (avoids polling forever on a skipped cycle).  With the default
            backoff schedule, 8 slips ≈ 6 h before cycle-jump.
        readiness_check: Symbolic name of the check_source dispatch target.
            Resolved by :mod:`sources`.
        interval: Publication cadence for an *interval* source — an observed
            stream that publishes every N minutes rather than at fixed hours.
            Mutually exclusive with ``cycles``.
        env_gate: Name of an environment variable that must be truthy for the
            source to appear in the freshness loop and the public catalog.
        subset_env: Name of a comma-separated allowlist variable which, when
            set, must contain this source's key prefix for it to be active.
        model_label: Display label for the underlying NWP model
            (e.g. "ICON-EU", "ICON-Global", "ECMWF IFS").  Distinct from
            the pack-model name in the key prefix — the same pack-model
            ``"icon"`` is served by both ``icon_eu:dwd`` and
            ``icon:openmeteo`` but those represent different variants of
            the ICON family.
        provider_label: Human-readable data provider ("DWD", "NOAA",
            "ECMWF", "Open-Meteo").  Single source of truth for the
            UI — replaces the duplicate dict in ``api/packs.py``.
        provider_url: Optional documentation/landing URL for the provider.
        role: What this source contributes to the briefing.  One of
            ``"primary-sounding"`` (full upper-air sounding replacement),
            ``"cloud-enrichment"`` (cloud microphysics + diagnostics
            patched onto an Open-Meteo base), ``"surface-base"``
            (Open-Meteo surface fields under a direct-GRIB sounding),
            or ``"primary"`` (Open-Meteo-only model — surface + sounding
            from the same feed).
        resolution: Spatial resolution string for display
            ("0.25° (~25 km)", "~6.5 km", etc.).
        coverage: Geographic coverage string for display
            ("Global", "Europe (29.5–70.5°N, 23.5°W–62.5°E)").
        pressure_levels: Number of pressure levels delivered by this
            source.  ``None`` for sources that don't deliver an upper-air
            sounding (e.g. cloud-enrichment-only).
        description: One-sentence description of what this source
            contributes — surfaced in the help-page table.
    """

    key: str
    cycles: tuple[int, ...] = ()
    delivery_offset: timedelta | dict[int, timedelta] = timedelta(0)
    horizon: timedelta | dict[int, timedelta] = timedelta(0)
    retry_interval: timedelta = timedelta(minutes=10)
    max_retry_interval: timedelta = timedelta(hours=1)
    max_slip_retries: int = 8
    readiness_check: str = ""

    # Publication cadence for an *interval* source (observed streams).
    # Mutually exclusive with ``cycles``: a source publishes either at fixed
    # hours of the day or on a fixed period, never both.
    interval: timedelta | None = None
    # Optional environment gate.  A source named here is described by the
    # registry but only takes part in the freshness loop and the public
    # catalog while the variable is truthy — so a deployment without EUMETSAT
    # credentials does not show a permanently-red row for a feature it never
    # turned on.
    env_gate: str = ""
    # Optional comma-separated allowlist variable.  When set and non-empty,
    # this source only counts as active if the key's prefix appears in it —
    # so a partial deployment does not advertise the sources it left off.
    subset_env: str = ""

    model_label: str = ""
    provider_label: str = ""
    provider_url: str = ""
    role: str = "primary"
    resolution: str = ""
    coverage: str = ""
    pressure_levels: int | None = None
    description: str = ""

    def __post_init__(self) -> None:
        """Catch malformed schedules at registry-construction time.

        A dict-shaped ``horizon`` or ``delivery_offset`` must cover *every*
        configured cycle hour — otherwise consumers like
        ``catalog._per_cycle_hours`` would ``KeyError`` and surface as an
        opaque 500 from ``/api/data-sources``.  Fail loudly at import.
        """
        if bool(self.cycles) == bool(self.interval):
            raise ValueError(
                f"SourceConfig({self.key!r}): set exactly one of cycles "
                f"(hours-of-day) or interval (fixed period)"
            )
        if self.interval is not None:
            if self.interval <= timedelta(0):
                raise ValueError(
                    f"SourceConfig({self.key!r}): interval must be positive"
                )
            if isinstance(self.horizon, dict) or isinstance(self.delivery_offset, dict):
                raise ValueError(
                    f"SourceConfig({self.key!r}): an interval source has no "
                    f"cycles to key a per-cycle horizon/offset on"
                )
        for field_name, value in (
            ("horizon", self.horizon),
            ("delivery_offset", self.delivery_offset),
        ):
            if isinstance(value, dict):
                missing = [h for h in self.cycles if h not in value]
                if missing:
                    raise ValueError(
                        f"SourceConfig({self.key!r}): {field_name} dict "
                        f"missing cycle hours {missing} "
                        f"(cycles={list(self.cycles)})"
                    )

    @property
    def schedule_kind(self) -> str:
        """``"interval"`` for observed streams, ``"cycle"`` for NWP runs."""
        return "interval" if self.interval is not None else "cycle"

    @property
    def is_active(self) -> bool:
        """False when this source is not switched on for the deployment.

        Two axes, both of which have to say yes:

        1. ``env_gate`` — the feature is enabled at all.
        2. ``subset_env`` — this *particular* source is in the enabled subset.

        The second is not decoration.  A deployment that runs radar without
        EUMETSAT credentials (``WB_OBSERVED_SOURCES=opera_dbzh,opera_rate``)
        is the documented half-a-feature case, and without this check the two
        satellite rows would stay active, be probed by the freshness loop
        against a frame store that will never hold them, and show permanently
        red on the help page — the exact outcome ``env_gate`` was added to
        prevent, reached by the subset axis instead of the all-off one.
        """
        if not self.env_gate:
            return True
        if os.environ.get(self.env_gate, "").strip() not in ("1", "true", "yes"):
            return False
        if not self.subset_env:
            return True
        subset = os.environ.get(self.subset_env, "").strip()
        if not subset:
            # Unset means "all of them", matching enabled_sources().
            return True
        wanted = {item.strip() for item in subset.split(",") if item.strip()}
        # The registry key is "{frame_source}:{provider}"; the frame source is
        # what WB_OBSERVED_SOURCES names.
        return self.key.split(":", 1)[0] in wanted

    def slip_bump(self, slip_count: int) -> timedelta:
        """Return the next-expected bump for the ``slip_count``-th slip.

        Exponential backoff capped at :attr:`max_retry_interval`.  ``slip_count``
        is 1-based — i.e. the first slip uses :attr:`retry_interval` exactly.
        """
        if slip_count <= 0:
            return self.retry_interval
        bump = self.retry_interval * (2 ** (slip_count - 1))
        return min(bump, self.max_retry_interval)

    def offset_for(self, cycle: int) -> timedelta:
        if isinstance(self.delivery_offset, dict):
            return self.delivery_offset[cycle]
        return self.delivery_offset

    def horizon_for(self, cycle: int) -> timedelta:
        if isinstance(self.horizon, dict):
            return self.horizon[cycle]
        return self.horizon


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# ECMWF: empirically +6h27m–6h37m across 14 days; +6h40m gives 3min margin
# and works for both long (00/12 = 168h) and medium-only (06/18) cycles.
_ECMWF_OFFSET = timedelta(hours=6, minutes=40)
# Horizon per cycle, read off the delivery manifest (``delivery_config.json``
# keyed by cycle hour): 00/12z carry the long tail to 168h, 06/18z stop at
# 144h.  Verified against the files actually on disk — the 2026-07-28 06z run
# delivers a 144h step.
#
# This used to say 90h for 06/18z, reasoning from where the *hourly* step
# cadence ends (init+90h, after which the manifest goes 3-hourly).  That is a
# cadence boundary, not a horizon: it under-reported 06/18z coverage by 54h,
# so anything gating on ``horizon_for(6)`` fell back to another model 2¼ days
# early.  Cadence and horizon are separate facts; don't re-derive one from the
# other.
_ECMWF_HORIZON: dict[int, timedelta] = {
    0: timedelta(hours=168),
    6: timedelta(hours=144),
    12: timedelta(hours=168),
    18: timedelta(hours=144),
}

# GFS: PUBLISH_DELAY_HOURS = 5 in grib_fetch.py; horizon 384h (16 days).
_GFS_NOAA_OFFSET = timedelta(hours=5)
_GFS_NOAA_HORIZON = timedelta(hours=384)

# HRRR (#457): full wrfprs file set completes ~45–60 min after init
# (HRRR_PUBLISH_DELAY_HOURS = 1.25 in hrrr_fetch.py). Only the EXTENDED
# cycles (00/06/12/18z, 48h horizon) are tracked here even though HRRR runs
# hourly: enrichment picks the freshest covering run opportunistically, but
# tracking every hourly cycle would flag short-lead US packs stale every
# hour and churn auto-refresh. The 6-hourly extended cadence matches how
# much a re-brief actually changes the picture.
_HRRR_OFFSET = timedelta(hours=1, minutes=15)
_HRRR_HORIZON = timedelta(hours=48)

# ICON-EU: ICON_EU_PUBLISH_DELAY_HOURS = 3; main cycles {0,6,12,18} reach
# 120h, intermediate {3,9,15,21} reach 78h.  See icon_eu_fetch.py:31-40.
_ICON_EU_OFFSET = timedelta(hours=3)
_ICON_EU_MAIN = {0, 6, 12, 18}
_ICON_EU_HORIZON: dict[int, timedelta] = {
    h: (timedelta(hours=120) if h in _ICON_EU_MAIN else timedelta(hours=78))
    for h in (0, 3, 6, 9, 12, 15, 18, 21)
}

# ICON-D2: 2.2 km convection-permitting, 8 runs/day (every 3h), all cycles
# hourly to 48h. Publication delay ~1–2h — 2h with margin (ICON_D2.publish_
# delay_hours in icon_eu_fetch.py). Used in place of ICON-EU for the icon slot
# on short, central-European flights (issue #456).
_ICON_D2_OFFSET = timedelta(hours=2)
_ICON_D2_HORIZON = timedelta(hours=48)

# Open-Meteo offsets — calibrated against meta.json observation 2026-05-03.
# Open-Meteo republishes other providers' models with notable lag; the figures
# in issue #108's table (sourced from OM docs) underestimate real delivery.
# Bumped here with margin over observed delays so the marker doesn't slip
# every cycle.  Drift detection (deque of last 20 observations on each marker)
# will surface further drift if real-world publish times keep changing.
_OM_GFS_OFFSET = timedelta(hours=6, minutes=45)      # observed 6h37m
_OM_ECMWF_OFFSET = timedelta(hours=8)                # observed 7h30m
_OM_ICON_OFFSET = timedelta(hours=4, minutes=30)     # observed 4h17m
_OM_ARPEGE_OFFSET = timedelta(hours=4, minutes=30)   # observed 4h00m
_OM_UKMO_OFFSET = timedelta(hours=8, minutes=30)     # observed 8h19m
# GEM (Canadian ECCC global) is the slowest OM-republished model we track;
# margin lets us avoid slip storms.  Recalibrate from /admin/freshness/markers
# once we have a few days of observations.
_OM_GEM_OFFSET = timedelta(hours=8)

# Observed streams (#574).  These are delivery lags from a frame's nominal
# valid time to its appearance in the cache, not forecast cut-offs — kept in
# step with weatherbrief.observed.collect, which uses the same figures to
# decide when a frame is worth asking for.
_OPERA_DELIVERY_OFFSET = timedelta(minutes=4)
_EUMETSAT_DELIVERY_OFFSET = timedelta(minutes=5)


# The full-resolution ECMWF GRIB feed. Its 168h horizon marks the boundary
# beyond which only global (Open-Meteo) models remain — used for the long-range
# digest regime and the "full briefing from <date>" coverage milestone. Shared
# so callers don't duplicate the registry key string.
ECMWF_GRIB_SOURCE = "ecmwf:direct"


SOURCE_REGISTRY: dict[str, SourceConfig] = {
    ECMWF_GRIB_SOURCE: SourceConfig(
        key="ecmwf:direct",
        cycles=(0, 6, 12, 18),
        delivery_offset=_ECMWF_OFFSET,
        horizon=_ECMWF_HORIZON,
        readiness_check="ecmwf_direct",
        model_label="ECMWF IFS",
        provider_label="ECMWF",
        provider_url="https://www.ecmwf.int/",
        role="primary-sounding",
        resolution="0.25° (~25 km)",
        coverage="Europe + US",
        pressure_levels=25,
        description=(
            "Direct GRIB delivery from ECMWF via ECPDS. Provides the full "
            "25-level upper-air sounding (t, r, u, v, w, gh, cc, clwc, ciwc) "
            "plus cloud diagnostics over Europe + US. 00/12Z cycles reach "
            "168h; the short cut-off 06/18Z cycles reach 144h. Beyond this "
            "horizon we fall back to ecmwf:openmeteo."
        ),
    ),
    "gfs:noaa": SourceConfig(
        key="gfs:noaa",
        cycles=(0, 6, 12, 18),
        delivery_offset=_GFS_NOAA_OFFSET,
        horizon=_GFS_NOAA_HORIZON,
        readiness_check="gfs_noaa",
        model_label="GFS",
        provider_label="NOAA",
        provider_url="https://www.nco.ncep.noaa.gov/pmb/products/gfs/",
        role="cloud-enrichment",
        resolution="0.25° (~25 km)",
        coverage="Global",
        pressure_levels=None,
        description=(
            "Direct GRIB2 from NOAA S3 (noaa-gfs-bdp-pds). Patches cloud "
            "liquid water, ice mixing ratio and cloud diagnostics (ceiling, "
            "low/mid/high covers, convective base/top) onto the 28-level "
            "Open-Meteo GFS sounding. 16-day horizon."
        ),
    ),
    "hrrr:noaa": SourceConfig(
        key="hrrr:noaa",
        cycles=(0, 6, 12, 18),
        delivery_offset=_HRRR_OFFSET,
        horizon=_HRRR_HORIZON,
        readiness_check="hrrr_noaa",
        model_label="HRRR",
        provider_label="NOAA",
        provider_url="https://rapidrefresh.noaa.gov/hrrr/",
        role="primary-sounding",
        resolution="3 km",
        coverage="CONUS (Lambert grid)",
        pressure_levels=35,
        description=(
            "Direct GRIB2 from NOAA S3 (noaa-hrrr-bdp-pds). 3 km convection-"
            "allowing, radar-assimilating model — full sounding replacement "
            "(T, Td, RH, wind, VVEL, HGT, CLMR/CIMIXR on 25-hPa levels) plus "
            "instantaneous cloud diagnostics and mixed-layer CAPE/CIN. Serves "
            "the gfs slot in place of GFS when the whole route fits CONUS and "
            "the flight window is within the run's horizon; otherwise plain "
            "GFS. Runs hourly, but only 00/06/12/18Z extend to 48h (others "
            "18h) — freshness tracks the extended cycles."
        ),
    ),
    "icon_eu:dwd": SourceConfig(
        key="icon_eu:dwd",
        cycles=(0, 3, 6, 9, 12, 15, 18, 21),
        delivery_offset=_ICON_EU_OFFSET,
        horizon=_ICON_EU_HORIZON,
        readiness_check="icon_eu_dwd",
        model_label="ICON-EU",
        provider_label="DWD",
        provider_url="https://www.dwd.de/EN/ourservices/opendata/opendata.html",
        role="primary-sounding",
        resolution="~6.5 km",
        coverage="Europe (29.5–70.5°N, 23.5°W–62.5°E)",
        pressure_levels=40,
        description=(
            "Direct GRIB from DWD opendata. 40 model levels interpolated to "
            "pressure levels — full sounding replacement plus cloud "
            "microphysics and diagnostics. 8 cycles/day (every 3h); main "
            "cycles 00/06/12/18 reach 120h, intermediate cycles reach 78h."
        ),
    ),
    "icon_d2:dwd": SourceConfig(
        key="icon_d2:dwd",
        cycles=(0, 3, 6, 9, 12, 15, 18, 21),
        delivery_offset=_ICON_D2_OFFSET,
        horizon=_ICON_D2_HORIZON,
        readiness_check="icon_d2_dwd",
        model_label="ICON-D2",
        provider_label="DWD",
        provider_url="https://www.dwd.de/EN/ourservices/opendata/opendata.html",
        role="primary-sounding",
        resolution="~2.2 km",
        coverage="Central Europe (43.18–58.08°N, 3.94°W–20.34°E)",
        pressure_levels=50,
        description=(
            "Direct GRIB from DWD ICON-D2 opendata (2.2 km, convection-"
            "permitting). Model levels 16–65 interpolated to pressure levels "
            "— full sounding replacement plus cloud microphysics and "
            "diagnostics. 8 cycles/day (every 3h), hourly to 48h. Serves the "
            "icon slot in place of ICON-EU when the whole route fits the D2 "
            "domain and the flight window is within 48h; otherwise ICON-EU. "
            "No deep-convection scheme: convective base/top and rain_con are "
            "absent by design (see #462 for the native replacements)."
        ),
    ),
    "gfs:openmeteo": SourceConfig(
        key="gfs:openmeteo",
        cycles=(0, 6, 12, 18),
        delivery_offset=_OM_GFS_OFFSET,
        horizon=timedelta(days=16),
        readiness_check="om_meta",
        model_label="GFS",
        provider_label="Open-Meteo",
        provider_url="https://open-meteo.com/en/docs/gfs-api",
        role="primary",
        resolution="0.25° (~25 km)",
        coverage="Global",
        pressure_levels=28,
        description=(
            "Open-Meteo's GFS seamless feed (28 pressure levels, surface + "
            "upper-air). Primary GFS sounding source; gfs:noaa enriches "
            "cloud microphysics on top."
        ),
    ),
    # Open-Meteo only effectively publishes ECMWF 00/12 main runs in time;
    # 06/18 are bc-runs that lag heavily — see issue #100.  We track only the
    # main cycles so the marker doesn't bounce on bc-run shuffles.
    "ecmwf:openmeteo": SourceConfig(
        key="ecmwf:openmeteo",
        cycles=(0, 12),
        delivery_offset=_OM_ECMWF_OFFSET,
        horizon=timedelta(days=10),
        readiness_check="om_meta",
        model_label="ECMWF IFS",
        provider_label="Open-Meteo",
        provider_url="https://open-meteo.com/en/docs/ecmwf-api",
        role="surface-base",
        resolution="0.25° (~25 km)",
        coverage="Global",
        pressure_levels=13,
        description=(
            "Open-Meteo's ECMWF IFS feed (13 pressure levels). Provides "
            "surface fields under the direct-GRIB sounding inside the "
            "ECMWF coverage area, and is the full fallback outside it or "
            "beyond ~7 days. Only 00/12Z tracked — 06/18 bc-runs lag heavily."
        ),
    ),
    # Open-Meteo's meta.json reports update_interval_seconds=21600 (6h) for
    # ICON, even though DWD itself runs ICON on a 3h cycle.  OM only
    # republishes the 6-hourly main runs (0/6/12/18Z) — tracking 3h here
    # would slip every other cycle for no benefit.
    "icon:openmeteo": SourceConfig(
        key="icon:openmeteo",
        cycles=(0, 6, 12, 18),
        delivery_offset=_OM_ICON_OFFSET,
        horizon=timedelta(days=7),
        readiness_check="om_meta",
        model_label="ICON-Global",
        provider_label="Open-Meteo",
        provider_url="https://open-meteo.com/en/docs/dwd-api",
        role="primary",
        resolution="~11 km global, ~7 km over Europe (seamless)",
        coverage="Global",
        pressure_levels=19,
        description=(
            "Open-Meteo's ICON seamless feed (19 pressure levels). Serves "
            "the global ICON-Global variant; over Europe the regional "
            "ICON-EU GRIB from DWD takes over as the primary sounding, and "
            "ICON-D2 (2.2 km) supersedes it on short central-European routes."
        ),
    ),
    "meteofrance:openmeteo": SourceConfig(
        key="meteofrance:openmeteo",
        cycles=(0, 6, 12, 18),
        delivery_offset=_OM_ARPEGE_OFFSET,
        horizon=timedelta(days=6),
        readiness_check="om_meta",
        model_label="Météo-France ARPEGE",
        provider_label="Open-Meteo",
        provider_url="https://open-meteo.com/en/docs/meteofrance-api",
        role="primary",
        resolution="~25 km global, ~11 km EU, ~2.5 km France (seamless)",
        coverage="Global (best over France/Europe)",
        pressure_levels=19,
        description=(
            "Open-Meteo's Météo-France seamless feed (19 pressure levels). "
            "Only fetched for routes containing a French (LF…) ICAO."
        ),
    ),
    "ukmo:openmeteo": SourceConfig(
        key="ukmo:openmeteo",
        cycles=(0, 6, 12, 18),
        delivery_offset=_OM_UKMO_OFFSET,
        horizon=timedelta(days=7),
        readiness_check="om_meta",
        model_label="UK Met Office",
        provider_label="Open-Meteo",
        provider_url="https://open-meteo.com/en/docs/ukmo-api",
        role="primary",
        resolution="~10 km global, ~2 km over UK (seamless)",
        coverage="Global (best over UK/Europe)",
        pressure_levels=20,
        description=(
            "Open-Meteo's UK Met Office seamless feed (20 pressure levels). "
            "Only fetched for routes containing a UK (EG…) ICAO."
        ),
    ),
    "gem:openmeteo": SourceConfig(
        key="gem:openmeteo",
        cycles=(0, 6, 12, 18),
        delivery_offset=_OM_GEM_OFFSET,
        horizon=timedelta(days=10),
        readiness_check="om_meta",
        model_label="GEM",
        provider_label="Open-Meteo",
        provider_url="https://open-meteo.com/en/docs/gem-api",
        role="primary",
        resolution="~15 km global, ~10 km over North America (seamless)",
        coverage="Global (best over North America)",
        pressure_levels=20,
        description=(
            "Open-Meteo's GEM seamless feed (20 pressure levels), from "
            "Environment and Climate Change Canada. Only fetched for routes "
            "containing a North-American (K/C/P…) ICAO."
        ),
    ),
    # --- Observed streams (#574) -------------------------------------------
    # Interval-scheduled, zero horizon: these describe the past.  They are
    # here so the same freshness display and the same data-sources table cover
    # observations as well as forecasts — a pilot reading "radar 6 min old"
    # next to "ECMWF 12Z" is reading one surface, not two.  Each is gated on
    # WB_OBSERVED_ENABLED so a deployment without the collector shows nothing
    # rather than a permanently-red row.
    "opera_dbzh:eumetnet": SourceConfig(
        key="opera_dbzh:eumetnet",
        interval=timedelta(minutes=5),
        delivery_offset=_OPERA_DELIVERY_OFFSET,
        readiness_check="observed_frames",
        env_gate="WB_OBSERVED_ENABLED",
        subset_env="WB_OBSERVED_SOURCES",
        retry_interval=timedelta(minutes=2),
        max_retry_interval=timedelta(minutes=15),
        model_label="OPERA composite",
        provider_label="EUMETNET OPERA",
        provider_url="https://www.eumetnet.eu/activities/observations-programme/current-activities/opera/",
        role="observed",
        resolution="2 km",
        coverage="Europe (radar network footprint; ~half the grid has no coverage)",
        description=(
            "Pan-European weather-radar composite reflectivity (DBZH), "
            "published every 5 minutes from contributing radar scans in the "
            "preceding 10-minute window, plus delivery lag. This is not a "
            "temporal maximum of earlier composite frames. Roughly half the grid carries no radar "
            "coverage at all — reported distinctly from 'looked, saw nothing'."
        ),
    ),
    "opera_rate:eumetnet": SourceConfig(
        key="opera_rate:eumetnet",
        interval=timedelta(minutes=15),
        delivery_offset=_OPERA_DELIVERY_OFFSET,
        readiness_check="observed_frames",
        env_gate="WB_OBSERVED_ENABLED",
        subset_env="WB_OBSERVED_SOURCES",
        retry_interval=timedelta(minutes=3),
        max_retry_interval=timedelta(minutes=30),
        model_label="OPERA composite",
        provider_label="EUMETNET OPERA",
        provider_url="https://www.eumetnet.eu/activities/observations-programme/current-activities/opera/",
        role="observed",
        resolution="2 km",
        coverage="Europe (radar network footprint)",
        description=(
            "Pan-European radar surface rain rate (RATE) in mm/h, published "
            "every 15 minutes. Same coverage caveat as the reflectivity "
            "composite."
        ),
    ),
    "eumetsat_li:eumetsat": SourceConfig(
        key="eumetsat_li:eumetsat",
        interval=timedelta(minutes=10),
        delivery_offset=_EUMETSAT_DELIVERY_OFFSET,
        readiness_check="observed_frames",
        env_gate="WB_OBSERVED_ENABLED",
        subset_env="WB_OBSERVED_SOURCES",
        retry_interval=timedelta(minutes=3),
        max_retry_interval=timedelta(minutes=30),
        model_label="MTG Lightning Imager",
        provider_label="EUMETSAT",
        provider_url="https://navigator.eumetsat.int/product/EO:EUM:DAT:0691",
        role="observed",
        resolution="~4.5 km",
        coverage="MTG full disc (Europe, Africa, Atlantic)",
        description=(
            "Total lightning (intra-cloud and cloud-to-ground) as detected "
            "flashes, every 10 minutes. A point product: the imager sees the "
            "whole disc, so 'no flashes' is an observation rather than a gap."
        ),
    ),
    "eumetsat_ctth:eumetsat": SourceConfig(
        key="eumetsat_ctth:eumetsat",
        interval=timedelta(minutes=10),
        delivery_offset=_EUMETSAT_DELIVERY_OFFSET,
        readiness_check="observed_frames",
        env_gate="WB_OBSERVED_ENABLED",
        subset_env="WB_OBSERVED_SOURCES",
        retry_interval=timedelta(minutes=3),
        max_retry_interval=timedelta(minutes=30),
        model_label="MTG FCI L2 CTTH",
        provider_label="EUMETSAT",
        provider_url="https://navigator.eumetsat.int/product/EO:EUM:DAT:0681",
        role="observed",
        resolution="~2 km",
        coverage="MTG full disc (Europe, Africa, Atlantic)",
        description=(
            "Satellite cloud-top height, every 10 minutes. Committed to one "
            "top per pixel, so it is sampled as a histogram over the corridor "
            "rather than a single value, and each pixel is placed by its own "
            "parallax correction (~52 km median at European latitudes)."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def _floor_to_cycle(dt: datetime, cfg: SourceConfig) -> datetime:
    """Return the most recent publication slot at-or-before ``dt``.

    For a cycle source that is the latest configured hour-of-day; for an
    interval source it is ``dt`` floored to the cadence, anchored on midnight
    so slots line up with the provider's own naming (OPERA composites are
    named ``T1405``, not ``T1403``).
    """
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if cfg.interval is not None:
        elapsed = dt - midnight
        slots = int(elapsed // cfg.interval)
        return midnight + cfg.interval * slots
    candidates = [midnight + timedelta(hours=h) for h in cfg.cycles]
    candidates += [c - timedelta(days=1) for c in candidates]
    valid = [c for c in candidates if c <= dt]
    return max(valid)


def _next_cycle_init(dt: datetime, cfg: SourceConfig) -> datetime:
    """Return the earliest publication slot strictly after ``dt``."""
    if cfg.interval is not None:
        return _floor_to_cycle(dt, cfg) + cfg.interval
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [midnight + timedelta(hours=h) for h in cfg.cycles]
    candidates += [c + timedelta(days=1) for c in candidates]
    future = [c for c in candidates if c > dt]
    return min(future)


def next_run_after(source: str, current_init: datetime) -> datetime:
    """Wallclock time at which the next run *after* ``current_init`` is expected.

    Handles cycles within the same day and rollover to next day.  The result
    is the next cycle's init time plus that cycle's delivery offset.
    """
    cfg = SOURCE_REGISTRY[source]
    next_init = _next_cycle_init(current_init, cfg)
    return next_init + cfg.offset_for(next_init.hour)


def next_cycle_init_after(source: str, init: datetime) -> datetime:
    """Return the next cycle init (without offset) strictly after ``init``.

    Companion to :func:`next_run_after`, exposed so callers can reason
    about cycles separately from delivery wallclocks.  Used by the
    marker store's slip-cap path to identify the cycle being skipped.
    """
    cfg = SOURCE_REGISTRY[source]
    return _next_cycle_init(init, cfg)


def run_horizon(source: str, init: datetime) -> timedelta:
    """Forecast horizon (timedelta from init) for the run started at ``init``."""
    cfg = SOURCE_REGISTRY[source]
    return cfg.horizon_for(init.hour)


def max_horizon(source: str) -> timedelta:
    """Largest forecast horizon any cycle of ``source`` ever reaches.

    For sources with a per-cycle horizon dict (e.g. ECMWF 00/12 = 168h vs
    06/18 = 144h) this is the maximum; for uniform-horizon sources it is that
    single value.
    """
    cfg = SOURCE_REGISTRY[source]
    return max(cfg.horizon_for(h) for h in cfg.cycles)


def next_full_horizon_run(source: str, after: datetime) -> tuple[datetime, datetime]:
    """Return ``(init, expected_delivery)`` of the next *full-horizon* run.

    A "full-horizon" run is one whose forecast horizon equals
    :func:`max_horizon` for the source — i.e. it extends the forecast as far
    as the source ever does.  For ECMWF the 00/12Z cycles reach 168h while the
    06/18Z cycles reach only 144h, so only 00/12Z qualify; for uniform-horizon
    sources every cycle qualifies.

    The returned run is the one with the earliest *expected delivery wallclock*
    strictly after ``after`` (delivery = ``init + delivery_offset``).  This is
    what the email scheduler waits on: a run that lands shortly after a regular
    slot and would replace it with a fresher, equally-long forecast.

    Waiting for a *medium*-only cycle far out would give a shorter horizon
    (actively worse), so those are excluded here by construction.
    """
    cfg = SOURCE_REGISTRY[source]
    full_h = max_horizon(source)
    base = after.replace(hour=0, minute=0, second=0, microsecond=0)
    best: tuple[datetime, datetime] | None = None
    # A run's delivery offset can be several hours, so a cycle init on the
    # previous calendar day may still deliver after ``after``; scan a small
    # window around it and keep the earliest qualifying delivery.
    for day in (-1, 0, 1, 2):
        for hour in cfg.cycles:
            init = base + timedelta(days=day, hours=hour)
            if cfg.horizon_for(init.hour) < full_h:
                continue
            delivery = init + cfg.offset_for(init.hour)
            if delivery > after and (best is None or delivery < best[1]):
                best = (init, delivery)
    if best is None:  # pragma: no cover - cycles always include a full run
        raise ValueError(f"no full-horizon cycle configured for {source!r}")
    return best


def first_full_coverage(source: str, target: datetime) -> tuple[datetime, datetime]:
    """Return ``(init, expected_delivery)`` of the earliest *full-horizon* run
    of ``source`` whose forecast reaches ``target``.

    This answers "when does high-resolution guidance first cover a far-out
    flight?".  For a flight 10 days out, ECMWF direct GRIB (168h = 7 days on
    its 00/12Z full-horizon cycles) cannot see it yet; this returns the first
    00/12Z run — and the wallclock it is expected to be delivered — whose
    ``init + 168h`` first includes ``target``.  The caller turns that delivery
    time into a "more detail expected from <date>" message for the long-range
    digest.

    Only full-horizon cycles qualify (see :func:`next_full_horizon_run`): a
    medium-only cycle would reach a shorter horizon and so cover ``target``
    later, not sooner.
    """
    cfg = SOURCE_REGISTRY[source]
    full_h = max_horizon(source)
    full_cycles = sorted(h for h in cfg.cycles if cfg.horizon_for(h) == full_h)
    # The earliest run reaching ``target`` is the first full-horizon cycle init
    # at-or-after ``target - full_horizon``.
    earliest_init = target - full_h
    base = earliest_init.replace(hour=0, minute=0, second=0, microsecond=0)
    for day in (0, 1):
        for hour in full_cycles:
            init = base + timedelta(days=day, hours=hour)
            if init >= earliest_init:
                return init, init + cfg.offset_for(init.hour)
    raise ValueError(  # pragma: no cover - full_cycles is always non-empty
        f"no full-horizon cycle configured for {source!r}"
    )


def cycle_init_for(source: str, dt: datetime) -> datetime:
    """Return the latest cycle init at-or-before ``dt`` for ``source``."""
    cfg = SOURCE_REGISTRY[source]
    return _floor_to_cycle(dt, cfg)


def expected_delivery_for_init(source: str, init: datetime) -> datetime:
    """Return the wallclock time at which ``init`` is expected to be ready."""
    cfg = SOURCE_REGISTRY[source]
    return init + cfg.offset_for(init.hour)


def initial_marker_for(source: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Bootstrap (init, next_expected) for a source at startup.

    Picks the most recent cycle whose expected delivery is at-or-before
    ``now`` (so the loop's first dynamic check has a plausible "current"
    init to compare against).  Falls back to one cycle prior if the latest
    cycle hasn't been delivered yet.
    """
    now = now or datetime.now(timezone.utc)
    cfg = SOURCE_REGISTRY[source]
    init = _floor_to_cycle(now, cfg)
    if init + cfg.offset_for(init.hour) > now:
        # current cycle not yet expected to be ready — use prior cycle
        init = _floor_to_cycle(init - timedelta(seconds=1), cfg)
    next_expected = next_run_after(source, init)
    return init, next_expected
