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
    _link_front_chains,
    _persistence,
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

    def test_cin_capped_but_negative_li_stays_convective(self):
        # Symmetric to the ML-CAPE gate: a strong cap (CIN -80) with a negative
        # lifted index (LI -4 <= -2) is realizable → still convective, EL kept.
        cl = [{"top_ft": 9000.0, "base_pressure_hpa": 900, "top_pressure_hpa": 850, "coverage": "sct"}]
        conv = {
            "risk_level": "moderate", "method": "thermo",
            "cin_jkg": -80.0, "el_altitude_ft": 34000.0, "top_ft": 34000.0,
            "lifted_index": -4.0,
        }
        indices = {"cape_mixed_layer_jkg": 120.0, "nwp_lifted_index": None}
        cat, top = _colocate(self._an_full(cl, conv, indices), "ecmwf", 0.0, 850)
        assert cat == "convective" and top == 34000.0

    def test_cin_capped_no_instability_data_defaults_realized(self):
        # Item #1 (PR #217 review): strong CIN but NO countervailing data — both
        # ML-CAPE and LI absent (e.g. ICON, which emits no lifted index). We must
        # NOT silently downgrade a real front on CIN alone → default to realized.
        cl = [{"top_ft": 9000.0, "base_pressure_hpa": 900, "top_pressure_hpa": 850, "coverage": "sct"}]
        conv = {
            "risk_level": "moderate", "method": "thermo",
            "cin_jkg": -90.0, "el_altitude_ft": 30000.0, "top_ft": 30000.0,
            "lifted_index": None,
        }
        indices = {"cape_mixed_layer_jkg": None, "nwp_lifted_index": None}
        cat, top = _colocate(self._an_full(cl, conv, indices), "ecmwf", 0.0, 850)
        assert cat == "convective" and top == 30000.0

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

    def test_wet_front_top_from_cloud_not_el(self):
        # Item #5 (PR #217 review): a non-convective (wet) front must take its
        # weather_top from the spanning cloud layer, never the parcel EL. Here a
        # low OVC spans the 850 hPa front (top 11000) while the sounding still
        # carries a high EL (28000); weather_top must be 11000, not the EL.
        cl = [{"top_ft": 11000.0, "base_pressure_hpa": 900, "top_pressure_hpa": 700, "coverage": "ovc"}]
        conv = {"risk_level": "low", "method": "thermo", "el_altitude_ft": 28000.0, "top_ft": 28000.0}
        indices = {"cape_mixed_layer_jkg": 80.0, "nwp_lifted_index": 1.0}
        cat, top = _colocate(self._an_full(cl, conv, indices), "ecmwf", 0.0, 850)
        assert cat == "wet" and top == 11000.0

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


class TestVerticalLinking:
    """Cross-level linking: a real front slopes through several levels into one
    chain; the linker gates on kind, Δθe sign, and a slope budget, and stamps
    each crossing with its chain depth (vertical_levels; 1 = shallow/suspect)."""

    def test_same_feature_across_two_levels_links(self):
        # Cold front at 925 (355 km) and 850 (387 km) — 32 km apart, well inside
        # the 925→850 budget → one chain spanning 2 levels.
        analyses = [_analysis(850, _xing(387.0)), _analysis(925, _xing(355.0))]
        chains = _link_front_chains(analyses)
        assert len(chains) == 1
        assert chains[0].n_levels == 2
        assert [n.level_hPa for n in chains[0].nodes] == [925, 850]  # bottom→top
        assert chains[0].kind == "cold"
        assert analyses[0].crossings[0].vertical_levels == 2
        assert analyses[1].crossings[0].vertical_levels == 2

    def test_single_level_feature_is_shallow(self):
        # A 925-only boundary far from any other-level crossing → 2 singleton chains.
        analyses = [_analysis(925, _xing(84.0)), _analysis(850, _xing(387.0))]
        chains = _link_front_chains(analyses)
        assert sorted(c.n_levels for c in chains) == [1, 1]
        assert analyses[0].crossings[0].vertical_levels == 1
        assert analyses[1].crossings[0].vertical_levels == 1

    def test_three_levels_link_into_one_sloping_chain(self):
        # 925@350, 850@370, 700@390: gaps 20 + 20, each within budget → one
        # 3-level chain. Δθe < 0 (cold downroute) and the front shifts to larger
        # distance with height → the physical coldward slope.
        analyses = [
            _analysis(925, _xing(350.0)),
            _analysis(850, _xing(370.0)),
            _analysis(700, _xing(390.0)),
        ]
        chains = _link_front_chains(analyses)
        assert len(chains) == 1 and chains[0].n_levels == 3
        assert chains[0].tilt == "coldward"
        assert all(a.crossings[0].vertical_levels == 3 for a in analyses)

    def test_warm_front_displaced_beyond_merge_km_still_links(self):
        # The key improvement over the old merge_km (60 km) cluster: a warm front
        # slopes shallowly, so 925→850 can be ~90 km apart and still be one front.
        analyses = [
            _analysis(925, _xing(200.0, kind="warm", delta_theta_e=12.0, advection=2.0)),
            _analysis(850, _xing(290.0, kind="warm", delta_theta_e=12.0, advection=2.0)),
        ]
        chains = _link_front_chains(analyses)
        assert len(chains) == 1 and chains[0].n_levels == 2
        assert chains[0].kind == "warm"

    def test_kind_mismatch_does_not_link(self):
        # A cold front at 925 and a warm front at 850 sitting nearby are two
        # different boundaries — never one chain.
        analyses = [
            _analysis(925, _xing(300.0, kind="cold", delta_theta_e=-12.0)),
            _analysis(850, _xing(320.0, kind="warm", delta_theta_e=12.0, advection=2.0)),
        ]
        chains = _link_front_chains(analyses)
        assert sorted(c.n_levels for c in chains) == [1, 1]

    def test_opposite_delta_theta_e_sign_does_not_link(self):
        # Same kind label but the air-mass contrast points opposite ways → not the
        # same boundary.
        analyses = [
            _analysis(925, _xing(300.0, kind="cold", delta_theta_e=-12.0)),
            _analysis(850, _xing(315.0, kind="cold", delta_theta_e=12.0)),
        ]
        chains = _link_front_chains(analyses)
        assert sorted(c.n_levels for c in chains) == [1, 1]

    def test_beyond_slope_budget_does_not_link(self):
        # 925@250 and 850@400 are 150 km apart — past the 100 km cold 925→850
        # budget → two separate chains.
        analyses = [_analysis(925, _xing(250.0)), _analysis(850, _xing(400.0))]
        chains = _link_front_chains(analyses)
        assert sorted(c.n_levels for c in chains) == [1, 1]

    def test_zero_delta_theta_e_still_links(self):
        # A crossing with exactly Δθe == 0 carries no contrast direction; it must
        # not be rejected by np.sign(0) against a signed same-kind neighbour
        # within budget (PR #280 review #4).
        analyses = [
            _analysis(925, _xing(300.0, delta_theta_e=0.0)),
            _analysis(850, _xing(320.0, delta_theta_e=-10.0)),
        ]
        chains = _link_front_chains(analyses)
        assert len(chains) == 1 and chains[0].n_levels == 2

    def test_chain_nodes_carry_co_location(self):
        # co_location propagates to chain nodes so the cross-section can draw the
        # convective glyph (PR #280 review #2).
        analyses = [
            _analysis(925, _xing(300.0, co_location="convective")),
            _analysis(850, _xing(320.0, co_location="wet")),
        ]
        chains = _link_front_chains(analyses)
        assert len(chains) == 1
        assert [n.co_location for n in chains[0].nodes] == ["convective", "wet"]

    def test_empty_returns_no_chains(self):
        assert _link_front_chains([]) == []


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

        # The meridional cold front is the same boundary at all three levels, so
        # the linker chains it vertically (depth ≥ 2) and exposes it in front_chains.
        chains = manifest.front_chains["ecmwf"]
        assert chains, "expected at least one linked front chain"
        deepest = max(chains, key=lambda c: c.n_levels)
        assert deepest.n_levels >= 2
        assert deepest.kind == "cold"
        assert [n.level_hPa for n in deepest.nodes] == sorted(
            (n.level_hPa for n in deepest.nodes), reverse=True
        )  # ordered bottom→top (925→850→700)

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

    def test_level_aware_terrain_masks_low_level_only(self, tmp_path):
        """#216 Fix 3: terrain reaching the 925 hPa surface (~762 m) masks the
        925 crossing while leaving the 850/700 hPa fronts intact. A planted
        elevation grid (1200 m over the route's front-crossing region) is below
        the 850/700 surfaces but above 925's."""
        out_dir = tmp_path / "hewson"
        _write_front_snapshot(out_dir, levels=(925, 850, 700))
        lat = np.linspace(45.0, 52.0, 29)
        lon = np.linspace(-2.0, 6.0, 33)
        la_grid, lo_grid = np.meshgrid(lat, lon, indexing="ij")
        elev = np.zeros((lat.size, lon.size))
        # High terrain over the crossing neighbourhood (route lat 47, front lon ~2).
        elev[(np.abs(la_grid - 47.0) < 2.0) & (np.abs(lo_grid - 2.0) < 2.5)] = 1200.0
        out_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            out_dir / "terrain_mask.npz",
            mask=np.ones((lat.size, lon.size), dtype=bool),  # flat fallback (unused)
            lat=lat, lon=lon, elevation=elev,
        )
        analyses = _route_analyses()
        manifest = compute_route_fronts(
            [(a.lat, a.lon) for a in analyses],
            [a.interpolated_time for a in analyses],
            route_name="r", cruise_altitude_ft=5000,
            advisory_models=["ecmwf"], output_dir=out_dir,
        )

        def n_cross(level: int) -> int:
            return sum(len(a.crossings) for a in manifest.per_model["ecmwf"]
                       if a.level_hPa == level)

        assert n_cross(700) >= 1   # free-atmosphere front intact (not over-masked)
        assert n_cross(850) >= 1   # 1200m < 850 hPa surface (~1457m) → not masked
        assert n_cross(925) == 0   # low-level crossing suppressed by level-aware mask


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
