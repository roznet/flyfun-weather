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


def test_build_context_names_airports_and_tags_navaids(sample_snapshot):
    """The ROUTE line and waypoint headers carry each point's real identity.

    The LLM is told to convert coordinates to place names; if the context gives
    it only a bare code it fills the gap from memory (it once called EGNY
    "Sandtoft", a different airfield 30 nm away). Airports therefore ship their
    full name, and navaids/fixes ship a chart tag so an en-route point is never
    narrated as a destination."""
    route = sample_snapshot.route
    route.waypoints = [
        Waypoint(icao="EGTF", name="Fairoaks Airport", lat=51.3481, lon=-0.5589),
        Waypoint(icao="GWC", name="GWC", lat=50.8553, lon=-0.7567, kind="VORDME"),
        Waypoint(icao="SITET", name="SITET", lat=50.1, lon=0.0, kind="5LNC"),
        Waypoint(icao="LFMD", name="Cannes Mandelieu Airport", lat=43.542, lon=6.953),
    ]

    context = build_digest_context(sample_snapshot, datetime(2026, 2, 17, 9, 0, 0))
    route_line = context.splitlines()[0]

    assert route_line == (
        "ROUTE: EGTF Fairoaks Airport (51.3N, 0.6W) -> GWC [VOR/DME] (50.9N, 0.8W) "
        "-> SITET [fix] (50.1N, 0.0E) -> LFMD Cannes Mandelieu Airport (43.5N, 7.0E)"
    )
    assert "--- EGTF (Fairoaks Airport) [51.3N, 0.6W] ---" in context
    assert "--- GWC [VOR/DME] [50.9N, 0.8W] ---" in context
    # Never the old redundant "GWC (GWC)" shape, which read as a name.
    assert "GWC (GWC)" not in context


def test_build_context_unnamed_waypoint_shows_bare_code(sample_snapshot):
    """A waypoint we know nothing about renders as the bare code — no
    parenthetical echo of the code that could be mistaken for a name."""
    route = sample_snapshot.route
    route.waypoints = [
        Waypoint(icao="EGTK", name="EGTK", lat=51.8361, lon=-1.32),
        Waypoint(icao="LSGS", name="Sion", lat=46.2192, lon=7.3267),
    ]

    context = build_digest_context(sample_snapshot, datetime(2026, 2, 17, 9, 0, 0))

    assert context.splitlines()[0] == (
        "ROUTE: EGTK (51.8N, 1.3W) -> LSGS Sion (46.2N, 7.3E)"
    )
    assert "--- EGTK [51.8N, 1.3W] ---" in context
    assert "EGTK (EGTK)" not in context


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
    assert "Wind 270@12kt" in context

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


def test_build_context_nwp_cloud_absent_falls_back_to_bulk(sample_snapshot):
    """No native NWP cloud envelope (nwp_cloud_layers is None) but bulk
    cloud_cover present → fall back to the bulk Open-Meteo summary under a
    disambiguated label, NOT mislabeled as 'NWP cloud Low/Mid/High' (the
    GFS-native paradigm that misrepresented ECMWF, #nwp-cloud-layer-ios-web).
    This covers both far-out ECMWF and Open-Meteo-only models (UKMO/MF/GEM)
    whose bulk fields are always populated — no data is dropped."""
    from weatherbrief.models import ThermodynamicIndices

    sample_snapshot.analyses[0].sounding["gfs"] = SoundingAnalysis(
        indices=ThermodynamicIndices(),
        cloud_cover_low_pct=81.0,
        cloud_cover_mid_pct=61.0,
        cloud_cover_high_pct=100.0,
        nwp_cloud_layers=None,
    )

    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert (
        "Bulk cloud [gfs] (Open-Meteo summary, no native NWP layer): "
        "Low=81%, Mid=61%, High=100%" in context
    )
    # The bulk triple must NOT be mislabeled as native NWP cloud…
    assert "NWP cloud [gfs]: Low=" not in context
    # …and the message must not imply a temporal gap (wrong for always-None models).
    assert "at this lead time" not in context


def test_build_context_nwp_cloud_absent_no_bulk(sample_snapshot):
    """No native NWP cloud layer AND no bulk summary → the explicit
    'no native NWP cloud layer' line (no temporal framing)."""
    from weatherbrief.models import ThermodynamicIndices

    sample_snapshot.analyses[0].sounding["gfs"] = SoundingAnalysis(
        indices=ThermodynamicIndices(),
        nwp_cloud_layers=None,
    )

    target_time = datetime(2026, 2, 17, 9, 0, 0)
    context = build_digest_context(sample_snapshot, target_time)

    assert "NWP cloud [gfs]: no native NWP cloud layer" in context
    assert "at this lead time" not in context


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
    assert "Thermo Convective shows" in context


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
    assert "NWP Convective shows convection" in context


# --- SIGMET flight-window activity tagging ------------------------------------


def _sigmet(valid_from, valid_to):
    from weatherbrief.models import SigmetAlongRoute

    return SigmetAlongRoute(
        fir_id="LFFF",
        fir_name="PARIS FIR/UIR",
        hazard="TS",
        qualifier="EMBD",
        top_ft=37000,
        valid_from=valid_from,
        valid_to=valid_to,
        direction="ENE",
        speed_kt=25,
        enroute_distance_from_nm=200,
        enroute_distance_to_nm=220,
        raw_text="LFFF SIGMET T10 VALID 170605/170800 ... EMBD TS ... MOV ENE 25KT NC=",
    )


def _sigmet_context(sig_report, departure, arrival):
    from weatherbrief.digest.prompt_builder import _format_sigmets_context

    return _format_sigmets_context(sig_report, departure=departure, arrival=arrival)


def test_sigmet_expired_before_departure_tagged_inactive():
    """A SIGMET whose validity ends before departure is flagged INACTIVE with
    the explicit expiry/departure times — the real 0605Z-0800Z vs 1030Z case."""
    from datetime import timedelta

    from weatherbrief.models import RouteSigmets

    dep = datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc)
    arr = dep + timedelta(hours=1.75)
    s = _sigmet(
        datetime(2026, 7, 17, 6, 5, tzinfo=timezone.utc),
        datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc),
    )
    ctx = _sigmet_context(
        RouteSigmets(corridor_nm=50, fetch_time=dep, sigmets=[s]), dep, arr
    )

    assert "INACTIVE during flight — expired 0800Z, before 1030Z departure" in ctx
    assert "Flight window: 1030Z" in ctx
    assert "ACTIVE during flight window" not in ctx


def test_sigmet_overlapping_flight_tagged_active():
    from datetime import timedelta

    from weatherbrief.models import RouteSigmets

    dep = datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc)
    arr = dep + timedelta(hours=1.75)
    s = _sigmet(
        datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc),
    )
    ctx = _sigmet_context(
        RouteSigmets(corridor_nm=50, fetch_time=dep, sigmets=[s]), dep, arr
    )

    assert "ACTIVE during flight window (1000Z–1400Z)" in ctx
    # The instructional header mentions INACTIVE; assert no per-SIGMET tag is.
    assert "** INACTIVE" not in ctx


def test_sigmet_begins_after_arrival_tagged_inactive():
    from datetime import timedelta

    from weatherbrief.models import RouteSigmets

    dep = datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc)
    arr = dep + timedelta(hours=1.75)
    s = _sigmet(
        datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 17, 18, 0, tzinfo=timezone.utc),
    )
    ctx = _sigmet_context(
        RouteSigmets(corridor_nm=50, fetch_time=dep, sigmets=[s]), dep, arr
    )

    assert "INACTIVE during flight — begins 1500Z, after 1215Z arrival" in ctx


def test_sigmet_no_departure_omits_activity_tag():
    """Old packs without a departure_time get no window header or activity tag
    (we cannot compute the relationship) — behaviour is unchanged for them."""
    from weatherbrief.models import RouteSigmets

    s = _sigmet(
        datetime(2026, 7, 17, 6, 5, tzinfo=timezone.utc),
        datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc),
    )
    ctx = _sigmet_context(
        RouteSigmets(corridor_nm=50, fetch_time=datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc), sigmets=[s]),
        None,
        None,
    )

    assert "Flight window:" not in ctx
    assert "INACTIVE" not in ctx
    assert "ACTIVE during flight" not in ctx
