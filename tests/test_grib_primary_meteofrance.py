"""Météo-France slot built from AROME GRIB only (#940).

The gate (kill switch, domain, horizon), the ``run_fetch`` wiring (no
Open-Meteo request for meteofrance in any case: covered, refused, drilled or
short), the no-fallback drop, the AROME surface mapping (units, ``tp``
de-accumulation, the ``0/6/1`` cloud field) and its write onto the skeleton,
the refresh gate counting advisory models only, and the AROME flight warm.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from weatherbrief.fetch.grib import arome_fetch as af
from weatherbrief.fetch.grib import primary
from weatherbrief.fetch.grib.primary import (
    NO_OPEN_METEO_MODELS,
    build_skeleton,
    fill_grib_primary_gaps,
    grib_primary_skip_reason,
    required_hours,
)
from weatherbrief.models import ModelSource, RouteCrossSection, RoutePoint
from weatherbrief.tasks import fetch as fetch_module

from tests.test_fetch_parallel import _disable_elevation, _route
from tests.test_grib_primary import _FakeClient, _fill_window

UTC = timezone.utc
NOW = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)
DEP = datetime(2026, 10, 26, 9, 30, tzinfo=UTC)  # 2 h flight, inside the 12z run
PARIS = (48.97, 2.44)
LONDON = (51.5, -0.4)


def _pts(*latlons) -> list[RoutePoint]:
    return [
        RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=10.0 * i)
        for i, (lat, lon) in enumerate(latlons)
    ]


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def test_meteofrance_is_grib_primary_without_open_meteo():
    assert ModelSource.METEOFRANCE in primary.GRIB_PRIMARY_MODELS
    assert NO_OPEN_METEO_MODELS == {ModelSource.METEOFRANCE}


@pytest.mark.parametrize("env,route,dep,expected", [
    (None, (LONDON, PARIS), DEP, None),
    ("1", (LONDON, PARIS), DEP, None),
    ("0", (LONDON, PARIS), DEP, "disabled"),
    (None, (LONDON, (56.0, -3.2)), DEP, "out_of_domain"),       # Edinburgh
    (None, (LONDON, PARIS), NOW + timedelta(days=3), "out_of_range"),
])
def test_gate(monkeypatch, env, route, dep, expected):
    monkeypatch.delenv("WB_GRIB_PRIMARY", raising=False)
    if env is None:
        monkeypatch.delenv(af.AROME_ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(af.AROME_ENABLED_ENV, env)
    assert grib_primary_skip_reason(
        ModelSource.METEOFRANCE, _pts(*route), dep, 2.0, as_of_time=NOW,
    ) == expected


def test_gate_historical_is_not_refused(monkeypatch):
    """The mirror keeps ~15 days: a replay tries AROME, never Open-Meteo."""
    monkeypatch.delenv(af.AROME_ENABLED_ENV, raising=False)
    as_of = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
    assert grib_primary_skip_reason(
        ModelSource.METEOFRANCE, _pts(LONDON, PARIS), as_of + timedelta(hours=1), 2.0,
        as_of_time=as_of, historical_mode=True,
    ) is None


def test_gate_switch_excludes_meteofrance(monkeypatch):
    monkeypatch.setenv("WB_GRIB_PRIMARY", "ecmwf,icon")
    assert grib_primary_skip_reason(
        ModelSource.METEOFRANCE, _pts(LONDON, PARIS), DEP, 2.0, as_of_time=NOW,
    ) == "disabled"


def test_horizon_end_is_the_newest_publishable_run():
    # 16:00Z: 15z not due (3 h delay) → 12z → horizon 12z + 51 h.
    assert af.arome_horizon_end(NOW) == datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def test_package_groups_read_the_hour_before_for_precipitation():
    groups = af.arome_package_groups([7, 8, 9])
    assert set(groups) == {"IP1", "IP2", "SP1", "SP2"}
    assert groups["IP1"] == {"07H12H": [7, 8, 9]}
    assert groups["SP2"] == {"07H12H": [7, 8, 9]}
    # f006 sits in the previous group: only its SP1 file is needed.
    assert groups["SP1"] == {"00H06H": [6], "07H12H": [7, 8, 9]}


def test_package_urls_and_cache_keys_per_package():
    assert af.arome_package_url("20260730", 12, "07H12H", package="SP1").endswith(
        "/arome/0025/SP1/arome__0025__SP1__07H12H__2026-07-30T12:00:00Z.grib2",
    )
    assert af.arome_group_cache_key("07H12H", package="IP1") == "f007_AROME_IP1_07H12H_V1.grib2"
    # IP2's key is unchanged, so a group cached before #940 is reused.
    assert af.arome_group_cache_key("07H12H") == "f007_AROME_IP2_07H12H_V1.grib2"


def test_window_fhours_drop_hours_past_the_horizon():
    init = datetime(2026, 10, 25, 12, tzinfo=UTC)
    hours = [init + timedelta(hours=h) for h in (50, 51, 52)]
    assert af.arome_window_fhours("20261025", 12, hours) == [50, 51]


# ---------------------------------------------------------------------------
# No Open-Meteo fallback
# ---------------------------------------------------------------------------


def _mf_sections(pts, *, covered: set[int]) -> list[RouteCrossSection]:
    pfs = build_skeleton(pts, ModelSource.METEOFRANCE, start_date="2026-10-26", end_date="2026-10-26")
    for i in covered:
        _fill_window(pfs[i], required_hours(DEP, 2.0))
    return [RouteCrossSection(
        model=ModelSource.METEOFRANCE, route_points=pts,
        fetched_at=pfs[0].fetched_at, point_forecasts=pfs,
    )]


def test_short_point_drops_the_slot_without_calling_open_meteo():
    pts = _pts(LONDON, PARIS)
    client = _FakeClient()
    outcomes = fill_grib_primary_gaps(
        _mf_sections(pts, covered={0}), pts, [ModelSource.METEOFRANCE], DEP, 2.0,
        client=client, start_date="2026-10-26", end_date="2026-10-26",
    )
    assert client.calls == []
    (o,) = outcomes
    assert o.failed and o.no_fallback == "grib_incomplete" and o.open_meteo_calls == 0


def test_covered_slot_is_kept():
    pts = _pts(LONDON, PARIS)
    client = _FakeClient()
    (o,) = fill_grib_primary_gaps(
        _mf_sections(pts, covered={0, 1}), pts, [ModelSource.METEOFRANCE], DEP, 2.0,
        client=client, start_date="2026-10-26", end_date="2026-10-26",
    )
    assert not o.failed and client.calls == []


# ---------------------------------------------------------------------------
# run_fetch wiring
# ---------------------------------------------------------------------------


def _run(monkeypatch, *, enrich, route=None, dep=DEP, enrich_grib=True, horizon=None):
    monkeypatch.delenv("WB_GRIB_PRIMARY", raising=False)
    om_calls: list[str] = []

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        self._record_call()
        om_calls.append(model.value)
        return _FakeClient().fetch_multi_point(points, model, start_date=start_date, end_date=end_date)

    def fake_enrich(cross_sections, all_forecasts, route_points, departure_time, **kw):
        return enrich(cross_sections) or ({}, {}, {})

    def fake_gate(model, *a, **kw):
        # ECMWF/ICON/GFS stay on Open-Meteo here; meteofrance takes the real gate.
        if model == ModelSource.METEOFRANCE:
            kw["as_of_time"] = NOW
            return real_gate(model, *a, **kw)
        return "disabled"

    real_gate = primary.grib_primary_skip_reason
    with _disable_elevation(), patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ), patch.object(fetch_module, "_open_meteo_live_horizons", return_value={}), patch(
        "weatherbrief.fetch.grib.enrich_forecasts", new=fake_enrich,
    ), patch.object(primary, "grib_primary_skip_reason", new=fake_gate), patch.object(
        af, "arome_horizon_end", return_value=horizon or af.arome_horizon_end(NOW),
    ):
        result = fetch_module.run_fetch(
            route=route or _route(), departure_time=dep,
            models=[ModelSource.GFS, ModelSource.METEOFRANCE],
            enrich_grib=enrich_grib,
        )
    return result, om_calls


def _cover_mf(skip: set[int] = frozenset()):
    def enrich(cross_sections):
        hours = required_hours(DEP, 2.0)
        for cs in cross_sections:
            if cs.model != ModelSource.METEOFRANCE:
                continue
            for i, wf in enumerate(cs.point_forecasts):
                if i not in skip:
                    _fill_window(wf, hours)
        return {"meteofrance": 1}, {}, {"meteofrance": "arome:mf"}
    return enrich


def _codes(result) -> dict[str, str]:
    return {d.code: d.message for d in result.diagnostics}


def test_in_domain_slot_from_arome_with_no_open_meteo_call(monkeypatch):
    result, om_calls = _run(monkeypatch, enrich=_cover_mf())
    assert om_calls == ["gfs"]
    assert sorted(result.models_fetched) == ["gfs", "meteofrance"]
    assert result.grib_primary == {"meteofrance": 0}
    assert result.grib_sources["meteofrance"] == "arome:mf"


def test_short_slot_is_dropped_not_filled(monkeypatch):
    result, om_calls = _run(monkeypatch, enrich=_cover_mf(skip={1}))
    assert om_calls == ["gfs"]
    assert result.models_fetched == ["gfs"]
    assert "meteofrance" in result.models_failed
    assert all(cs.model != ModelSource.METEOFRANCE for cs in result.cross_sections)
    assert all(wf.model != ModelSource.METEOFRANCE for wf in result.all_forecasts)


def test_failure_drill_drops_the_slot(monkeypatch, tmp_path):
    monkeypatch.setenv("WB_GRIB_PRIMARY_DRILL", "meteofrance")
    with patch("weatherbrief.tasks.artifacts.save_fetch_artifacts") as save:
        monkeypatch.delenv("WB_GRIB_PRIMARY", raising=False)
        result, om_calls = _run(monkeypatch, enrich=_cover_mf())
    assert om_calls == ["gfs"]
    assert result.models_fetched == ["gfs"]
    assert result.models_failed["meteofrance"].startswith("AROME GRIB")


def test_failure_drill_recorded_in_fetch_facts(monkeypatch, tmp_path):
    monkeypatch.setenv("WB_GRIB_PRIMARY_DRILL", "meteofrance")
    monkeypatch.delenv("WB_GRIB_PRIMARY", raising=False)
    captured = {}

    def fake_save(pack_dir, *a, fetch_facts=None, **kw):
        captured.update(fetch_facts or {})

    with patch("weatherbrief.tasks.artifacts.save_fetch_artifacts", new=fake_save):
        real_run = fetch_module.run_fetch

        def run_with_pack(**kw):
            return real_run(pack_dir=tmp_path, **kw)

        with patch.object(fetch_module, "run_fetch", new=run_with_pack):
            _run(monkeypatch, enrich=_cover_mf())
    assert captured["grib_primary_dropped"] == {"meteofrance": "grib_incomplete"}
    assert captured["open_meteo_calls"] == 1


def test_route_leaving_the_domain_has_no_slot(monkeypatch):
    from weatherbrief.models import RouteConfig, Waypoint

    route = RouteConfig(
        name="EGPH-LFPB",
        waypoints=[
            Waypoint(icao="EGPH", name="Edinburgh", lat=55.95, lon=-3.37),
            Waypoint(icao="LFPB", name="Paris Le Bourget", lat=48.9694, lon=2.4414),
        ],
        cruise_altitude_ft=8000,
        flight_duration_hours=2.0,
    )
    result, om_calls = _run(monkeypatch, enrich=_cover_mf(), route=route)
    assert om_calls == ["gfs"]
    assert "meteofrance" not in result.models_fetched
    assert "meteofrance" in result.models_skipped_region
    assert "outside the AROME area" in _codes(result)["model_skipped_region"]


def test_beyond_the_horizon_has_no_slot(monkeypatch):
    result, om_calls = _run(
        monkeypatch, enrich=_cover_mf(), horizon=DEP + timedelta(hours=1),
    )
    assert om_calls == ["gfs"]
    assert "meteofrance" not in result.models_fetched
    assert "AROME" in _codes(result)["model_skipped_horizon"]


@pytest.mark.parametrize("how", ["arome_off", "enrich_off", "grib_primary_off"])
def test_switched_off_has_no_slot_and_no_open_meteo(monkeypatch, how):
    enrich_grib = how != "enrich_off"
    if how == "arome_off":
        monkeypatch.setenv(af.AROME_ENABLED_ENV, "0")
    if how == "grib_primary_off":
        monkeypatch.setattr(primary, "grib_primary_models", lambda: (ModelSource.ECMWF,))
    result, om_calls = _run(monkeypatch, enrich=_cover_mf(), enrich_grib=enrich_grib)
    assert om_calls == ["gfs"]
    assert "meteofrance" not in result.models_fetched
    assert "model_disabled" in _codes(result)
    assert "meteofrance" not in result.models_failed


# ---------------------------------------------------------------------------
# Surface mapping
# ---------------------------------------------------------------------------


def test_surface_snapshot_units():
    from weatherbrief.fetch.grib.decode import build_arome_surface_snapshot

    snap = build_arome_surface_snapshot({
        "t2m_k": 288.15, "rh2m_pct": 80.0, "u10_m_s": 0.0, "v10_m_s": -5.0,
        "gust10_m_s": 10.0, "prmsl_pa": 101500.0, "sp_pa": 99000.0,
        "cape_jkg": -3.0, "tcc": 75.0, "lcc": 20.0, "mcc": 30.0, "hcc": 110.0,
    })
    assert snap["temperature_2m_c"] == pytest.approx(15.0)
    assert snap["relative_humidity_2m_pct"] == 80.0
    assert snap["dewpoint_2m_c"] == pytest.approx(11.6, abs=0.2)
    assert snap["wind_speed_10m_kt"] == pytest.approx(9.72, abs=0.01)
    assert snap["wind_direction_10m_deg"] == pytest.approx(0.0)   # from the north
    assert snap["wind_gusts_10m_kt"] == pytest.approx(19.44, abs=0.01)
    assert snap["pressure_msl_hpa"] == pytest.approx(1015.0)
    assert snap["surface_pressure_hpa"] == pytest.approx(990.0)
    assert snap["cape_jkg"] == 0.0
    assert snap["cloud_cover_pct"] == 75.0 and snap["cloud_cover_high_pct"] == 100.0
    # AROME publishes none of these.
    for f in ("visibility_m", "freezing_level_m", "convective_inhibition_jkg",
              "lifted_index_raw", "precipitation_probability_pct"):
        assert f not in snap


def test_tp_is_de_accumulated_per_hour():
    from weatherbrief.fetch.grib.decode import arome_surface_rates

    assert arome_surface_rates({"tp_kg_m2": 3.5}, {"tp_kg_m2": 1.0}, 1.0) == {
        "precipitation_mm": pytest.approx(2.5),
    }
    # Packing noise never makes negative rain; a missing step leaves it unknown.
    assert arome_surface_rates({"tp_kg_m2": 0.99}, {"tp_kg_m2": 1.0}, 1.0) == {
        "precipitation_mm": 0.0,
    }
    assert arome_surface_rates({"tp_kg_m2": 1.0}, None, 1.0) == {}
    assert arome_surface_rates({}, {"tp_kg_m2": 1.0}, 1.0) == {}


def _surface_message(*, short=None, param=None, level_type, level, step, value, stat=None):
    import eccodes

    gid = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        eccodes.codes_set(gid, "centre", "lfpw")
        if stat is not None:
            eccodes.codes_set(gid, "productDefinitionTemplateNumber", 8)
            eccodes.codes_set(gid, "typeOfStatisticalProcessing", stat)
        if short is not None:
            eccodes.codes_set(gid, "shortName", short)
        else:
            d, c, n = param
            eccodes.codes_set(gid, "discipline", d)
            eccodes.codes_set(gid, "parameterCategory", c)
            eccodes.codes_set(gid, "parameterNumber", n)
        eccodes.codes_set(gid, "typeOfFirstFixedSurface", level_type)
        if level is not None:
            eccodes.codes_set(gid, "scaledValueOfFirstFixedSurface", level)
            eccodes.codes_set(gid, "scaleFactorOfFirstFixedSurface", 0)
        eccodes.codes_set(gid, "stepUnits", "h")
        if stat is not None:
            eccodes.codes_set(gid, "startStep", 0)
        eccodes.codes_set(gid, "endStep", step)
        eccodes.codes_set(gid, "Ni", 11)
        eccodes.codes_set(gid, "Nj", 11)
        eccodes.codes_set(gid, "latitudeOfFirstGridPointInDegrees", 50.0)
        eccodes.codes_set(gid, "latitudeOfLastGridPointInDegrees", 45.0)
        eccodes.codes_set(gid, "longitudeOfFirstGridPointInDegrees", 0.0)
        eccodes.codes_set(gid, "longitudeOfLastGridPointInDegrees", 5.0)
        eccodes.codes_set(gid, "iDirectionIncrementInDegrees", 0.5)
        eccodes.codes_set(gid, "jDirectionIncrementInDegrees", 0.5)
        eccodes.codes_set(gid, "jScansPositively", 0)
        eccodes.codes_set_values(gid, np.full(121, value, dtype=np.float64))
        return bytes(eccodes.codes_get_message(gid))
    finally:
        eccodes.codes_release(gid)


def test_surface_decode_maps_fields_and_scales_fraction_covers(tmp_path):
    from weatherbrief.fetch.grib.decode import decode_arome_surface_per_point

    msgs = [
        _surface_message(short="2t", level_type=103, level=2, step=7, value=290.0),
        # Total cloud as MF's 0/6/1 in 0–1: stored as %.
        _surface_message(param=(0, 6, 1), level_type=1, level=None, step=7, value=0.6),
        _surface_message(short="tp", level_type=1, level=None, step=7, value=4.0, stat=1),
        _surface_message(short="tp", level_type=1, level=None, step=6, value=1.5, stat=1),
        _surface_message(short="2t", level_type=103, level=2, step=8, value=291.0),
    ]
    path = tmp_path / "sp1.grib2"
    path.write_bytes(b"".join(msgs))
    out = decode_arome_surface_per_point(path, [6, 7], [47.0], [2.0])
    assert set(out["points"]) == {6, 7}
    p7 = out["points"][7][0]
    assert p7["t2m_k"] == pytest.approx(290.0, abs=0.01)
    assert p7["tcc"] == pytest.approx(60.0, abs=0.1)
    assert p7["tp_kg_m2"] == pytest.approx(4.0, abs=0.01)
    assert out["points"][6][0] == {"tp_kg_m2": pytest.approx(1.5, abs=0.01)}


def test_surface_written_onto_the_skeleton():
    import weatherbrief.fetch.grib as grib_mod

    pts = _pts(LONDON, PARIS)
    pfs = build_skeleton(pts, ModelSource.METEOFRANCE, start_date="2026-10-26", end_date="2026-10-26")
    cs = RouteCrossSection(
        model=ModelSource.METEOFRANCE, route_points=pts,
        fetched_at=pfs[0].fetched_at, point_forecasts=pfs,
    )
    # 00z run; window f009..f010; f008 seeds the first hour's precipitation.
    ctx = grib_mod._AromeContext(
        "20261026", 0, [9, 10], {}, Path("."), [850], [], [], session=None,
    )
    raw = lambda tp: {"t2m_k": 283.15, "u10_m_s": 3.0, "v10_m_s": 4.0, "tp_kg_m2": tp,
                      "tcc": 50.0}
    surface = {
        8: [{"tp_kg_m2": 1.0}, {"tp_kg_m2": 0.0}],
        9: [raw(1.5), raw(0.0)],
        10: [raw(3.5), raw(0.2)],
    }
    grib_mod._apply_arome_surface_to_sections([cs], [], pts, ctx, surface)
    by_time = {h.time: h for h in pfs[0].hourly}
    t8, t9, t10 = (datetime(2026, 10, 26, 0, tzinfo=UTC) + timedelta(hours=h) for h in (8, 9, 10))
    assert by_time[t9].precipitation_mm == pytest.approx(0.5)
    assert by_time[t10].precipitation_mm == pytest.approx(2.0)
    assert by_time[t10].temperature_2m_c == pytest.approx(10.0)
    assert by_time[t10].wind_speed_10m_kt == pytest.approx(9.72, abs=0.01)
    assert by_time[t10].cloud_cover_pct == 50.0
    # The seed hour is outside the window: nothing written there.
    assert by_time[t8].temperature_2m_c is None and by_time[t8].precipitation_mm is None
    assert {h.time: h for h in pfs[1].hourly}[t10].precipitation_mm == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Refresh gate: only advisory models count (#940 decision 5)
# ---------------------------------------------------------------------------


def _status(stale: set[str], models=("ecmwf", "gfs", "icon", "meteofrance", "ukmo")):
    from weatherbrief.api.packs import DataStatus, ModelStatus

    nxt = (NOW + timedelta(hours=3)).isoformat()
    return DataStatus(
        fresh=not stale, stale_models=sorted(stale), model_init_times={},
        next_expected_update=nxt, next_expected_model=None, marker_health="ok",
        models={
            m: ModelStatus(
                source=f"{m}:x", pack_init=1, latest_available=2, next_expected=nxt,
                published_at=None, state="stale" if m in stale else "current",
                covers_horizon=True,
            )
            for m in models
        },
        sources=[],
    )


ADVISORY = ["gfs", "ecmwf", "icon"]


@pytest.mark.parametrize("stale", [{"meteofrance"}, {"ukmo"}, {"meteofrance", "ukmo"}])
def test_d0_display_only_newer_run_is_realtime(stale):
    from weatherbrief.api.packs import decide_refresh

    d = decide_refresh(_status(stale), 0, advisory_models=ADVISORY)
    assert d.mode == "realtime"
    assert d.n_eligible == 3 and d.n_updated == 0
    assert "meteofrance" not in d.pending_models


def test_d0_advisory_model_newer_is_full():
    from weatherbrief.api.packs import decide_refresh

    d = decide_refresh(_status({"meteofrance", "icon"}), 0, advisory_models=ADVISORY)
    assert d.mode == "full" and d.n_updated == 1


def test_no_advisory_setting_counts_every_model_as_the_advisories_do():
    from weatherbrief.api.packs import decide_refresh, refresh_counting_models
    from weatherbrief.tasks.advise import _compute_advisory_model_names

    names = ["ecmwf", "gfs", "meteofrance"]
    assert refresh_counting_models(names, None) == _compute_advisory_model_names(names, None)
    assert decide_refresh(_status({"meteofrance"}), 0).mode == "full"


def test_gated_data_status_resolves_the_profile_once(monkeypatch):
    from weatherbrief.api import packs

    seen = {}
    monkeypatch.setattr(packs, "_build_data_status", lambda latest, flight: _status({"meteofrance"}))
    monkeypatch.setattr(packs, "resolve_flight_advisory_models", lambda flight: ADVISORY)
    monkeypatch.setattr(packs, "_days_out_now", lambda flight, now=None: 0)
    real = packs.decide_refresh

    def spy(status, days_out, **kw):
        seen.update(kw)
        return real(status, days_out, **kw)

    monkeypatch.setattr(packs, "decide_refresh", spy)
    status = packs.gated_data_status(object(), object(), params_override=False)
    assert seen["advisory_models"] == ADVISORY
    assert status.refresh_decision.mode == "realtime"


# ---------------------------------------------------------------------------
# Flight warm
# ---------------------------------------------------------------------------


def _flight(route_pts, dep=DEP):
    from weatherbrief.fetch.grib.flight_warm import _Flight

    return _Flight(id="ZZ-1", departure_time=dep, duration_hours=2.0, route_points=route_pts)


def test_warm_skips_out_of_domain_and_far_flights(monkeypatch, tmp_path):
    from weatherbrief.fetch.grib import flight_warm

    monkeypatch.delenv(af.AROME_ENABLED_ENV, raising=False)
    out = flight_warm._warm_arome(_flight(_pts(LONDON, (56.0, -3.2))), tmp_path, lambda: False)
    assert (out.status, out.reason) == ("skipped", "meteofrance_out_of_domain")
    far = datetime.now(UTC) + timedelta(days=3)
    out = flight_warm._warm_arome(_flight(_pts(LONDON, PARIS), dep=far), tmp_path, lambda: False)
    assert (out.status, out.reason) == ("skipped", "meteofrance_out_of_range")


def test_warm_skips_flights_without_a_meteofrance_slot(monkeypatch, tmp_path):
    from weatherbrief.fetch.grib import flight_warm

    monkeypatch.delenv(af.AROME_ENABLED_ENV, raising=False)
    monkeypatch.setattr(flight_warm, "_wants_meteofrance", lambda flight: False)
    dep = datetime.now(UTC) + timedelta(hours=20)
    out = flight_warm._warm_arome(_flight(_pts(LONDON, PARIS), dep=dep), tmp_path, lambda: False)
    assert (out.status, out.reason) == ("skipped", "no_meteofrance_slot")


def test_warm_downloads_every_package_group_then_hits_the_cache(monkeypatch, tmp_path):
    import weatherbrief.fetch.grib as grib_mod
    from weatherbrief.fetch.grib import flight_warm

    monkeypatch.delenv(af.AROME_ENABLED_ENV, raising=False)
    monkeypatch.setattr(flight_warm, "_wants_meteofrance", lambda flight: True)
    monkeypatch.setattr(af, "find_latest_arome_run", lambda *a, **kw: ("20261025", 12))
    monkeypatch.setattr(grib_mod, "_grib_session", lambda: None)
    got: list[tuple[str, str]] = []

    def fake_download(init_date, init_hour, label, run_dir, session, config=af.AROME, package=None):
        got.append((package, label))
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / af.arome_group_cache_key(label, package=package)
        path.write_bytes(b"GRIB")
        return path

    monkeypatch.setattr(af, "download_arome_group", fake_download)
    dep = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=20)
    flight = _flight(_pts(LONDON, PARIS), dep=dep)
    with patch.object(af, "arome_window_out_of_range", return_value=False):
        out = flight_warm._warm_arome(flight, tmp_path, lambda: False)
        assert out.status == "warmed"
        assert {p for p, _ in got} == {"IP1", "IP2", "SP1", "SP2"}
        n = len(got)
        # The next pass (or the briefing on the same run) downloads nothing.
        again = flight_warm._warm_arome(flight, tmp_path, lambda: False)
    assert again.status == "warmed" and len(got) == n


def test_warm_stops_at_the_limit(monkeypatch, tmp_path):
    import weatherbrief.fetch.grib as grib_mod
    from weatherbrief.fetch.grib import flight_warm

    monkeypatch.delenv(af.AROME_ENABLED_ENV, raising=False)
    monkeypatch.setattr(flight_warm, "_wants_meteofrance", lambda flight: True)
    monkeypatch.setattr(af, "find_latest_arome_run", lambda *a, **kw: ("20261025", 12))
    monkeypatch.setattr(grib_mod, "_grib_session", lambda: None)
    monkeypatch.setattr(af, "download_arome_group", lambda *a, **kw: pytest.fail("downloaded"))
    dep = datetime.now(UTC) + timedelta(hours=20)
    with patch.object(af, "arome_window_out_of_range", return_value=False):
        out = flight_warm._warm_arome(_flight(_pts(LONDON, PARIS), dep=dep), tmp_path, lambda: True)
    assert out.status == "stopped"


def test_warm_source_is_registered():
    from weatherbrief.fetch.grib import flight_warm

    assert "arome" in flight_warm.WARM_SOURCES
    limits = flight_warm.warm_limits("arome")
    assert limits.horizon_hours == 51.0 and limits.max_bytes == 4 * 10**9
