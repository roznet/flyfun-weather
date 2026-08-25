"""Observed conditions where they surface: refresh, digest, prompt, PDF.

One deterministic string, four surfaces. The point of these tests is that the
web section, the text digest, the LLM prompt and the PDF all quote the SAME
sentence the server computed — a client or a template that re-worded it would
give a pilot three different accounts of one observation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

from weatherbrief.models.analysis import RouteConfig, Waypoint
from weatherbrief.models.observations import RouteObservations
from weatherbrief.models.observed import (
    ObservedAttribution,
    ObservedConditions,
    ObservedField,
    ObservedFieldMeta,
    ObservedSourceStatus,
    ObservedStationRef,
    ObservedTopsField,
)

ROUTE = RouteConfig(
    name="LFAT-LFAC",
    waypoints=[
        Waypoint(icao="LFAT", name="Le Touquet", lat=50.517, lon=1.627),
        Waypoint(icao="LFAC", name="Calais", lat=50.962, lon=1.955),
    ],
)


def _field(source: str, age: float, window: float, text: str, cls=ObservedField):
    return cls(
        source=source,
        quantity=source,
        units="dBZ",
        valid_time=datetime(2026, 8, 25, 14, 5, tzinfo=timezone.utc),
        age_minutes=age,
        window_minutes=window,
        attribution=ObservedAttribution(producer="Météo-France", text=text),
    )


def _conditions() -> ObservedConditions:
    return ObservedConditions(
        computed_at=datetime(2026, 8, 25, 14, 10, tzinfo=timezone.utc),
        corridor_nm=20.0,
        radii_nm=[5.0, 10.0, 20.0],
        stations=[ObservedStationRef(id="P000", name="LFAT", lat=50.5, lon=1.6)],
        reflectivity=_field("opera_dbzh", 12.0, 10.0, "EUMETNET OPERA · Météo-France"),
        rain_rate=_field("opera_rate", 14.0, 15.0, "EUMETNET OPERA · Météo-France"),
        cloud_tops=_field("eumetsat_ctth", 17.0, 0.0, "EUMETSAT · MTG CTTH", cls=ObservedTopsField),
        summary="Radar: peak 45 dBZ within 20 NM of LFAT (observed 12 min ago).",
        summary_lines=["Radar: peak 45 dBZ within 20 NM of LFAT (observed 12 min ago)."],
        sources=[
            ObservedSourceStatus(source="opera_dbzh", available=True),
            ObservedSourceStatus(
                source="eumetsat_li", available=False, reason="no frames collected"
            ),
        ],
    )


# --- Snapshot / refresh ----------------------------------------------------


def test_observed_conditions_ride_inline_on_the_briefing(tmp_path):
    """Beside route_observations, not in a sidecar artifact."""
    from weatherbrief.models.analysis import ForecastSnapshot
    from weatherbrief.storage.snapshots import save_snapshot

    snapshot = ForecastSnapshot(
        route=ROUTE,
        target_date="2026-08-25",
        fetch_date="2026-08-25",
        days_out=0,
        observed_conditions=_conditions(),
    )
    briefing_path = save_snapshot(snapshot, data_dir=tmp_path)
    briefing = json.loads(briefing_path.read_text())
    assert briefing["observed_conditions"]["corridor_nm"] == 20.0
    assert briefing["observed_conditions"]["summary_lines"]
    # Imagery is served from /api/observed, never embedded.
    assert "png" not in briefing_path.read_text().lower()


def test_realtime_refresh_resamples_observed_conditions(tmp_path):
    """The ↻ button updates the observed panel — no provider fetch involved."""
    from weatherbrief.tasks.route_weather import run_realtime_refresh

    (tmp_path / "briefing.json").write_text(json.dumps({
        "route": ROUTE.model_dump(mode="json"),
        "departure_time": "2026-08-25T14:00:00+00:00",
        "days_out": 0,
    }))
    (tmp_path / "forecasts.json").write_text(json.dumps({"forecasts": []}))

    fresh_obs = RouteObservations(
        corridor_nm=30.0,
        fetch_time=datetime(2026, 8, 25, 14, 10, tzinfo=timezone.utc),
        airports_found=0, airports_with_metar=0, airports_with_taf=0,
    )
    with patch(
        "weatherbrief.tasks.route_weather.run_route_weather", return_value=fresh_obs,
    ), patch(
        "weatherbrief.tasks.route_weather.run_route_sigmets", return_value=None,
    ), patch(
        "weatherbrief.observed.collect.observed_enabled", return_value=True,
    ), patch(
        "weatherbrief.observed.payload.build_observed_conditions",
        return_value=_conditions(),
    ) as mock_build:
        result = run_realtime_refresh(tmp_path, "/fake/db")

    mock_build.assert_called_once()
    assert result.observed is not None
    patched = json.loads((tmp_path / "briefing.json").read_text())
    assert patched["observed_conditions"]["corridor_nm"] == 20.0


def test_realtime_refresh_skips_observed_when_not_enabled(tmp_path):
    from weatherbrief.tasks.route_weather import run_realtime_refresh

    (tmp_path / "briefing.json").write_text(json.dumps({
        "route": ROUTE.model_dump(mode="json"),
        "departure_time": "2026-08-25T14:00:00+00:00",
        "days_out": 0,
    }))
    (tmp_path / "forecasts.json").write_text(json.dumps({"forecasts": []}))

    fresh_obs = RouteObservations(
        corridor_nm=30.0,
        fetch_time=datetime(2026, 8, 25, 14, 10, tzinfo=timezone.utc),
        airports_found=0, airports_with_metar=0, airports_with_taf=0,
    )
    with patch(
        "weatherbrief.tasks.route_weather.run_route_weather", return_value=fresh_obs,
    ), patch(
        "weatherbrief.tasks.route_weather.run_route_sigmets", return_value=None,
    ), patch(
        "weatherbrief.observed.collect.observed_enabled", return_value=False,
    ):
        result = run_realtime_refresh(tmp_path, "/fake/db")

    assert result.observed is None
    patched = json.loads((tmp_path / "briefing.json").read_text())
    assert "observed_conditions" not in patched


# --- Text digest -----------------------------------------------------------


def test_text_digest_quotes_the_summary_verbatim():
    from weatherbrief.digest.text import _format_observed_conditions

    lines = _format_observed_conditions(_conditions())
    assert "OBSERVED NOW" in lines
    assert _conditions().summary_lines[0] in lines
    # An absent source is named, not omitted: "no lightning collected" and
    # "no lightning detected" are different facts.
    assert any("Not collected: eumetsat_li" in line for line in lines)
    # Attribution is de-duplicated, not repeated once per field.
    attribution_lines = [line for line in lines if "EUMETNET OPERA" in line]
    assert len(attribution_lines) == 1


# --- LLM prompt ------------------------------------------------------------


def test_prompt_carries_each_source_age():
    from weatherbrief.digest.prompt_builder import _format_observed_context

    context = _format_observed_context(_conditions())
    assert "OBSERVED CONDITIONS ALONG ROUTE (measured, not forecast)" in context
    assert _conditions().summary_lines[0] in context
    # Without per-source ages the model would read four measurements as one
    # instant; a radar composite alone can be ~15 min behind.
    assert "opera_dbzh 12 min old" in context
    assert "eumetsat_ctth 17 min old" in context
    assert "Not collected: eumetsat_li" in context


def test_prompt_does_not_ask_the_model_to_grade_or_compare():
    """Phase 1 computes no verdict, and must not smuggle one via the prompt."""
    from weatherbrief.digest.prompt_builder import _format_observed_context

    context = _format_observed_context(_conditions()).lower()
    for word in ("compare", "verdict", "confirm", "contradict", "match"):
        assert word not in context, f"prompt invites a phase-2 comparison: {word!r}"


# --- PDF -------------------------------------------------------------------


def test_report_deduplicates_attribution_lines():
    from weatherbrief.report.render import _observed_attributions

    briefing = _conditions().model_dump(mode="json")
    lines = _observed_attributions(briefing)
    # The two OPERA products share a producer and licence; repeating the line
    # four times in a footer helps nobody.
    assert lines == ["EUMETNET OPERA · Météo-France", "EUMETSAT · MTG CTTH"]


def test_report_attribution_is_empty_without_observed_data():
    from weatherbrief.report.render import _observed_attributions

    assert _observed_attributions(None) == []
    assert _observed_attributions({}) == []


def test_pdf_template_renders_the_summary_and_attribution():
    from jinja2 import Environment, PackageLoader

    env = Environment(loader=PackageLoader("weatherbrief.report", "templates"), autoescape=True)
    source = env.loader.get_source(env, "briefing.html")[0]
    assert "observed_conditions" in source
    assert "observed_attributions" in source
    # The PDF says what a frame's age means, not just how old it is.
    assert "own observation time" in source


# --- Model shape -----------------------------------------------------------


def test_field_meta_has_no_shared_timestamp():
    """There is deliberately no payload-level "as of"."""
    fields = set(ObservedConditions.model_fields)
    assert "observed_at" not in fields
    assert "as_of" not in fields
    assert "valid_time" not in fields
    # It lives on each field instead.
    assert "valid_time" in ObservedFieldMeta.model_fields
    assert "age_minutes" in ObservedFieldMeta.model_fields


def test_conditions_reports_when_it_has_nothing():
    empty = ObservedConditions(
        computed_at=datetime(2026, 8, 25, 14, 10, tzinfo=timezone.utc),
        corridor_nm=20.0,
    )
    assert empty.has_any_field is False
    assert _conditions().has_any_field is True
