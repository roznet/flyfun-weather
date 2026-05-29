"""Tests for weatherbrief.frontal.route_sampling.

Two goals:
  1. Bilinear interpolation math is correct on synthetic fields with
     known analytic values.
  2. `sample_hewson_at_route` glues spatial + temporal interp together
     correctly over a small synthetic Case.
"""

from __future__ import annotations

import numpy as np
import pytest

from weatherbrief.frontal.case import Case
from weatherbrief.frontal.route_sampling import (
    analyze_route_fronts,
    bilinear_sample,
    detect_front_crossings,
    extract_gated_fronts,
    find_nearby_fronts,
    sample_hewson_at_route,
)


# ---------------------------------------------------------------------------
# bilinear_sample


class TestBilinearSample:
    def _axes(self):
        lat = np.linspace(45.0, 55.0, 11)   # 1° spacing
        lon = np.linspace(0.0, 10.0, 11)
        return lat, lon

    def test_at_grid_node(self):
        lat, lon = self._axes()
        grid = np.outer(lat, lon)           # f(la,lo) = la*lo
        # node value matches underlying function exactly
        assert bilinear_sample(grid, lat, lon, 50.0, 5.0) == pytest.approx(250.0)

    def test_linear_field_exact(self):
        """Bilinear reproduces any linear field exactly."""
        lat, lon = self._axes()
        lat_grid, lon_grid = np.meshgrid(lat, lon, indexing="ij")
        grid = 3.0 * lat_grid - 2.0 * lon_grid + 7.0

        for wp in [(46.5, 1.25), (52.7, 8.3), (45.0, 0.0), (55.0, 10.0)]:
            expected = 3.0 * wp[0] - 2.0 * wp[1] + 7.0
            assert bilinear_sample(grid, lat, lon, *wp) == pytest.approx(expected, rel=1e-10)

    def test_midpoint_of_cell(self):
        """Midpoint of a cell = average of the four corners."""
        lat, lon = self._axes()
        grid = np.zeros((11, 11))
        grid[5, 5] = 1.0
        grid[5, 6] = 3.0
        grid[6, 5] = 5.0
        grid[6, 6] = 7.0
        # midpoint between lat[5]=50, lat[6]=51 → 50.5; similarly 5.5
        assert bilinear_sample(grid, lat, lon, 50.5, 5.5) == pytest.approx(
            (1.0 + 3.0 + 5.0 + 7.0) / 4.0
        )

    def test_outside_grid_returns_nan(self):
        lat, lon = self._axes()
        grid = np.zeros((11, 11))
        assert np.isnan(bilinear_sample(grid, lat, lon, 44.0, 5.0))
        assert np.isnan(bilinear_sample(grid, lat, lon, 56.0, 5.0))
        assert np.isnan(bilinear_sample(grid, lat, lon, 50.0, -0.01))
        assert np.isnan(bilinear_sample(grid, lat, lon, 50.0, 10.01))


# ---------------------------------------------------------------------------
# sample_hewson_at_route
#
# Build a tiny synthetic Case in-memory so the glue logic is tested
# end-to-end without needing an actual NPZ on disk.


def _synthetic_case(n_hours: int = 3) -> Case:
    """Build a Case whose fields(model, hour) returns known analytic grids.

    θe is linear in lat + linear in hour, so:
      - gradient is a known constant
      - tendency is a known constant
      - advection depends on u/v (zero wind → zero advection)
    """
    lat = np.linspace(45.0, 50.0, 21)       # 0.25° × 5°
    lon = np.linspace(0.0, 5.0, 21)
    lat_grid, _ = np.meshgrid(lat, lon, indexing="ij")

    # θe varies 2 K per degree latitude, +1 K per hour
    fields_by_hour: dict[int, dict] = {}
    for h in range(n_hours):
        theta_e = 290.0 + 2.0 * lat_grid + 1.0 * h
        fields_by_hour[h] = {
            "T850": theta_e - 40.0,                    # placeholder
            "Td850": theta_e - 50.0,
            "theta_e": theta_e,
            "u850": np.zeros_like(theta_e),            # no advection
            "v850": np.zeros_like(theta_e),
        }

    valid_times = np.array(
        [np.datetime64("2024-01-01T00", "ns") + np.timedelta64(h, "h")
         for h in range(n_hours)],
        dtype="datetime64[ns]",
    )

    case = Case(
        case_dir=None,  # fields() is overridden below; case_dir never used
        case_name="synthetic",
        source="synthetic",
        resolution_deg=0.25,
        lat=lat,
        lon=lon,
        models=["syn"],
        valid_times={"syn": valid_times},
        init_times={"syn": 0},
    )

    # Monkey-patch fields / available_hours — synthetic grids don't live on disk
    case.fields = lambda model, hour, level_hPa=None, _src=fields_by_hour: _src.get(hour)  # type: ignore
    case.available_hours = lambda model: list(range(n_hours))              # type: ignore
    return case


class TestSampleHewsonAtRoute:
    def test_integer_hour_single_waypoint(self):
        case = _synthetic_case()
        out = sample_hewson_at_route(
            case, "syn", [(47.5, 2.5)], hours=1,
        )
        assert len(out) == 1
        entry = out[0]
        assert entry["lat"] == 47.5
        assert entry["lon"] == 2.5
        assert entry["hour"] == 1.0
        # θe = 290 + 2*47.5 + 1*1 = 386
        assert entry["theta_e"] == pytest.approx(386.0, abs=1e-6)
        # No wind → zero advection
        assert entry["advection"] == pytest.approx(0.0, abs=1e-9)
        # +1 K/h tendency (centered diff between h=0 and h=2)
        assert entry["tendency"] == pytest.approx(1.0, abs=1e-6)
        # Gradient: 2 K/deg ≈ 2/111 K/km → ~1.8 K/100km
        # Just sanity-check the direction (non-zero, positive)
        assert entry["gradient"] > 1.0
        assert entry["gradient"] < 3.0

    def test_fractional_hour_interpolates(self):
        """Target hour 0.25 should return θe 25% of the way from h=0 to h=1."""
        case = _synthetic_case()
        wp = (47.0, 2.0)
        out = sample_hewson_at_route(case, "syn", [wp], hours=0.25)
        # θe at h=0: 290 + 94 = 384; at h=1: 385; 25% → 384.25
        assert out[0]["theta_e"] == pytest.approx(384.25, abs=1e-6)

    def test_per_waypoint_hour(self):
        case = _synthetic_case()
        wps = [(47.0, 2.0), (48.0, 3.0)]
        out = sample_hewson_at_route(case, "syn", wps, hours=[0.0, 2.0])
        # wp0: θe = 290 + 2*47 + 0 = 384
        assert out[0]["theta_e"] == pytest.approx(384.0, abs=1e-6)
        # wp1: θe = 290 + 2*48 + 2 = 388
        assert out[1]["theta_e"] == pytest.approx(388.0, abs=1e-6)

    def test_waypoint_outside_grid(self):
        case = _synthetic_case()
        out = sample_hewson_at_route(case, "syn", [(60.0, 2.0)], hours=0)
        # Out-of-bounds waypoint: every field NaN but lat/lon/hour preserved
        assert np.isnan(out[0]["theta_e"])
        assert np.isnan(out[0]["gradient"])
        assert out[0]["lat"] == 60.0

    def test_hour_outside_range(self):
        case = _synthetic_case(n_hours=3)
        out = sample_hewson_at_route(case, "syn", [(47.0, 2.0)], hours=5.0)
        assert np.isnan(out[0]["theta_e"])
        assert out[0]["hour"] == 5.0

    def test_unknown_model_raises(self):
        case = _synthetic_case()
        with pytest.raises(ValueError, match="not in case"):
            sample_hewson_at_route(case, "nope", [(47.0, 2.0)])

    def test_hours_length_mismatch_raises(self):
        case = _synthetic_case()
        with pytest.raises(ValueError, match="hours has length"):
            sample_hewson_at_route(
                case, "syn",
                [(47.0, 2.0), (48.0, 3.0)],
                hours=[0.0, 1.0, 2.0],
            )

    def test_default_hour_zero(self):
        case = _synthetic_case()
        out = sample_hewson_at_route(case, "syn", [(47.0, 2.0)])
        # Default h=0 → θe = 290 + 94 + 0 = 384
        assert out[0]["theta_e"] == pytest.approx(384.0, abs=1e-6)
        assert out[0]["hour"] == 0.0


# ---------------------------------------------------------------------------
# detect_front_crossings — the v2 gate (gradient + |Δθe| jump, NOT −∇²θe).


def _route_series(theta_e, tfp, gradient, advection=0.0, step_km=15.0):
    """Build a synthetic densified-route series for the crossing detector."""
    n = len(tfp)
    return [
        dict(lat=50.0, lon=i * 0.1, distance_km=i * step_km,
             theta_e=theta_e[i], tfp=tfp[i], gradient=gradient[i],
             neg_laplacian=0.0, advection=advection)
        for i in range(n)
    ]


class TestDetectFrontCrossings:
    def test_real_front_detected(self):
        # 15 pts @15km; θe steps 320→300 at i=7; TFP flips +→− between 6 and 7.
        n = 15
        theta_e = [320.0 if i < 7 else 300.0 for i in range(n)]
        tfp = [1.0 if i < 7 else -1.0 for i in range(n)]
        gradient = [10.0] * n
        out = detect_front_crossings(_route_series(theta_e, tfp, gradient, advection=-1.0))
        assert len(out) == 1
        c = out[0]
        assert c.kind == "cold"                       # advection < 0
        assert c.delta_theta_e == pytest.approx(-20.0, abs=1e-6)
        assert 80.0 < c.distance_km < 110.0

    def test_col_rejected_no_airmass_jump(self):
        # TFP flips and gradient is high, but θe is flat → a col, not a front.
        n = 15
        theta_e = [320.0] * n
        tfp = [1.0 if i < 7 else -1.0 for i in range(n)]
        gradient = [10.0] * n
        assert detect_front_crossings(_route_series(theta_e, tfp, gradient)) == []

    def test_weak_gradient_rejected(self):
        n = 15
        theta_e = [320.0 if i < 7 else 300.0 for i in range(n)]
        tfp = [1.0 if i < 7 else -1.0 for i in range(n)]
        gradient = [3.0] * n                          # below gradient_min
        assert detect_front_crossings(_route_series(theta_e, tfp, gradient)) == []

    def test_no_sign_change_no_front(self):
        n = 15
        theta_e = [320.0 - i for i in range(n)]
        tfp = [1.0] * n                               # never changes sign
        gradient = [10.0] * n
        assert detect_front_crossings(_route_series(theta_e, tfp, gradient)) == []


# ---------------------------------------------------------------------------
# Grid proximity: extract_gated_fronts / find_nearby_fronts / analyze_route_fronts
#
# Synthetic Case with a tanh θe step in longitude → a single N–S front axis.


def _front_case(front_lon=4.0, amplitude=30.0, n_hours=4, drift_per_h=0.0):
    """Case whose θe is a tanh step in lon (warm west, cold east) at front_lon,
    optionally drifting ``drift_per_h`` °lon/h."""
    lat = np.arange(45.0, 52.001, 0.25)
    lon = np.arange(0.0, 8.001, 0.25)
    _, LON = np.meshgrid(lat, lon, indexing="ij")

    fields_by_hour: dict[int, dict] = {}
    for h in range(n_hours):
        c = front_lon + drift_per_h * h
        theta_e = 320.0 - amplitude * 0.5 * (1.0 + np.tanh((LON - c) / 0.4))
        fields_by_hour[h] = {
            "T850": theta_e - 40.0, "Td850": theta_e - 50.0, "theta_e": theta_e,
            "u850": np.full_like(theta_e, 20.0),   # km/h, eastward
            "v850": np.zeros_like(theta_e),
        }
    vt = np.array(
        [np.datetime64("2024-01-01T00", "ns") + np.timedelta64(h, "h") for h in range(n_hours)],
        dtype="datetime64[ns]",
    )
    case = Case(
        case_dir=None, case_name="front", source="synthetic", resolution_deg=0.25,
        lat=lat, lon=lon, models=["syn"], valid_times={"syn": vt}, init_times={"syn": 0},
    )
    case.fields = lambda model, hour, level_hPa=None, _s=fields_by_hour: _s.get(hour)  # type: ignore
    case.available_hours = lambda model: list(range(n_hours))                          # type: ignore
    return case


class TestGridProximity:
    def test_extract_gated_fronts_on_axis(self):
        case = _front_case(front_lon=4.0)
        cells = extract_gated_fronts(case, "syn", 0)
        assert cells, "expected gated front cells on the tanh step"
        # all detected cells sit near the front longitude
        assert all(3.0 < c.lon < 5.0 for c in cells)
        assert max(c.gradient for c in cells) >= 6.0
        assert max(abs(c.delta_theta_e) for c in cells) >= 5.0

    def test_find_nearby_fronts_offtrack_distance(self):
        case = _front_case(front_lon=4.0)
        # N–S route at lon=3.0 → front at lon≈4.0 is ~70 km east at ~48°N.
        wps = [(46.0, 3.0), (51.0, 3.0)]
        nf = find_nearby_fronts(case, "syn", wps, 0, use_anomaly_filter=False,
                                approach_dh=None)
        assert nf is not None
        assert not nf.on_track
        assert 50.0 < nf.distance_km < 95.0

    def test_find_nearby_fronts_closing(self):
        # Front drifts west (−0.25°/h) toward the lon=3.0 route → closing.
        case = _front_case(front_lon=4.0, drift_per_h=-0.25)
        wps = [(46.0, 3.0), (51.0, 3.0)]
        nf = find_nearby_fronts(case, "syn", wps, 0, use_anomaly_filter=False,
                                approach_dh=2.0)
        assert nf is not None
        assert nf.trend == "closing"
        assert nf.closing_km_per_h is not None and nf.closing_km_per_h < 0

    def test_analyze_route_fronts_crossing(self):
        case = _front_case(front_lon=4.0)
        # E–W route crossing the front at lon≈4.0.
        wps = [(48.0, 2.0), (48.0, 6.0)]
        res = analyze_route_fronts(case, "syn", wps, 0, use_anomaly_filter=False)
        assert res.model == "syn"
        assert len(res.crossings) >= 1
        assert res.nearest is not None and res.nearest.on_track
