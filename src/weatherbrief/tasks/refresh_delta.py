"""Deterministic worsening detection for the cheap D-0 real-time refresh.

The realtime refresh (``run_realtime_refresh``) re-fetches METAR/TAF and route
SIGMETs but does NOT regenerate the LLM digest, so a freshly-appeared hazard
would otherwise be visible in the tables while the AI assessment stayed silent.
``compute_refresh_delta`` closes that gap cheaply: it diffs the previous state
against the new one and reports only what got *worse*, which the UI surfaces as
a banner. No tokens spent.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from weatherbrief.models.observations import (
    RefreshDelta,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
)

# VFR < MVFR < IFR < LIFR — higher rank is worse.
_CATEGORY_RANK = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}
_SEQ_RE = re.compile(r"SIGMET\s+(\w+)", re.IGNORECASE)


def _category_rank(cat: str | None) -> int | None:
    if not cat:
        return None
    return _CATEGORY_RANK.get(cat.upper())


def _is_severe(s: SigmetAlongRoute) -> bool:
    return bool(s.qualifier) and s.qualifier.upper() == "SEV"


def _sigmet_seq(s: SigmetAlongRoute) -> str | None:
    """Sequence id (e.g. ``13`` from "LTBB SIGMET 13"), parsed from raw text."""
    m = _SEQ_RE.search(s.raw_text or "")
    return m.group(1) if m else None


def _sigmet_key(s: SigmetAlongRoute) -> tuple:
    """Stable identity across refreshes: prefer FIR + sequence, else fall back
    to FIR + hazard + validity so a re-issued SIGMET isn't mistaken for new."""
    seq = _sigmet_seq(s)
    if seq is not None:
        return (s.fir_id, seq)
    return (s.fir_id, s.hazard, s.valid_from)


def _sigmet_label(s: SigmetAlongRoute) -> str:
    seq = _sigmet_seq(s)
    hazard = " ".join(p for p in (s.qualifier, s.hazard) if p) or "SIGMET"
    fir = f"{s.fir_id} {seq}" if seq else s.fir_id
    return f"{fir}: {hazard}"


def compute_refresh_delta(
    *,
    old_obs: RouteObservations | None,
    new_obs: RouteObservations | None,
    old_sigmets: RouteSigmets | None,
    new_sigmets: RouteSigmets | None,
) -> RefreshDelta:
    """Report only what got worse between the previous and refreshed state.

    A ``None`` *old* side means there is no prior state to compare against
    (e.g. a pre-SIGMET pack, or the very first fetch) — that dimension is
    skipped rather than flagged, so an initial fetch never raises the banner.
    A ``None`` *new* side (fetch failed) is likewise skipped.
    """
    messages: list[str] = []

    # --- SIGMETs: new arrivals and escalation to SEV ---
    if old_sigmets is not None and new_sigmets is not None:
        old_by_key = {_sigmet_key(s): s for s in old_sigmets.sigmets}
        for s in new_sigmets.sigmets:
            prev = old_by_key.get(_sigmet_key(s))
            if prev is None:
                prefix = "New SEV SIGMET" if _is_severe(s) else "New SIGMET"
                messages.append(f"{prefix} {_sigmet_label(s)}")
            elif _is_severe(s) and not _is_severe(prev):
                messages.append(f"SIGMET {_sigmet_label(s)} escalated to SEV")

    # --- Observations: METAR / TAF flight-category degradation per airport ---
    if old_obs is not None and new_obs is not None:
        old_apts = {a.icao: a for a in old_obs.airports}
        for a in new_obs.airports:
            prev = old_apts.get(a.icao)
            if prev is None:
                continue
            new_m, old_m = _category_rank(a.metar_flight_category), _category_rank(prev.metar_flight_category)
            if new_m is not None and old_m is not None and new_m > old_m:
                messages.append(
                    f"{a.icao} METAR: {prev.metar_flight_category} → {a.metar_flight_category}"
                )
            new_t, old_t = _category_rank(a.taf_flight_category_at_eta), _category_rank(prev.taf_flight_category_at_eta)
            if new_t is not None and old_t is not None and new_t > old_t:
                messages.append(
                    f"{a.icao} TAF: {prev.taf_flight_category_at_eta} → {a.taf_flight_category_at_eta}"
                )

    return RefreshDelta(
        worsened=bool(messages),
        messages=messages,
        computed_at=datetime.now(timezone.utc),
    )
