"""Tests for prompt builder (context assembly)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.digest.prompt_builder import build_digest_context
from weatherbrief.fetch.text_forecasts import (
    ForecastRegion,
    TextForecastEntry,
    TextForecasts,
)
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

    assert "EGTK" in context and "LFPB" in context and "LSGS" in context
    assert "Tuesday 2026-02-17" in context
    assert "D-7" in context
    assert "Tuesday 2026-02-10" in context  # fetch date
    assert "8000ft" in context
    assert "QUANTITATIVE DATA" in context


def test_build_context_with_text_forecasts(sample_snapshot):
    """Text forecasts section included when provided."""
    target_time = datetime(2026, 2, 17, 9, 0, 0)
    text_fcsts = TextForecasts(
        region=ForecastRegion.EUROPE,
        source_label="DWD Synoptic Overview",
        language_note="German — translate relevant content",
        entries=[
            TextForecastEntry(
                label="Kurzfrist (short-range)",
                text="Kurzfrist: Hochdruckeinfluss.",
            ),
            TextForecastEntry(
                label="Mittelfrist (medium-range)",
                text="Mittelfrist: Umstellung auf Westwetterlage.",
            ),
        ],
        fetched_at=datetime(2026, 2, 10, 12, 0, 0, tzinfo=timezone.utc),
    )

    context = build_digest_context(sample_snapshot, target_time, text_forecasts=text_fcsts)

    assert "TEXT FORECASTS" in context
    assert "DWD Synoptic Overview" in context
    assert "German" in context
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
        specific_concerns="Foehn possible in valleys.",
        trend="Deteriorating from yesterday.",
        watch_items="Check updated TAFs tomorrow.",
    )

    context = build_digest_context(sample_snapshot, target_time, previous_digest=prev)

    assert "PREVIOUS DIGEST" in context
    assert "Previous assessment: AMBER" in context
    assert "Previous reason: Frontal passage timing uncertain" in context
    assert "Previous synoptic: Low pressure approaching" in context
    assert "Previous trend: Deteriorating from yesterday." in context


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


def test_build_context_divergence_good_omitted(sample_snapshot):
    """Good-agreement divergence is omitted from context (noise reduction)."""
    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    # The fixture has only good agreement — should not appear
    assert "Divergence:" not in context


def test_build_context_divergence_poor_included(sample_snapshot):
    """Moderate/poor divergence appears inline in quantitative section."""
    from weatherbrief.models import AgreementLevel, ModelDivergence

    # Add a poor-agreement divergence to the analysis
    sample_snapshot.analyses[0].model_divergence.append(
        ModelDivergence(
            variable="cloud_cover_pct",
            model_values={"gfs": 100.0, "ecmwf": 30.0},
            mean=65.0,
            spread=70.0,
            agreement=AgreementLevel.POOR,
        )
    )

    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert "Divergence:" in context
    assert "cloud_cover_pct poor" in context
    assert "spread=70.0" in context


def test_build_context_nwp_cloud_absent_says_no_native_layer(sample_snapshot):
    """Far-out models with no native NWP cloud enrichment (nwp_cloud_layers is
    None) report 'no native NWP cloud layer' — NOT the bulk Open-Meteo low/mid/
    high summary mislabeled as 'NWP cloud'. That bulk triple is the GFS-native
    paradigm and mislabels ECMWF; emitting it as NWP cloud contradicted the app's
    empty cross-section NWP layer (#nwp-cloud-layer-ios-web)."""
    from weatherbrief.models import ThermodynamicIndices

    sample_snapshot.analyses[0].sounding["gfs"] = SoundingAnalysis(
        indices=ThermodynamicIndices(),
        # Bulk fields populated (as Open-Meteo now returns for ECMWF too) — these
        # must NOT be surfaced as "NWP cloud Low/Mid/High" any more.
        cloud_cover_low_pct=81.0,
        cloud_cover_mid_pct=61.0,
        cloud_cover_high_pct=100.0,
        nwp_cloud_layers=None,
    )

    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert "NWP cloud [gfs]: no native NWP cloud layer at this lead time" in context
    assert "NWP cloud [gfs]: Low=" not in context


def test_build_context_nwp_cloud_clear(sample_snapshot):
    """A native NWP source that reports no cloud ([]) says 'none (model clear)' —
    distinct from the None 'no native layer' case."""
    from weatherbrief.models import ThermodynamicIndices

    sample_snapshot.analyses[0].sounding["gfs"] = SoundingAnalysis(
        indices=ThermodynamicIndices(),
        nwp_cloud_layers=[],
    )

    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert "NWP cloud [gfs]: none (model clear)" in context


def test_build_context_nwp_cloud_native_decks(sample_snapshot):
    """Native NWP cloud decks are emitted as coverage/base/top/CC/T — the same
    signal the app cross-section's NWP cloud layer draws, not a bulk summary."""
    from weatherbrief.models import CloudCoverage, ThermodynamicIndices

    sample_snapshot.analyses[0].sounding["gfs"] = SoundingAnalysis(
        indices=ThermodynamicIndices(),
        nwp_cloud_layers=[
            EnhancedCloudLayer(
                base_ft=3000.0,
                top_ft=8000.0,
                coverage=CloudCoverage.BKN,
                mean_cloud_cover_pct=72.0,
                mean_temperature_c=4.0,
                source="nwp_3d",
            ),
        ],
    )

    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert "NWP cloud [gfs]: BKN 3000-8000ft CC=72% T=4C" in context


def test_build_context_convective_model_scheme_and_cross_check(sample_snapshot):
    """Divergent sounding emits the model-scheme line and a cross-check note
    (dd_not_corroborated: thermo MODERATE but model convective cover 0%)."""
    from weatherbrief.models import ConvectiveAssessment, ConvectiveRisk, ThermodynamicIndices

    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=1100.0, method="thermo"
    )
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cover_pct=0.0, method="nwp"
    )
    sample_snapshot.analyses[0].sounding["gfs"] = SoundingAnalysis(
        indices=ThermodynamicIndices(),
        convective=thermo,
        convective_thermo=thermo,
        convective_nwp=nwp,
    )

    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert "Convective model scheme [gfs]" in context
    assert "cover=0%" in context
    assert "cross-check:" in context
    assert "not corroborated" in context


def test_build_context_convective_cross_check_when_thermo_none(sample_snapshot):
    """The model-active / DD-quiet direction is emitted even when the thermo
    risk is NONE (the old risk!=NONE guard would have hidden it)."""
    from weatherbrief.models import ConvectiveAssessment, ConvectiveRisk, ThermodynamicIndices

    thermo = ConvectiveAssessment(risk_level=ConvectiveRisk.NONE, method="thermo")
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.NONE, cover_pct=40.0, top_ft=22000.0, method="nwp"
    )
    sample_snapshot.analyses[0].sounding["gfs"] = SoundingAnalysis(
        indices=ThermodynamicIndices(),
        convective=thermo,
        convective_thermo=thermo,
        convective_nwp=nwp,
    )

    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert "Convective model scheme [gfs]" in context
    assert "cover=40%" in context
    assert "cross-check:" in context
    assert "model convective scheme fired" in context
