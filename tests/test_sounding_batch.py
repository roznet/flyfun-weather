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
