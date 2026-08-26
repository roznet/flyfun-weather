"""End-to-end payload assembly: store → readers → sampler → ObservedConditions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.models.analysis import RouteConfig, Waypoint
from weatherbrief.observed.frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    FrameStore,
)
from weatherbrief.observed.payload import build_observed_conditions

# A short leg through the fixture scene: Le Touquet to Calais, both inside the
# synthetic OPERA domain and under the synthetic CTTH granule.
ROUTE = RouteConfig(
    name="LFAT-LFAC",
    waypoints=[
        Waypoint(icao="LFAT", name="Le Touquet", lat=50.517, lon=1.627),
        Waypoint(icao="LFAC", name="Calais", lat=50.962, lon=1.955),
    ],
    cruise_altitude_ft=6000,
)

NOW = datetime(2026, 8, 25, 14, 10, tzinfo=timezone.utc)
DBZH_TIME = datetime(2026, 8, 25, 14, 5, tzinfo=timezone.utc)
SAT_TIME = datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def stocked_store(tmp_path, dbzh_path, rate_path, ctth_path, li_path) -> FrameStore:
    store = FrameStore(tmp_path / "observed")
    store.write(SOURCE_OPERA_DBZH, DBZH_TIME, dbzh_path.read_bytes(), {})
    store.write(SOURCE_OPERA_RATE, DBZH_TIME, rate_path.read_bytes(), {})
    store.write(SOURCE_EUMETSAT_CTTH, SAT_TIME, ctth_path.read_bytes(), {})
    store.write(SOURCE_EUMETSAT_LI, SAT_TIME, li_path.read_bytes(), {})
    return store


def test_payload_covers_every_source(stocked_store):
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    assert conditions.has_any_field
    assert conditions.reflectivity is not None
    assert conditions.rain_rate is not None
    assert conditions.cloud_tops is not None
    assert conditions.lightning is not None
    assert all(s.available for s in conditions.sources)


def test_each_field_carries_its_own_valid_time(stocked_store):
    """No synthetic common timestamp: the sources do not share an instant."""
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    assert conditions.reflectivity.valid_time == DBZH_TIME
    assert conditions.cloud_tops.valid_time == SAT_TIME
    assert conditions.reflectivity.age_minutes == pytest.approx(5.0)
    assert conditions.cloud_tops.age_minutes == pytest.approx(10.0)


def test_rolling_window_is_reported_alongside_the_valid_time(stocked_store):
    """A 10-minute rolling maximum is not an instantaneous observation."""
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    assert conditions.reflectivity.window_minutes == pytest.approx(10.0)


def test_all_three_radii_ship_together(stocked_store):
    """The corridor selector must be a client-side pick, not a re-fetch."""
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    for station in conditions.reflectivity.stations:
        assert [a.radius_nm for a in station.annuli] == [5.0, 10.0, 20.0]
    assert conditions.radii_nm == [5.0, 10.0, 20.0]
    assert conditions.corridor_nm == 20.0


def test_stations_are_shared_and_keyed_consistently(stocked_store):
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    station_ids = {s.id for s in conditions.stations}
    assert station_ids
    for field in (conditions.reflectivity, conditions.cloud_tops, conditions.lightning):
        assert {s.station_id for s in field.stations} == station_ids
    # Stations sit on the route's own cross-section X axis.
    distances = [s.enroute_distance_nm for s in conditions.stations]
    assert distances == sorted(distances)
    assert conditions.stations[0].name == "LFAT"


def test_attribution_reaches_the_payload(stocked_store):
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    assert "MeteoFrance" in conditions.reflectivity.attribution.producer
    assert conditions.reflectivity.attribution.text
    assert conditions.cloud_tops.attribution.producer == "EUMETSAT"


def test_a_stale_frame_is_not_presented_as_current(tmp_path, dbzh_path):
    store = FrameStore(tmp_path / "observed")
    store.write(
        SOURCE_OPERA_DBZH, NOW - timedelta(hours=2), dbzh_path.read_bytes(), {}
    )
    conditions = build_observed_conditions(
        ROUTE, store=store, now=NOW, sources=(SOURCE_OPERA_DBZH,)
    )
    assert conditions.reflectivity is None
    status = conditions.sources[0]
    assert status.available is False
    assert "old" in status.reason


def test_missing_source_is_reported_not_silently_dropped(tmp_path):
    store = FrameStore(tmp_path / "observed")
    conditions = build_observed_conditions(ROUTE, store=store, now=NOW)
    assert not conditions.has_any_field
    assert {s.source for s in conditions.sources} == {
        SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE, SOURCE_EUMETSAT_CTTH, SOURCE_EUMETSAT_LI,
    }
    assert all(s.reason == "no frames collected" for s in conditions.sources)
    assert conditions.summary == "No observed data available along the route."


def test_a_broken_frame_does_not_take_the_others_with_it(stocked_store):
    """Half the observed picture beats none of it."""
    corrupt = stocked_store.payload_path(SOURCE_EUMETSAT_CTTH, SAT_TIME)
    corrupt.write_bytes(b"truncated")
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    assert conditions.cloud_tops is None
    assert conditions.reflectivity is not None
    broken = next(s for s in conditions.sources if s.source == SOURCE_EUMETSAT_CTTH)
    assert broken.available is False
    assert "unreadable" in broken.reason


def test_payload_serialises_to_json(stocked_store):
    """It rides inline on briefing.json, so it must round-trip."""
    from weatherbrief.models.observed import ObservedConditions

    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    dumped = conditions.model_dump(mode="json")
    restored = ObservedConditions.model_validate(dumped)
    assert restored.reflectivity.valid_time == conditions.reflectivity.valid_time
    assert restored.summary == conditions.summary
    # Imagery is served, never embedded.
    assert "png" not in str(dumped).lower()


def test_no_network_during_payload_assembly(stocked_store, monkeypatch):
    """The acceptance criterion, asserted rather than assumed.

    Asserting on the RESULT, not merely that the call returns.  Every source's
    sampling is wrapped in ``except Exception`` so one malformed frame cannot
    take the other three down — which also swallows the ``AssertionError`` this
    test injects.  Calling the function and checking nothing therefore passed
    happily while a socket was being opened.  A network call now shows up as a
    source that went unavailable, and that is what fails the test.
    """
    import socket

    def _forbidden(*args, **kwargs):  # pragma: no cover - only on failure
        raise AssertionError("payload assembly opened a socket")

    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)

    unavailable = [s for s in conditions.sources if not s.available]
    assert not unavailable, (
        "a source went unavailable during assembly — most likely a network "
        f"call swallowed by the per-source guard: {unavailable}"
    )
    assert conditions.reflectivity is not None
    assert conditions.cloud_tops is not None
    assert conditions.lightning is not None


def test_no_network_test_can_actually_fail(stocked_store, monkeypatch):
    """Guard the guard: prove the assertion above detects a real regression.

    A test that cannot fail is worse than no test, because it gets cited as
    evidence.  This injects the regression the previous test exists to catch
    and confirms it is caught.
    """
    import socket

    from weatherbrief.observed import payload as payload_module

    real_grid_field = payload_module._grid_field

    def _leaky(*args, **kwargs):
        socket.socket().connect(("example.invalid", 80))
        return real_grid_field(*args, **kwargs)

    def _forbidden(*args, **kwargs):
        raise AssertionError("payload assembly opened a socket")

    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    monkeypatch.setattr(payload_module, "_grid_field", _leaky)

    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    assert [s for s in conditions.sources if not s.available], (
        "the injected socket call was not visible in the result — the "
        "no-network test would pass through a real regression"
    )


# --- Summary ---------------------------------------------------------------


def test_summary_entries_tag_each_clause_with_its_source(stocked_store):
    """The client renders these as per-source rows and must not parse the prose.

    The clauses are deliberately not uniformly shaped — "Radar: peak 38 dBZ…"
    against "Rain rate to 1.8 mm/h…" — so recovering the source from the text
    would mean guessing, and pairing a clause with the wrong source's frame age
    is exactly the cross-source age blending the design forbids.
    """
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    entries = conditions.summary_entries
    assert entries, "expected a structured readout"

    by_kind = {e.kind: e for e in entries}
    # Every clause names a kind the client knows how to pair with a field.
    assert set(by_kind) <= {
        "lightning", "reflectivity", "rain_rate", "cloud_tops", "coverage", "unavailable",
    }
    # …and points at a metric-catalog card, so the row can carry the (i) popup.
    for entry in entries:
        if entry.kind != "unavailable":
            assert entry.metric_id in ("observed_surface", "observed_tops"), entry
    if "cloud_tops" in by_kind:
        assert by_kind["cloud_tops"].metric_id == "observed_tops"
    if "reflectivity" in by_kind:
        assert by_kind["reflectivity"].metric_id == "observed_surface"


def test_summary_lines_stay_derived_from_the_entries(stocked_store):
    """The PDF and the digest read the plain strings; they must not drift."""
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    assert conditions.summary_lines == [e.text for e in conditions.summary_entries]
    assert conditions.summary == " ".join(conditions.summary_lines)


def test_summary_names_the_echo_and_its_age(stocked_store):
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    radar = next(line for line in conditions.summary_lines if line.startswith("Radar: peak"))
    assert "dBZ" in radar
    assert "observed 5 min ago" in radar


def test_summary_reports_missing_coverage_distinctly_from_no_echo(stocked_store):
    """"We cannot see there" and "it is clear there" are different sentences.

    The westbound leg runs into the fixture's deliberate no-radar-coverage
    half, which is where the distinction has teeth.
    """
    westbound = RouteConfig(
        name="LFAT-BLIND",
        waypoints=[
            Waypoint(icao="LFAT", name="Le Touquet", lat=50.517, lon=1.627),
            Waypoint(icao="BLIND", name="Offshore", lat=50.517, lon=0.55),
        ],
    )
    conditions = build_observed_conditions(
        westbound, store=stocked_store, now=NOW, sources=(SOURCE_OPERA_DBZH,)
    )
    coverage = [line for line in conditions.summary_lines if "no coverage" in line]
    assert coverage, conditions.summary_lines
    assert not any("no echo" in line for line in coverage)


def test_summary_reports_cloud_tops_with_the_multilayer_flag(stocked_store):
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    tops = next(line for line in conditions.summary_lines if line.startswith("Cloud tops"))
    assert "FL" in tops


def test_summary_is_deterministic(stocked_store):
    """The digest quotes it as fact; it must not vary run to run."""
    first = build_observed_conditions(ROUTE, store=stocked_store, now=NOW).summary
    second = build_observed_conditions(ROUTE, store=stocked_store, now=NOW).summary
    assert first == second


def test_summary_grades_nothing(stocked_store):
    """Phase 1 displays observations; it computes no verdict."""
    conditions = build_observed_conditions(ROUTE, store=stocked_store, now=NOW)
    forbidden = ("severe", "hazard", "significant", "amber", "red", "warning")
    lowered = conditions.summary.lower()
    assert not any(word in lowered for word in forbidden), conditions.summary


def test_lightning_absence_is_stated_as_an_observation(tmp_path, li_path):
    store = FrameStore(tmp_path / "observed")
    store.write(SOURCE_EUMETSAT_LI, SAT_TIME, li_path.read_bytes(), {})
    quiet = RouteConfig(
        name="EGLL-EGKK",
        waypoints=[
            Waypoint(icao="EGLL", name="Heathrow", lat=51.477, lon=-0.461),
            Waypoint(icao="EGKK", name="Gatwick", lat=51.148, lon=-0.190),
        ],
    )
    conditions = build_observed_conditions(
        quiet, store=store, now=NOW, sources=(SOURCE_EUMETSAT_LI,)
    )
    assert "Lightning: none within 20 NM" in conditions.summary


def test_no_echo_is_never_asserted_from_mostly_blind_data(stocked_store):
    """"No echo along the route" needs the coverage to back it.

    The westbound leg runs into the fixture's no-radar half, so most of it is
    unseen. Claiming the whole route is echo-free off the handful of covered
    points would be the clear-versus-unknown conflation the payload exists to
    prevent; the claim is scoped to what the radar can actually see.
    """
    westbound = RouteConfig(
        name="LFAT-BLIND",
        waypoints=[
            Waypoint(icao="LFAT", name="Le Touquet", lat=50.517, lon=1.627),
            Waypoint(icao="BLIND", name="Offshore", lat=50.517, lon=0.55),
        ],
    )
    conditions = build_observed_conditions(
        westbound, store=stocked_store, now=NOW, sources=(SOURCE_OPERA_DBZH,)
    )
    unscoped = [
        line for line in conditions.summary_lines
        if "no echo" in line and "along the route" in line
    ]
    assert not unscoped, conditions.summary_lines


def test_a_detection_is_reported_even_from_a_poorly_covered_disc(stocked_store):
    """A detection is positive evidence and must not be suppressed.

    Coverage bounds what an ABSENCE can claim; it does not make a real echo
    less real. Hiding a measured cell because the surrounding disc is patchy
    would be strictly more dangerous than reporting it with a caveat.
    """
    conditions = build_observed_conditions(
        ROUTE, store=stocked_store, now=NOW, sources=(SOURCE_OPERA_DBZH,)
    )
    peak = [line for line in conditions.summary_lines if "peak" in line]
    assert peak, conditions.summary_lines
    assert "dBZ" in peak[0]
