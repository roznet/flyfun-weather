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
