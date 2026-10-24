"""Android ↔ server API contract (#885).

The Android client (`app/android`) was written against a guessed API, and the server
ignores unknown JSON keys, so a wrong verb, path or field name fails silently: a PUT
that 405s, a `flexibility_mode` the server drops, a bulk delete that 422s. There is no
Android SDK in the cloud and CI only compiles Kotlin, so this test reads the Kotlin source
and checks it against the live FastAPI app:

1. every call in `ApiClient.kt` names a (method, path) the server routes;
2. every key of the request DTOs listed in ``REQUEST_DTOS`` is a field of its server model;
3. every key `FlightResponse` decodes is one the server sends.

``KNOWN_BROKEN_*`` list the drift found when this test was written that is outside the
flight-edit fix. They are tolerated so CI stays green, and the test fails when one is
fixed and not removed, so the lists only ever shrink. Add nothing to them: fix the client.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from flyfun_common import autorouter as autorouter_api

from weatherbrief.api import devices as devices_api
from weatherbrief.api import feedback as feedback_api
from weatherbrief.api import flights as flights_api
from weatherbrief.api import trips as trips_api
from weatherbrief.api.app import create_app

ANDROID = Path(__file__).resolve().parents[1] / "app/android/app/src/main/kotlin/aero/flyfun/weather"
API_CLIENT = ANDROID / "data/ApiClient.kt"
MODEL_DIR = ANDROID / "model"

# ApiClient helper → HTTP method.
VERBS = {
    "get": "GET",
    "post": "POST",
    "postNoBody": "POST",
    "postEmpty": "POST",
    "postIgnoreResponse": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "deleteWithBody": "DELETE",
}

# Calls whose path or verb the server does not route. #889 fixed the rest and #907 the observed
# cells/flashes calls; flexible departure needs design work rather than a path fix.
# Not catchable here: `GET /api/observed/{layer}/frames` matches the literal
# `/api/observed/cells/frames`, but the server's radar/satellite frames are
# `/api/observed/frames/{source}`.
KNOWN_BROKEN_CALLS = {
    # Flexible departure: per flight on Android, per pack on the server (a port of the iOS flow).
    ("GET", "/api/flights/{}/time-options"),
    ("POST", "/api/flights/{}/time-options/confirm"),
    ("POST", "/api/flights/{}/time-options/scan"),
}

# Kotlin request class → server model. The keys Android encodes must all be server fields.
REQUEST_DTOS = {
    "CreateFlightRequest": flights_api.CreateFlightRequest,
    "UpdateFlightRequest": flights_api.UpdateFlightRequest,
    "MoveFlightRequest": flights_api.MoveFlightRequest,
    "BulkDeleteRequest": flights_api.BulkDeleteRequest,
    "InterpretRouteRequest": flights_api.InterpretRouteRequest,
    "ParseFplRequest": flights_api.ParseFplRequest,
    "RouteDistanceRequest": flights_api.RouteDistanceRequest,
    "AddTripLegsRequest": trips_api.AddLegsRequest,
    "CreateTripRequest": trips_api.CreateTripRequest,
    "UpdateTripRequest": trips_api.UpdateTripRequest,
    "DeviceRegistrationRequest": devices_api.DeviceRegistration,
    "FeedbackRequest": feedback_api.FeedbackRequest,
    "AutorouterLinkTicketRequest": autorouter_api.LinkTicketRequest,
    "AutorouterLinkCompleteRequest": autorouter_api.LinkCompleteRequest,
}

# Request keys the server ignores. Add nothing here: fix the client.
KNOWN_EXTRA_REQUEST_KEYS: set[tuple[str, str]] = set()

# `FlightResponse` keys the server never sends (they would decode to their defaults).
KNOWN_UNSENT_FLIGHT_KEYS: set[str] = set()


def _normalize(path: str) -> str:
    """`/api/flights/${encodePathSegment(id)}/move?x=1` → `/api/flights/{}/move`.

    A bare `$suffix` becomes `{*}`: it may hold several segments (`advisories/alt`).
    """
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    return re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "{*}", path)


def _android_calls() -> set[tuple[str, str]]:
    src = API_CLIENT.read_text()
    calls = set()
    helper = "|".join(VERBS)
    for m in re.finditer(rf'\b({helper})(?:<[^>]*>)?\(\s*"(/[^"]*)"', src):
        calls.add((VERBS[m.group(1)], _normalize(m.group(2))))
    for m in re.finditer(r'path\s*=\s*"(/[^"]*)",\s*method\s*=\s*"([A-Z]+)"', src):
        calls.add((m.group(2), _normalize(m.group(1))))
    return calls


@pytest.fixture(scope="module")
def server_routes() -> set[tuple[str, str]]:
    paths = create_app().openapi()["paths"]
    return {
        (method.upper(), re.sub(r"\{[^}]+\}", "X", path))
        for path, ops in paths.items()
        for method in ops
    }


def _routed(call: tuple[str, str], routes: set[tuple[str, str]]) -> bool:
    """A Kotlin `{}` stands for one path segment, a `{*}` for one or more."""
    method, path = call
    escaped = re.escape(path).replace(r"\{\*\}", ".+").replace(r"\{\}", "[^/]+")
    pattern = re.compile("^" + escaped + "$")
    return any(m == method and pattern.match(p) for m, p in routes)


def _kotlin_class_keys(name: str) -> set[str]:
    """The JSON keys a Kotlin `@Serializable data class` encodes or decodes."""
    for file in MODEL_DIR.glob("*.kt"):
        src = file.read_text()
        m = re.search(rf"\ndata class {name}\(\n(.*?)\n\)", src, re.S)
        if m is None:
            continue
        keys = set()
        serial_name = None
        for line in m.group(1).splitlines():
            line = line.strip()
            if sn := re.match(r'@SerialName\("([^"]+)"\)', line):
                serial_name = sn.group(1)
            elif prop := re.match(r"val (\w+)\s*:", line):
                keys.add(serial_name or prop.group(1))
                serial_name = None
        return keys
    raise AssertionError(f"data class {name} not found under {MODEL_DIR}")


def test_android_calls_are_routed(server_routes):
    calls = _android_calls()
    assert len(calls) > 50, "ApiClient.kt parse found too few calls; did the helpers change?"
    unrouted = {c for c in calls if not _routed(c, server_routes)}
    assert unrouted - KNOWN_BROKEN_CALLS == set(), "Android calls the server does not route"
    assert KNOWN_BROKEN_CALLS - unrouted == set(), (
        "These calls are fixed (or gone): remove them from KNOWN_BROKEN_CALLS"
    )


def test_flight_edit_calls_use_the_server_verbs(server_routes):
    """The #885 regressions, spelled out: a `PUT` here was a 405 for every edit."""
    calls = _android_calls()
    for call in [
        ("PATCH", "/api/flights/{}"),
        ("POST", "/api/flights/{}/move"),
        ("POST", "/api/flights/bulk-delete"),
        ("POST", "/api/flights/interpret-route"),
        ("POST", "/api/trips/{}/legs"),
        ("PATCH", "/api/trips/{}"),
    ]:
        assert call in calls, f"ApiClient.kt no longer calls {call}"
        assert _routed(call, server_routes), call
    assert ("PUT", "/api/flights/{}") not in calls


@pytest.mark.parametrize("kotlin_name", sorted(REQUEST_DTOS))
def test_request_keys_are_server_fields(kotlin_name):
    model = REQUEST_DTOS[kotlin_name]
    server_keys = {f.alias or name for name, f in model.model_fields.items()}
    extra = {
        key for key in _kotlin_class_keys(kotlin_name) - server_keys
        if (kotlin_name, key) not in KNOWN_EXTRA_REQUEST_KEYS
    }
    assert extra == set(), f"{kotlin_name} sends keys the server drops: {sorted(extra)}"


def test_known_extra_request_keys_are_still_extra():
    still = set()
    for kotlin_name, key in KNOWN_EXTRA_REQUEST_KEYS:
        model = REQUEST_DTOS[kotlin_name]
        if key in _kotlin_class_keys(kotlin_name) and key not in model.model_fields:
            still.add((kotlin_name, key))
    assert KNOWN_EXTRA_REQUEST_KEYS - still == set(), "Fixed: remove from KNOWN_EXTRA_REQUEST_KEYS"


def test_flight_response_keys_are_sent():
    server_keys = set(flights_api.UpdateFlightResponse.model_fields)
    android_keys = _kotlin_class_keys("FlightResponse")
    unsent = android_keys - server_keys
    assert unsent - KNOWN_UNSENT_FLIGHT_KEYS == set(), "FlightResponse decodes keys the server never sends"
    assert KNOWN_UNSENT_FLIGHT_KEYS - unsent == set(), "Fixed: remove from KNOWN_UNSENT_FLIGHT_KEYS"
    # The edit form is built from these; a rename would silently empty it again.
    assert {"raw_route", "flexibility", "alt_departure_time", "latest_briefing", "trip"} <= android_keys
