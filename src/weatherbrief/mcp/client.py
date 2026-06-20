"""HTTP client for proxying MCP tool calls to the weatherbrief API.

The MCP server is a thin proxy — it forwards the user's Bearer token
to the weatherbrief API running on localhost. All auth, rate limiting,
and cost tracking happen in the API layer.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_BASE = os.getenv("WEATHERBRIEF_API_URL", "http://localhost:8020")
_TIMEOUT = httpx.Timeout(connect=5, read=180, write=5, pool=5)


class WeatherbriefClient:
    """Thin wrapper around the weatherbrief REST API.

    Uses a single httpx.Client for the lifetime of the instance so that
    tool calls that make multiple API requests (e.g. get_briefing) reuse
    the same connection instead of opening a new one per request.
    """

    def __init__(self, token: str) -> None:
        self.token = token
        self._client = httpx.Client(
            base_url=API_BASE,
            timeout=_TIMEOUT,
            headers={"Authorization": f"Bearer {token}"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WeatherbriefClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _get(self, path: str, params: dict | None = None) -> Any:
        r = self._client.get(f"/api{path}", params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: dict | None = None, params: dict | None = None) -> Any:
        r = self._client.post(f"/api{path}", json=json, params=params)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> int:
        r = self._client.delete(f"/api{path}")
        r.raise_for_status()
        return r.status_code

    # -- Flights --

    def list_flights(self) -> list[dict]:
        return self._get("/flights")

    def create_flight(
        self,
        waypoints: list[str],
        departure_time: str,
        cruise_altitude_ft: int | None = None,
        flight_duration_hours: float | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "waypoints": waypoints,
            "departure_time": departure_time,
        }
        if cruise_altitude_ft is not None:
            body["cruise_altitude_ft"] = cruise_altitude_ft
        if flight_duration_hours is not None:
            body["flight_duration_hours"] = flight_duration_hours
        return self._post("/flights", json=body)

    # -- Briefing packs --

    def get_latest_pack(self, flight_id: str) -> dict | None:
        """Get the latest briefing pack metadata, or None if no packs yet."""
        try:
            return self._get(f"/flights/{flight_id}/packs/latest")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def get_freshness(self, flight_id: str) -> dict:
        return self._get(f"/flights/{flight_id}/packs/freshness")

    def refresh_briefing(self, flight_id: str) -> dict:
        """Trigger a briefing refresh. Returns pack meta on success."""
        return self._post(f"/flights/{flight_id}/packs/refresh")

    def get_refresh_status(self, flight_id: str) -> dict:
        return self._get(f"/flights/{flight_id}/packs/refresh/status")

    def get_advisories(self, flight_id: str, timestamp: str) -> dict | None:
        try:
            return self._get(f"/flights/{flight_id}/packs/{timestamp}/advisories")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def get_route_analyses(self, flight_id: str, timestamp: str) -> dict | None:
        """Get the per-point route analyses (soundings per model) for a pack."""
        try:
            return self._get(f"/flights/{flight_id}/packs/{timestamp}/route-analyses")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def get_digest_json(self, flight_id: str, timestamp: str) -> dict | None:
        try:
            return self._get(f"/flights/{flight_id}/packs/{timestamp}/digest/json")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def get_digest_text(self, flight_id: str, timestamp: str) -> str | None:
        """Get the markdown digest text."""
        try:
            r = self._client.get(
                f"/api/flights/{flight_id}/packs/{timestamp}/digest",
            )
            r.raise_for_status()
            return r.text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def get_digest_context(self, flight_id: str, timestamp: str) -> str | None:
        """Get the exact plain-text context the LLM digest saw, if persisted."""
        try:
            # text/plain response — intentionally bypasses _get(), whose .json()
            # decode would fail on this endpoint.
            r = self._client.get(
                f"/api/flights/{flight_id}/packs/{timestamp}/digest/context",
            )
            r.raise_for_status()
            return r.text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def get_altitude_table(self, flight_id: str, timestamp: str) -> dict | None:
        try:
            return self._post(f"/flights/{flight_id}/packs/{timestamp}/advisories/altitude-table")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # -- Airport weather (forecast map) --

    def get_airport_weather(
        self,
        icao_codes: list[str],
        day: int = 0,
        hour_utc: int = 12,
    ) -> dict:
        return self._get(
            "/maps/airport-weather",
            params={"icao": icao_codes, "day": day, "hour": hour_utc},
        )
