"""Tests for weather-based alternate airports (issue #210).

Covers:
- geometry: before/after classification + the detour pair
- candidate filters: instrument-approach gate, runway suitability; major/scheduled
  fields are returned and flagged (is_major), not excluded
- consistency: the shared assembly yields the same category/crosswind as the
  forecast map's ``map_queries`` wrappers for a fixed snapshot ("Seam 2").
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from weatherbrief.models.analysis import RouteConfig, Waypoint
from weatherbrief.tasks import alternates as alt_mod
from weatherbrief.tasks.alternates import run_alternates

NOW = datetime(2026, 6, 5, 6, 0, 0, tzinfo=timezone.utc)
DEPARTURE = datetime(2026, 6, 6, 8, 0, 0, tzinfo=timezone.utc)  # D-1 → stage gate ok


# ---------------------------------------------------------------------------
# Fake euro_aip model
# ---------------------------------------------------------------------------


class _FakeApproaches:
    def __init__(self, exists: bool, best_type: str | None):
        self._exists = exists
        self._best_type = best_type

    def exists(self) -> bool:
        return self._exists

    def most_precise(self):
        if not self._exists:
            return None
        return SimpleNamespace(approach_type=self._best_type)


class _FakeProceduresQuery:
    def __init__(self, exists: bool, best_type: str | None):
        self._approaches = _FakeApproaches(exists, best_type)

    def approaches(self):
        return self._approaches


class _FakeAirport:
    def __init__(
        self, ident, lat, lon, *,
        type="small_airport", scheduled_service="no",
        has_hard_runway=True, longest_runway_length_ft=4000,
        point_of_entry=False, has_approach=True, best_approach="ILS", name=None,
    ):
        self.ident = ident
        self.latitude_deg = lat
        self.longitude_deg = lon
        self.type = type
        self.scheduled_service = scheduled_service
        self.has_hard_runway = has_hard_runway
        self.longest_runway_length_ft = longest_runway_length_ft
        self.point_of_entry = point_of_entry
        self.name = name or ident
        self._pq = _FakeProceduresQuery(has_approach, best_approach)

    @property
    def procedures_query(self):
        return self._pq


class _FakeAirportCollection:
    def __init__(self, airports):
        self._airports = airports

    def all(self):
        return self._airports

    def get(self, icao):
        return next((a for a in self._airports if a.ident == icao), None)


class _FakeModel:
    def __init__(self, near_results, all_airports=None):
        self._near = near_results
        self.airports = _FakeAirportCollection(all_airports or [])

    def find_airports_near_route(self, route_icaos, distance_nm=50.0):
        return self._near


def _route():
    return RouteConfig(
        name="EGKK-EGPF",
        waypoints=[
            Waypoint(icao="EGKK", name="Gatwick", lat=51.15, lon=-0.18),
            Waypoint(icao="EGPF", name="Glasgow", lat=55.87, lon=-4.43),
        ],
        flight_duration_hours=2.0,
    )


def _snap(icao, model, *, ceiling=5000.0, vis_m=9999.0, ws=8.0, wd=270.0):
    """A column-keyed snapshot dict (keys == AirportForecastSnapshotRow columns)."""
    return {
        "icao": icao,
        "model": model,
        "model_init_time": NOW,
        "forecast_hour": NOW,
        "sounding_ceiling_ft": ceiling,
        "nwp_ceiling_ft": None,
        "cloud_base_ft": None,
        "lcl_ft": None,
        "visibility_m": vis_m,
        "wind_speed_10m_kt": ws,
        "wind_direction_10m_deg": wd,
        "wind_gusts_10m_kt": None,
        "cloud_cover_pct": 20.0,
        "cape_jkg": 10.0,
        "sounding_convective_risk": "none",
        "temperature_2m_c": 14.0,
    }


def _all_models(icao, **kw):
    return {m: _snap(icao, m, **kw) for m in ("gfs", "icon", "ecmwf")}


def _run(route, near_results, snapshots_by_icao, *, all_airports=None, runways=None):
    """Drive run_alternates with euro_aip + fetch fully mocked."""
    model = _FakeModel(near_results, all_airports=all_airports)
    with patch("weatherbrief.airports._load_airport_model", return_value=model), \
         patch("weatherbrief.airports.get_runway_ends", return_value=runways or {}), \
         patch.object(alt_mod, "_fetch_eta_snapshots", return_value=snapshots_by_icao):
        return run_alternates(
            route=route,
            target_time=DEPARTURE,
            airports_db_path="/fake/nav.db",
            now=NOW,
        )


# ---------------------------------------------------------------------------
# Geometry: before / after + detour pair
# ---------------------------------------------------------------------------


def test_before_after_and_detour_pair():
    route = _route()
    # A "before" field near the departure, an "after" field near the destination.
    before = _FakeAirport("EGTC", 51.50, -0.50)
    after = _FakeAirport("EGPN", 55.50, -3.40)
    near = [
        {"airport": before, "enroute_distance_nm": 30.0, "segment_distance_nm": 8.0},
        {"airport": after, "enroute_distance_nm": None, "segment_distance_nm": 12.0},
    ]
    snaps = {
        "EGPF": _all_models("EGPF"),
        "EGTC": _all_models("EGTC"),
        "EGPN": _all_models("EGPN"),
    }
    result = _run(route, near, snaps)
    assert result is not None
    by_icao = {a.icao: a for a in result.alternates}

    assert by_icao["EGTC"].position == "before"
    assert by_icao["EGPN"].position == "after"

    # The whole point of the detour pair: a "before" field is cheap to divert to
    # early but expensive late (you'd backtrack from the destination).
    egtc = by_icao["EGTC"]
    assert egtc.detour_early_nm < egtc.detour_late_nm

    # Closest-first ranking.
    dists = [a.distance_from_dest_nm for a in result.alternates]
    assert dists == sorted(dists)


# ---------------------------------------------------------------------------
# Per-candidate instrument-approach gate
# ---------------------------------------------------------------------------


def test_per_candidate_approach_gate():
    # The gate is per-candidate, by the candidate's OWN weather (not the
    # destination's): a sub-VFR field with no approach is dropped; a VFR field
    # with no approach is kept (visual divert); a sub-VFR field WITH an approach
    # is kept.
    route = _route()
    ifr_iap = _FakeAirport("EGAA", 54.66, -6.22, has_approach=True, best_approach="ILS")
    ifr_no_iap = _FakeAirport("EGAE", 55.04, -7.16, has_approach=False, best_approach=None)
    vfr_no_iap = _FakeAirport("EGAC", 54.62, -5.87, has_approach=False, best_approach=None)
    near = [
        {"airport": ifr_iap, "enroute_distance_nm": 120.0, "segment_distance_nm": 10.0},
        {"airport": ifr_no_iap, "enroute_distance_nm": 130.0, "segment_distance_nm": 15.0},
        {"airport": vfr_no_iap, "enroute_distance_nm": 125.0, "segment_distance_nm": 12.0},
    ]
    snaps = {
        "EGPF": _all_models("EGPF", ceiling=600.0),   # destination IFR (irrelevant to the gate)
        "EGAA": _all_models("EGAA", ceiling=600.0),   # IFR + approach  → kept
        "EGAE": _all_models("EGAE", ceiling=600.0),   # IFR + no approach → dropped
        "EGAC": _all_models("EGAC", ceiling=5000.0),  # VFR + no approach → kept (visual divert)
    }
    result = _run(route, near, snaps)
    assert result is not None
    assert result.approach_filter_relaxed is False
    icaos = {a.icao for a in result.alternates}
    assert icaos == {"EGAA", "EGAC"}  # IFR-no-approach dropped; VFR-no-approach kept


def test_approach_gate_independent_of_destination_vfr():
    # Even when the destination is VFR, a sub-VFR candidate with no approach is
    # still unusable in its own conditions → dropped. A VFR candidate is kept.
    # (An approach-bearing candidate is present so procedure data exists and the
    # relaxation safety net does not trigger.)
    route = _route()
    ifr_iap = _FakeAirport("EGAA", 54.66, -6.22, has_approach=True, best_approach="ILS")
    ifr_no_iap = _FakeAirport("EGAE", 55.04, -7.16, has_approach=False, best_approach=None)
    vfr_no_iap = _FakeAirport("EGAC", 54.62, -5.87, has_approach=False, best_approach=None)
    near = [
        {"airport": ifr_iap, "enroute_distance_nm": 120.0, "segment_distance_nm": 10.0},
        {"airport": ifr_no_iap, "enroute_distance_nm": 130.0, "segment_distance_nm": 15.0},
        {"airport": vfr_no_iap, "enroute_distance_nm": 125.0, "segment_distance_nm": 12.0},
    ]
    snaps = {
        "EGPF": _all_models("EGPF", ceiling=5000.0),  # destination VFR
        "EGAA": _all_models("EGAA", ceiling=600.0),   # IFR + approach → kept
        "EGAE": _all_models("EGAE", ceiling=600.0),   # IFR + no approach → dropped
        "EGAC": _all_models("EGAC", ceiling=5000.0),  # VFR + no approach → kept
    }
    result = _run(route, near, snaps)
    assert result is not None
    assert result.require_approach is False  # informational: destination is VFR
    assert result.approach_filter_relaxed is False
    assert {a.icao for a in result.alternates} == {"EGAA", "EGAC"}


def test_approach_gate_relaxed_when_no_procedure_data():
    # Sub-VFR candidates lack an approach AND no candidate has any approach data
    # (procedure data absent from the airport DB) → don't go dark; keep them flagged.
    route = _route()
    a = _FakeAirport("EGKE", 51.10, -0.20, has_approach=False, best_approach=None)
    b = _FakeAirport("EGKH", 51.05, -0.30, has_approach=False, best_approach=None)
    near = [
        {"airport": a, "enroute_distance_nm": 200.0, "segment_distance_nm": 8.0},
        {"airport": b, "enroute_distance_nm": 205.0, "segment_distance_nm": 9.0},
    ]
    snaps = {
        "EGPF": _all_models("EGPF", ceiling=600.0),
        "EGKE": _all_models("EGKE", ceiling=600.0),  # IFR + no approach
        "EGKH": _all_models("EGKH", ceiling=600.0),  # IFR + no approach
    }
    result = _run(route, near, snaps)
    assert result is not None
    assert result.approach_filter_relaxed is True
    # Both shown (flagged), not dropped — missing reference data, not a real gate.
    assert {a.icao for a in result.alternates} == {"EGKE", "EGKH"}


# ---------------------------------------------------------------------------
# GA-appropriateness filters
# ---------------------------------------------------------------------------


def test_major_and_scheduled_returned_and_flagged():
    """large_airport / scheduled_service are no longer dropped — they are
    returned and flagged so the UI can hide them by default. Only is_major
    (== large_airport) marks a candidate; scheduled-service regional fields
    (e.g. EGTE Exeter) stay non-major and visible (the #-regression)."""
    route = _route()
    ok = _FakeAirport("EGPN", 55.50, -3.40)
    large = _FakeAirport("EGPK", 55.51, -4.59, type="large_airport")
    scheduled = _FakeAirport("EGPH", 55.95, -3.37, scheduled_service="yes")
    near = [
        {"airport": ok, "enroute_distance_nm": 200.0, "segment_distance_nm": 5.0},
        {"airport": large, "enroute_distance_nm": 205.0, "segment_distance_nm": 6.0},
        {"airport": scheduled, "enroute_distance_nm": 210.0, "segment_distance_nm": 7.0},
    ]
    snaps = {
        "EGPF": _all_models("EGPF"),
        "EGPN": _all_models("EGPN"),
        "EGPK": _all_models("EGPK"),
        "EGPH": _all_models("EGPH"),
    }
    result = _run(route, near, snaps)
    assert result is not None
    # All three now survive (each has a hard runway, length and an approach).
    by_icao = {a.icao: a for a in result.alternates}
    assert set(by_icao) == {"EGPN", "EGPK", "EGPH"}
    # Only the large_airport is flagged major.
    assert by_icao["EGPK"].is_major is True
    # A scheduled-service regional field is NOT major and stays visible.
    assert by_icao["EGPH"].is_major is False
    assert by_icao["EGPN"].is_major is False


def test_short_runway_excluded():
    route = _route()
    short = _FakeAirport("EGPN", 55.50, -3.40, longest_runway_length_ft=1200)
    ok = _FakeAirport("EGPT", 55.40, -3.50, longest_runway_length_ft=4000)
    near = [
        {"airport": short, "enroute_distance_nm": 200.0, "segment_distance_nm": 5.0},
        {"airport": ok, "enroute_distance_nm": 195.0, "segment_distance_nm": 5.0},
    ]
    snaps = {
        "EGPF": _all_models("EGPF"),
        "EGPN": _all_models("EGPN"),
        "EGPT": _all_models("EGPT"),
    }
    result = _run(route, near, snaps)
    assert result is not None
    assert {a.icao for a in result.alternates} == {"EGPT"}


# ---------------------------------------------------------------------------
# Nearest-improving picks
# ---------------------------------------------------------------------------


def test_nearest_improving_category_pick():
    route = _route()
    # Destination IFR; a nearer VFR field and a farther VFR field.
    near_vfr = _FakeAirport("EGPN", 55.60, -3.80)
    far_vfr = _FakeAirport("EGAA", 54.66, -6.22)
    near = [
        {"airport": near_vfr, "enroute_distance_nm": 220.0, "segment_distance_nm": 5.0},
        {"airport": far_vfr, "enroute_distance_nm": 150.0, "segment_distance_nm": 10.0},
    ]
    snaps = {
        "EGPF": _all_models("EGPF", ceiling=600.0),  # IFR
        "EGPN": _all_models("EGPN", ceiling=5000.0),  # VFR
        "EGAA": _all_models("EGAA", ceiling=5000.0),  # VFR
    }
    result = _run(route, near, snaps)
    assert result is not None
    picks = {p.axis: p for p in result.nearest_improving}
    cat_pick = picks["category"]
    # The geographically nearer VFR field wins the category axis.
    assert cat_pick.icao == "EGPN"
    egpn = next(a for a in result.alternates if a.icao == "EGPN")
    assert egpn.better_category is True


# ---------------------------------------------------------------------------
# Consistency: shared assembly == map_queries wrappers (Seam 2)
# ---------------------------------------------------------------------------


def test_shared_assembly_matches_map_queries():
    from weatherbrief.analysis import airport_consensus as ac
    from weatherbrief.models.airport_conditions import RunwayEnd
    from weatherbrief.tasks import map_queries as mq

    snap_dict = _snap("EGPF", "gfs", ceiling=800.0, vis_m=5000.0, ws=18.0, wd=240.0)
    row = SimpleNamespace(**snap_dict)

    # snap_to_dict: same lightweight per-model dict from a dict and from a row.
    shared = ac.snap_to_dict(snap_dict)
    via_row = mq._snap_to_dict(row)
    assert shared == via_row

    # enrich_wind: identical crosswind/headwind on the best runway.
    runways = [RunwayEnd(id="05", heading_deg=50.0), RunwayEnd(id="23", heading_deg=230.0)]
    d_shared = dict(shared)
    d_row = dict(via_row)
    ac.enrich_wind(d_shared, runways)
    mq._enrich_wind(d_row, runways)
    assert d_shared == d_row
    assert d_shared["crosswind_kt"] == pytest.approx(d_row["crosswind_kt"])

    # consensus: identical category + worst crosswind across models.
    per_model = {"gfs": d_shared, "icon": dict(d_shared)}
    assert ac.consensus(per_model) == mq._consensus(per_model)


# ---------------------------------------------------------------------------
# Stage timing optimisation (issue #271): concurrent model passes + decode
# priority inheritance.
# ---------------------------------------------------------------------------


def _meta(epoch: int):
    """Minimal ModelMetadata stub for the up-front metadata call."""
    from weatherbrief.fetch.model_status import ModelMetadata

    return ModelMetadata(
        model="x",
        last_init_time=epoch,
        last_availability_time=epoch,
        update_interval_seconds=21600,
    )


def test_fetch_eta_snapshots_runs_models_concurrently_and_inherits_priority():
    """The 3 model passes overlap (Change 1) and the decode priority set on the
    caller's ContextVar reaches each worker thread (Change 1 × Change 2).

    A ``threading.Barrier(3)`` proves concurrency without timing flakiness: a
    serial loop can never get 3 threads to the barrier at once, so it would time
    out and the model would drop out of the result. ``_resolve_priority`` is
    recorded inside each worker to prove ``copy_context`` propagated the
    INTERACTIVE ContextVar across the thread boundary.
    """
    import threading
    from contextvars import copy_context

    from weatherbrief.fetch.grib import (
        DecodePriority,
        _resolve_priority,
        set_decode_priority,
    )
    from weatherbrief.tasks.airport_watchlist import WatchlistAirport

    eta = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    airports = [WatchlistAirport(icao="EGTE", lat=50.73, lon=-3.41)]
    meta_map = {m: _meta(int(eta.timestamp())) for m in alt_mod._MODELS}

    barrier = threading.Barrier(len(alt_mod._MODELS), timeout=5)
    resolved: dict[str, int] = {}
    lock = threading.Lock()

    def fake_fetch_forecasts(model, init_time, airports, session, sample_hours=None, **kw):
        # All three model passes must be in-flight at once, or this times out.
        barrier.wait()
        snap = {"icao": airports[0].icao, "model": model, "forecast_hour": eta}
        return [snap], 0

    def fake_enrich(snaps, model, init_time, airports, session, priority=None):
        with lock:
            resolved[model] = _resolve_priority(priority)

    sv = "weatherbrief.tasks.standalone_verification"
    with patch("weatherbrief.fetch.model_status.fetch_model_metadata", return_value=meta_map), \
         patch(f"{sv}._fetch_forecasts_for_model", fake_fetch_forecasts), \
         patch(f"{sv}._enrich_with_grib", fake_enrich), \
         patch(f"{sv}._select_ecmwf_grib_run", return_value=None):
        # Run inside a copied context so set_decode_priority can't leak out.
        ctx = copy_context()

        def _run():
            set_decode_priority(DecodePriority.INTERACTIVE)
            return alt_mod._fetch_eta_snapshots(airports, eta)

        by_icao = ctx.run(_run)

    # All three models contributed (barrier did not time out → ran concurrently).
    assert set(by_icao["EGTE"].keys()) == set(alt_mod._MODELS)
    # The INTERACTIVE ContextVar reached every worker thread.
    assert resolved == {m: int(DecodePriority.INTERACTIVE) for m in alt_mod._MODELS}


def test_grib_helper_priority_resolves_contextvar_vs_explicit():
    """``fetch_gfs_cloud_diag`` lets ``priority=None`` fall through to the
    ContextVar (INTERACTIVE for an interactive briefing), while an explicit
    ``BACKGROUND`` (the standalone cycle) still wins over it.
    """
    from contextvars import copy_context

    from weatherbrief.fetch.grib import (
        DecodePriority,
        _resolve_priority,
        set_decode_priority,
    )
    from weatherbrief.tasks.standalone_grib import fetch_gfs_cloud_diag

    seen: list[int] = []

    def fake_dispatch(name, path, lats, lons, priority=None):
        seen.append(_resolve_priority(priority))
        return []  # empty decode → helper returns {} without touching GRIB

    def _run():
        set_decode_priority(DecodePriority.INTERACTIVE)
        with patch("weatherbrief.tasks.standalone_grib.is_cached", return_value=True), \
             patch("weatherbrief.fetch.grib._dispatch_decode", fake_dispatch):
            # Interactive path: no explicit priority → inherits the ContextVar.
            fetch_gfs_cloud_diag("20260618", 0, [6], [50.0], [0.0])
            # Standalone path: explicit BACKGROUND wins over the ContextVar.
            fetch_gfs_cloud_diag(
                "20260618", 0, [6], [50.0], [0.0],
                priority=DecodePriority.BACKGROUND,
            )

    copy_context().run(_run)

    assert seen == [int(DecodePriority.INTERACTIVE), int(DecodePriority.BACKGROUND)]
