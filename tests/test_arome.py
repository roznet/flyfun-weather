"""Météo-France AROME condensate enrichment (#529).

Covers the domain predicate and its derivation, the fetch helpers (URLs,
package groups, window hours, run finder, streamed download), the
message-level decode against synthetic GRIB2 written with eccodes, and the
orchestrator's gating + staged all-or-nothing merge onto the meteofrance slot.
No network: every HTTP call goes through a fake session.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from weatherbrief.fetch.grib import arome_domain as dom
from weatherbrief.fetch.grib import arome_fetch as af


class _Pt:
    def __init__(self, lat: float, lon: float):
        self.lat = lat
        self.lon = lon


# ---------------------------------------------------------------------------
# Domain predicate
# ---------------------------------------------------------------------------

# Verified on issue #529 against live data (2026-07-30).
_INSIDE = [(51.5, -0.4), (48.9, 2.4), (52.5, 13.4), (43.5, 5.2)]
_OUTSIDE = [(37.55, 2.0), (45.0, -11.95), (45.0, 15.95), (56.0, -3.2)]


class TestDomainPredicate:
    @pytest.mark.parametrize("lat,lon", _INSIDE)
    def test_verified_inside(self, lat, lon):
        assert dom.point_in_arome_domain(lat, lon)

    @pytest.mark.parametrize("lat,lon", _OUTSIDE)
    def test_verified_outside(self, lat, lon):
        assert not dom.point_in_arome_domain(lat, lon)

    def test_route_is_all_or_nothing(self):
        london, paris = _Pt(51.5, -0.4), _Pt(48.9, 2.4)
        assert dom.route_in_arome_domain([london, paris])
        # One point south of the domain's bottom edge sinks the whole route.
        assert not dom.route_in_arome_domain([london, paris, _Pt(37.55, 2.0)])

    def test_empty_route_is_not_inside(self):
        assert not dom.route_in_arome_domain([])

    def test_issue_corridors_are_inside(self):
        """The routes the issue measured as fully AROME-covered."""
        for route in (
            [(51.47, -0.45), (43.54, 6.95)],   # London → Cannes
            [(51.47, -0.45), (48.45, -4.42)],  # London → Brest
            [(51.47, -0.45), (53.42, -6.27)],  # London → Dublin
            [(43.54, 6.95), (41.30, 2.08)],    # Cannes → Barcelona
        ):
            assert dom.route_in_arome_domain([_Pt(*p) for p in route]), route

    def test_checked_in_table_is_marked_provisional(self):
        # Flip this when scripts/regen_arome_domain.py has been run.
        assert dom.arome_domain_is_provisional()

    def test_bands_are_well_formed(self):
        prev_north = None
        for b in dom.AROME_DOMAIN_BANDS:
            assert b.lat_south < b.lat_north
            assert b.west < b.east
            if prev_north is not None:
                assert b.lat_south >= prev_north - 1e-9
            prev_north = b.lat_north


class TestDeriveDomainBands:
    @staticmethod
    def _bowed_mask():
        """A domain narrowing southward on a 0.1° grid, like AROME's."""
        lats = np.round(np.arange(40.0, 45.01, 0.1), 4)
        lons = np.round(np.arange(-5.0, 5.01, 0.1), 4)
        mask = np.zeros((lats.size, lons.size), dtype=bool)
        for i, lat in enumerate(lats):
            half = 2.0 + (lat - 40.0) * 0.5   # 2° wide at 40N → 4.5° at 45N
            mask[i] = np.abs(lons) <= half + 1e-9
        return lats, lons, mask

    def test_spans_are_inset_one_cell_and_intersected(self):
        lats, lons, mask = self._bowed_mask()
        bands = dom.derive_domain_bands(lats, lons, mask, band_deg=1.0)
        assert bands
        for b in bands:
            # The narrowest row the band or its outer neighbour touches (the
            # row just south of it, since the domain narrows southward), then
            # one more cell in for the bilinear stencil.
            rows = np.flatnonzero((lats >= b.lat_south - 0.1 - 1e-9) & (lats <= b.lat_north + 0.1 + 1e-9))
            narrowest = min(lons[mask[r]].max() for r in rows)
            assert b.east == pytest.approx(narrowest - 0.1, abs=1e-6)
            assert b.west == pytest.approx(-(narrowest - 0.1), abs=1e-6)

    def test_every_band_point_has_a_finite_stencil(self):
        lats, lons, mask = self._bowed_mask()
        bands = dom.derive_domain_bands(lats, lons, mask, band_deg=0.5)
        rng = np.random.default_rng(0)
        for _ in range(500):
            lat = rng.uniform(40.0, 45.0)
            lon = rng.uniform(-5.0, 5.0)
            if not dom.point_in_arome_domain(lat, lon, bands):
                continue
            i = int(np.floor((lat - 40.0) / 0.1 + 1e-9))
            j = int(np.floor((lon + 5.0) / 0.1 + 1e-9))
            assert mask[i:i + 2, j:j + 2].all(), (lat, lon)

    def test_outermost_rows_and_non_contiguous_rows_are_dropped(self):
        lats, lons, mask = self._bowed_mask()
        mask[25, 50] = False  # a hole in the middle of row 42.5N
        bands = dom.derive_domain_bands(lats, lons, mask, band_deg=0.5)
        for b in bands:
            assert not (b.lat_south - 0.1 <= 42.5 <= b.lat_north + 0.1)
        # Grid's first and last rows have no outer neighbour → no band there.
        assert bands[0].lat_south > lats[0]
        assert bands[-1].lat_north < lats[-1]

    def test_descending_axes_give_the_same_bands(self):
        lats, lons, mask = self._bowed_mask()
        a = dom.derive_domain_bands(lats, lons, mask, band_deg=0.5)
        b = dom.derive_domain_bands(lats[::-1], lons, mask[::-1, :], band_deg=0.5)
        assert a == b

    def test_rendered_module_round_trips(self):
        lats, lons, mask = self._bowed_mask()
        bands = dom.derive_domain_bands(lats, lons, mask, band_deg=0.5)
        ns: dict = {}
        exec(dom.render_bands_module(bands, "derived from test"), ns)
        assert ns["SOURCE"] == "derived from test"
        assert [dom.DomainBand(*b) for b in ns["BANDS"]] == bands


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


class TestFetchHelpers:
    def test_enabled_flag(self, monkeypatch):
        monkeypatch.delenv(af.AROME_ENABLED_ENV, raising=False)
        assert not af.arome_enabled()
        monkeypatch.setenv(af.AROME_ENABLED_ENV, "1")
        assert af.arome_enabled()
        monkeypatch.setenv(af.AROME_ENABLED_ENV, "no")
        assert not af.arome_enabled()

    def test_package_url(self):
        assert af.arome_package_url("20260730", 12, "07H12H") == (
            "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/"
            "2026-07-30T12:00:00Z/arome/0025/IP2/"
            "arome__0025__IP2__07H12H__2026-07-30T12:00:00Z.grib2"
        )

    @pytest.mark.parametrize("fhour,label", [
        (0, "00H06H"), (6, "00H06H"), (7, "07H12H"), (12, "07H12H"),
        (13, "13H18H"), (48, "43H48H"), (49, "49H51H"), (51, "49H51H"),
    ])
    def test_group_for_fhour(self, fhour, label):
        assert af.arome_group_for_fhour(fhour) == label

    def test_group_beyond_horizon_raises(self):
        with pytest.raises(ValueError):
            af.arome_group_for_fhour(52)

    def test_groups_cover_every_hour_exactly_once(self):
        hours = [h for _, first, last in af.AROME.groups for h in range(first, last + 1)]
        assert hours == list(range(0, af.AROME.horizon_h + 1))

    def test_groups_for_window_are_capped_to_the_window(self):
        groups = af.arome_groups_for_fhours([5, 6, 7, 8])
        assert groups == {"00H06H": [5, 6], "07H12H": [7, 8]}

    def test_window_hours_are_contiguous_for_half_hour_departures(self):
        dep = datetime(2026, 7, 30, 15, 30, tzinfo=timezone.utc)
        hours = af.compute_arome_flight_window_hours("20260730", 12, dep, 2.5)
        assert hours == [3, 4, 5, 6]

    def test_window_hours_clamped_to_horizon(self):
        dep = datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)  # f050
        hours = af.compute_arome_flight_window_hours("20260730", 12, dep, 3.0)
        assert hours == [50, 51]

    def test_cache_key(self):
        assert af.arome_group_cache_key("07H12H") == "f007_AROME_IP2_07H12H_V1.grib2"

    def test_window_out_of_range(self):
        now = datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc)
        # Freshest publishable run is 12z (3h delay) → horizon 2026-08-01 15z.
        assert not af.arome_window_out_of_range(
            now + timedelta(hours=40), 2.0, as_of_time=now,
        )
        assert af.arome_window_out_of_range(
            now + timedelta(hours=60), 2.0, as_of_time=now,
        )


class _Resp:
    def __init__(self, status=200, headers=None, chunks=()):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Session:
    def __init__(self, published: set[str] | None = None, body: dict | None = None):
        self.published = published or set()
        self.body = body or {}
        self.heads: list[str] = []
        self.gets: list[str] = []

    def head(self, url, timeout=None):
        self.heads.append(url)
        return _Resp(200 if url in self.published else 404)

    def get(self, url, stream=False, timeout=None):
        self.gets.append(url)
        return self.body[url]


class TestRunFinder:
    def test_probes_the_group_holding_the_last_needed_hour(self):
        now = datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc)
        dep = datetime(2026, 7, 30, 20, 0, tzinfo=timezone.utc)
        end = dep + timedelta(hours=2)        # f010 of the 12z run
        want = af.arome_package_url("20260730", 12, "07H12H")
        sess = _Session(published={want})
        found = af.find_latest_arome_run(dep, sess, as_of_time=now, cover_until=end)
        assert found == ("20260730", 12)
        # 15z is not yet due at 16:00 (3h delay); 12z is the first probe.
        assert sess.heads[0] == want

    def test_falls_back_to_previous_cycle_when_group_not_up(self):
        now = datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc)
        dep = datetime(2026, 7, 30, 20, 0, tzinfo=timezone.utc)
        # From the 9z run the window ends at f013 → the 13H18H group.
        prev = af.arome_package_url("20260730", 9, "13H18H")
        sess = _Session(published={prev})
        found = af.find_latest_arome_run(
            dep, sess, as_of_time=now, cover_until=dep + timedelta(hours=2),
        )
        assert found == ("20260730", 9)

    def test_nothing_published(self):
        now = datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc)
        assert af.find_latest_arome_run(now, _Session(), as_of_time=now) is None


class TestDownload:
    def test_streams_into_cache(self, tmp_path):
        url = af.arome_package_url("20260730", 12, "00H06H")
        sess = _Session(body={url: _Resp(headers={"Content-Length": "6"},
                                         chunks=[b"GRI", b"B2!"])})
        path = af.download_arome_group("20260730", 12, "00H06H", tmp_path, sess)
        assert path is not None and path.read_bytes() == b"GRIB2!"
        # Second call is a cache hit — no second GET.
        af.download_arome_group("20260730", 12, "00H06H", tmp_path, sess)
        assert sess.gets == [url]

    def test_truncated_download_is_not_cached(self, tmp_path):
        url = af.arome_package_url("20260730", 12, "00H06H")
        sess = _Session(body={url: _Resp(headers={"Content-Length": "100"},
                                         chunks=[b"short"])})
        assert af.download_arome_group("20260730", 12, "00H06H", tmp_path, sess) is None
        assert not list(tmp_path.glob("*.grib2"))

    def test_http_error_returns_none(self, tmp_path):
        url = af.arome_package_url("20260730", 12, "00H06H")
        sess = _Session(body={url: _Resp(status=404)})
        assert af.download_arome_group("20260730", 12, "00H06H", tmp_path, sess) is None


# ---------------------------------------------------------------------------
# Decode — synthetic GRIB2 with eccodes
# ---------------------------------------------------------------------------

# 0.5° test grid: 45–50N, 0–5E.
_LAT0, _LAT1, _LON0, _LON1 = 45.0, 50.0, 0.0, 5.0
_NJ, _NI = 11, 11
_MISSING = 9999.0


def _message(
    *, short: str | None = None, local: tuple[int, int, int] | None = None,
    level: int, step: int, values: np.ndarray,
) -> bytes:
    import eccodes

    gid = eccodes.codes_grib_new_from_samples("regular_ll_pl_grib2")
    try:
        eccodes.codes_set(gid, "centre", "lfpw")
        if short is not None:
            eccodes.codes_set(gid, "shortName", short)
        else:
            d, c, n = local
            eccodes.codes_set(gid, "discipline", d)
            eccodes.codes_set(gid, "parameterCategory", c)
            eccodes.codes_set(gid, "parameterNumber", n)
        eccodes.codes_set(gid, "typeOfLevel", "isobaricInhPa")
        eccodes.codes_set(gid, "level", level)
        eccodes.codes_set(gid, "stepUnits", "h")
        eccodes.codes_set(gid, "endStep", step)
        eccodes.codes_set(gid, "Ni", _NI)
        eccodes.codes_set(gid, "Nj", _NJ)
        eccodes.codes_set(gid, "latitudeOfFirstGridPointInDegrees", _LAT1)
        eccodes.codes_set(gid, "latitudeOfLastGridPointInDegrees", _LAT0)
        eccodes.codes_set(gid, "longitudeOfFirstGridPointInDegrees", _LON0)
        eccodes.codes_set(gid, "longitudeOfLastGridPointInDegrees", _LON1)
        eccodes.codes_set(gid, "iDirectionIncrementInDegrees", 0.5)
        eccodes.codes_set(gid, "jDirectionIncrementInDegrees", 0.5)
        eccodes.codes_set(gid, "jScansPositively", 0)
        eccodes.codes_set(gid, "bitmapPresent", 1)
        eccodes.codes_set(gid, "missingValue", _MISSING)
        vals = values[::-1, :]  # north → south scan
        vals = np.where(np.isnan(vals), _MISSING, vals)
        eccodes.codes_set_values(gid, vals.flatten())
        return bytes(eccodes.codes_get_message(gid))
    finally:
        eccodes.codes_release(gid)


def _field(value: float, *, nan_east_of: float | None = None) -> np.ndarray:
    grid = np.full((_NJ, _NI), value, dtype=np.float64)
    if nan_east_of is not None:
        lons = np.linspace(_LON0, _LON1, _NI)
        grid[:, lons > nan_east_of] = np.nan
    return grid


def _write(tmp_path: Path, messages: list[bytes]) -> Path:
    path = tmp_path / "group.grib2"
    path.write_bytes(b"".join(messages))
    return path


def _full_set(step: int, level: int, scale: float = 1.0, cc: float = 0.5,
              nan_east_of: float | None = None) -> list[bytes]:
    kw = dict(level=level, step=step)
    return [
        _message(short="clwc", values=_field(2e-4 * scale, nan_east_of=nan_east_of), **kw),
        _message(short="ciwc", values=_field(1e-4 * scale, nan_east_of=nan_east_of), **kw),
        _message(short="crwc", values=_field(3e-5 * scale, nan_east_of=nan_east_of), **kw),
        _message(short="cswc", values=_field(4e-5 * scale, nan_east_of=nan_east_of), **kw),
        _message(local=(0, 1, 201), values=_field(5e-6 * scale, nan_east_of=nan_east_of), **kw),
        _message(short="cc", values=_field(cc, nan_east_of=nan_east_of), **kw),
    ]


class TestDecode:
    def _decode(self, path, fhours, lats, lons, levels=None):
        from weatherbrief.fetch.grib.decode import decode_arome_pressure_per_point
        return decode_arome_pressure_per_point(path, fhours, lats, lons, levels)

    def test_all_six_fields_map_and_select_by_step(self, tmp_path):
        msgs = _full_set(step=3, level=850) + _full_set(step=4, level=850, scale=2.0)
        out = self._decode(_write(tmp_path, msgs), [4], [47.3], [2.2])
        assert set(out["points"]) == {4}
        lvl = out["points"][4][0][850]
        assert lvl["cloud_liquid_water_kg_kg"] == pytest.approx(4e-4, rel=1e-3)
        assert lvl["ice_mixing_ratio_kg_kg"] == pytest.approx(2e-4, rel=1e-3)
        assert lvl["rain_water_kg_kg"] == pytest.approx(6e-5, rel=1e-3)
        assert lvl["snow_water_kg_kg"] == pytest.approx(8e-5, rel=1e-3)
        # Graupel is an MF local parameter (eccodes says "unknown").
        assert lvl["graupel_water_kg_kg"] == pytest.approx(1e-5, rel=1e-2)
        assert lvl["cloud_area_fraction_pct"] == pytest.approx(50.0, rel=1e-3)

    def test_level_filter(self, tmp_path):
        msgs = _full_set(step=3, level=850) + _full_set(step=3, level=125)
        out = self._decode(_write(tmp_path, msgs), [3], [47.0], [2.0], levels=[850])
        assert set(out["points"][3][0]) == {850}

    def test_cc_already_in_percent_is_not_rescaled(self, tmp_path):
        msgs = _full_set(step=3, level=850, cc=40.0) + _full_set(step=3, level=200, cc=0.8)
        out = self._decode(_write(tmp_path, msgs), [3], [47.0], [2.0])
        pt = out["points"][3][0]
        assert out["cc_scale"] == 1.0
        assert pt[850]["cloud_area_fraction_pct"] == pytest.approx(40.0, rel=1e-3)
        # The near-clear level stays 0.8 %, not 80 % — decided per FILE.
        assert pt[200]["cloud_area_fraction_pct"] == pytest.approx(0.8, rel=1e-2)

    def test_cc_fraction_is_scaled_to_percent(self, tmp_path):
        msgs = _full_set(step=3, level=850, cc=0.25)
        out = self._decode(_write(tmp_path, msgs), [3], [47.0], [2.0])
        assert out["cc_scale"] == 100.0
        assert out["points"][3][0][850]["cloud_area_fraction_pct"] == pytest.approx(25.0, rel=1e-3)

    def test_stencil_touching_missing_cells_decodes_nothing(self, tmp_path):
        msgs = _full_set(step=3, level=850, nan_east_of=3.0)
        out = self._decode(_write(tmp_path, msgs), [3], [47.0, 47.0], [1.0, 3.2])
        inside, edge = out["points"][3]
        assert inside[850]["cloud_liquid_water_kg_kg"] > 0
        assert edge == {}
        assert out["finite_fraction"] == pytest.approx(7 / 11, rel=1e-6)

    def test_implausible_condensate_is_dropped(self, tmp_path):
        """A field 1000× too large (g/kg) is missing data, not stored."""
        msgs = [
            _message(short="clwc", level=850, step=3, values=_field(0.2)),
            _message(short="ciwc", level=850, step=3, values=_field(1e-4)),
        ]
        out = self._decode(_write(tmp_path, msgs), [3], [47.0], [2.0])
        lvl = out["points"][3][0][850]
        assert "cloud_liquid_water_kg_kg" not in lvl
        assert "ice_mixing_ratio_kg_kg" in lvl
        assert len(out["dropped"]) == 1

    def test_worker_entry_point(self, tmp_path):
        from weatherbrief.fetch.grib.decode_worker import decode_arome_pressure
        path = _write(tmp_path, _full_set(step=3, level=850))
        out = decode_arome_pressure(str(path), [3], [47.0], [2.0], [850])
        assert out["points"][3][0][850]["cloud_liquid_water_kg_kg"] > 0


# ---------------------------------------------------------------------------
# Orchestrator — gating and staged merge
# ---------------------------------------------------------------------------

_LEVELS = [850, 700]


def _mf_section(times, lat=48.9, lon=2.4, model=None):
    from weatherbrief.models import (
        HourlyForecast,
        ModelSource,
        PressureLevelData,
        RouteCrossSection,
        Waypoint,
        WaypointForecast,
    )
    model = model or ModelSource.METEOFRANCE
    now = datetime.now(timezone.utc)
    hourly = [
        HourlyForecast(
            time=t, pressure_levels=[PressureLevelData(pressure_hpa=p) for p in _LEVELS],
        )
        for t in times
    ]
    wf = WaypointForecast(
        waypoint=Waypoint(icao="ZZPA", name="Test", lat=lat, lon=lon),
        model=model, fetched_at=now, hourly=hourly,
    )
    return RouteCrossSection(
        model=model, route_points=[], fetched_at=now, point_forecasts=[wf],
    )


def _rp(lat=48.9, lon=2.4):
    from weatherbrief.models import RoutePoint
    return RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=0.0)


def _col(clw=2e-4, ice=1e-4):
    return {p: {"cloud_liquid_water_kg_kg": clw, "ice_mixing_ratio_kg_kg": ice,
                "cloud_area_fraction_pct": 60.0, "rain_water_kg_kg": 1e-5}
            for p in _LEVELS}


class TestPrepare:
    DEP = datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc)
    NOW = datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc)

    def _prepare(self, sections, route, tmp_path, dep=None):
        import weatherbrief.fetch.grib as grib_mod
        return grib_mod._prepare_arome(
            sections, route, dep or self.DEP, data_dir=tmp_path,
            flight_duration_hours=2.0, as_of_time=self.NOW,
        )

    def test_disabled_by_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv(af.AROME_ENABLED_ENV, raising=False)
        assert self._prepare([_mf_section([self.DEP])], [_rp()], tmp_path) == (None, None)

    def test_no_meteofrance_section(self, monkeypatch, tmp_path):
        from weatherbrief.models import ModelSource
        monkeypatch.setenv(af.AROME_ENABLED_ENV, "1")
        sections = [_mf_section([self.DEP], model=ModelSource.GFS)]
        assert self._prepare(sections, [_rp()], tmp_path) == (None, None)

    def test_out_of_domain(self, monkeypatch, tmp_path):
        monkeypatch.setenv(af.AROME_ENABLED_ENV, "1")
        route = [_rp(), _rp(37.55, 2.0)]
        assert self._prepare([_mf_section([self.DEP])], route, tmp_path) == (None, "out_of_domain")

    def test_beyond_horizon(self, monkeypatch, tmp_path):
        monkeypatch.setenv(af.AROME_ENABLED_ENV, "1")
        dep = self.NOW + timedelta(hours=60)
        assert self._prepare([_mf_section([dep])], [_rp()], tmp_path, dep=dep) == (
            None, "out_of_range",
        )

    def test_no_published_run(self, monkeypatch, tmp_path):
        import weatherbrief.fetch.grib as grib_mod
        monkeypatch.setenv(af.AROME_ENABLED_ENV, "1")
        monkeypatch.setattr(grib_mod, "_grib_session", lambda: _Session())
        assert self._prepare([_mf_section([self.DEP])], [_rp()], tmp_path) == (None, "no_data")

    def test_resolves_run_and_caps_groups_to_the_window(self, monkeypatch, tmp_path):
        import weatherbrief.fetch.grib as grib_mod
        monkeypatch.setenv(af.AROME_ENABLED_ENV, "1")
        # 12z run; window 18–20z = f006..f008 → two groups, never all nine.
        url = af.arome_package_url("20260730", 12, "07H12H")
        monkeypatch.setattr(grib_mod, "_grib_session", lambda: _Session(published={url}))
        ctx, skip = self._prepare([_mf_section([self.DEP])], [_rp()], tmp_path)
        assert skip is None
        assert (ctx.init_date, ctx.init_hour) == ("20260730", 12)
        assert ctx.groups == {"00H06H": [6], "07H12H": [7, 8]}
        assert ctx.levels == sorted(_LEVELS)
        assert ctx.run_dir.parent.name == "arome"


class TestDecodeAndMerge:
    INIT = ("20260730", 12)

    def _ctx(self, tmp_path, groups):
        import weatherbrief.fetch.grib as grib_mod
        run_dir = tmp_path / "arome" / "20260730_12z"
        run_dir.mkdir(parents=True)
        for label in groups:
            (run_dir / af.arome_group_cache_key(label)).write_bytes(b"x")
        return grib_mod._AromeContext(
            *self.INIT, groups, run_dir, sorted(_LEVELS), [48.9], [2.4], session=None,
        )

    @staticmethod
    def _times(*fhours):
        init = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        return [init + timedelta(hours=f) for f in fhours]

    def _run(self, monkeypatch, tmp_path, decoded_by_hour, groups):
        import weatherbrief.fetch.grib as grib_mod

        def fake_dispatch(worker, path, hours, lats, lons, levels):
            assert worker == "decode_arome_pressure"
            return {"points": {h: decoded_by_hour[h] for h in hours},
                    "finite_fraction": 0.828, "cc_scale": 100.0, "dropped": []}

        monkeypatch.setattr(grib_mod, "_dispatch_decode", fake_dispatch)
        cs = _mf_section(self._times(*[h for hs in groups.values() for h in hs]))
        result = grib_mod._decode_and_merge_arome(
            self._ctx(tmp_path, groups), [cs], [], [_rp()],
        )
        return result, cs

    def test_complete_window_is_merged(self, monkeypatch, tmp_path):
        groups = {"00H06H": [6], "07H12H": [7]}
        (ts, skip), cs = self._run(
            monkeypatch, tmp_path, {6: [_col()], 7: [_col(clw=3e-4)]}, groups,
        )
        assert skip is None
        assert ts == int(datetime(2026, 7, 30, 12, tzinfo=timezone.utc).timestamp())
        h6, h7 = cs.point_forecasts[0].hourly
        pl = {p.pressure_hpa: p for p in h7.pressure_levels}
        assert pl[850].cloud_liquid_water_kg_kg == pytest.approx(3e-4)
        assert pl[850].ice_mixing_ratio_kg_kg == pytest.approx(1e-4)
        assert pl[850].rain_water_kg_kg == pytest.approx(1e-5)
        assert pl[850].cloud_area_fraction_pct == pytest.approx(60.0)
        assert h6.pressure_levels[0].cloud_liquid_water_kg_kg is not None

    def test_one_incomplete_hour_leaves_the_slot_untouched(self, monkeypatch, tmp_path):
        bad = _col()
        del bad[700]["ice_mixing_ratio_kg_kg"]   # liquid without ice
        groups = {"00H06H": [6], "07H12H": [7]}
        (ts, skip), cs = self._run(monkeypatch, tmp_path, {6: [_col()], 7: [bad]}, groups)
        assert (ts, skip) == (None, "incomplete")
        for h in cs.point_forecasts[0].hourly:
            assert all(p.cloud_liquid_water_kg_kg is None for p in h.pressure_levels)

    def test_point_outside_the_data_is_out_of_domain(self, monkeypatch, tmp_path):
        (ts, skip), _ = self._run(monkeypatch, tmp_path, {6: [{}]}, {"00H06H": [6]})
        assert (ts, skip) == (None, "out_of_domain")

    def test_missing_group_file_is_no_data(self, monkeypatch, tmp_path):
        import weatherbrief.fetch.grib as grib_mod
        ctx = self._ctx(tmp_path, {"00H06H": [6]})
        (ctx.run_dir / af.arome_group_cache_key("00H06H")).unlink()
        cs = _mf_section(self._times(6))
        assert grib_mod._decode_and_merge_arome(ctx, [cs], [], [_rp()]) == (None, "no_data")


class TestFreshnessRegistration:
    def test_registry_entry(self):
        from weatherbrief.fetch.freshness.registry import SOURCE_REGISTRY
        cfg = SOURCE_REGISTRY["arome:mf"]
        assert cfg.cycles == (0, 3, 6, 9, 12, 15, 18, 21)
        assert cfg.horizon == timedelta(hours=51)
        assert cfg.readiness_check == "arome_mf"
        assert cfg.role == "cloud-enrichment"

    def test_env_gated(self, monkeypatch):
        from weatherbrief.fetch.freshness.registry import SOURCE_REGISTRY
        monkeypatch.delenv("WB_AROME_ENABLED", raising=False)
        assert not SOURCE_REGISTRY["arome:mf"].is_active
        monkeypatch.setenv("WB_AROME_ENABLED", "1")
        assert SOURCE_REGISTRY["arome:mf"].is_active

    def test_readiness_check_is_dispatchable(self):
        from weatherbrief.fetch.freshness.sources import _DISPATCH
        assert "arome_mf" in _DISPATCH

    def test_env_gate_matches_the_fetch_flag(self):
        from weatherbrief.fetch.freshness.registry import SOURCE_REGISTRY
        assert SOURCE_REGISTRY["arome:mf"].env_gate == af.AROME_ENABLED_ENV

    def test_source_key_matches(self):
        assert af.AROME.source_key == "arome:mf"


def test_meteofrance_levels_are_all_published_by_arome():
    """Every Open-Meteo Météo-France level must be patchable from IP2."""
    from weatherbrief.fetch.variables import METEOFRANCE_PRESSURE_LEVELS
    assert set(METEOFRANCE_PRESSURE_LEVELS) <= set(af.AROME.pressure_levels)
