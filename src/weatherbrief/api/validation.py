"""Shared input validation constants for API endpoints."""

import re

from euro_aip.utils.dms_parser import is_icao_coordinate

# Named-waypoint code pattern: 2-5 alphanumeric characters.
# Matches ICAO airports (4 letters), VOR/NDB navaids (2-3 chars),
# five-letter fixes, and alphanumeric codes (e.g. BRK63, LNZ01).
WAYPOINT_RE = re.compile(r"^[A-Z0-9]{2,5}$", re.IGNORECASE)


def is_valid_waypoint(token: str) -> bool:
    """Accept either a named-waypoint code or an inline ICAO coordinate.

    Inline coords (``DDMM[NS]DDDMM[EW]`` and the DMS variant) come in
    longer than ``WAYPOINT_RE`` allows but are real route points the
    resolver can place geometrically. Validators upstream of the
    resolver should accept both shapes; the resolver itself decides
    whether a token actually plots on-route.
    """
    upper = token.upper().strip()
    return bool(WAYPOINT_RE.match(upper)) or is_icao_coordinate(upper)
