"""Fill-rate tests for nwp_conv_precip_mm_h on the standalone GFS/ICON path (#585).

The field was NULL for every GFS and ICON snapshot while every unit test on the
builders passed: the builders were correct in isolation, and the loss happened
at the hand-off. GFS's CPRAT decodes under cfgrib shortName ``cpr``, so a map
keyed on ``cprat`` matched nothing. ICON's ``crr`` is accumulated since init,
and the standalone fetcher decoded each sampled hour alone, with no predecessor
to difference against.

So these tests do not hand-build raw dicts. They encode a real GRIB2 message
with eccodes, put it in the GRIB cache, and drive it through the production
chain: cache -> cfgrib decode -> diagnostics builder -> DTO -> snapshot dict.
The assertion is the fraction of snapshots that end up with a value, not that
the key exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

eccodes = pytest.importorskip("eccodes")
pytest.importorskip("cfgrib")

from weatherbrief.fetch.grib.cache import cache_dir_for_run, cache_key, put_cached  # noqa: E402
from weatherbrief.tasks.airport_watchlist import WatchlistAirport  # noqa: E402

INIT = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
INIT_DATE = "20260930"

# Inside the GRIB2 sample grid (60N..0, 0E..30E, 2 deg).
AIRPORTS = [
    WatchlistAirport(icao="EGLL", lat=51.47, lon=0.46),
    WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55),
    WatchlistAirport(icao="EDDF", lat=50.03, lon=8.56),
    WatchlistAirport(icao="LIRF", lat=41.80, lon=12.25),
]


def _grib_message(
    *,
    centre: int,
    category: int,
    number: int,
    value: float,
    step_h: int,
    accumulated: bool = False,
    level_type: int = 1,
) -> bytes:
    """One constant-valued surface GRIB2 message on the eccodes sample grid."""
    h = eccodes.codes_grib_new_from_samples("GRIB2")
    try:
        eccodes.codes_set(h, "centre", centre)
        if accumulated:
            # DWD writes RAIN_CON as an accumulation since init (PDT 4.8).
            eccodes.codes_set(h, "productDefinitionTemplateNumber", 8)
        eccodes.codes_set(h, "discipline", 0)
        eccodes.codes_set(h, "parameterCategory", category)
        eccodes.codes_set(h, "parameterNumber", number)
        eccodes.codes_set(h, "typeOfFirstFixedSurface", level_type)
        eccodes.codes_set(h, "dataDate", int(INIT_DATE))
        eccodes.codes_set(h, "dataTime", 0)
        if accumulated:
            eccodes.codes_set(h, "typeOfStatisticalProcessing", 1)
            eccodes.codes_set(h, "startStep", 0)
            eccodes.codes_set(h, "endStep", step_h)
        else:
            eccodes.codes_set(h, "step", step_h)
        n = eccodes.codes_get(h, "numberOfValues")
        eccodes.codes_set_values(h, [value] * n)
        return eccodes.codes_get_message(h)
    finally:
        eccodes.codes_release(h)


@pytest.fixture
def inline_decode(monkeypatch, tmp_path):
    """Decode in-process, and point DATA_DIR at an empty temp cache."""
    import weatherbrief.fetch.grib as grib

    # Legacy FIFO path with no pool runs each job in-process through the real
    # decode_worker functions; the priority dispatcher would boot a pool.
    monkeypatch.setenv("GRIB_DECODE_PRIORITY_ENABLED", "0")
    monkeypatch.setattr(grib, "_get_decode_pool", lambda: None)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def _snapshots(model: str, offsets: list[int]) -> list[dict]:
    return [
        {"icao": a.icao, "model": model, "forecast_hour": INIT + timedelta(hours=h)}
        for h in offsets
        for a in AIRPORTS
    ]


def _fill_rate(snapshots: list[dict], key: str) -> float:
    return sum(s.get(key) is not None for s in snapshots) / len(snapshots)


class TestGfsCpratReachesTheSnapshot:
    """NCEP CPRAT: discipline 0, category 1, number 37, kg/m2/s instantaneous."""

    RATE_KG_M2_S = 0.0005  # 1.8 mm/h

    def _cache(self, data_dir, fhour):
        run_dir = cache_dir_for_run(data_dir, INIT_DATE, 0, model="gfs")
        put_cached(
            run_dir, cache_key(fhour, "CLOUD_DIAG"),
            _grib_message(centre=7, category=1, number=37,
                          value=self.RATE_KG_M2_S, step_h=fhour),
        )

    def test_cfgrib_names_cprat_cpr_and_the_map_matches_it(self, tmp_path):
        """The half #566 did not verify: what cfgrib calls the decoded field."""
        import cfgrib

        from weatherbrief.fetch.grib.decode import _CLOUD_DIAG_FIELD_MAP

        path = tmp_path / "cprat.grib2"
        path.write_bytes(
            _grib_message(centre=7, category=1, number=37, value=0.0, step_h=6)
        )
        names = set()
        for ds in cfgrib.open_datasets(str(path), backend_kwargs={"indexpath": ""}):
            for var_name, var in ds.data_vars.items():
                names.add((str(var_name), var.attrs.get("GRIB_typeOfLevel")))
            ds.close()

        assert names, "synthetic CPRAT message did not decode"
        for key in names:
            assert _CLOUD_DIAG_FIELD_MAP.get(key) == "conv_precip_rate_kg_m2_s", (
                f"cfgrib decodes CPRAT as {key}, which the field map does not key"
            )

    def test_fill_rate_through_enrich_with_grib(self, inline_decode):
        from weatherbrief.tasks.standalone_verification import _enrich_with_grib

        offsets = [6, 30, 54]
        for h in offsets:
            self._cache(inline_decode, h)
        snapshots = _snapshots("gfs", offsets)

        _enrich_with_grib(snapshots, "gfs", INIT, AIRPORTS, MagicMock())

        assert _fill_rate(snapshots, "nwp_conv_precip_mm_h") == 1.0
        for s in snapshots:
            assert s["nwp_conv_precip_mm_h"] == pytest.approx(1.8)


class TestIconRainConIsDeaccumulated:
    """DWD RAIN_CON: discipline 0, category 1, number 76, accumulated kg/m2.

    Decodes as cfgrib ``crr``. The standalone path must fetch each sampled
    hour's predecessor step to turn the accumulation into a rate.
    """

    def _run_dir(self, data_dir):
        return cache_dir_for_run(data_dir, INIT_DATE, 0, model="icon-eu")

    def _rain_con(self, fhour, accumulated_mm):
        return _grib_message(centre=78, category=1, number=76,
                             value=accumulated_mm, step_h=fhour, accumulated=True)

    def _full_blob(self, fhour, accumulated_mm):
        """Sampled-hour blob: rain_con plus CLCT, since a blob carrying no
        cloud field at all builds no diagnostics (true of the briefing path
        too, and never the case for the real twelve-variable blob)."""
        return (
            _grib_message(centre=78, category=6, number=1, value=40.0, step_h=fhour)
            + self._rain_con(fhour, accumulated_mm)
        )

    def test_fill_rate_through_enrich_with_grib(self, inline_decode, monkeypatch):
        from weatherbrief.fetch.grib.icon_eu_fetch import (
            ICON_EU_CLOUD_DIAG_CACHE_KEY,
            ICON_EU_RAIN_CON_CACHE_KEY,
        )
        from weatherbrief.tasks.standalone_verification import _enrich_with_grib

        run_dir = self._run_dir(inline_decode)
        # Sampled hours (full blob) and their predecessors (rain_con-only blob,
        # as the standalone fetcher would cache them): 2 mm/h in the hourly
        # region, 3 mm over the 3 h window past +78 h = 1 mm/h.
        accum = {4: 10.0, 28: 50.0, 52: 90.0, 84: 200.0}
        prev = {3: 8.0, 27: 48.0, 51: 88.0, 81: 197.0}
        for h, mm in accum.items():
            put_cached(run_dir, cache_key(h, ICON_EU_CLOUD_DIAG_CACHE_KEY),
                       self._full_blob(h, mm))
        for h, mm in prev.items():
            put_cached(run_dir, cache_key(h, ICON_EU_RAIN_CON_CACHE_KEY),
                       self._rain_con(h, mm))

        def no_network(*args, **kwargs):
            raise AssertionError("everything is cached; nothing should be fetched")

        monkeypatch.setattr(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level",
            no_network,
        )

        offsets = sorted(accum)
        snapshots = _snapshots("icon", offsets)
        _enrich_with_grib(snapshots, "icon", INIT, AIRPORTS, MagicMock())

        assert _fill_rate(snapshots, "nwp_conv_precip_mm_h") == 1.0
        for s in snapshots:
            offset = int((s["forecast_hour"] - INIT).total_seconds() // 3600)
            expected = 1.0 if offset == 84 else 2.0
            assert s["nwp_conv_precip_mm_h"] == pytest.approx(expected, abs=1e-4)

    def test_predecessor_is_fetched_as_rain_con_only(self, inline_decode, monkeypatch):
        """A cold predecessor costs one file, not the full cloud-diag set."""
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU_CLOUD_DIAG_CACHE_KEY
        from weatherbrief.tasks.standalone_grib import fetch_icon_cloud_diag

        put_cached(self._run_dir(inline_decode),
                   cache_key(28, ICON_EU_CLOUD_DIAG_CACHE_KEY),
                   self._full_blob(28, 50.0))

        calls: list[tuple[list[int], list[str] | None]] = []

        def fake_fetch(init_date, init_hour, hours, variables=None, session=None, **kw):
            calls.append((list(hours), variables))
            return {h: self._rain_con(h, 47.0) for h in hours}

        monkeypatch.setattr(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level",
            fake_fetch,
        )

        result = fetch_icon_cloud_diag(INIT_DATE, 0, [28], [51.47], [0.46])

        assert calls == [([27], ["rain_con"])]
        assert result[28][0].convective_precip_mm_h == pytest.approx(3.0, abs=1e-4)

    def test_missing_predecessor_leaves_rate_unknown_not_zero(
        self, inline_decode, monkeypatch,
    ):
        """No predecessor -> None (missing data), never a fabricated 0.0."""
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU_CLOUD_DIAG_CACHE_KEY
        from weatherbrief.tasks.standalone_grib import fetch_icon_cloud_diag

        put_cached(self._run_dir(inline_decode),
                   cache_key(28, ICON_EU_CLOUD_DIAG_CACHE_KEY),
                   self._full_blob(28, 50.0))
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level",
            lambda *a, **kw: {},
        )

        result = fetch_icon_cloud_diag(INIT_DATE, 0, [28], [51.47], [0.46])

        assert 28 in result
        assert result[28][0].convective_precip_mm_h is None


# ---------------------------------------------------------------------------
# #635: the GRIB diagnostics reach the sounding pass, not just the columns
# ---------------------------------------------------------------------------

# A moist, conditionally unstable column — enough for MetPy to build a parcel.
_LEVELS = [
    (1000, 22, 85), (925, 17, 80), (850, 12, 75), (700, 4, 65),
    (600, -3, 60), (500, -12, 55), (400, -24, 45), (300, -39, 35),
    (250, -49, 30), (200, -55, 25), (150, -58, 20), (100, -60, 15),
]

# One sample hour a day over a 4-day horizon: offsets 4, 28, 52, 76, 100. For
# ICON, f100 is past the +78 h hourly region and snaps to f099 (predecessor
# f096), so the lookup has to apply the same snap as the fetch.
_SAMPLE_HOURS = [4]
_DAYS = 4
_OFFSETS = [4, 28, 52, 76, 100]


def _fake_open_meteo(model_source):
    """Patch target for ``OpenMeteoClient.fetch_multi_point``.

    Every hour of the horizon at every point, with pressure levels, filtered
    the way the real client filters at parse time (``hour_filter``).
    """
    from weatherbrief.models import HourlyForecast, Waypoint, WaypointForecast
    from weatherbrief.models.analysis import PressureLevelData

    def fake(self, points, model, *, start_date=None, end_date=None,
             chunk_size=None, hour_filter=None):
        self._record_call()
        out = []
        for p in points:
            hourly = []
            for h in range((_DAYS + 1) * 24):
                t = INIT + timedelta(hours=h)
                if hour_filter is not None and t.hour not in hour_filter:
                    continue
                hourly.append(HourlyForecast(
                    time=t,
                    temperature_2m_c=22.0,
                    dewpoint_2m_c=18.0,
                    cape_jkg=1200.0,
                    pressure_levels=[
                        PressureLevelData(
                            pressure_hpa=hpa, temperature_c=tc,
                            relative_humidity_pct=rh,
                            wind_speed_kt=25, wind_direction_deg=250,
                        )
                        for hpa, tc, rh in _LEVELS
                    ],
                ))
            out.append(WaypointForecast(
                waypoint=Waypoint(icao=p.waypoint_icao, name=p.waypoint_icao,
                                  lat=p.lat, lon=p.lon),
                model=model_source,
                fetched_at=INIT,
                hourly=hourly,
            ))
        return out

    return fake


def _no_network(*args, **kwargs):
    raise AssertionError("everything is cached; nothing should be fetched")


def _run_cycle_leg(model, monkeypatch, pooled):
    """Drive ``fetch_model_snapshots`` (the cycle's GFS/ICON entry point)."""
    from weatherbrief.models import ModelSource
    from weatherbrief.tasks import standalone_verification as sv

    monkeypatch.setattr(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        _fake_open_meteo(ModelSource(model)),
    )
    # The pooled branch normally needs a live pool; with none configured the
    # legacy dispatcher runs ``analyze_sounding_batch`` in-process, which still
    # round-trips each hour through ``build_sounding_payload``.
    monkeypatch.setattr(sv, "_pooled_soundings_active", lambda requested: requested)

    snaps, _ = sv.fetch_model_snapshots(
        model, INIT, AIRPORTS, MagicMock(),
        sample_hours=_SAMPLE_HOURS, days=_DAYS, pool_soundings=pooled,
    )
    return snaps


def _comparable(snaps):
    return sorted(
        (tuple(sorted((k, repr(v)) for k, v in s.items())) for s in snaps),
    )


class TestGfsGradesThroughTheNativeTrack:
    """GFS: TCDC on the convective cloud layer + CPRAT, all cached."""

    COVER_PCT = 45.0
    RATE_KG_M2_S = 0.0005  # 1.8 mm/h

    def _cache(self, data_dir, fhour):
        run_dir = cache_dir_for_run(data_dir, INIT_DATE, 0, model="gfs")
        put_cached(
            run_dir, cache_key(fhour, "CLOUD_DIAG"),
            # 244 = convective cloud layer (cfgrib ``convectiveCloudLayer``).
            _grib_message(centre=7, category=6, number=1, value=self.COVER_PCT,
                          step_h=fhour, level_type=244)
            + _grib_message(centre=7, category=1, number=37,
                            value=self.RATE_KG_M2_S, step_h=fhour),
        )

    @pytest.fixture
    def cached(self, inline_decode, monkeypatch):
        for h in _OFFSETS:
            self._cache(inline_decode, h)
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.grib_fetch.fetch_idx", _no_network,
        )
        return inline_decode

    @pytest.mark.parametrize("pooled", [False, True], ids=["inline", "pooled"])
    def test_every_row_carries_a_native_method(self, cached, monkeypatch, pooled):
        snaps = _run_cycle_leg("gfs", monkeypatch, pooled)

        assert len(snaps) == len(AIRPORTS) * len(_OFFSETS)
        assert _fill_rate(snaps, "nwp_conv_method") == 1.0
        # Cover present -> the GFS cover pathway, not the CAPE fallback.
        assert {s["nwp_conv_method"] for s in snaps} == {"nwp"}
        # The persisted ingredient columns are still the GRIB's own values.
        for s in snaps:
            assert s["nwp_conv_cover_pct"] == pytest.approx(self.COVER_PCT)
            assert s["nwp_conv_precip_mm_h"] == pytest.approx(1.8)

    def test_pooled_and_inline_agree(self, cached, monkeypatch):
        inline = _run_cycle_leg("gfs", monkeypatch, pooled=False)
        pooled = _run_cycle_leg("gfs", monkeypatch, pooled=True)
        assert _comparable(inline) == _comparable(pooled)


class TestIconGradesThroughTheNativeTrack:
    """ICON: CLCT + accumulated RAIN_CON, with predecessors, all cached."""

    def _rain_con(self, fhour, accumulated_mm):
        return _grib_message(centre=78, category=1, number=76,
                             value=accumulated_mm, step_h=fhour, accumulated=True)

    @pytest.fixture
    def cached(self, inline_decode, monkeypatch):
        from weatherbrief.fetch.grib.icon_eu_fetch import (
            ICON_EU_CLOUD_DIAG_CACHE_KEY,
            ICON_EU_RAIN_CON_CACHE_KEY,
        )

        run_dir = cache_dir_for_run(inline_decode, INIT_DATE, 0, model="icon-eu")
        # Sampled steps (f100 snapped to f099) with 2 mm/h in the hourly region
        # and 6 mm over the 3 h window f096->f099, also 2 mm/h.
        accum = {4: 10.0, 28: 50.0, 52: 90.0, 76: 150.0, 99: 206.0}
        prev = {3: 8.0, 27: 48.0, 51: 88.0, 75: 148.0, 96: 200.0}
        for h, mm in accum.items():
            put_cached(
                run_dir, cache_key(h, ICON_EU_CLOUD_DIAG_CACHE_KEY),
                _grib_message(centre=78, category=6, number=1, value=40.0, step_h=h)
                + self._rain_con(h, mm),
            )
        for h, mm in prev.items():
            put_cached(run_dir, cache_key(h, ICON_EU_RAIN_CON_CACHE_KEY),
                       self._rain_con(h, mm))
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level",
            _no_network,
        )
        return inline_decode

    @pytest.mark.parametrize("pooled", [False, True], ids=["inline", "pooled"])
    def test_every_row_carries_a_native_method(self, cached, monkeypatch, pooled):
        snaps = _run_cycle_leg("icon", monkeypatch, pooled)

        assert len(snaps) == len(AIRPORTS) * len(_OFFSETS)
        # Including the snapped f100 rows: a lookup keyed on the raw offset
        # would miss them.
        assert _fill_rate(snaps, "nwp_conv_method") == 1.0
        # No tower geometry, but the scheme is precipitating: the rate ladder.
        assert {s["nwp_conv_method"] for s in snaps} == {"nwp_precip"}
        for s in snaps:
            assert s["nwp_conv_precip_mm_h"] == pytest.approx(2.0, abs=1e-4)

    def test_pooled_and_inline_agree(self, cached, monkeypatch):
        inline = _run_cycle_leg("icon", monkeypatch, pooled=False)
        pooled = _run_cycle_leg("icon", monkeypatch, pooled=True)
        assert _comparable(inline) == _comparable(pooled)


class TestPlannedHoursMatchTheFetchFilter:
    """The GRIB fetch is planned before the Open-Meteo response exists, so the
    planner must pick exactly the hours the fetch loop keeps — a drift would
    leave rows with no diagnostics and no warning."""

    @pytest.mark.parametrize("sample_hours,days", [
        (None, 4),   # the map cycle's per-day grid
        ([4], 4),
        ([13], 2),   # alternates: one ETA hour
    ])
    def test_planned_equals_kept(self, monkeypatch, sample_hours, days):
        from weatherbrief.models import ModelSource
        from weatherbrief.tasks import standalone_verification as sv

        monkeypatch.setattr(
            "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
            _fake_open_meteo(ModelSource.GFS),
        )
        # Analysis is irrelevant here and slow over the full grid.
        monkeypatch.setattr(sv, "_enrich_with_sounding", lambda *a, **kw: None)

        snaps, _ = sv._fetch_forecasts_for_model(
            "gfs", INIT, AIRPORTS[:1], MagicMock(),
            sample_hours=sample_hours, days=days,
        )
        kept = sorted({s["forecast_hour"] for s in snaps})
        planned = sv._planned_valid_times(INIT, days, sample_hours)
        assert kept and planned == kept
