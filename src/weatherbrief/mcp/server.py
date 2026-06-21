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
from mcp.types import ToolAnnotations
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
        "before answering. The per-model split and cross_check note in "
        "get_briefing are only a hook — they are NOT the full picture: the "
        "per-point reconciliation data (e.g. CAPE vs the model's own cloud "
        "cover, the peak location and valid-time) lives ONLY in "
        "get_advisory_detail. So whenever an advisory carries cross_check_present "
        "or the user asks 'why', drill in first — do not answer from the "
        "get_briefing summary alone even though it looks sufficient. Treat "
        "cross-check notes and per-model splits as context for the discussion, "
        "not a reason to downgrade an advisory.\n\n"
        "Provenance note: the AI digest narrates 'convective tops' that are "
        "parcel-derived (the thermodynamic equilibrium level from CAPE), NOT the "
        "model's own convective cloud field. A convective advisory driven RED by "
        "high CAPE while the model's convective cover is ~0 ('blue sky') is "
        "consistent, not contradictory — explain it, don't argue it down.\n\n"
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

@mcp.tool(
    title="List Flights",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
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

        # Check freshness. Prefer the tiered refresh-gate decision (what the web
        # refresh button actually does at this lead time) over the raw ``fresh``
        # min-rule: the min-rule flips to stale whenever *any* model has a newer
        # run, even when the gate has decided a refresh isn't worthwhile yet —
        # which surfaced as a false "needs refresh" right after a manual refresh.
        try:
            freshness = client.get_freshness(flight_id)
            decision = freshness.get("refresh_decision") or {}
            mode = decision.get("mode")
            if mode is not None:
                # "none" → nothing worthwhile to refresh; "full"/"realtime" → an
                # update the button would actually fetch.
                is_fresh = mode == "none"
            else:
                # Gate didn't run (no flight ctx) — fall back to the min-rule.
                is_fresh = freshness.get("fresh", True)
            stale_models = freshness.get("stale_models", [])
            stale_reason = decision.get("reason")
            pending_models = decision.get("pending_models", [])
            eta_useful = decision.get("eta_useful")
        except httpx.HTTPStatusError:
            is_fresh = True
            stale_models = []
            stale_reason = None
            pending_models = []
            eta_useful = None

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
            # Use the gate's own reason so the note matches the web button — and
            # only when a refresh is genuinely worthwhile (mode != "none").
            note = stale_reason or "Newer model data is available for this briefing."
            note += " Call refresh_briefing for the latest data."
            if pending_models:
                note += f" Awaiting updated runs: {', '.join(pending_models)}."
            if eta_useful:
                note += f" Useful refresh ETA: {eta_useful}."
            result["stale_note"] = note

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


def _summarize_advisories(advisories: dict) -> list[dict]:
    """Extract advisory information for the agent, retaining per-model detail.

    Unlike a flat status list, this keeps each model's status/detail and the
    ``cross_check`` note plus the ``parameters_used`` thresholds, so the agent
    can see the per-model split and the reasoning behind a grade directly in
    the briefing output. That visible detail is the primary discoverability
    hook (Layer A): it provokes the follow-up question and a ``detail_tool``
    pointer names the drill-down tool.

    The hint flags are deliberately **neutral** (``cross_check_present`` /
    ``per_model_present``, never "model_disagreement"): a valenced flag would
    prime the exact red→amber downgrade the guardrail forbids. The cross-check
    is display-only **context for discussion**, never a downgrade signal
    (#178) — high CAPE matters even when the deterministic model scheme is
    quiet.
    """
    catalog = {c.get("id"): c for c in advisories.get("catalog", [])}
    results = []
    for adv in advisories.get("advisories", []):
        adv_id = adv.get("advisory_id")
        per_model: list[dict] = []
        cross_check_present = False
        for m in adv.get("per_model", []):
            entry_m: dict[str, Any] = {
                "model": m.get("model"),
                "status": m.get("status"),
                "detail": m.get("detail"),
                "affected_pct": m.get("affected_pct"),
            }
            cc = m.get("cross_check")
            if cc:
                cross_check_present = True
                entry_m["cross_check"] = cc
            per_model.append(entry_m)

        entry: dict[str, Any] = {
            "id": adv_id,
            "status": adv.get("aggregate_status"),
            "detail": adv.get("aggregate_detail"),
            "cross_check_present": cross_check_present,
            "per_model_present": bool(per_model),
        }
        cat = catalog.get(adv_id)
        if cat:
            entry["name"] = cat.get("name")
            entry["category"] = cat.get("category")

        # Layer A: expand the full per-model detail + thresholds, and point the
        # agent at the drill-down tool, only when there is something worth
        # explaining (a non-green grade or a cross-check note). Green-and-quiet
        # advisories keep just the ``*_present`` flags so every get_briefing
        # call stays compact — their per-model data is almost always noise, and
        # the agent can still call get_advisory_detail explicitly if asked.
        if entry["status"] in ("amber", "red") or cross_check_present:
            entry["per_model"] = per_model
            entry["parameters_used"] = adv.get("parameters_used", {})
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

        if advisory_id == "convective":
            try:
                route_analyses = client.get_route_analyses(flight_id, timestamp)
            except httpx.HTTPStatusError:
                route_analyses = None
            if route_analyses:
                models = [m.get("model") for m in adv.get("per_model", [])]
                result["convective"] = _convective_detail(route_analyses, models)
                result["convective_note"] = (
                    "thermo.peak.el_top_ft is parcel-derived (the equilibrium "
                    "level the digest narrates as 'convective tops'), NOT the "
                    "model's convective cloud field. nwp.max_cover_pct ~0 means "
                    "the model's own convective scheme is quiet ('blue sky'); "
                    "high CAPE with cover ~0 is the expected pattern, not a "
                    "contradiction. assessment_method is which derivation graded "
                    "the route."
                )

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

    Splits the two independent derivations so a digest that narrates "tops to
    27,000 ft" reconciles with a quiet model scheme:

    - ``thermo`` — the CAPE/parcel view: CAPE range across the route and the
      peak point (CAPE, the equilibrium-level top the digest calls "convective
      tops", location, and the forecast valid-time vs flight ETA so the diurnal
      timing is explicit).
    - ``nwp`` — the model's own convective scheme: max convective cover %
      (``~0`` is the machine-readable "blue sky" signal) and its convective top.
    - ``assessment_method`` — which derivation actually graded the route.

    cover ~0 next to high CAPE is the expected pattern, not a contradiction —
    this surfaces the data so it can be explained, never argued down.
    """
    points = route_analyses.get("analyses", [])
    out: dict[str, Any] = {}
    for model in models:
        thermo_capes: list[float] = []
        thermo_peak: dict | None = None
        thermo_peak_cape: float | None = None  # raw CAPE for comparison (output rounds)
        nwp_max_cover: float | None = None
        nwp_peak_top: float | None = None
        method_counts: dict[str, int] = {}
        for p in points:
            sounding = (p.get("sounding") or {}).get(model)
            if not sounding:
                continue
            resolved = sounding.get("convective")
            method = resolved.get("method") if resolved else None
            if method:
                method_counts[method] = method_counts.get(method, 0) + 1

            # Parcel/CAPE view — explicit thermo, falling back to the resolved
            # assessment for old packs that never split the two.
            thermo = sounding.get("convective_thermo") or resolved
            if thermo:
                cape = thermo.get("cape_jkg")
                if cape is not None:
                    thermo_capes.append(cape)
                    if thermo_peak is None or cape > thermo_peak_cape:
                        thermo_peak_cape = cape
                        top = thermo.get("top_ft")
                        thermo_peak = {
                            "cape_jkg": round(cape),
                            "el_top_ft": round(top) if top is not None else None,
                            "risk_level": thermo.get("risk_level"),
                            "distance_nm": p.get("distance_from_origin_nm"),
                            "waypoint_icao": p.get("waypoint_icao"),
                            "valid_time": p.get("forecast_hour"),
                            "eta": p.get("interpolated_time"),
                        }

            # Model's own convective scheme.
            nwp = sounding.get("convective_nwp")
            if nwp:
                cover = nwp.get("cover_pct")
                if cover is not None:
                    nwp_max_cover = cover if nwp_max_cover is None else max(nwp_max_cover, cover)
                top = nwp.get("top_ft")
                if top is not None:
                    nwp_peak_top = top if nwp_peak_top is None else max(nwp_peak_top, top)

        if not thermo_capes and not method_counts and nwp_max_cover is None:
            continue
        out[model] = {
            "assessment_method": (
                max(method_counts, key=lambda k: method_counts[k]) if method_counts else None
            ),
            "method_counts": method_counts,
            "thermo": {
                "cape_range_jkg": (
                    [round(min(thermo_capes)), round(max(thermo_capes))] if thermo_capes else None
                ),
                "peak": thermo_peak,
            },
            "nwp": _nwp_block(nwp_max_cover, nwp_peak_top),
        }
    return out


def _nwp_block(max_cover: float | None, peak_top: float | None) -> dict[str, Any]:
    """Model convective-scheme summary. ``max_cover_pct`` and ``peak_top_ft``
    are maximized independently across the route, so they may come from
    different points — the note guards against reading them as one peak."""
    block: dict[str, Any] = {
        "max_cover_pct": round(max_cover) if max_cover is not None else None,
        "peak_top_ft": round(peak_top) if peak_top is not None else None,
    }
    if max_cover is not None or peak_top is not None:
        block["note"] = (
            "max_cover_pct and peak_top_ft are independent route-wide maxima "
            "and may come from different points"
        )
    return block


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
