"""Tests for the front-detection pipeline stage (weatherbrief.tasks.fronts)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from weatherbrief.models import RoutePointAnalysis
from weatherbrief.tasks.artifacts import load_route_fronts
from weatherbrief.models.fronts import FrontCrossingModel, RouteFrontAnalysisModel
from weatherbrief.tasks.fronts import (
    _colocate,
    _persistence,
    _stamp_vertical_coherence,
    compute_route_fronts,
    nearest_cruise_level,
    run_fronts,
)


_INIT_DT = datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)
_INIT_UNIX = int(_INIT_DT.timestamp())
_LEVELS = (925, 850, 700)
_STRIDE = 3
_N_TIME = 9  # 0..24 h


def _write_front_snapshot(
    out_dir: Path,
    model: str = "ecmwf",
    *,
    init_dt: datetime = _INIT_DT,
    levels: tuple[int, ...] = _LEVELS,
) -> Path:
    """A snapshot with a meridional θe front (cold front, eastward flow).

    ``init_dt`` lets a test plant snapshots at several model inits (#201); the
    ``levels`` override lets it plant a partial snapshot — fewer pressure levels
    for one model — to exercise the per-model primary level (#203).
    """
    from weatherbrief.frontal.detect import compute_hewson_diagnostics
    from weatherbrief.hewson.precompute import (
        snapshot_path,
        tendency_k_per_hour,
        write_snapshot,
    )

    init_unix = int(init_dt.timestamp())
    lat = np.linspace(45.0, 52.0, 29)
    lon = np.linspace(-2.0, 6.0, 33)
    _, lon_grid = np.meshgrid(lat, lon, indexing="ij")

    per_level: dict[int, dict] = {}
    for L in levels:
        m = {
            k: np.full((_N_TIME, lat.size, lon.size), np.nan, dtype=np.float32)
            for k in ("theta_e", "gradient", "neg_laplacian", "tfp", "advection")
        }
        for h in range(_N_TIME):
            axis = 2.0 + 0.03 * h * _STRIDE
            theta = 290.0 + 6.0 * np.tanh((lon_grid - axis) / 0.3)
            u = np.full_like(theta, 30.0)
            v = np.zeros_like(theta)
            d = compute_hewson_diagnostics(theta, lat, lon, u, v)
            m["theta_e"][h] = theta
            m["gradient"][h] = d["gradient"]
            m["neg_laplacian"][h] = d["neg_laplacian"]
            m["tfp"][h] = d["tfp"]
            m["advection"][h] = d["advection"]
        m["tendency"] = tendency_k_per_hour(m["theta_e"], step_hours=_STRIDE)
        per_level[L] = m

    valid_times = np.array(
        [np.datetime64(init_dt.replace(tzinfo=None) + timedelta(hours=h * _STRIDE))
         for h in range(_N_TIME)],
        dtype="datetime64[ns]",
    )
    path = snapshot_path(model, init_unix, output_dir=out_dir)
    write_snapshot(
        path, init_time_unix=init_unix, valid_times=valid_times,
        lat=lat, lon=lon, levels=list(levels), stride_hours=_STRIDE,
        per_level=per_level,
    )
    return path


def _route_analyses():
    """West→east route across the front axis, departing at init + 6 h."""
    dep = _INIT_DT + timedelta(hours=6)
    pts = [(47.0, -1.5), (47.0, 1.5), (47.0, 4.5)]
    out = []
    for i, (la, lo) in enumerate(pts):
        out.append(RoutePointAnalysis(
            point_index=i, lat=la, lon=lo,
            distance_from_origin_nm=float(i * 80),
            interpolated_time=dep + timedelta(minutes=40 * i),
            forecast_hour=6 + i, track_deg=90.0,
        ))
    return out


class TestColocate:
    """Cloud/convection co-location classification (the wet/dry/convective gate)."""

    @staticmethod
    def _an(cloud_layers, convective, precip=None):
        return [{
            "distance_from_origin_nm": 0.0,
            "sounding": {"ecmwf": {
                "cloud_layers": cloud_layers,
                "convective": convective,
                "precipitation": precip,
            }},
        }]

    def test_dry_blue_sky(self):
        # No cloud, low convection → demote (the Alpine-artifact case).
        assert _colocate(self._an([], {"risk_level": "low"}), "ecmwf", 0.0, 850) == ("dry", None)

    def test_wet_cloud_spans_level(self):
        cl = [{"top_ft": 18000.0, "base_pressure_hpa": 900, "top_pressure_hpa": 700, "coverage": "ovc"}]
        cat, top = _colocate(self._an(cl, {"risk_level": "none"}), "ecmwf", 0.0, 850)
        assert cat == "wet" and top == 18000.0

    def test_convective_uses_el_for_vertical_extent(self):
        # Overflown boundary but convective towers far above (the Dijon case):
        # weather_top must reflect the convective EL, not the shallow cloud layer.
        # No CIN/ML-CAPE/LI in the fixture → convection defaults to realized.
        cl = [{"top_ft": 9000.0, "base_pressure_hpa": 900, "top_pressure_hpa": 850, "coverage": "sct"}]
        conv = {"risk_level": "moderate", "el_altitude_ft": 33000.0}
        cat, top = _colocate(self._an(cl, conv), "ecmwf", 0.0, 850)
        assert cat == "convective" and top == 33000.0

    @staticmethod
    def _an_full(cloud_layers, convective, indices, precip=None):
        """Analysis fixture that also carries the sounding ``indices`` column
        (ML-CAPE / NWP LI) needed by the realized-convection gate (#216)."""
        return [{
            "distance_from_origin_nm": 0.0,
            "sounding": {"ecmwf": {
                "cloud_layers": cloud_layers,
                "convective": convective,
                "indices": indices,
                "precipitation": precip,
            }},
        }]

    def test_cin_capped_potential_not_convective(self):
        # LSGS 2026-06-07 regression (#216): GFS 925 hPa θe crossing over Alpine
        # terrain. risk_level "moderate" is driven by CIN-capped potential CAPE
        # (CIN -59.5, ML-CAPE 147, NWP LI -1) — convection is NOT realized. The
        # only cloud is high cirrus (FL270+) that does NOT span 925, so the
        # boundary is dry, and the parcel EL (27,233 ft) must NOT become a
        # convective top. (Was the false-RED "convective tops to FL272".)
        cl = [{"top_ft": 29298.0, "base_pressure_hpa": 400, "top_pressure_hpa": 300, "coverage": "ovc"}]
        conv = {
            "risk_level": "moderate", "method": "thermo",
            "cin_jkg": -59.5, "el_altitude_ft": 27233.0, "top_ft": 27233.0,
            "lifted_index": None,
        }
        indices = {"cape_mixed_layer_jkg": 147.3, "nwp_lifted_index": -1.0}
        cat, top = _colocate(self._an_full(cl, conv, indices), "ecmwf", 0.0, 925)
        assert cat == "dry"
        assert top is None  # the parcel EL is never used as a realized top

    def test_cin_capped_but_high_ml_cape_stays_convective(self):
        # Guard the gate isn't over-aggressive: a strong cap (CIN -80) with a
        # genuinely unstable air mass (ML-CAPE 1200) is realizable (loaded gun) →
        # still convective, EL still its tower-depth proxy.
        cl = [{"top_ft": 9000.0, "base_pressure_hpa": 900, "top_pressure_hpa": 850, "coverage": "sct"}]
        conv = {
            "risk_level": "high", "method": "thermo",
            "cin_jkg": -80.0, "el_altitude_ft": 36000.0, "top_ft": 36000.0,
        }
        indices = {"cape_mixed_layer_jkg": 1200.0, "nwp_lifted_index": -1.0}
        cat, top = _colocate(self._an_full(cl, conv, indices), "ecmwf", 0.0, 850)
        assert cat == "convective" and top == 36000.0

    def test_nwp_convective_cloud_is_realized(self):
        # An NWP-method assessment (method != "thermo") reflects modeled
        # convective cloud → realized regardless of CIN; its NWP top is used.
        conv = {
            "risk_level": "moderate", "method": "nwp",
            "cin_jkg": -90.0, "top_ft": 25000.0,
        }
        indices = {"cape_mixed_layer_jkg": 50.0, "nwp_lifted_index": 2.0}
        cat, top = _colocate(self._an_full([], conv, indices), "ecmwf", 0.0, 850)
        assert cat == "convective" and top == 25000.0

    def test_partly_cloud_at_level_is_partly(self):
        cl = [{"top_ft": 12000.0, "base_pressure_hpa": 900, "top_pressure_hpa": 700, "coverage": "sct"}]
        cat, _ = _colocate(self._an(cl, {"risk_level": "none"}), "ecmwf", 0.0, 850)
        assert cat == "partly"

    def test_high_cirrus_unrelated_to_front_is_dry(self):
        # few/sct cirrus at 300 hPa does NOT span an 850 hPa front → dry, not
        # "partly" (the false-AMBER the level-gate prevents).
        cl = [{"top_ft": 38000.0, "base_pressure_hpa": 300, "top_pressure_hpa": 250, "coverage": "sct"}]
        cat, _ = _colocate(self._an(cl, {"risk_level": "none"}), "ecmwf", 0.0, 850)
        assert cat == "dry"

    def test_weather_top_excludes_unrelated_cirrus(self):
        # Low OVC spanning an 850 hPa front (top 12000) + unrelated cirrus at
        # 300 hPa (top 38000). Category is "wet" from the OVC; weather_top must
        # come from the spanning layer only — else the cirrus false-AMBERs a
        # front that's well below cruise (PR #200 review finding #1).
        cl = [
            {"top_ft": 12000.0, "base_pressure_hpa": 900, "top_pressure_hpa": 700, "coverage": "ovc"},
            {"top_ft": 38000.0, "base_pressure_hpa": 300, "top_pressure_hpa": 250, "coverage": "sct"},
        ]
        cat, top = _colocate(self._an(cl, {"risk_level": "none"}), "ecmwf", 0.0, 850)
        assert cat == "wet" and top == 12000.0

    def test_drizzle_below_front_is_wet(self):
        # Surface precip at a detected boundary, but the precipitating cloud is
        # shallow and does NOT span the 850 hPa front. surface_intensity is
        # column-wide (not level-gated), so category is "wet" and weather_top_ft
        # is None (no spanning layer). _grade_crossing then treats None as
        # "reaches" — a deliberate conservative bias (PR #200 review #1).
        cl = [{"top_ft": 4000.0, "base_pressure_hpa": 960, "top_pressure_hpa": 940, "coverage": "sct"}]
        precip = {"surface_intensity": "light"}
        cat, top = _colocate(self._an(cl, {"risk_level": "none"}, precip), "ecmwf", 0.0, 850)
        assert cat == "wet" and top is None

    def test_missing_model_degrades_gracefully(self):
        assert _colocate(self._an([], {"risk_level": "low"}), "gfs", 0.0, 850) == (None, None)


class _StubSource:
    """Minimal SnapshotFieldSource stand-in for persistence testing."""

    def __init__(self, grid_by_hour, stride=3):
        self.lat = np.array([47.0])
        self.lon = np.array([0.0])
        self.stride_hours = stride
        self._g = grid_by_hour

    def available_hours(self, model):
        return sorted(self._g)

    def gradient_at_hour(self, model, hour, level):
        return self._g.get(hour)


class TestPersistence:
    def test_fraction_over_window(self):
        # eta_hour=3, offsets ±6/3/0 → hours -3,0,3,6,9; only {0,3,6} exist.
        g = {0: np.array([[10.0]]), 3: np.array([[2.0]]), 6: np.array([[10.0]])}
        p = _persistence(_StubSource(g), "ecmwf", 47.0, 0.0, 850, eta_hour=3.0, gradient_min=6.0)
        assert abs(p - 2 / 3) < 1e-6  # 0h & 6h hold (10≥6), 3h does not (2<6)

    def test_none_when_no_frames(self):
        assert _persistence(_StubSource({}), "ecmwf", 47.0, 0.0, 850, 3.0, 6.0) is None


def _xing(distance_km: float, **kw) -> FrontCrossingModel:
    base = dict(
        lat=48.0, lon=4.0, distance_km=distance_km, gradient=10.0,
        neg_laplacian=0.0, advection=-1.0, tfp_before=1.0, tfp_after=-1.0,
        delta_theta_e=-12.0, kind="cold", intensity="classical",
    )
    base.update(kw)
    return FrontCrossingModel(**base)


def _analysis(level: int, *crossings: FrontCrossingModel) -> RouteFrontAnalysisModel:
    return RouteFrontAnalysisModel(
        model="ecmwf", level_hPa=level, hour=9.0, crossings=list(crossings),
    )


class TestVerticalCoherence:
    """Cross-level stamping: a real front slopes through several levels; a
    single-level detection is shallow/suspect (vertical_levels == 1)."""

    def test_same_feature_across_two_levels_is_coherent(self):
        # Cold front seen at 850 (387 km) and 925 (355 km) — 32 km apart, within
        # merge_km (60) → one feature spanning 2 levels.
        analyses = [_analysis(850, _xing(387.0)), _analysis(925, _xing(355.0))]
        _stamp_vertical_coherence(analyses, tolerance_km=60.0)
        assert analyses[0].crossings[0].vertical_levels == 2
        assert analyses[1].crossings[0].vertical_levels == 2

    def test_single_level_feature_is_shallow(self):
        # A 925-only boundary far from any other-level crossing → vertical_levels 1.
        analyses = [_analysis(925, _xing(84.0)), _analysis(850, _xing(387.0))]
        _stamp_vertical_coherence(analyses, tolerance_km=60.0)
        shallow = analyses[0].crossings[0]
        deep = analyses[1].crossings[0]
        assert shallow.vertical_levels == 1
        assert deep.vertical_levels == 1  # 850 one is also alone here

    def test_three_levels_cluster_together(self):
        analyses = [
            _analysis(925, _xing(350.0)),
            _analysis(850, _xing(370.0)),
            _analysis(700, _xing(390.0)),
        ]
        _stamp_vertical_coherence(analyses, tolerance_km=60.0)
        assert all(a.crossings[0].vertical_levels == 3 for a in analyses)

    def test_two_distinct_features_not_merged(self):
        # An early 925 feature (84 km) and a late front on 850+925 (~355-387):
        # early stays single-level, late spans 2.
        analyses = [
            _analysis(925, _xing(84.0), _xing(355.0)),
            _analysis(850, _xing(387.0)),
        ]
        _stamp_vertical_coherence(analyses, tolerance_km=60.0)
        early = analyses[0].crossings[0]
        late_925 = analyses[0].crossings[1]
        late_850 = analyses[1].crossings[0]
        assert early.vertical_levels == 1
        assert late_925.vertical_levels == 2 and late_850.vertical_levels == 2

    def test_complete_linkage_prevents_chaining(self):
        # 925@250, 850@300, 700@360: single-linkage would chain all three
        # (gaps 50, 60) into one 110 km cluster → every crossing vlev=3, inflating
        # a shallow 250 km feature. Complete-linkage caps the span at tolerance:
        # {250, 300} cluster (span 50), and 360 splits off alone.
        analyses = [
            _analysis(925, _xing(250.0)),
            _analysis(850, _xing(300.0)),
            _analysis(700, _xing(360.0)),
        ]
        _stamp_vertical_coherence(analyses, tolerance_km=60.0)
        assert analyses[0].crossings[0].vertical_levels == 2  # 925@250 with 850@300
        assert analyses[1].crossings[0].vertical_levels == 2  # 850@300
        assert analyses[2].crossings[0].vertical_levels == 1  # 700@360 not chained

    def test_empty_is_noop(self):
        _stamp_vertical_coherence([], tolerance_km=60.0)  # no crash


class TestNearestCruiseLevel:
    def test_picks_closest_altitude(self):
        assert nearest_cruise_level(2500, [925, 850, 700]) == 925
        assert nearest_cruise_level(5500, [925, 850, 700]) == 850
        assert nearest_cruise_level(11000, [925, 850, 700]) == 700


class TestComputeRouteFronts:
    def test_detects_front_all_levels(self, tmp_path):
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir)
        analyses = _route_analyses()
        wps = [(a.lat, a.lon) for a in analyses]
        etas = [a.interpolated_time for a in analyses]

        manifest = compute_route_fronts(
            wps, etas, route_name="EGKB-LFAT", cruise_altitude_ft=5000,
            advisory_models=["ecmwf", "gfs"], output_dir=out_dir,
        )
        assert manifest.models == ["ecmwf"]            # only ecmwf has a snapshot
        assert manifest.models_without_snapshot == ["gfs"]
        assert manifest.primary_level_hPa == 850       # cruise 5000 ft
        assert manifest.levels == [700, 850, 925]
        assert "ecmwf" in manifest.snapshot_inits

        analyses_850 = [
            a for a in manifest.per_model["ecmwf"] if a.level_hPa == 850
        ]
        assert len(analyses_850) == 1
        a = analyses_850[0]
        assert len(a.crossings) >= 1
        assert a.crossings[0].kind == "cold"
        assert a.decisions  # candidate/decision trace stamped in

    def test_gate_config_stamped(self, tmp_path):
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir)
        analyses = _route_analyses()
        manifest = compute_route_fronts(
            [(a.lat, a.lon) for a in analyses],
            [a.interpolated_time for a in analyses],
            route_name="r", cruise_altitude_ft=2500,
            advisory_models=["ecmwf"], output_dir=out_dir,
        )
        assert manifest.gate_config["name"] == "default"
        assert manifest.gate_config["level_hPa"] == 925  # cruise 2500 → primary 925

    def test_no_models_when_no_snapshots(self, tmp_path):
        manifest = compute_route_fronts(
            [(47.0, -1.5), (47.0, 4.5)],
            [_INIT_DT + timedelta(hours=6), _INIT_DT + timedelta(hours=7)],
            route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], output_dir=tmp_path / "empty",
        )
        assert manifest.models == []
        assert manifest.models_without_snapshot == ["ecmwf"]
        assert manifest.per_model == {}

    def test_stale_terrain_mask_does_not_crash(self, tmp_path):
        """A wrong-shaped cached terrain mask is skipped, not fatal (#198 review)."""
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir)
        # Plant a terrain mask for a different (smaller) grid.
        out_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            out_dir / "terrain_mask.npz",
            mask=np.ones((3, 3), dtype=bool),
            lat=np.linspace(45, 47, 3), lon=np.linspace(0, 2, 3),
        )
        analyses = _route_analyses()
        manifest = compute_route_fronts(
            [(a.lat, a.lon) for a in analyses],
            [a.interpolated_time for a in analyses],
            route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], output_dir=out_dir,
        )
        # Detection still runs (mask ignored) — no shape-mismatch crash.
        assert manifest.models == ["ecmwf"]
        assert manifest.per_model["ecmwf"]


class TestRunFronts:
    def test_writes_artifact_and_roundtrips(self, tmp_path):
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()

        manifest = run_fronts(
            _route_analyses(), route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], pack_dir=pack_dir, output_dir=out_dir,
        )
        assert manifest is not None
        assert (pack_dir / "route_fronts.json").exists()
        loaded = load_route_fronts(pack_dir)
        assert loaded is not None
        assert loaded.models == ["ecmwf"]
        assert loaded.per_model["ecmwf"]

    def test_alt_out_name_writes_separate_artifact(self, tmp_path):
        # The alt-departure re-run writes route_fronts_alt.json without touching
        # the primary artifact, and load_route_fronts(filename=...) reads it back.
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()

        manifest = run_fronts(
            _route_analyses(), route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], pack_dir=pack_dir, output_dir=out_dir,
            out_name="route_fronts_alt.json",
        )
        assert manifest is not None
        assert (pack_dir / "route_fronts_alt.json").exists()
        assert not (pack_dir / "route_fronts.json").exists()
        assert load_route_fronts(pack_dir, filename="route_fronts_alt.json") is not None
        assert load_route_fronts(pack_dir) is None  # primary untouched

    def test_skips_route_without_etas(self, tmp_path):
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        # Single point → fewer than 2 ETA-stamped waypoints.
        one = _route_analyses()[:1]
        assert run_fronts(
            one, route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], pack_dir=pack_dir, output_dir=out_dir,
        ) is None
        assert not (pack_dir / "route_fronts.json").exists()


class TestSnapshotForWindow:
    """Valid-time snapshot selection (#201) — pick the init that brackets the
    flight window, not merely the newest."""

    def test_prefers_latest_init_that_brackets(self, tmp_path):
        from weatherbrief.hewson.precompute import snapshot_for_window
        out_dir = tmp_path / "hewson"
        # Two inits 12 h apart; both cover [15Z, 18Z]. Expect the newer (12Z).
        _write_front_snapshot(out_dir, init_dt=_INIT_DT)
        _write_front_snapshot(out_dir, init_dt=_INIT_DT + timedelta(hours=12))
        sel = snapshot_for_window(
            "ecmwf", _INIT_DT + timedelta(hours=15), _INIT_DT + timedelta(hours=18),
            output_dir=out_dir,
        )
        assert sel is not None
        assert sel.stem == "2026-05-31T12:00:00Z"

    def test_none_when_window_before_all_inits(self, tmp_path):
        from weatherbrief.hewson.precompute import snapshot_for_window
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir, init_dt=_INIT_DT)
        # A past flight: window precedes the only init → no bracketing snapshot.
        assert snapshot_for_window(
            "ecmwf", _INIT_DT - timedelta(hours=6), _INIT_DT - timedelta(hours=3),
            output_dir=out_dir,
        ) is None

    def test_none_when_window_past_horizon(self, tmp_path):
        from weatherbrief.hewson.precompute import snapshot_for_window
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir, init_dt=_INIT_DT)  # covers 0..24 h
        assert snapshot_for_window(
            "ecmwf", _INIT_DT + timedelta(hours=30), _INIT_DT + timedelta(hours=33),
            output_dir=out_dir,
        ) is None

    def test_missing_model_dir(self, tmp_path):
        from weatherbrief.hewson.precompute import snapshot_for_window
        assert snapshot_for_window(
            "ecmwf", _INIT_DT, _INIT_DT + timedelta(hours=1),
            output_dir=tmp_path / "empty",
        ) is None


class TestWindowSelectionInCompute:
    """compute_route_fronts wires snapshot-by-window with a logged fallback (#201)."""

    def test_picks_bracketing_init_over_newest(self, tmp_path):
        out_dir = tmp_path / "hewson"
        # Older init brackets the flight; a much newer init does NOT (starts +48h).
        _write_front_snapshot(out_dir, init_dt=_INIT_DT)
        _write_front_snapshot(out_dir, init_dt=_INIT_DT + timedelta(hours=48))
        analyses = _route_analyses()  # flight at init + 6 h
        manifest = compute_route_fronts(
            [(a.lat, a.lon) for a in analyses],
            [a.interpolated_time for a in analyses],
            route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], output_dir=out_dir,
        )
        # latest_snapshot would have picked the +48h init; window selection
        # picks the older init that actually brackets the flight.
        assert manifest.snapshot_inits["ecmwf"] == "2026-05-31T00:00:00Z"

    def test_falls_back_to_latest_with_note(self, tmp_path):
        out_dir = tmp_path / "hewson"
        # Only a future init exists; the flight precedes it → no bracket → fall
        # back to the latest available init and note the approximation.
        _write_front_snapshot(out_dir, init_dt=_INIT_DT + timedelta(hours=48))
        analyses = _route_analyses()  # flight at _INIT_DT + 6 h (before the init)
        manifest = compute_route_fronts(
            [(a.lat, a.lon) for a in analyses],
            [a.interpolated_time for a in analyses],
            route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], output_dir=out_dir,
        )
        assert manifest.snapshot_inits["ecmwf"] == "2026-06-02T00:00:00Z"
        assert any("latest available init" in n for n in manifest.notes)


class TestPerModelPrimaryLevel:
    """primary_level_hPa is tracked per model, not last-model-wins (#203)."""

    def test_partial_snapshot_gets_own_primary(self, tmp_path):
        out_dir = tmp_path / "hewson"
        # ecmwf carries all three levels; gfs is a partial snapshot (850 only).
        _write_front_snapshot(out_dir, model="ecmwf", levels=(925, 850, 700))
        _write_front_snapshot(out_dir, model="gfs", levels=(850,))
        analyses = _route_analyses()
        manifest = compute_route_fronts(
            [(a.lat, a.lon) for a in analyses],
            [a.interpolated_time for a in analyses],
            route_name="r", cruise_altitude_ft=11000,  # nearest cruise → 700
            advisory_models=["ecmwf", "gfs"], output_dir=out_dir,
        )
        # ecmwf has 700 (nearest to FL110); gfs only exposes 850, so its own
        # nearest-cruise primary is 850 — not flattened to ecmwf's 700.
        assert manifest.per_model_primary_hPa == {"ecmwf": 700, "gfs": 850}
