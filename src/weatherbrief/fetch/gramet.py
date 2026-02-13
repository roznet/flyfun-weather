"""Autorouter GRAMET cross-section fetcher."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import requests
from euro_aip.utils.autorouter_credentials import AutorouterCredentialManager

logger = logging.getLogger(__name__)

GRAMET_URL = "https://api.autorouter.aero/v1.0/met/gramet"


class AutorouterGramet:
    """Client for fetching GRAMET cross-section images from the Autorouter API.

    Uses AutorouterCredentialManager from euro_aip for OAuth2 authentication.
    """

    def __init__(
        self,
        cache_dir: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ):
        self._cred_manager = AutorouterCredentialManager(
            cache_dir or str(Path.home() / ".cache" / "weatherbrief")
        )
        # Use explicit credentials if provided, else fall back to env vars
        username = username or os.environ.get("AUTOROUTER_USERNAME")
        password = password or os.environ.get("AUTOROUTER_PASSWORD")
        if username and password:
            self._cred_manager.set_credentials(username, password)
        self.session = requests.Session()

    def fetch_gramet(
        self,
        icao_codes: list[str],
        altitude_ft: int,
        departure_time: datetime,
        duration_hours: float,
        fmt: str = "png",
    ) -> bytes:
        """Fetch a GRAMET cross-section image.

        Args:
            icao_codes: Ordered list of ICAO waypoints (space-separated in API).
            altitude_ft: Cruise altitude in feet.
            departure_time: Departure datetime (converted to Unix timestamp).
            duration_hours: Total estimated elapsed time in hours.
            fmt: Output format, "png" or "pdf".

        Returns:
            Raw bytes of the GRAMET image.
        """
        token = self._cred_manager.get_token()

        params = {
            "waypoints": " ".join(icao_codes),
            "altitude": altitude_ft,
            "departuretime": int(departure_time.timestamp()),
            "totaleet": int(duration_hours * 3600),
            "format": fmt,
        }

        logger.info("Fetching GRAMET: %s at FL%03d", " ".join(icao_codes), altitude_ft // 100)

        resp = self.session.get(
            GRAMET_URL,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content
