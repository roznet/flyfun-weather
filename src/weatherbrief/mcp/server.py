#!/usr/bin/env python3
"""WeatherBrief MCP Server.

Exposes aviation weather briefing capabilities to AI agents via the
Model Context Protocol. Designed for flight planning workflows where
an agent helps a pilot assess weather for upcoming flights.

Authentication: Bearer token in HTTP headers, forwarded to the
weatherbrief API on localhost. Users generate tokens at
weather.flyfun.aero → Settings → MCP Access.

Transport: stateless HTTP (streamable-http) for remote deployment
behind Caddy, or stdio for local development.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import time
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.auth import AccessToken, RemoteAuthProvider, TokenVerifier
from fastmcp.server.dependencies import get_http_request
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field

from weatherbrief.connectors.views import (
    CONVECTIVE_NOTE as _CONVECTIVE_NOTE,
    advisory_detail as _advisory_detail,
    assessment_note as _assessment_note,
    alternates_hook as _alternates_hook,
    briefing_freshness_status as _briefing_freshness_status,
    convective_detail as _convective_detail,
    summarize_advisories as _summarize_advisories,
    summarize_alternates as _summarize_alternates,
    summarize_altitude_table as _summarize_altitude_table,
)
from weatherbrief.mcp.client import API_BASE, WeatherbriefClient

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

WEATHER_BASE_URL = os.getenv("WEATHER_BASE_URL", "https://weather.flyfun.aero")
MCP_BASE_URL = os.getenv("MCP_BASE_URL", "https://mcp.flyfun.aero")


class _WeatherbriefTokenVerifier(TokenVerifier):
    """Validate Bearer tokens against the weatherbrief API at the edge.

    Every tool call still forwards the token to the API (which remains the
    authority for auth, suspension, and revocation), but verifying here too
    rejects garbage tokens before they burn API/DB work and ensures any
    future tool that does local work without an API round-trip is not
    silently unauthenticated. Results are cached briefly, keyed by token
    hash so plaintext tokens are never held in the cache.
    """

    _CACHE_TTL_SECONDS = 60.0
    _cache: dict[str, tuple[float, bool]] = {}

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or not token.strip():
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            valid = cached[1]
        else:
            valid = await self._check_with_api(token)
            if valid is None:
                # API unreachable — reject but don't cache, so a transient
                # outage doesn't lock the token out for the full TTL.
                return None
            for stale in [k for k, (exp, _) in self._cache.items() if exp <= now]:
                self._cache.pop(stale, None)
            self._cache[key] = (now + self._CACHE_TTL_SECONDS, valid)
        if not valid:
            return None
        return AccessToken(
            token=token,
            client_id="weatherbrief",
            scopes=["mcp"],
        )

    @staticmethod
    async def _check_with_api(token: str) -> bool | None:
        """True/False for a definitive answer, None if the API is unreachable."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{API_BASE}/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError:
            logger.warning("Token verification call to the weatherbrief API failed")
            return None
        return resp.status_code == 200


def _build_auth() -> RemoteAuthProvider | None:
    """Build OAuth auth provider for HTTP transport.

    Points clients to weather.flyfun.aero as the authorization server
    and serves RFC 9728 protected resource metadata on the MCP server.
    """
    return RemoteAuthProvider(
        token_verifier=_WeatherbriefTokenVerifier(),
        authorization_servers=[AnyHttpUrl(WEATHER_BASE_URL)],
        base_url=MCP_BASE_URL,
        resource_name="FlyFun Weather Briefings",
    )


mcp = FastMCP(
    name="weatherbrief",
    auth=_build_auth(),
    instructions=(
        "Aviation weather briefing tools for GA flight planning in Europe. "
        "Provides multi-model weather forecasts, route advisories, AI weather "
        "digests, and METAR/TAF observations for ~620 European airports.\n\n"
        "Typical workflow:\n"
        "1. list_flights — see the user's upcoming flights and briefing status\n"
        "2. create_flight — set up a new flight (auto-triggers briefing generation)\n"
        "3. get_briefing — retrieve weather assessment, advisories, and AI digest\n"
        "4. refresh_briefing — update stale briefings (checks model freshness first)\n"
        "5. get_airport_weather — quick forecast + METAR for specific airports\n\n"
        "Briefing generation takes ~2 minutes. If get_briefing returns "
        "status='processing', tell the user and check again shortly.\n\n"
        "When the user questions, doubts, or wants to understand an advisory "
        "(e.g. 'why is convective red when it looks like blue sky?'), call "
        "get_advisory_detail (and get_digest_context for the deepest context) "
        "before answering. The per-model split and cross_check note in "
        "get_briefing are only a hook — they are NOT the full picture: the "
        "per-point reconciliation data (e.g. CAPE vs the model's own cloud "
        "cover, the peak location and valid-time) lives ONLY in "
        "get_advisory_detail. So whenever an advisory carries cross_check_present "
        "or the user asks 'why', drill in first — do not answer from the "
        "get_briefing summary alone even though it looks sufficient. Treat "
        "cross-check notes and per-model splits as context for the discussion, "
        "not a reason to downgrade an advisory.\n\n"
        "Mitigations: when get_briefing flags aggregate_mitigations_present on an "
        "advisory (the same two-layer hook as cross_check), drill in with "
        "get_advisory_detail for the full mitigation objects. A mitigation is a "
        "decision that would improve a specific flagged sub-issue (e.g. fly a "
        "lower altitude, climb after N nm) — it is ADVICE ONLY and never changes "
        "the grade. Each carries mitigated_status: the status of the sub-issue it "
        "addresses if applied, NOT the advisory overall. Explain the trade-off "
        "('flying 6,000 ft would improve that sub-issue to GREEN — the advisory "
        "itself stays RED'); never use a mitigation to downgrade an advisory.\n\n"
        "Provenance note: the AI digest narrates 'convective tops' that are "
        "parcel-derived (the thermodynamic equilibrium level from CAPE), NOT the "
        "model's own convective cloud field. A convective advisory driven RED by "
        "high CAPE while the model's convective cover is ~0 ('blue sky') is "
        "consistent, not contradictory — explain it, don't argue it down.\n\n"
        "When the user asks about diversions or alternates (or get_briefing's "
        "'alternates' hook flags a candidate), call get_alternates for the full "
        "divert picture. These are WEATHER-improvement candidates, NOT "
        "operational alternates: operational suitability (hours, customs, fuel, "
        "NOTAMs, approach currency) is not evaluated — combine each candidate "
        "with airport/AIP data before recommending a divert.\n\n"
        "All tools return a web_url for the full interactive briefing with "
        "cross-sections, Skew-T diagrams, and route maps — present this link "
        "to the pilot for visual details."
    ),
)


def _extract_token() -> str:
    """Extract Bearer token from the HTTP request or environment."""
    # Try HTTP request headers first (remote/HTTP transport)
    try:
        request = get_http_request()
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
    except RuntimeError:
        pass  # No HTTP request (stdio transport)

    # Fallback: env var for stdio/testing
    token = os.getenv("WEATHERBRIEF_TOKEN")
    if token:
        return token

    raise ValueError(
        "No authentication token. Set Authorization: Bearer <token> header "
        "or WEATHERBRIEF_TOKEN environment variable."
    )


def _get_client() -> WeatherbriefClient:
    """Build a WeatherbriefClient using the token from the current request.

    Returns a context-manager — callers should use ``with _get_client() as client:``.
    """
    return WeatherbriefClient(_extract_token())


def _flight_web_url(flight_id: str) -> str:
    return f"{WEATHER_BASE_URL}/briefing.html?flight={flight_id}"


def _advisory_web_url(
    flight_id: str,
    advisory_id: str,
    *,
    point_index: int | None = None,
    model: str | None = None,
) -> str:
    """Build a deep link that opens the briefing's Skew-T focused on this
    advisory's lens (#308). We always carry the raw ``advisory`` id and let the
    web app resolve it → preset via its own ``getPresetForAdvisory`` mapping
    (the single source of truth — no server-side copy to drift): unknown ids
    just open the Skew-T with no lens. Adds ``point``/``model`` when a specific
    peak point is known (e.g. convective)."""
    params: list[tuple[str, str]] = [
        ("flight", flight_id),
        ("view", "skewt"),
        ("advisory", advisory_id),
    ]
    if point_index is not None:
        params.append(("point", str(point_index)))
    if model:
        params.append(("model", model))
    return f"{WEATHER_BASE_URL}/briefing.html?{urlencode(params)}"


def _resolve_pack(client: WeatherbriefClient, flight_id: str) -> tuple[str | None, dict | None]:
    """Resolve the latest pack timestamp for a flight, or a status dict.

    Returns ``(timestamp, None)`` when a pack exists, or ``(None, status)``
    when there is none / one is still generating — the status dict is shaped
    like the other tools' early returns so callers can return it directly.
    """
    try:
        refresh_info = client.get_refresh_status(flight_id)
        if refresh_info.get("active"):
            return None, {
                "status": "processing",
                "flight_id": flight_id,
                "message": "Briefing is being generated. Check again in ~1-2 minutes.",
                "web_url": _flight_web_url(flight_id),
            }
    except httpx.HTTPStatusError:
        pass

    pack = client.get_latest_pack(flight_id)
    if pack is None:
        return None, {
            "status": "none",
            "flight_id": flight_id,
            "message": "No briefing exists yet. Call refresh_briefing to generate one.",
            "web_url": _flight_web_url(flight_id),
        }
    return pack["fetch_timestamp"], None


def _error_result(message: str, code: int | None = None) -> dict:
    """Return a structured error that the agent can understand."""
    result: dict[str, Any] = {"error": message}
    if code:
        result["http_status"] = code
    return result


# ---------------------------------------------------------------------------
# Tool: list_flights
# ---------------------------------------------------------------------------

@mcp.tool(
    title="List Flights",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
def list_flights() -> dict[str, Any]:
    """List the user's upcoming flights with briefing status.

    Returns each flight's route, departure time, and weather assessment
    (GREEN/AMBER/RED/UNAVAILABLE) from the latest briefing. Use this to see
    which flights need attention or have stale briefings.

    UNAVAILABLE means we produced a briefing but no advisory could be graded (no usable model data) — it is NOT a clear sky and must never be narrated as reassuring; say we could not assess the flight.
    """
    try:
        with _get_client() as client:
            flights = client.list_flights()
    except httpx.HTTPStatusError as e:
        return _error_result(f"API error: {e.response.text}", e.response.status_code)
    except ValueError as e:
        return _error_result(str(e))

    result = []
    for f in flights:
        entry: dict[str, Any] = {
            "id": f["id"],
            "route_name": f["route_name"],
            "waypoints": f.get("waypoints", []),
            "departure_time": f["departure_time"],
            "cruise_altitude_ft": f["cruise_altitude_ft"],
            "web_url": _flight_web_url(f["id"]),
        }
        pack = f.get("latest_briefing")
        if pack:
            entry["assessment"] = pack.get("assessment")
            entry["assessment_reason"] = pack.get("assessment_reason")
            entry["has_digest"] = pack.get("has_digest", False)
            entry["briefing_timestamp"] = pack.get("fetch_timestamp")
            entry["days_out"] = pack.get("days_out")
        else:
            entry["assessment"] = None
            entry["briefing_timestamp"] = None
        result.append(entry)

    return {"flights": result}


# ---------------------------------------------------------------------------
# Tool: create_flight
# ---------------------------------------------------------------------------

@mcp.tool(
    title="Create Flight",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def create_flight(
    waypoints: Annotated[
        list[str],
        Field(description="Route waypoints as ICAO codes or navaid names, e.g. ['LFBO', 'LFML']. Minimum 2."),
    ],
    departure_time: Annotated[
        str,
        Field(description="Departure time as ISO 8601 with timezone, e.g. '2026-04-10T14:00:00+02:00'"),
    ],
    flight_duration_hours: Annotated[
        float,
        Field(description="Expected flight duration in hours, e.g. 1.5"),
    ],
    cruise_altitude_ft: Annotated[
        int | None,
        Field(description="Cruise altitude in feet (default: from user profile, typically 8000)"),
    ] = None,
) -> dict[str, Any]:
    """Create a new flight and automatically trigger a weather briefing.

    The briefing generation takes ~2 minutes. The response includes the
    flight details and a processing status. Call get_briefing after a
    couple of minutes to retrieve the results.
    """
    try:
        client = _get_client()
    except ValueError as e:
        return _error_result(str(e))

    with client:
        try:
            flight = client.create_flight(
                waypoints=waypoints,
                departure_time=departure_time,
                cruise_altitude_ft=cruise_altitude_ft,
                flight_duration_hours=flight_duration_hours,
            )
        except httpx.HTTPStatusError as e:
            return _error_result(f"Failed to create flight: {e.response.text}", e.response.status_code)

        flight_id = flight["id"]
        coverage = flight.get("coverage")

        if coverage:
            # Saved beyond the forecast horizon — no model reaches the date yet.
            # Don't trigger a briefing (it would be a no-op); report when weather
            # coverage begins so the agent knows when to call get_briefing.
            available = coverage.get("available_date")
            refresh_status: dict[str, Any] = {
                "status": "pending_coverage",
                "available_date": available,
                "full_briefing_date": coverage.get("full_briefing_date"),
                "message": (
                    f"Flight saved. No weather model reaches the departure date yet — "
                    f"forecast coverage begins {available}. The briefing generates "
                    f"automatically once the flight is within range; call get_briefing "
                    f"on or after {available}."
                ),
            }
        else:
            # Auto-trigger briefing refresh
            refresh_status = {"status": "not_triggered"}
            try:
                client.refresh_briefing(flight_id)
                refresh_status = {
                    "status": "processing",
                    "estimated_seconds": 120,
                    "message": "Briefing is being generated. Call get_briefing in ~2 minutes.",
                }
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 409:
                    refresh_status = {"status": "already_in_progress"}
                elif e.response.status_code == 429:
                    refresh_status = {"status": "rate_limited", "message": e.response.text}
                else:
                    refresh_status = {"status": "failed", "message": e.response.text}

    return {
        "flight": {
            "id": flight_id,
            "route_name": flight.get("route_name"),
            "waypoints": flight.get("waypoints", []),
            "departure_time": flight.get("departure_time"),
            "cruise_altitude_ft": flight.get("cruise_altitude_ft"),
            "flight_duration_hours": flight.get("flight_duration_hours"),
            "coverage": coverage,
            "web_url": _flight_web_url(flight_id),
        },
        "briefing": refresh_status,
    }


# ---------------------------------------------------------------------------
# Tool: get_briefing
# ---------------------------------------------------------------------------

@mcp.tool(
    title="Get Flight Briefing",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
def get_briefing(
    flight_id: Annotated[
        str,
        Field(description="Flight ID from list_flights or create_flight"),
    ],
) -> dict[str, Any]:
    """Get the latest weather briefing for a flight.

    Returns the overall assessment (GREEN/AMBER/RED/UNAVAILABLE), route
    advisories, AI weather digest, and a link to the full interactive briefing.

    UNAVAILABLE means we produced a briefing but no advisory could be graded (no usable model data) — it is NOT a clear sky and must never be narrated as reassuring; say we could not assess the flight.

    Each advisory carries neutral drill-in hooks: ``cross_check_present`` (model
    scheme reconciliation) and ``aggregate_mitigations_present`` (advice-only
    options that would improve a flagged sub-issue). When either is set, call
    get_advisory_detail for the full objects — a mitigation is ADVICE ONLY and
    never changes the grade.

    Status values:
    - ready: briefing available with fresh data
    - stale: briefing exists but weather models have updated since
    - processing: briefing is currently being generated
    - none: no briefing exists yet (call refresh_briefing)
    """
    try:
        client = _get_client()
    except ValueError as e:
        return _error_result(str(e))

    with client:
        # Check if a refresh is in progress
        try:
            refresh_info = client.get_refresh_status(flight_id)
            if refresh_info.get("active"):
                return {
                    "status": "processing",
                    "flight_id": flight_id,
                    "message": "Briefing is being generated. Check again in ~1-2 minutes.",
                    "web_url": _flight_web_url(flight_id),
                }
        except httpx.HTTPStatusError:
            pass  # endpoint may return error, continue

        # Get latest pack
        try:
            pack = client.get_latest_pack(flight_id)
        except httpx.HTTPStatusError as e:
            return _error_result(f"API error: {e.response.text}", e.response.status_code)

        if pack is None:
            return {
                "status": "none",
                "flight_id": flight_id,
                "message": "No briefing exists yet. Call refresh_briefing to generate one.",
                "web_url": _flight_web_url(flight_id),
            }

        timestamp = pack["fetch_timestamp"]

        # Map freshness → status + stale note via the shared shaper (identical
        # tiered-gate logic across the MCP and GPT/OpenAPI connectors).
        try:
            freshness = client.get_freshness(flight_id)
            fresh_status = _briefing_freshness_status(freshness)
        except httpx.HTTPStatusError:
            fresh_status = {"status": "ready", "is_fresh": True}

        result: dict[str, Any] = {
            "status": fresh_status["status"],
            "flight_id": flight_id,
            "assessment": pack.get("assessment"),
            "assessment_reason": pack.get("assessment_reason"),
            "days_out": pack.get("days_out"),
            "briefing_timestamp": timestamp,
            "web_url": _flight_web_url(flight_id),
        }

        # #392: tell the agent what UNAVAILABLE means before it improvises.
        _assess_note = _assessment_note(pack.get("assessment"))
        if _assess_note:
            result["assessment_note"] = _assess_note

        if not fresh_status["is_fresh"]:
            result["stale_models"] = fresh_status.get("stale_models", [])
            result["stale_note"] = fresh_status["stale_note"]

        # Fetch advisories
        advisories = client.get_advisories(flight_id, timestamp)
        if advisories:
            result["advisories"] = _summarize_advisories(advisories)

        # Fetch digest
        digest_json = client.get_digest_json(flight_id, timestamp)
        if digest_json:
            result["digest"] = digest_json

        digest_text = client.get_digest_text(flight_id, timestamp)
        if digest_text:
            result["digest_text"] = digest_text

        # Fetch altitude table
        alt_table = client.get_altitude_table(flight_id, timestamp)
        if alt_table:
            result["altitude_table"] = _summarize_altitude_table(alt_table)

        # Weather-based divert alternates: a compact hook only (the required-flag,
        # candidate count and nearest-improving picks). The full candidate list +
        # regulatory detail lives in get_alternates — keep the briefing lean.
        alternates = client.get_alternates(flight_id, timestamp)
        if alternates:
            result["alternates"] = _alternates_hook(alternates)

    return result


# ---------------------------------------------------------------------------
# Tool: refresh_briefing
# ---------------------------------------------------------------------------

@mcp.tool(
    title="Refresh Briefing",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def refresh_briefing(
    flight_id: Annotated[
        str,
        Field(description="Flight ID to refresh"),
    ],
) -> dict[str, Any]:
    """Trigger a weather briefing refresh for a flight.

    Checks model freshness first — if all data is current, returns
    the existing briefing without re-running the pipeline. This is
    safe to call repeatedly without wasting resources.

    The refresh takes ~2 minutes. Call get_briefing to check results.
    """
    try:
        client = _get_client()
    except ValueError as e:
        return _error_result(str(e))

    with client:
        try:
            result = client.refresh_briefing(flight_id)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 409:
                return {
                    "status": "already_in_progress",
                    "flight_id": flight_id,
                    "message": "A refresh is already running for this flight.",
                }
            if code == 429:
                return {
                    "status": "rate_limited",
                    "flight_id": flight_id,
                    "message": e.response.text,
                }
            if code == 503:
                return {
                    "status": "server_busy",
                    "flight_id": flight_id,
                    "message": "Server is busy with other refreshes. Try again shortly.",
                }
            return _error_result(f"Refresh failed: {e.response.text}", code)

    # API returns {"status": "queued"|"already_fresh", "flight_id": ..., "message": ...}
    status = result.get("status", "queued")
    resp: dict[str, Any] = {
        "status": status,
        "flight_id": flight_id,
        "message": result.get("message", ""),
        "web_url": _flight_web_url(flight_id),
    }
    if status == "queued":
        resp["estimated_seconds"] = 120
    return resp


# ---------------------------------------------------------------------------
# Tool: get_airport_weather
# ---------------------------------------------------------------------------

@mcp.tool(
    title="Get Airport Weather",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
)
def get_airport_weather(
    icao_codes: Annotated[
        list[str],
        Field(description="Airport ICAO codes, e.g. ['LFBO', 'LFML']. Max 20. European airports only."),
    ],
    day: Annotated[
        int,
        Field(description="Days from today: 0=today, 1=tomorrow, 2=D+2, 3=D+3", ge=0, le=3),
    ] = 0,
    hour_utc: Annotated[
        int,
        Field(description="Forecast hour in UTC: 6, 9, 12, 15, or 18", ge=0, le=23),
    ] = 12,
) -> dict[str, Any]:
    """Get weather forecasts and observations for specific airports.

    Returns multi-model predictions (GFS, ICON, ECMWF) with consensus
    flight category and agreement scoring. For today (day=0), also
    includes the latest METAR and TAF from the verification cache
    (may be up to ~3 hours old — this tool serves data from the app's
    forecast monitoring, not a live METAR feed).

    Airports not in the monitoring network are resolved to the nearest
    monitored airport (with distance noted). Non-European airports
    return in the 'unsupported' list.
    """
    try:
        with _get_client() as client:
            return client.get_airport_weather(icao_codes, day=day, hour_utc=hour_utc)
    except httpx.HTTPStatusError as e:
        return _error_result(f"API error: {e.response.text}", e.response.status_code)
    except ValueError as e:
        return _error_result(str(e))


# ---------------------------------------------------------------------------
# Tool: get_advisory_detail
# ---------------------------------------------------------------------------

@mcp.tool(
    title="Get Advisory Detail",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
def get_advisory_detail(
    flight_id: Annotated[
        str,
        Field(description="Flight ID from list_flights or create_flight"),
    ],
    advisory_id: Annotated[
        str,
        Field(description="Advisory id to drill into, e.g. 'convective', 'fiki_icing', 'cloud_top' (the 'id' field from get_briefing's advisories)"),
    ],
) -> dict[str, Any]:
    """Drill into ONE advisory to explain WHY it is red/amber (or any grade).

    Use this when the user asks why an advisory is red/amber, questions a
    result that looks inconsistent (e.g. "red convective but clear skies"),
    doubts a grade, or wants the per-model breakdown and cross-check reasoning.
    Never explain a red/amber from the aggregate status alone — call this first.

    Returns a server-side summary (never the raw forecast grid):
    - per-model status, detail, affected extent, and the cross-check note
    - parameters_used: the thresholds that produced the grade
    - aggregate_mitigations + per-model mitigations (when present): advice-only
      options that would improve a flagged sub-issue, with a mitigation_note
      guardrail. Each carries mitigated_status (the status of the addressed
      sub-issue if applied, NOT the advisory overall).
    - for the convective advisory: per-model CAPE range + the location (nm /
      waypoint) of the peak, the convective cover % (cover ≈ 0 is the
      machine-readable "blue sky" signal), and whether the assessment used the
      thermo (CAPE-derived) or NWP (model-scheme) method.

    The cross-check is **context for discussion, not a downgrade signal** — high
    CAPE matters even when the model's own convective scheme is quiet. Use it to
    EXPLAIN the grade, not to argue it down. Mitigations are likewise ADVICE
    ONLY — explain the trade-off, never use one to downgrade an advisory.
    """
    try:
        client = _get_client()
    except ValueError as e:
        return _error_result(str(e))

    with client:
        try:
            timestamp, status = _resolve_pack(client, flight_id)
        except httpx.HTTPStatusError as e:
            return _error_result(f"API error: {e.response.text}", e.response.status_code)
        if status is not None:
            return status

        try:
            advisories = client.get_advisories(flight_id, timestamp)
        except httpx.HTTPStatusError as e:
            return _error_result(f"API error: {e.response.text}", e.response.status_code)
        if not advisories:
            return _error_result("No advisories available for this briefing.")

        adv = next(
            (a for a in advisories.get("advisories", []) if a.get("advisory_id") == advisory_id),
            None,
        )
        if adv is None:
            available = [a.get("advisory_id") for a in advisories.get("advisories", [])]
            listed = ", ".join(x for x in available if x)
            return _error_result(
                f"Advisory '{advisory_id}' not found. Available: {listed}"
            )

        catalog = {c.get("id"): c for c in advisories.get("catalog", [])}
        result = _advisory_detail(adv, catalog.get(advisory_id))

        # Per-briefing staleness so a forensic per-model answer carries the
        # caveat: reasoning on stale data is confidently-wrong territory.
        try:
            freshness = client.get_freshness(flight_id)
            if not freshness.get("fresh", True):
                result["stale"] = True
                result["stale_models"] = freshness.get("stale_models", [])
                result["model_init_times"] = freshness.get("model_init_times", {})
                result["stale_note"] = (
                    "Models have updated since this briefing was generated. This "
                    "drill-down reflects the run used at briefing time, not the "
                    "latest — caveat any forensic reasoning and suggest "
                    "refresh_briefing for current data."
                )
        except httpx.HTTPStatusError:
            pass

        # Deep link straight to this advisory's Skew-T lens (#308). For
        # convective we also point at the worst (highest-CAPE) route point and
        # its model so the pilot/assistant lands on the relevant sounding.
        peak_point: int | None = None
        peak_model: str | None = None

        if advisory_id == "convective":
            try:
                route_analyses = client.get_route_analyses(flight_id, timestamp)
            except httpx.HTTPStatusError:
                route_analyses = None
            if route_analyses:
                models = [m.get("model") for m in adv.get("per_model", [])]
                convective = _convective_detail(route_analyses, models)
                result["convective"] = convective
                result["convective_note"] = _CONVECTIVE_NOTE
                # Pick the model+point with the highest CAPE peak for the link.
                best_cape = -1.0
                for model_name, block in convective.items():
                    peak = (block.get("thermo") or {}).get("peak") or {}
                    cape = peak.get("cape_jkg")
                    idx = peak.get("point_index")
                    if cape is not None and idx is not None and cape > best_cape:
                        best_cape = cape
                        peak_point = idx
                        peak_model = model_name

        result["flight_id"] = flight_id
        result["web_url"] = _advisory_web_url(
            flight_id, advisory_id, point_index=peak_point, model=peak_model
        )

    return result


# ---------------------------------------------------------------------------
# Tool: get_alternates
# ---------------------------------------------------------------------------

@mcp.tool(
    title="Get Weather Alternates",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
def get_alternates(
    flight_id: Annotated[
        str,
        Field(description="Flight ID from list_flights or create_flight"),
    ],
) -> dict[str, Any]:
    """Get the weather-based divert alternates for a flight's destination.

    Use this when the user asks about diversions, alternates, "where could I go
    if the destination is below minima", or whether a filed alternate is
    required. get_briefing carries only a compact 'alternates' hook (the
    required-flag + candidate count); the full picture is here.

    Returns, for the destination:
    - requirement: the advisory FAA + EASA "is an alternate required?" trigger
      (FAA is binary; EASA is a Likely/Marginal/Unlikely band off estimated
      approach minima), each with the forecast vs required ceiling/visibility.
    - nearest_improving: the closest airport that fixes each deficient axis
      (better category / lower wind / lower crosswind).
    - candidates: ranked divert airports with consensus weather at ETA AND
      suitability fields (approach type, longest/hard runway, point_of_entry).

    IMPORTANT — these are WEATHER-improvement candidates, NOT operational
    alternates. Operational suitability (opening hours, customs/PPR, fuel,
    NOTAMs, approach currency) is NOT evaluated. When advising a pilot, combine
    each candidate with airport/AIP data (e.g. the euro_aip / airfield-directory
    tools) to confirm it is operationally usable before recommending it. The
    returned 'note' restates this caveat.
    """
    try:
        client = _get_client()
    except ValueError as e:
        return _error_result(str(e))

    with client:
        try:
            timestamp, status = _resolve_pack(client, flight_id)
        except httpx.HTTPStatusError as e:
            return _error_result(f"API error: {e.response.text}", e.response.status_code)
        if status is not None:
            return status

        try:
            alternates = client.get_alternates(flight_id, timestamp)
        except httpx.HTTPStatusError as e:
            return _error_result(f"API error: {e.response.text}", e.response.status_code)

    if not alternates:
        return {
            "status": "none",
            "flight_id": flight_id,
            "message": (
                "No weather alternates were computed for this briefing. Either "
                "compute_alternates is disabled in the user's profile, or the "
                "destination forecast was not marginal enough to surface diverts."
            ),
            "web_url": _flight_web_url(flight_id),
        }

    result = _summarize_alternates(alternates)
    result["status"] = "ready"
    result["flight_id"] = flight_id
    result["briefing_timestamp"] = timestamp
    result["web_url"] = _flight_web_url(flight_id)
    return result


# ---------------------------------------------------------------------------
# Tool: get_digest_context
# ---------------------------------------------------------------------------

@mcp.tool(
    title="Get Digest Context",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
def get_digest_context(
    flight_id: Annotated[
        str,
        Field(description="Flight ID from list_flights or create_flight"),
    ],
) -> dict[str, Any]:
    """Get the exact text input the AI weather digest saw for this flight.

    Use when the user wants the deepest possible context behind the briefing —
    e.g. to reconcile a red/amber advisory with the digest narrative, or to see
    every advisory, route statistic, and the raw DWD synoptic overview exactly
    as the digest LLM received them.

    This is the maximum detail exposed over MCP and a genuine last resort: the
    text can be large (typically a few KB up to tens of KB) and will consume a
    meaningful slice of the context window. Do NOT fetch it reflexively —
    prefer get_advisory_detail for a targeted, structured drill-down, and reach
    for this only when you specifically need the byte-faithful LLM input.

    Returns the plain-text digest context, or status 'none' for older briefings
    that predate this artifact / had no digest generated.
    """
    try:
        client = _get_client()
    except ValueError as e:
        return _error_result(str(e))

    with client:
        try:
            timestamp, status = _resolve_pack(client, flight_id)
        except httpx.HTTPStatusError as e:
            return _error_result(f"API error: {e.response.text}", e.response.status_code)
        if status is not None:
            return status

        try:
            context = client.get_digest_context(flight_id, timestamp)
        except httpx.HTTPStatusError as e:
            return _error_result(f"API error: {e.response.text}", e.response.status_code)

    if not context:
        return {
            "status": "none",
            "flight_id": flight_id,
            "message": "No digest context available for this briefing.",
            "web_url": _flight_web_url(flight_id),
        }

    return {
        "status": "ready",
        "flight_id": flight_id,
        "briefing_timestamp": timestamp,
        "digest_context": context,
        "web_url": _flight_web_url(flight_id),
    }


# ---------------------------------------------------------------------------
# Prompt: explain_advisory
# ---------------------------------------------------------------------------

@mcp.prompt(title="Explain Advisory")
def explain_advisory(
    flight_id: Annotated[
        str,
        Field(description="Flight ID to investigate"),
    ],
    advisory_id: Annotated[
        str,
        Field(description="Advisory to explain, e.g. 'convective'"),
    ] = "convective",
) -> str:
    """On-ramp for an in-depth, per-model discussion of one advisory's grade."""
    return (
        f"For flight {flight_id}, explain why the '{advisory_id}' advisory has its "
        "current status. First call get_briefing to see the per-model split and any "
        f"cross-check notes, then get_advisory_detail('{flight_id}', '{advisory_id}') "
        "for the per-model breakdown — for convective that includes CAPE range, the "
        "location of the peak, convective cover % (cover ~0 means the model scheme "
        "sees blue sky), and whether the thermo or NWP method was used. Pull "
        "get_digest_context if you need the exact LLM input. Explain the grade from "
        "that evidence, including when high CAPE drives a red while the model's own "
        "convective scheme is quiet. Treat the cross-check as context for the "
        "discussion, not a reason to downgrade the advisory."
    )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="WeatherBrief MCP Server")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="Transport mode (default: stdio)",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8021)
    args = parser.parse_args()

    if args.transport == "http":
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            stateless_http=True,
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
