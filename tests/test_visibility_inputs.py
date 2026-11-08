"""Visibility-derivation input columns (#979 phase 0)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from weatherbrief.analysis.visibility import (
    VISIBILITY_INPUT_COLUMNS,
    icon_lowest_level_columns,
    level_850_columns,
    snapshot_input_columns,
)
from weatherbrief.db.models import AirportForecastSnapshotRow
from weatherbrief.tasks.snapshot_artifact import snapshot_columns


def _lv(p, t=None, td=None, w=None):
    return SimpleNamespace(pressure_hpa=p, temperature_c=t, dewpoint_c=td, wind_speed_kt=w)


class TestIconLowestLevel:
    def test_maps_each_variable(self):
        out = icon_lowest_level_columns({
            "ml_qc": 1e-4, "ml_qi": 2e-6, "ml_qr": 3e-5, "ml_qs": 4e-5, "ml_clc": 90.0,
            "t_2m": 280.0,  # a surface field is not an input column
        })
        assert out == {
            "lowest_level_cloud_water_kg_kg": 1e-4,
            "lowest_level_cloud_ice_kg_kg": 2e-6,
            "lowest_level_rain_kg_kg": 3e-5,
            "lowest_level_snow_kg_kg": 4e-5,
            "lowest_level_cloud_cover_pct": 90.0,
        }

    def test_missing_is_absent_not_zero(self):
        assert icon_lowest_level_columns({"ml_qc": 1e-4}) == {
            "lowest_level_cloud_water_kg_kg": 1e-4,
        }
        assert icon_lowest_level_columns(None) == {}
        assert icon_lowest_level_columns({"ml_qc": float("nan")}) == {}

    def test_packing_noise_and_garbage(self):
        out = icon_lowest_level_columns({"ml_qc": -1e-9, "ml_qi": 0.5, "ml_clc": 100.4})
        assert out["lowest_level_cloud_water_kg_kg"] == 0.0
        assert "lowest_level_cloud_ice_kg_kg" not in out  # 500 g/kg is not air
        assert out["lowest_level_cloud_cover_pct"] == 100.0


class TestLevel850:
    LEVELS = [_lv(1000, 12.0, 11.0, 5.0), _lv(850, 4.0, -2.0, 20.0), _lv(700, -5.0)]

    def test_reads_the_850_level(self):
        assert level_850_columns(self.LEVELS, 1015.0) == {
            "t850_c": 4.0, "td850_c": -2.0, "wind_speed_850_kt": 20.0,
        }

    def test_unknown_surface_pressure_still_reads(self):
        assert level_850_columns(self.LEVELS)["t850_c"] == 4.0

    @pytest.mark.parametrize("sp", [850.0, 855.0, 800.0])
    def test_not_clear_above_the_ground(self, sp):
        assert level_850_columns(self.LEVELS, sp) == {}

    def test_float_or_string_850_level(self):
        for p in (850.0, 850.0000001, "850"):
            assert level_850_columns([_lv(p, 4.0, 1.0, 30.0)])["t850_c"] == 4.0
        assert level_850_columns([_lv(None, 4.0, 1.0, 30.0)]) == {}

    def test_no_850_level_or_no_levels(self):
        assert level_850_columns([_lv(1000, 1.0)], 1000.0) == {}
        assert level_850_columns(None) == {}
        assert level_850_columns([_lv(850, None, None, 12.0)]) == {"wind_speed_850_kt": 12.0}

    def test_snapshot_input_columns_from_hourly(self):
        hourly = SimpleNamespace(surface_pressure_hpa=1000.0, pressure_levels=self.LEVELS)
        assert snapshot_input_columns(hourly) == {
            "surface_pressure_hpa": 1000.0,
            "t850_c": 4.0, "td850_c": -2.0, "wind_speed_850_kt": 20.0,
        }
        assert snapshot_input_columns(SimpleNamespace()) == {"surface_pressure_hpa": None}


def test_columns_exist_and_travel_in_the_artifact():
    table_cols = {c.name for c in AirportForecastSnapshotRow.__table__.columns}
    assert set(VISIBILITY_INPUT_COLUMNS) <= table_cols
    # The off-box snapshot artifact is column-driven: the inputs ride along.
    assert set(VISIBILITY_INPUT_COLUMNS) <= set(snapshot_columns())
