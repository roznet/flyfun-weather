"""Tests for prompt builder (context assembly)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.digest.prompt_builder import build_digest_context
from weatherbrief.fetch.dwd_text import DWDTextForecasts
from weatherbrief.models import (
    AgreementLevel,
    EnhancedCloudLayer,
    ForecastSnapshot,
    HourlyForecast,
    IcingRisk,
    IcingType,
    IcingZone,
    ModelDivergence,
    ModelSource,
    PressureLevelData,
    RouteConfig,
    SoundingAnalysis,
    Waypoint,
    WaypointAnalysis,
    WaypointForecast,
    WindComponent,
)


@pytest.fixture
def sample_snapshot(sample_route, sample_pressure_levels):
    """Build a minimal ForecastSnapshot for testing."""
    target_time = datetime(2026, 2, 17, 9, 0, 0)

    hourly = HourlyForecast(
        time=target_time,
        temperature_2m_c=5.0,
        dewpoint_2m_c=2.0,
        wind_speed_10m_kt=12.0,
        wind_direction_10m_deg=270.0,
        cloud_cover_pct=60.0,
        precipitation_mm=0.5,
        freezing_level_m=1800.0,
        cape_jkg=50.0,
        visibility_m=8000.0,
        pressure_levels=sample_pressure_levels,
    )

    forecast = WaypointForecast(
        waypoint=sample_route.waypoints[0],
        model=ModelSource.GFS,
        fetched_at=datetime(2026, 2, 10, 12, 0, 0, tzinfo=timezone.utc),
        hourly=[hourly],
    )

    sounding = SoundingAnalysis(
        icing_zones=[
            IcingZone(
                base_ft=5000.0,
                top_ft=5000.0,
                base_pressure_hpa=850,
                top_pressure_hpa=850,
                risk=IcingRisk.MODERATE,
                icing_type=IcingType.MIXED,
                mean_temperature_c=-2.0,
                mean_wet_bulb_c=-3.0,
            )
        ],
        cloud_layers=[
            EnhancedCloudLayer(base_ft=3000.0, top_ft=6000.0)
        ],
    )

    analysis = WaypointAnalysis(
        waypoint=sample_route.waypoints[0],
        target_time=target_time,
        wind_components={
            "gfs": WindComponent(
                wind_speed_kt=25.0,
                wind_direction_deg=290.0,
                track_deg=135.0,
                headwind_kt=15.0,
                crosswind_kt=8.0,
            )
        },
        sounding={"gfs": sounding},
        model_divergence=[
            ModelDivergence(
                variable="temperature_c",
                model_values={"gfs": 5.0, "ecmwf": 6.0},
                mean=5.5,
                spread=1.0,
                agreement=AgreementLevel.GOOD,
            )
        ],
    )

    return ForecastSnapshot(
        route=sample_route,
        target_date="2026-02-17",
        fetch_date="2026-02-10",
        days_out=7,
        forecasts=[forecast],
        analyses=[analysis],
    )


def test_build_context_basic(sample_snapshot):
    """Context contains all required sections."""
    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert "EGTK -> LFPB -> LSGS" in context
    assert "2026-02-17" in context
    assert "D-7" in context
    assert "8000ft" in context
    assert "QUANTITATIVE DATA" in context
    assert "MODEL COMPARISON" in context


def test_build_context_with_text_forecasts(sample_snapshot):
    """Text forecasts section included when provided."""
    target_time = datetime(2026, 2, 17, 9, 0, 0)
    text_fcsts = DWDTextForecasts(
        short_range="Kurzfrist: Hochdruckeinfluss.",
        medium_range="Mittelfrist: Umstellung auf Westwetterlage.",
        fetched_at=datetime(2026, 2, 10, 12, 0, 0, tzinfo=timezone.utc),
    )

    context = build_digest_context(sample_snapshot, target_time, text_forecasts=text_fcsts)

    assert "TEXT FORECASTS" in context
    assert "Kurzfrist" in context
    assert "Mittelfrist" in context


def test_build_context_without_text_forecasts(sample_snapshot):
    """No text forecasts section when not provided."""
    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert "TEXT FORECASTS" not in context


def test_build_context_with_previous_digest(sample_snapshot):
    """Trend section included when previous digest provided."""
    from weatherbrief.digest.llm_digest import WeatherDigest

    target_time = datetime(2026, 2, 17, 9, 0, 0)
    prev = WeatherDigest(
        assessment="AMBER",
        assessment_reason="Frontal passage timing uncertain",
        synoptic="Low pressure approaching from the west.",
        winds="20kt headwind at FL080.",
        cloud_visibility="BKN at 3000ft.",
        precipitation_convection="Light rain likely.",
        icing="Moderate risk at 5000ft.",
        specific_concerns="Foehn possible in valleys.",
        model_agreement="GFS and ECMWF diverge on timing.",
        trend="Deteriorating from yesterday.",
        watch_items="Check updated TAFs tomorrow.",
    )

    context = build_digest_context(sample_snapshot, target_time, previous_digest=prev)

    assert "PREVIOUS DIGEST" in context
    assert "AMBER" in context
    assert "Frontal passage timing uncertain" in context


def test_build_context_quantitative_detail(sample_snapshot):
    """Quantitative data includes surface, wx, cruise-level, wind components, icing."""
    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    # Surface data
    assert "T=5.0C" in context
    assert "Wind 270/12kt" in context

    # Weather data
    assert "Cloud=60%" in context
    assert "Precip=0.5mm" in context
    assert "CAPE=50J/kg" in context

    # Wind components
    assert "15kt headwind" in context

    # Icing
    assert "moderate" in context.lower()
    assert "5000" in context

    # Cloud layers
    assert "3000" in context
    assert "6000" in context


def test_build_context_model_comparison(sample_snapshot):
    """Model comparison section includes divergence data."""
    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert "temperature_c" in context
    assert "good agreement" in context
    assert "spread=1.0" in context
