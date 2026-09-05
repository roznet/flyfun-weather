"""Observation prose must preserve coverage, datum, and acquisition time."""

from datetime import datetime, timezone

import pytest

from weatherbrief.models.observed import (
    ObservedAnnulus, ObservedConditions, ObservedField, ObservedFlashAnnulus,
    ObservedFlashField, ObservedFlashStationSamples, ObservedStationSamples,
    ObservedTopsAnnulus, ObservedTopsField, ObservedTopsStationSamples,
)
from weatherbrief.observed.summary import build_summary

STAMP = datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)


def _tops_summary(*annuli):
    field = ObservedTopsField(
        source="eumetsat_ctth", quantity="cloud_top_height", valid_time=STAMP,
        age_minutes=25, stations=[
            ObservedTopsStationSamples(station_id=str(i), annuli=[a])
            for i, a in enumerate(annuli)
        ],
    )
    return " ".join(build_summary(ObservedConditions(
        computed_at=STAMP, corridor_nm=20, radii_nm=[20], cloud_tops=field,
    )))


def _grid_summary(*, source, quantity, annulus, field_name):
    field = ObservedField(
        source=source, quantity=quantity, valid_time=STAMP, age_minutes=25,
        stations=[ObservedStationSamples(station_id="A", annuli=[annulus])],
    )
    conditions = ObservedConditions(
        computed_at=STAMP, corridor_nm=20, radii_nm=[20], **{field_name: field},
    )
    return " ".join(build_summary(conditions))


def test_clear_claim_cannot_skip_unavailable_discs():
    text = _tops_summary(
        ObservedTopsAnnulus(radius_nm=20, total_px=100, valid_px=100, undetect_px=100),
        ObservedTopsAnnulus(radius_nm=20, total_px=100, nodata_px=100),
    )
    assert "whole corridor" not in text
    assert "1 of 2" in text
    assert "unavailable" in text


def test_clear_claim_scopes_unknown_pixels_even_above_coverage_threshold():
    text = _tops_summary(ObservedTopsAnnulus(
        radius_nm=20, total_px=100, valid_px=40, undetect_px=40, nodata_px=60,
    ))
    assert "whole corridor" not in text
    assert "sampled" in text or "covered" in text


def test_radar_absence_is_scoped_to_covered_samples_above_minimum_coverage():
    text = _grid_summary(
        source="opera_dbzh",
        quantity="reflectivity",
        field_name="reflectivity",
        annulus=ObservedAnnulus(
            radius_nm=20, total_px=100, valid_px=40, undetect_px=40, nodata_px=60,
        ),
    )
    assert "no echo above 20 dBZ in covered radar samples" in text
    assert "40% sample coverage" in text
    assert "no echo above 20 dBZ along the route" not in text


def test_positive_rain_rate_reports_own_partial_coverage_without_reflectivity():
    text = _grid_summary(
        source="opera_rate",
        quantity="rain_rate",
        field_name="rain_rate",
        annulus=ObservedAnnulus(
            radius_nm=20, total_px=100, valid_px=1, detected_px=1, nodata_px=99,
            max_value=25.0, mean_value=25.0, p90_value=25.0,
        ),
    )
    assert "Rain rate to 25.0 mm/h" in text
    assert "1% rain-rate sample coverage there" in text


def test_high_top_survives_partial_coverage_as_geometric_height():
    text = _tops_summary(ObservedTopsAnnulus(
        radius_nm=20, total_px=100, valid_px=1, detected_px=1, nodata_px=99,
        highest_fl=350, quality_method={"9": 1},
    ))
    assert "35,000 ft MSL" in text
    assert "geometric" in text
    assert "partial" in text
    assert "multi-layer" not in text


def test_method_nine_does_not_claim_multilayer_cloud():
    text = _tops_summary(ObservedTopsAnnulus(
        radius_nm=20, total_px=100, valid_px=100, detected_px=100,
        highest_fl=100, quality_method={"9": 100},
    ))
    assert "multi-layer" not in text


@pytest.mark.parametrize("count", [0, 2])
def test_lightning_names_acquisition_window_not_last_minutes(count):
    field = ObservedFlashField(
        source="eumetsat_li", quantity="flashes", valid_time=STAMP,
        age_minutes=25, window_minutes=10,
        stations=[ObservedFlashStationSamples(station_id="A", annuli=[
            ObservedFlashAnnulus(radius_nm=20, flash_count=count, nearest_flash_nm=5 if count else None),
        ])],
    )
    text = " ".join(build_summary(ObservedConditions(
        computed_at=STAMP, corridor_nm=20, radii_nm=[20], lightning=field,
    )))
    assert "2026-08-25 13:50" in text
    assert "14:00 UTC" in text
    assert "last" not in text
    assert "ago" not in text
    if not count:
        assert "no flashes detected" in text.lower()


def test_saved_cloud_summary_has_immutable_observation_time():
    text = _tops_summary(ObservedTopsAnnulus(
        radius_nm=20, total_px=100, valid_px=100, undetect_px=100,
    ))
    assert "2026-08-25 14:00 UTC" in text
    assert "ago" not in text
