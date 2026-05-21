"""Tests for compute_refresh_delta — deterministic worsening detection used by
the real-time refresh banner (#168 follow-on)."""

from datetime import datetime

from weatherbrief.models.observations import (
    AirportObservation,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
)
from weatherbrief.tasks.refresh_delta import compute_refresh_delta


def _obs(airports):
    return RouteObservations(
        corridor_nm=30.0,
        fetch_time=datetime(2026, 5, 21, 12),
        airports_found=len(airports),
        airports_with_metar=len(airports),
        airports_with_taf=len(airports),
        airports=airports,
    )


def _apt(icao, metar=None, taf=None):
    return AirportObservation(
        icao=icao,
        distance_from_route_nm=1.0,
        nearest_waypoint_icao=icao,
        metar_flight_category=metar,
        taf_flight_category_at_eta=taf,
    )


def _sigmets(sigmets):
    return RouteSigmets(
        corridor_nm=50.0, fetch_time=datetime(2026, 5, 21, 12), sigmets=sigmets,
    )


def _sig(seq, qualifier="EMBD", hazard="TS", fir="LTBB"):
    return SigmetAlongRoute(
        fir_id=fir, hazard=hazard, qualifier=qualifier,
        raw_text=f"{fir} SIGMET {seq} VALID 211644/211918",
    )


# --- SIGMET worsening ---

def test_new_sigmet_flags_worsened():
    delta = compute_refresh_delta(
        old_obs=None, new_obs=None,
        old_sigmets=_sigmets([]), new_sigmets=_sigmets([_sig("13")]),
    )
    assert delta.worsened
    assert any("New SIGMET" in m and "LTBB 13" in m for m in delta.messages)


def test_new_severe_sigmet_labels_sev():
    delta = compute_refresh_delta(
        old_obs=None, new_obs=None,
        old_sigmets=_sigmets([]), new_sigmets=_sigmets([_sig("14", qualifier="SEV")]),
    )
    assert delta.worsened
    assert any("New SEV SIGMET" in m for m in delta.messages)


def test_sigmet_escalation_to_sev_flags_worsened():
    delta = compute_refresh_delta(
        old_obs=None, new_obs=None,
        old_sigmets=_sigmets([_sig("13", qualifier="EMBD")]),
        new_sigmets=_sigmets([_sig("13", qualifier="SEV")]),
    )
    assert delta.worsened
    assert any("escalated to SEV" in m for m in delta.messages)


def test_removed_sigmet_is_not_worsening():
    delta = compute_refresh_delta(
        old_obs=None, new_obs=None,
        old_sigmets=_sigmets([_sig("13")]), new_sigmets=_sigmets([]),
    )
    assert not delta.worsened
    assert delta.messages == []


def test_unchanged_sigmet_is_not_worsening():
    delta = compute_refresh_delta(
        old_obs=None, new_obs=None,
        old_sigmets=_sigmets([_sig("13")]), new_sigmets=_sigmets([_sig("13")]),
    )
    assert not delta.worsened


# --- Observation worsening ---

def test_metar_degradation_flags_worsened():
    delta = compute_refresh_delta(
        old_obs=_obs([_apt("LBSF", metar="VFR")]),
        new_obs=_obs([_apt("LBSF", metar="IFR")]),
        old_sigmets=None, new_sigmets=None,
    )
    assert delta.worsened
    assert any("LBSF METAR" in m and "VFR" in m and "IFR" in m for m in delta.messages)


def test_metar_improvement_is_not_worsening():
    delta = compute_refresh_delta(
        old_obs=_obs([_apt("LBSF", metar="IFR")]),
        new_obs=_obs([_apt("LBSF", metar="VFR")]),
        old_sigmets=None, new_sigmets=None,
    )
    assert not delta.worsened


def test_taf_degradation_flags_worsened():
    delta = compute_refresh_delta(
        old_obs=_obs([_apt("LTBU", taf="MVFR")]),
        new_obs=_obs([_apt("LTBU", taf="IFR")]),
        old_sigmets=None, new_sigmets=None,
    )
    assert delta.worsened
    assert any("LTBU TAF" in m for m in delta.messages)


# --- No prior state / failed fetch: never flags ---

def test_none_old_sigmets_skips_sigmet_diff():
    delta = compute_refresh_delta(
        old_obs=None, new_obs=None,
        old_sigmets=None, new_sigmets=_sigmets([_sig("13", qualifier="SEV")]),
    )
    assert not delta.worsened


def test_none_new_sigmets_skips_sigmet_diff():
    delta = compute_refresh_delta(
        old_obs=None, new_obs=None,
        old_sigmets=_sigmets([_sig("13")]), new_sigmets=None,
    )
    assert not delta.worsened


def test_none_old_obs_skips_obs_diff():
    delta = compute_refresh_delta(
        old_obs=None, new_obs=_obs([_apt("LBSF", metar="LIFR")]),
        old_sigmets=None, new_sigmets=None,
    )
    assert not delta.worsened
