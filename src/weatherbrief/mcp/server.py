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

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.auth import AccessToken, RemoteAuthProvider, TokenVerifier
from fastmcp.server.dependencies import get_http_request
from pydantic import AnyHttpUrl, Field

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
        "before answering — never explain a red/amber from the aggregate status "
        "alone. Treat cross-check notes as context for the discussion, not a "
        "reason to downgrade an advisory.\n\n"
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

@mcp.tool()
def list_flights() -> dict[str, Any]:
    """List the user's upcoming flights with briefing status.

    Returns each flight's route, departure time, and weather assessment
    (GREEN/AMBER/RED) from the latest briefing. Use this to see which
    flights need attention or have stale briefings.
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

@mcp.tool()
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

        # Auto-trigger briefing refresh
        refresh_status: dict[str, Any] = {"status": "not_triggered"}
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
            "web_url": _flight_web_url(flight_id),
        },
        "briefing": refresh_status,
    }


# ---------------------------------------------------------------------------
# Tool: get_briefing
# ---------------------------------------------------------------------------

@mcp.tool()
def get_briefing(
    flight_id: Annotated[
        str,
        Field(description="Flight ID from list_flights or create_flight"),
    ],
) -> dict[str, Any]:
    """Get the latest weather briefing for a flight.

    Returns the overall assessment (GREEN/AMBER/RED), route advisories,
    AI weather digest, and a link to the full interactive briefing.

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

        # Check freshness
        try:
            freshness = client.get_freshness(flight_id)
            is_fresh = freshness.get("fresh", True)
            stale_models = freshness.get("stale_models", [])
        except httpx.HTTPStatusError:
            is_fresh = True
            stale_models = []

        status = "ready" if is_fresh else "stale"

        result: dict[str, Any] = {
            "status": status,
            "flight_id": flight_id,
            "assessment": pack.get("assessment"),
            "assessment_reason": pack.get("assessment_reason"),
            "days_out": pack.get("days_out"),
            "briefing_timestamp": timestamp,
            "web_url": _flight_web_url(flight_id),
        }

        if not is_fresh:
            result["stale_models"] = stale_models
            result["stale_note"] = (
                "Weather models have updated since this briefing. "
                "Call refresh_briefing for the latest data."
            )

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

    return result


_GRADED_STATUSES = ("green", "amber", "red")


def _summarize_advisories(advisories: dict) -> list[dict]:
    """Extract advisory information for the agent, retaining per-model detail.

    Unlike a flat status list, this keeps each model's status/detail and the
    ``cross_check`` note plus the ``parameters_used`` thresholds, so the agent
    can see model disagreement and the reasoning behind a grade directly in the
    briefing output. That visible disagreement is the primary discoverability
    hook (Layer A): it provokes the follow-up question and a ``detail_tool``
    pointer names the drill-down tool.

    The cross-check is display-only **context for discussion**, never a
    downgrade signal (#178) — high CAPE matters even when the deterministic
    model scheme is quiet.
    """
    catalog = {c.get("id"): c for c in advisories.get("catalog", [])}
    results = []
    for adv in advisories.get("advisories", []):
        adv_id = adv.get("advisory_id")
        per_model: list[dict] = []
        graded: set[str] = set()
        cross_check_present = False
        for m in adv.get("per_model", []):
            status = m.get("status")
            if status in _GRADED_STATUSES:
                graded.add(status)
            entry_m: dict[str, Any] = {
                "model": m.get("model"),
                "status": status,
                "detail": m.get("detail"),
                "affected_pct": m.get("affected_pct"),
            }
            cc = m.get("cross_check")
            if cc:
                cross_check_present = True
                entry_m["cross_check"] = cc
            per_model.append(entry_m)

        model_disagreement = len(graded) > 1
        entry: dict[str, Any] = {
            "id": adv_id,
            "status": adv.get("aggregate_status"),
            "detail": adv.get("aggregate_detail"),
            "per_model": per_model,
            "parameters_used": adv.get("parameters_used", {}),
            "cross_check_present": cross_check_present,
            "model_disagreement": model_disagreement,
        }
        cat = catalog.get(adv_id)
        if cat:
            entry["name"] = cat.get("name")
            entry["category"] = cat.get("category")

        # Layer A: point the agent at the drill-down tool whenever there is
        # something worth explaining (a non-green grade, model disagreement, or
        # a cross-check note). Green-and-quiet advisories omit it to avoid
        # nudging wasteful drills.
        if entry["status"] in ("amber", "red") or cross_check_present or model_disagreement:
            entry["detail_tool"] = "get_advisory_detail"

        results.append(entry)
    return results


def _summarize_altitude_table(table: dict) -> dict:
    """Summarize the altitude advisory table for the agent."""
    return {
        "cruise_altitude_ft": table.get("cruise_altitude_ft"),
        "best_below_cruise": table.get("best_below_cruise"),
        "best_above_cruise": table.get("best_above_cruise"),
        "advisory_names": table.get("advisory_names", {}),
        "rows": [
            {
                "altitude_ft": row.get("altitude_ft"),
                "red_count": row.get("red_count"),
                "amber_count": row.get("amber_count"),
                "green_count": row.get("green_count"),
                "statuses": row.get("statuses"),
            }
            for row in table.get("rows", [])
        ],
    }


# ---------------------------------------------------------------------------
# Tool: refresh_briefing
# ---------------------------------------------------------------------------

@mcp.tool()
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

@mcp.tool()
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

@mcp.tool()
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
    - for the convective advisory: per-model CAPE range + the location (nm /
      waypoint) of the peak, the convective cover % (cover ≈ 0 is the
      machine-readable "blue sky" signal), and whether the assessment used the
      thermo (CAPE-derived) or NWP (model-scheme) method.

    The cross-check is **context for discussion, not a downgrade signal** — high
    CAPE matters even when the model's own convective scheme is quiet. Use it to
    EXPLAIN the grade, not to argue it down.
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
            return _error_result(
                f"Advisory '{advisory_id}' not found. Available: {', '.join(available)}"
            )

        catalog = {c.get("id"): c for c in advisories.get("catalog", [])}
        result = _advisory_detail(adv, catalog.get(advisory_id))

        if advisory_id == "convective":
            try:
                route_analyses = client.get_route_analyses(flight_id, timestamp)
            except httpx.HTTPStatusError:
                route_analyses = None
            if route_analyses:
                models = [m.get("model") for m in adv.get("per_model", [])]
                result["convective"] = _convective_detail(route_analyses, models)

        result["flight_id"] = flight_id
        result["web_url"] = _flight_web_url(flight_id)

    return result


def _advisory_detail(adv: dict, catalog_entry: dict | None) -> dict[str, Any]:
    """Build a generic per-model drill-down summary for one advisory."""
    per_model: list[dict] = []
    for m in adv.get("per_model", []):
        entry_m: dict[str, Any] = {
            "model": m.get("model"),
            "status": m.get("status"),
            "detail": m.get("detail"),
            "affected_pct": m.get("affected_pct"),
            "affected_nm": m.get("affected_nm"),
            "total_nm": m.get("total_nm"),
        }
        cc = m.get("cross_check")
        if cc:
            entry_m["cross_check"] = cc
        per_model.append(entry_m)

    result: dict[str, Any] = {
        "advisory_id": adv.get("advisory_id"),
        "aggregate_status": adv.get("aggregate_status"),
        "aggregate_detail": adv.get("aggregate_detail"),
        "per_model": per_model,
        "parameters_used": adv.get("parameters_used", {}),
        "cross_check_note": (
            "Cross-check notes are display-only context for discussion, not a "
            "downgrade signal. Explain the grade with them; do not argue it down."
        ),
    }
    if catalog_entry:
        result["name"] = catalog_entry.get("name")
        result["category"] = catalog_entry.get("category")
        result["description"] = catalog_entry.get("description")
    return result


def _convective_detail(route_analyses: dict, models: list[str]) -> dict[str, Any]:
    """Per-model convective drill-down from the route analyses soundings.

    For each model: CAPE range across the route, the peak point (CAPE, cover %,
    location), the max convective cover %, and which assessment method
    (thermo vs NWP) each point used. ``cover_pct`` near 0 is the model scheme
    saying "blue sky" even when CAPE (thermo) is high — the apparent
    contradiction the cross-check explains.
    """
    points = route_analyses.get("analyses", [])
    out: dict[str, Any] = {}
    for model in models:
        capes: list[float] = []
        peak: dict | None = None
        max_cover: float | None = None
        method_counts: dict[str, int] = {}
        for p in points:
            sounding = (p.get("sounding") or {}).get(model)
            if not sounding:
                continue
            conv = sounding.get("convective")
            if not conv:
                continue
            method = conv.get("method")
            if method:
                method_counts[method] = method_counts.get(method, 0) + 1
            cover = conv.get("cover_pct")
            if cover is not None:
                max_cover = cover if max_cover is None else max(max_cover, cover)
            cape = conv.get("cape_jkg")
            if cape is not None:
                capes.append(cape)
                if peak is None or cape > peak["cape_jkg"]:
                    peak = {
                        "cape_jkg": round(cape),
                        "cover_pct": round(cover) if cover is not None else None,
                        "risk_level": conv.get("risk_level"),
                        "method": method,
                        "distance_nm": p.get("distance_from_origin_nm"),
                        "waypoint_icao": p.get("waypoint_icao"),
                    }
        if not capes and not method_counts:
            continue
        out[model] = {
            "cape_range_jkg": [round(min(capes)), round(max(capes))] if capes else None,
            "peak": peak,
            "max_cover_pct": round(max_cover) if max_cover is not None else None,
            "assessment_method": (
                max(method_counts, key=lambda k: method_counts[k]) if method_counts else None
            ),
            "method_counts": method_counts,
        }
    return out


# ---------------------------------------------------------------------------
# Tool: get_digest_context
# ---------------------------------------------------------------------------

@mcp.tool()
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
    as the digest LLM received them. This is the maximum detail exposed over MCP;
    pair it with get_advisory_detail when diagnosing a specific advisory.

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

@mcp.prompt()
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
