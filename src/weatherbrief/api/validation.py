"""Shared input validation constants for API endpoints."""

import re

# Waypoint code pattern: 2-5 alphanumeric characters.
# Matches ICAO airports (4 letters), VOR/NDB navaids (2-3 chars),
# five-letter fixes, and alphanumeric codes (e.g. BRK63, LNZ01).
WAYPOINT_RE = re.compile(r"^[A-Z0-9]{2,5}$", re.IGNORECASE)
