"""ECMWF must populate the model-native convective ingredient columns (#581).

ECMWF has the richest convective GRIB of any model — ``mlcape100``, ``mucape``,
``mlcin100``, ``hcct``, ``cp``, ``kx``, ``totalx``, all decoded by
``_ECMWF_CLOUD_DIAG_FIELD_MAP`` — yet it populated *none* of the ingredient
columns added in #565. GFS and ICON get theirs through ``_enrich_with_grib``;
ECMWF takes the GRIB-first path and was losing them at two hand-offs:

1. the snapshot dict never copied ``nwp_k_index`` / ``nwp_total_totals``;
2. the ``HourlyForecast`` built for sounding enrichment carried no
   ``nwp_cloud_diagnostics``, so ``analyze_sounding_lite`` saw nothing to
   assess and every ``nwp_conv_*`` / ``nwp_ml_*`` column stored NULL.

Both builders were correct in isolation, which is exactly why a unit test on
either one stayed green through the whole outage. So these tests assert **fill
rates over a decoded run** — the same measurement the issue made in prod —
rather than field presence on a builder's return value.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from weatherbrief.tasks.airport_watchlist import WatchlistAirport
from weatherbrief.tasks.standalone_verification import fetch_ecmwf_grib_snapshots

INIT = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)

# Hourly region only — every sampled step then has a delivered predecessor,
# which is what the accumulated `cp` step-diff needs.
DELIVERED = list(range(0, 91))

# A conditionally-unstable summer profile: ~7 K/km lapse rate, moist below.
# (pressure_hPa, geopotential height m, temperature C, RH %)
PROFILE = [
    (1000, 110, 19.2, 90.0),
    (925, 760, 14.7, 85.0),
    (850, 1460, 9.8, 80.0),
    (700, 3010, -1.1, 60.0),
    (600, 4200, -9.4, 55.0),
    (500, 5570, -19.0, 45.0),
    (400, 7180, -30.3, 40.0),
    (300, 9160, -44.1, 35.0),
    (250, 10360, -52.5, 30.0),
    (200, 11780, -62.5, 25.0),
]

# Every model-native ingredient column the a1 GRIB can support, and what it
# needs. The issue measured all of these at 0 % for ECMWF.
SURFACE_ONLY_COLUMNS = (
    "nwp_k_index",        # kx
    "nwp_total_totals",   # totalx
    "nwp_ml_cape_jkg",    # mlcape100
    "nwp_ml_cin_jkg",     # mlcin100
)
SOUNDING_COLUMNS = (
    "nwp_conv_method",       # which native pathway graded the hour
    "nwp_conv_top_ft",       # hcct
    "nwp_conv_precip_mm_h",  # de-accumulated cp
    "nwp_cape_type",         # 'mu' — what makes cape_jkg interpretable
)


def _a1(step_h: int) -> dict[str, float]:
    """One point's decoded a1 surface fields (internal raw names)."""
    return {
        "temperature_2m_k": 292.35,
        "dewpoint_2m_k": 290.35,
        "u_wind_10m_ms": 3.0,
        "v_wind_10m_ms": -4.0,
        "wind_gust_10m_ms": 9.0,
        "visibility_m": 20000.0,
        "surface_pressure_pa": 100000.0,
        "total_precip_m": step_h * 0.001,
        "snowfall_m_we": 0.0,
        # Accumulated convective precip: 0.5 mm/h since init.
        "conv_precip_m": step_h * 0.0005,
        "mucape_jkg": 1200.0,
        "ml_cape_jkg": 850.0,
        "ml_cin_jkg": 40.0,          # positive magnitude → negative internally
        "k_index_c": 305.15,         # delivered in Kelvin → 32 °C
        "total_totals_c": 48.0,
        "ceiling_m": 900.0,
        "cloud_base_height_m": 850.0,
        "convective_cloud_top_m": 6000.0,   # hcct, AGL
        "freezing_level_m": 3300.0,         # deg0l, AGL
        "low_cover_frac": 0.45,
        "mid_cover_frac": 0.30,
        "high_cover_frac": 0.20,
        "total_cover_frac": 0.65,
    }


def _a2() -> dict[int, dict[str, float]]:
    """One point's decoded a2 pressure-level fields."""
    return {
        p: {
            "raw_temperature_k": t_c + 273.15,
            "raw_relative_humidity_pct": rh,
            "geopotential_height_m": float(gh),
            "raw_u_wind_m_s": 5.0,
            "raw_v_wind_m_s": -5.0,
        }
        for p, gh, t_c, rh in PROFILE
    }


def _file(step_h: int, part: str):
    return SimpleNamespace(
        step_hours=step_h,
        base_time=INIT,
        path=f"/fake/{part}_{step_h}h.grib",
        is_surface=(part == "a1"),
        is_pressure_level=(part == "a2"),
    )


@pytest.fixture
def run_files():
    return [_file(s, p) for s in DELIVERED for p in ("a1", "a2")]


@pytest.fixture
def airports():
    return [
        WatchlistAirport(icao="EGLL", lat=51.5, lon=-0.5),
        WatchlistAirport(icao="LFPG", lat=49.0, lon=2.5),
    ]


def _install_decode(monkeypatch, *, with_pressure: bool = True):
    """Stand in for the GRIB decoder for both a1 and a2 jobs."""
    n_points = 2

    def _decode_one(job: str, path: str):
        step_h = int(path.split("_")[-1].removesuffix("h.grib"))
        if job == "decode_ecmwf_surface":
            return [_a1(step_h) for _ in range(n_points)], None
        if not with_pressure:
            return [{} for _ in range(n_points)], None
        return [_a2() for _ in range(n_points)], None

    def _dispatch_parallel(
        jobs, *, priority=None, return_exceptions=False, max_inflight=None,
    ):
        return [_decode_one(name, args[0]) for name, args in jobs]

    monkeypatch.setattr(
        "weatherbrief.fetch.grib._dispatch_decode_parallel", _dispatch_parallel,
    )


def _fill_rate(snaps: list[dict], column: str) -> float:
    return sum(s.get(column) is not None for s in snaps) / len(snaps)


@pytest.fixture
def snaps(run_files, airports, monkeypatch):
    _install_decode(monkeypatch)
    out = fetch_ecmwf_grib_snapshots(run_files, airports, [6, 12, 18], 3)
    assert out, "expected decoded ECMWF samples"
    return out


class TestIngredientFillRates:
    """The measurement from the issue: per-column fill rate over a real cycle."""

    @pytest.mark.parametrize("column", SURFACE_ONLY_COLUMNS + SOUNDING_COLUMNS)
    def test_column_is_filled_on_every_row(self, snaps, column):
        rate = _fill_rate(snaps, column)
        assert rate == 1.0, (
            f"{column} filled on {rate:.0%} of {len(snaps)} ECMWF rows — the "
            f"a1 GRIB carries the source field for every one of them"
        )

    def test_no_ingredient_column_is_entirely_empty(self, snaps):
        """The shape of the regression: a whole column at 0 %."""
        empty = [
            c for c in SURFACE_ONLY_COLUMNS + SOUNDING_COLUMNS
            if _fill_rate(snaps, c) == 0.0
        ]
        assert not empty, f"columns at 0 % fill for ecmwf: {empty}"


class TestIngredientValues:
    def test_k_index_is_normalized_to_celsius(self, snaps):
        # kx arrives in Kelvin; feeding it raw makes the K>=40 character nudge
        # fire unconditionally.
        assert snaps[0]["nwp_k_index"] == pytest.approx(32.0, abs=0.1)

    def test_total_totals_passes_through(self, snaps):
        assert snaps[0]["nwp_total_totals"] == pytest.approx(48.0)

    def test_ml_cin_uses_the_negative_convention(self, snaps):
        # ECMWF publishes mlcin100 as a positive magnitude; internally CIN is
        # negative (more negative = stronger cap).
        assert snaps[0]["nwp_ml_cin_jkg"] == pytest.approx(-40.0)
        assert snaps[0]["nwp_ml_cape_jkg"] == pytest.approx(850.0)

    def test_convective_grade_takes_the_ecmwf_tower_pathway(self, snaps):
        # hcct with no convective cover fraction → LCL-as-base + tower top.
        # This is the pathway no model was exercising before #581.
        assert snaps[0]["nwp_conv_method"] == "nwp_lcl_top"
        assert snaps[0]["nwp_conv_top_ft"] == pytest.approx(6000 * 3.28084, rel=1e-3)

    def test_convective_precip_is_a_rate_not_the_accumulation(self, snaps):
        # cp accumulates 0.5 mm/h since init; every sampled step is 1 h past
        # its predecessor here, so the rate is 0.5 mm/h at step 6 and at 66.
        for snap in snaps:
            assert snap["nwp_conv_precip_mm_h"] == pytest.approx(0.5, abs=1e-6)

    def test_cape_type_records_which_parcel_cape_jkg_is(self, snaps):
        assert snaps[0]["nwp_cape_type"] == "mu"
        assert snaps[0]["cape_jkg"] == pytest.approx(1200.0)

    def test_mu_cin_and_lifted_index_stay_null(self, snaps):
        """Deliberately unfilled: ECMWF a1 has no MU CIN and no LI.

        Both columns pair with the model's own ``cape_jkg`` (here ``mucape``).
        Filling them from the mixed-layer pair would mix parcel types — the
        confusion ``nwp_cape_type`` exists to prevent — so the ML values go to
        ``nwp_ml_cin_jkg`` and these stay NULL.
        """
        assert all(s.get("nwp_cin_jkg") is None for s in snaps)
        assert all(s.get("nwp_lifted_index") is None for s in snaps)


class TestSurfaceOnlyRows:
    """A row whose a2 file is missing still records what a1 delivered."""

    def test_ingredients_survive_without_pressure_levels(
        self, run_files, airports, monkeypatch,
    ):
        _install_decode(monkeypatch, with_pressure=False)
        snaps = fetch_ecmwf_grib_snapshots(run_files, airports, [12], 3)
        assert snaps

        for column in SURFACE_ONLY_COLUMNS:
            assert _fill_rate(snaps, column) == 1.0, (
                f"{column} is an a1 field — a missing a2 file must not blank it"
            )
        # The sounding-derived ones legitimately cannot exist here.
        assert all(s.get("nwp_conv_method") is None for s in snaps)


class TestFreezingLevelDatum:
    """``deg0l`` is AGL; it must reach the sounding on the MSL datum (#487)."""

    def test_diagnostics_freezing_level_is_referenced_to_model_terrain(
        self, run_files, airports, monkeypatch,
    ):
        from weatherbrief.fetch.grib.decode import (
            build_ecmwf_cloud_diagnostics,
            build_pressure_levels_from_grib,
            model_surface_height_m,
        )

        levels = build_pressure_levels_from_grib(_a2())
        terrain_m = model_surface_height_m(levels, 1000.0)
        assert terrain_m is not None

        diag = build_ecmwf_cloud_diagnostics(
            _a1(12), terrain_elevation_m=terrain_m,
        )
        assert diag is not None
        # 3300 m AGL + the model's own orography, not the bare AGL value.
        assert diag.freezing_level_ft == pytest.approx(
            (3300.0 + terrain_m) * 3.28084, rel=1e-3,
        )

        # And without terrain it is dropped rather than passed through as AGL.
        assert build_ecmwf_cloud_diagnostics(
            _a1(12), terrain_elevation_m=None,
        ).freezing_level_ft is None


class TestPooledSoundingPath:
    """The production cycle ships profiles to the decode pool as plain dicts.

    ``build_sounding_payload`` serialises the ``HourlyForecast`` with
    ``model_dump`` and the worker rebuilds it with ``model_validate`` — the
    attached diagnostics have to survive that round-trip, or the fix would
    hold only on the inline path the cycle does not take.
    """

    def test_ingredients_survive_the_payload_round_trip(
        self, run_files, airports, monkeypatch,
    ):
        from weatherbrief.analysis.sounding.snapshot_fields import (
            analyze_sounding_batch_items,
        )

        n_points = len(airports)

        def _decode_one(job: str, args: tuple):
            if job == "analyze_sounding_batch":
                # Exactly what the pool worker does, minus the process hop.
                return analyze_sounding_batch_items(args[0])
            step_h = int(args[0].split("_")[-1].removesuffix("h.grib"))
            if job == "decode_ecmwf_surface":
                return [_a1(step_h) for _ in range(n_points)], None
            return [_a2() for _ in range(n_points)], None

        def _dispatch_parallel(
            jobs, *, priority=None, return_exceptions=False, max_inflight=None,
        ):
            return [_decode_one(name, args) for name, args in jobs]

        monkeypatch.setattr(
            "weatherbrief.fetch.grib._dispatch_decode_parallel", _dispatch_parallel,
        )
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.decode_pool_enabled", lambda: True,
        )

        snaps = fetch_ecmwf_grib_snapshots(
            run_files, airports, [6, 12, 18], 3, pool_soundings=True,
        )
        assert snaps

        for column in SURFACE_ONLY_COLUMNS + SOUNDING_COLUMNS:
            assert _fill_rate(snaps, column) == 1.0, (
                f"{column} lost across the pool payload round-trip"
            )
        assert snaps[0]["nwp_conv_method"] == "nwp_lcl_top"
