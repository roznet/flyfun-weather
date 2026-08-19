"""Pooled sounding analysis for the standalone cycle (#448 PR B).

Covers the serialisation round-trip (inline path ≡ batch path), per-item
fail-silence, the merge helper, the opt-in gating, and one real spawn-pool
round-trip to catch pickling / worker-import regressions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from weatherbrief.analysis.sounding.snapshot_fields import (
    analyze_sounding_batch_items,
    build_sounding_payload,
    compute_snapshot_sounding_fields,
)
from weatherbrief.models.analysis import HourlyForecast, PressureLevelData

SOUNDING_FIELDS = (
    "sounding_ceiling_ft", "freezing_level_ft", "sounding_cape_jkg",
    "sounding_cin_jkg", "sounding_lifted_index", "sounding_cloud_base_ft",
    "sounding_convective_risk",
)


def _make_hourly(temp_offset: float = 0.0) -> HourlyForecast:
    levels = []
    for i, p in enumerate([1000, 950, 900, 850, 800, 750, 700, 600, 500, 400, 300, 250]):
        t = 15.0 + temp_offset - (1000 - p) * 0.065
        levels.append(PressureLevelData(
            pressure_hpa=p,
            temperature_c=t,
            dewpoint_c=t - 3 - i * 0.4,
            wind_speed_kt=10 + i,
            wind_direction_deg=(200 + i * 3) % 360,
            geopotential_height_m=44330 * (1 - (p / 1013.25) ** 0.1903),
            relative_humidity_pct=max(10.0, 85.0 - i * 3),
            cloud_area_fraction_pct=60.0,
        ))
    return HourlyForecast(
        time=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
        temperature_2m_c=15.0 + temp_offset,
        dewpoint_2m_c=11.0,
        surface_pressure_hpa=1013.0,
        cape_jkg=150.0,
        cloud_cover_pct=70.0,
        cloud_cover_low_pct=50.0,
    ).model_copy(update={"pressure_levels": levels})


class TestBatchEquivalence:
    def test_batch_round_trip_matches_inline(self):
        """Serialised batch path must produce the same fields as inline."""
        hourly = _make_hourly()
        inline = compute_snapshot_sounding_fields(hourly, "gfs")
        assert inline, "fixture profile should analyse successfully"

        [batched] = analyze_sounding_batch_items(
            [build_sounding_payload(hourly, "gfs")]
        )
        assert batched == inline

    def test_payload_surface_excludes_pressure_levels(self):
        payload = build_sounding_payload(_make_hourly(), "icon")
        assert "pressure_levels" not in payload["surface"]
        assert len(payload["levels"]) == 12
        assert payload["model"] == "icon"

    def test_no_pressure_levels_yields_empty(self):
        hourly = _make_hourly().model_copy(update={"pressure_levels": []})
        assert compute_snapshot_sounding_fields(hourly, "gfs") == {}


class TestBatchFailSilence:
    def test_bad_item_isolated_from_neighbours(self):
        good = build_sounding_payload(_make_hourly(), "gfs")
        bad = {"levels": [{"pressure_hpa": "garbage"}], "surface": {}, "model": "gfs"}
        results = analyze_sounding_batch_items([good, bad, good])
        assert len(results) == 3
        assert results[0] and results[2]
        assert results[0] == results[2]
        assert results[1] == {}


class TestPooledMergeHelper:
    def test_merge_by_index(self):
        from weatherbrief.tasks.standalone_verification import (
            _analyze_soundings_pooled,
        )

        hourly = _make_hourly()
        snaps = [{"icao": "LFPG"}, {"icao": "EGLL"}, {"icao": "EDDF"}]
        pending = [
            (0, build_sounding_payload(hourly, "gfs")),
            (2, build_sounding_payload(hourly, "gfs")),
        ]

        with patch(
            "weatherbrief.fetch.grib._dispatch_decode_parallel",
            side_effect=lambda jobs, **kw: [
                analyze_sounding_batch_items(args[0]) for _, args in jobs
            ],
        ):
            _analyze_soundings_pooled(pending, snaps)

        assert "sounding_ceiling_ft" in snaps[0] or "sounding_cape_jkg" in snaps[0]
        assert snaps[1] == {"icao": "EGLL"}, "untouched snapshot must stay surface-only"
        assert {k: v for k, v in snaps[0].items() if k != "icao"} == \
               {k: v for k, v in snaps[2].items() if k != "icao"}

    def test_dispatch_failure_degrades_to_surface_only(self):
        from weatherbrief.tasks.standalone_verification import (
            _analyze_soundings_pooled,
        )

        snaps = [{"icao": "LFPG"}]
        pending = [(0, build_sounding_payload(_make_hourly(), "gfs"))]
        with patch(
            "weatherbrief.fetch.grib._dispatch_decode_parallel",
            side_effect=RuntimeError("pool exploded"),
        ):
            _analyze_soundings_pooled(pending, snaps)
        assert snaps == [{"icao": "LFPG"}]

    def test_partial_batch_failure_isolated(self, monkeypatch):
        """One dead-lettered batch loses only its own snapshots' fields."""
        from weatherbrief.tasks import standalone_verification as sv

        monkeypatch.setattr(sv, "_SOUNDING_BATCH_SIZE", 1)  # 1 job per profile
        hourly = _make_hourly()
        snaps = [{"icao": "LFPG"}, {"icao": "EGLL"}]
        pending = [
            (0, build_sounding_payload(hourly, "gfs")),
            (1, build_sounding_payload(hourly, "gfs")),
        ]

        def fake_dispatch(jobs, **kw):
            assert kw.get("return_exceptions") is True
            out = []
            for j, (_, args) in enumerate(jobs):
                if j == 0:
                    out.append(RuntimeError("dead-lettered"))
                else:
                    out.append(analyze_sounding_batch_items(args[0]))
            return out

        with patch(
            "weatherbrief.fetch.grib._dispatch_decode_parallel",
            side_effect=fake_dispatch,
        ):
            sv._analyze_soundings_pooled(pending, snaps)

        assert snaps[0] == {"icao": "LFPG"}, "failed batch stays surface-only"
        assert len(snaps[1]) > 1, "sibling batch must keep its results"

    def test_priority_forwarded_to_dispatch(self):
        """The cycle's explicit BACKGROUND priority must reach the dispatcher
        (prod subprocess path never sets the ContextVar — PR #450 review)."""
        from weatherbrief.fetch.grib import DecodePriority
        from weatherbrief.tasks.standalone_verification import (
            _analyze_soundings_pooled,
        )

        captured: dict = {}

        def fake_dispatch(jobs, **kw):
            captured.update(kw)
            return [analyze_sounding_batch_items(args[0]) for _, args in jobs]

        snaps = [{"icao": "LFPG"}]
        pending = [(0, build_sounding_payload(_make_hourly(), "gfs"))]
        with patch(
            "weatherbrief.fetch.grib._dispatch_decode_parallel",
            side_effect=fake_dispatch,
        ):
            _analyze_soundings_pooled(
                pending, snaps, priority=DecodePriority.BACKGROUND,
            )
        assert captured["priority"] == DecodePriority.BACKGROUND


class TestDispatchReturnExceptions:
    def test_per_job_isolation_inline(self, monkeypatch):
        """return_exceptions=True yields the exception in-slot; default raises."""
        monkeypatch.setenv("GRIB_DECODE_WORKERS", "0")
        from weatherbrief.fetch.grib import _dispatch_decode_parallel

        jobs = [
            ("_test_echo", ("ok",)),
            ("_test_echo", ("x", 0.0, "boom")),
            ("_test_echo", ("also-ok",)),
        ]
        res = _dispatch_decode_parallel(jobs, return_exceptions=True)
        assert res[0] == "ok"
        assert isinstance(res[1], Exception)
        assert res[2] == "also-ok"

        with pytest.raises(Exception):
            _dispatch_decode_parallel(jobs)


class TestGating:
    def test_requires_both_opt_in_and_pool(self, monkeypatch):
        from weatherbrief.tasks.standalone_verification import (
            _pooled_soundings_active,
        )

        monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
        assert _pooled_soundings_active(True) is True
        assert _pooled_soundings_active(False) is False, \
            "alternates path (no opt-in) must stay inline"

        monkeypatch.setenv("GRIB_DECODE_WORKERS", "0")
        assert _pooled_soundings_active(True) is False


@pytest.mark.slow
class TestRealPoolRoundTrip:
    def test_batch_through_spawned_worker(self, monkeypatch):
        """One real spawn-pool dispatch — catches pickling and worker-side
        import regressions that inline mocks cannot."""
        import weatherbrief.fetch.grib as grib_pkg
        from weatherbrief.fetch.grib import _dispatch_decode, shutdown_decode_pool

        monkeypatch.setenv("GRIB_DECODE_WORKERS", "1")
        monkeypatch.setenv("GRIB_DECODE_PRIORITY_ENABLED", "0")
        shutdown_decode_pool()
        try:
            hourly = _make_hourly()
            payload = build_sounding_payload(hourly, "gfs")
            results = _dispatch_decode("analyze_sounding_batch", [payload, payload])
            assert len(results) == 2
            assert results[0] == results[1]
            assert results[0] == compute_snapshot_sounding_fields(hourly, "gfs")
        finally:
            shutdown_decode_pool()


# --- model-native layer ceiling is persisted alongside the DD one ---


def _hourly_with_3d_deck():
    """Profile whose 3D cloud fraction carries a BKN deck aloft."""
    from weatherbrief.models.analysis import HourlyForecast, PressureLevelData
    from datetime import datetime, timezone

    def lvl(p, t, td, caf, gh):
        return PressureLevelData(
            pressure_hpa=p, temperature_c=t, dewpoint_c=td,
            relative_humidity_pct=80.0, cloud_area_fraction_pct=caf,
            geopotential_height_m=gh,
        )
    return HourlyForecast(
        time=datetime(2026, 8, 20, 11, tzinfo=timezone.utc),
        pressure_levels=[
            lvl(1000, 20.0, 15.0, 0.0, 110.0),
            lvl(950, 17.0, 14.0, 5.0, 540.0),
            lvl(900, 15.0, 14.5, 70.0, 990.0),    # BKN
            lvl(850, 12.0, 11.0, 65.0, 1460.0),   # BKN
            lvl(700, 5.0, -10.0, 0.0, 3010.0),
        ],
    )


def test_snapshot_fields_record_the_native_layer_ceiling_and_source():
    """The third ceiling estimate is stored so DD/layer/diag can be compared."""
    from weatherbrief.analysis.sounding.snapshot_fields import (
        compute_snapshot_sounding_fields,
    )

    f = compute_snapshot_sounding_fields(_hourly_with_3d_deck(), "ecmwf")
    assert f.get("nwp_layer_source") == "nwp_3d"
    # Lowest BKN/OVC base from the model's own cloud field, not from DD.
    assert f.get("nwp_layer_ceiling_ft") is not None
    assert f["nwp_layer_ceiling_ft"] > 0


def test_snapshot_fields_omit_layer_ceiling_without_a_native_source():
    """No native layers -> both columns stay unset.

    NULL source is what tells a later analysis that the engine fell back to DD
    for this row, so the new ceiling equals the old one. It must not be
    confused with "native layers present, no BKN/OVC deck".
    """
    from datetime import datetime, timezone

    from weatherbrief.analysis.sounding.snapshot_fields import (
        compute_snapshot_sounding_fields,
    )
    from weatherbrief.models.analysis import HourlyForecast, PressureLevelData

    hourly = HourlyForecast(
        time=datetime(2026, 8, 20, 11, tzinfo=timezone.utc),
        pressure_levels=[
            PressureLevelData(pressure_hpa=p, temperature_c=t, dewpoint_c=td,
                              relative_humidity_pct=70.0)
            for p, t, td in ((1000, 20.0, 12.0), (900, 15.0, 8.0), (700, 5.0, -10.0))
        ],
    )
    f = compute_snapshot_sounding_fields(hourly, "ukmo")
    assert "nwp_layer_source" not in f
    assert "nwp_layer_ceiling_ft" not in f


def test_snapshot_fields_flag_a_native_source_that_found_no_deck():
    """Native layers present but empty -> source set, ceiling NULL.

    Distinct from "no native source" (both NULL): here the engine grades on the
    model's own cloud field and finds no BKN/OVC, rather than falling back to DD.
    """
    from datetime import datetime, timezone

    from weatherbrief.analysis.sounding.snapshot_fields import (
        compute_snapshot_sounding_fields,
    )
    from weatherbrief.models.analysis import HourlyForecast, PressureLevelData

    hourly = HourlyForecast(
        time=datetime(2026, 8, 20, 11, tzinfo=timezone.utc),
        pressure_levels=[
            PressureLevelData(pressure_hpa=p, temperature_c=t, dewpoint_c=td,
                              relative_humidity_pct=60.0,
                              cloud_area_fraction_pct=0.0, geopotential_height_m=gh)
            for p, t, td, gh in ((1000, 20.0, 8.0, 110.0), (900, 15.0, 2.0, 990.0),
                                 (700, 5.0, -15.0, 3010.0))
        ],
    )
    f = compute_snapshot_sounding_fields(hourly, "ecmwf")
    assert f.get("nwp_layer_source") is not None
    assert f.get("nwp_layer_ceiling_ft") is None
