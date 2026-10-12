"""Shared formatting helpers for digest text and LLM prompt builders."""

from __future__ import annotations


def format_flight_level(ft: int | None) -> str:
    """Format an altitude in feet MSL as a flight-level / surface label.

    ``None`` → ``"?"``, ``<= 0`` → ``"SFC"``, otherwise ``"FL{NNN}"``.
    """
    if ft is None:
        return "?"
    if ft <= 0:
        return "SFC"
    return f"FL{ft // 100:03d}"


def terminal_roles(snapshot) -> dict[str, str]:
    """ICAO -> "DEPARTURE" / "DESTINATION" for the observation sections."""
    wps = getattr(getattr(snapshot, "route", None), "waypoints", None) or []
    if not wps:
        return {}
    return {wps[-1].icao.upper(): "DESTINATION", wps[0].icao.upper(): "DEPARTURE"}


def terminal_proxy_lines(obs, terminals: dict[str, str]) -> list[str]:
    """One line per departure/destination read through a proxy (#767):
    "DEPARTURE EGTF has no METAR or TAF; EGLL (8 NM NE, elevation +3 ft) used
    as proxy". Shared by the LLM context and the text digest."""
    out = []
    for icao, role in terminals.items():
        term = obs.terminal(icao)
        note = term.proxy_note if term is not None else None
        if note:
            out.append(f"{role} {note}")
    return out
