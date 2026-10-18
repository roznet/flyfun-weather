"""ECMWF feeds mid/high band cover to the sounding pass, like GFS/ICON (#805).

``airport_forecast_snapshots`` has stored ``cloud_cover_mid_pct`` /
``cloud_cover_high_pct`` for every model since migration 107, but the ECMWF
GRIB-first path built its sounding ``HourlyForecast`` with total and low cover
only. ``build_nwp_cloud_layers`` reads all three bands, so where ECMWF's
per-level ``cc`` is missing and the condensate fallback runs, ECMWF's
``nwp_layer_*`` columns were computed from different inputs than GFS/ICON's.

The parity check: a decoded ECMWF row's ``nwp_layer_*`` must equal what the
same forecast hour yields when enriched as a GFS sample.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import weatherbrief.tasks.standalone_verification as sv
from weatherbrief.tasks.airport_watchlist import WatchlistAirport

INIT = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)
DELIVERED = list(range(0, 25))

# (pressure_hPa, geopotential height m, temperature C, RH %, cloud water kg/kg)
# Condensate only in the ICAO mid band (~10-14 kft), no per-level ``cc`` — so
# build_nwp_cloud_layers takes the condensate path, whose amount comes from
# the bulk band covers.
PROFILE = [
    (1000, 110, 19.2, 70.0, None),
    (925, 760, 14.7, 65.0, None),
    (850, 1460, 9.8, 60.0, None),
    (700, 3010, -1.1, 95.0, 2e-4),
    (600, 4200, -9.4, 95.0, 2e-4),
    (500, 5570, -19.0, 45.0, None),
    (400, 7180, -30.3, 40.0, None),
    (300, 9160, -44.1, 35.0, None),
    (250, 10360, -52.5, 30.0, None),
    (200, 11780, -62.5, 25.0, None),
]

NWP_LAYER_COLUMNS = (
    "nwp_layer_source",
    "nwp_layer_ceiling_ft",
    "nwp_layer_lowest_base_ft",
)


def _a1(step_h: int) -> dict[str, float]:
    return {
        "temperature_2m_k": 292.35,
        "dewpoint_2m_k": 285.35,
        "u_wind_10m_ms": 3.0,
        "v_wind_10m_ms": -4.0,
        "wind_gust_10m_ms": 9.0,
        "visibility_m": 20000.0,
        "surface_pressure_pa": 100000.0,
        "total_precip_m": 0.0,
        "snowfall_m_we": 0.0,
        "mucape_jkg": 0.0,
        "low_cover_frac": 0.0,
        # FEW/SCT in the mid band. Without it the condensate builder falls
        # back to an unknown-extent BKN, i.e. a ceiling that isn't there.
        "mid_cover_frac": 0.30,
        "high_cover_frac": 0.10,
        "total_cover_frac": 0.35,
    }


def _a2() -> dict[int, dict[str, float]]:
    out = {}
    for p, gh, t_c, rh, qc in PROFILE:
        lvl = {
            "raw_temperature_k": t_c + 273.15,
            "raw_relative_humidity_pct": rh,
            "geopotential_height_m": float(gh),
            "raw_u_wind_m_s": 5.0,
            "raw_v_wind_m_s": -5.0,
            "cloud_liquid_water_kg_kg": qc if qc is not None else 0.0,
        }
        out[p] = lvl
    return out


def _file(step_h: int, part: str):
    return SimpleNamespace(
        step_hours=step_h,
        base_time=INIT,
        path=f"/fake/{part}_{step_h}h.grib",
        is_surface=(part == "a1"),
        is_pressure_level=(part == "a2"),
    )


@pytest.fixture
def captured(monkeypatch):
    """Run one ECMWF decode; return (snapshots, the HourlyForecasts enriched)."""
    airports = [WatchlistAirport(icao="ZZAA", lat=51.5, lon=-0.5)]
    files = [_file(s, p) for s in DELIVERED for p in ("a1", "a2")]

    def _decode_one(job: str, path: str):
        step_h = int(path.split("_")[-1].removesuffix("h.grib"))
        if job == "decode_ecmwf_surface":
            return [_a1(step_h)], None
        return [_a2()], None

    def _dispatch_parallel(
        jobs, *, priority=None, return_exceptions=False, max_inflight=None,
    ):
        return [_decode_one(name, args[0]) for name, args in jobs]

    monkeypatch.setattr(
        "weatherbrief.fetch.grib._dispatch_decode_parallel", _dispatch_parallel,
    )

    seen = []
    real_enrich = sv._enrich_with_sounding

    def _spy(snap, hourly, model, **kw):
        seen.append(hourly.model_copy(deep=True))
        return real_enrich(snap, hourly, model, **kw)

    monkeypatch.setattr(sv, "_enrich_with_sounding", _spy)
    snaps = sv.fetch_ecmwf_grib_snapshots(files, airports, [12], 1)
    assert snaps and seen
    return snaps, seen


def test_hourly_carries_mid_and_high_cover(captured):
    _, seen = captured
    assert seen[0].cloud_cover_mid_pct == pytest.approx(30.0)
    assert seen[0].cloud_cover_high_pct == pytest.approx(10.0)


def test_nwp_layer_columns_match_an_equivalent_gfs_sample(captured):
    snaps, seen = captured
    ecmwf = snaps[0]
    assert ecmwf.get("nwp_layer_lowest_base_ft") is not None, (
        "fixture should produce a mid-band condensate deck"
    )
    assert ecmwf.get("nwp_layer_ceiling_ft") is None, (
        "a 30 % mid band is not a ceiling"
    )

    gfs: dict = {}
    sv._enrich_with_sounding(gfs, seen[0].model_copy(deep=True), "gfs")
    for col in NWP_LAYER_COLUMNS:
        assert ecmwf.get(col) == gfs.get(col), col


def test_without_mid_cover_the_deck_would_differ(captured):
    """Guards the test above: mid/high cover is what moves nwp_layer_*."""
    snaps, seen = captured
    stripped = seen[0].model_copy(
        update={"cloud_cover_mid_pct": None, "cloud_cover_high_pct": None},
        deep=True,
    )
    old: dict = {}
    sv._enrich_with_sounding(old, stripped, "ecmwf")
    # The pre-#805 inputs: no mid amount → unknown-extent BKN → a ceiling.
    assert old.get("nwp_layer_ceiling_ft") is not None
    assert snaps[0].get("nwp_layer_ceiling_ft") is None
