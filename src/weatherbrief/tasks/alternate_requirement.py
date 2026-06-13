"""Wiring step: regulatory alternate-requirement assessment (issue #249).

A pipeline post-step run **after both** ``run_alternates`` and
``run_route_weather`` (so the destination TAF, if any, is available). It is the
only place that touches euro_aip / the airports DB for this feature; all the
verdict logic lives in the pure ``analysis.alternate_requirement`` module.

Steps:
1. Find the destination observation → ``taf_raw`` → ``WeatherReport.from_taf``.
2. Build the destination ceiling/visibility window (TAF when it covers the ETA,
   else the NWP-consensus fallback stored on ``RouteAlternates``).
3. Look up the destination approach class.
4. Compute the ``AlternateRequirement`` (FAA + EASA triggers).
5. For each candidate, compute FAA / EASA ``AlternateQual`` from its consensus
   ceiling/vis + ``best_approach_type``.
6. Write results back onto ``snapshot.alternates``.

Gated on ``snapshot.alternates is not None`` (the alternates stage gate already
bounds this to D-2 inward + opt-in).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from weatherbrief.analysis.alternate_requirement import (
    TrendView,
    build_window,
    compute_easa_qual,
    compute_easa_trigger,
    compute_faa_qual,
    compute_faa_trigger,
    no_forecast_window,
    nwp_window,
    proxy_for_approach,
)
from weatherbrief.models.alternate_requirement import AlternateRequirement

logger = logging.getLogger(__name__)

# Sample offsets within the ETA window (ETA−60 .. ETA+60 min) for the TAF path.
_WINDOW_OFFSETS_MIN = (-60, -30, 0, 30, 60)

# Mandatory display caveats (issue #249). The UI/digest surface these verbatim.
_CAVEATS = [
    "EASA requirements are computed from estimated plate minima expressed as a "
    "range; the Likely/Marginal/Unlikely band reflects that uncertainty. "
    "Forecast ceiling/visibility inputs are real.",
    "Per-candidate qualification uses NWP consensus, not a TAF.",
    "Planning guidance only — not an operational minima computation or a "
    "go/no-go decision.",
]
_NWP_CAVEAT = (
    "The destination trigger uses an NWP model estimate (no TAF covers the ETA "
    "window yet), not an aviation forecast product."
)
_RELAXED_CAVEAT = (
    "Published-approach data is missing for some candidates; approach-class "
    "estimates are degraded and treated as VFR-only."
)


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    """Shift a (year, month) by ``n`` months."""
    idx = (year * 12 + (month - 1)) + n
    return idx // 12, idx % 12 + 1


def _coerce_dt(value, ref: datetime | None):
    """Coerce a euro_aip validity marker (datetime or day/hour) to a datetime.

    Only used to order prevailing groups within the ETA window (FM supersession),
    so we need a consistent ordering key — but a TAF can straddle a month
    boundary (a 29th-of-month TAF valid to 01/06Z), so a fixed Jan anchor would
    sort an early-of-next-month FM *before* the late-of-this-month base and drop
    the FM's (possibly worse) conditions. Resolve the day/hour to the calendar
    month nearest ``ref`` (the ETA) so the ordering is rollover-safe. Returns a
    naive datetime so the ordering key never mixes aware/naive with the
    ``datetime.min`` sentinel used for groups without a validity_start.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    day = getattr(value, "day", None)
    hour = getattr(value, "hour", None)
    if day is None or hour is None:
        return None
    try:
        day = int(day)
        hour = int(hour)
    except (ValueError, TypeError):
        return None
    if ref is None:
        try:
            return datetime(2000, 1, day, hour)
        except ValueError:
            return None
    ref_naive = ref.replace(tzinfo=None)
    candidates: list[datetime] = []
    for delta in (-1, 0, 1):
        y, m = _add_months(ref_naive.year, ref_naive.month, delta)
        try:
            candidates.append(datetime(y, m, day, hour))
        except ValueError:
            continue  # day out of range for that month (e.g. 31 in a 30-day month)
    if not candidates:
        return None
    return min(candidates, key=lambda d: abs((d - ref_naive).total_seconds()))


def _trend_to_view(obj, *, is_base: bool, ref: datetime | None = None) -> TrendView:
    """Map a euro_aip WeatherReport / trend to the pure ``TrendView``."""
    return TrendView(
        ceiling_ft=getattr(obj, "ceiling_ft", None),
        visibility_m=getattr(obj, "visibility_meters", None),
        cavok=bool(getattr(obj, "cavok", False)),
        # The base group is always the prevailing line (trend_type None).
        trend_type=None if is_base else getattr(obj, "trend_type", None),
        # The base group never has a PROB; don't let a stray attribute on the
        # parent WeatherReport misclassify it as a temporary group (#250 review).
        probability=None if is_base else getattr(obj, "probability", None),
        validity_start=_coerce_dt(getattr(obj, "validity_start", None), ref),
    )


def _taf_instant_trends(
    taf, sample_times, applicable_trends_fn=None, ref: datetime | None = None
) -> list[list[TrendView]]:
    """Build the per-instant applicable-trend lists for the window builder.

    At each sample time the prevailing line is the base TAF group plus any
    applicable FM/BECMG groups; TEMPO/PROB groups come in as candidate-worse.
    ``applicable_trends_fn`` is injectable so the windowing can be tested without
    euro_aip installed. ``ref`` (the ETA) anchors validity-marker rollover so a
    month-straddling TAF orders its FM groups correctly; it defaults to the first
    sample time when not given.
    """
    if applicable_trends_fn is None:  # pragma: no cover - exercised in CI
        from euro_aip.briefing.weather.analysis import WeatherAnalyzer

        applicable_trends_fn = WeatherAnalyzer.applicable_trends

    if ref is None and sample_times:
        ref = sample_times[0]

    base_view = _trend_to_view(taf, is_base=True, ref=ref)
    instants: list[list[TrendView]] = []
    for t in sample_times:
        views = [base_view]
        try:
            for change in applicable_trends_fn(taf, t) or []:
                views.append(_trend_to_view(change, is_base=False, ref=ref))
        except Exception:
            logger.debug("alternate_requirement: applicable_trends failed", exc_info=True)
        instants.append(views)
    return instants


def _build_destination_window(
    taf_raw: str | None,
    eta: datetime,
    nwp_ceiling: float | None,
    nwp_vis: float | None,
):
    """Build the destination window: prefer a TAF that covers the ETA, else NWP."""
    if taf_raw:
        try:
            from euro_aip.briefing.weather.models import WeatherReport

            taf = WeatherReport.from_taf(taf_raw)
            sample_times = [eta + timedelta(minutes=m) for m in _WINDOW_OFFSETS_MIN]
            instants = _taf_instant_trends(taf, sample_times, ref=eta)
            window = build_window(instants, source="taf")
            if window.has_forecast:
                return window
        except Exception:
            logger.warning(
                "alternate_requirement: TAF parse/window failed; falling back to NWP",
                exc_info=True,
            )

    if nwp_ceiling is not None or nwp_vis is not None:
        return nwp_window(nwp_ceiling, nwp_vis)
    return no_forecast_window()


def _destination_approach_class(airports_db_path: str, dest_icao: str) -> tuple[str | None, bool]:
    """Look up the destination's most-precise approach type + IAP presence.

    Mirrors the per-candidate approach query in ``tasks/alternates.py``.
    """
    try:
        from weatherbrief.airports import _load_airport_model

        model = _load_airport_model(airports_db_path)
        airport = model.airports.get(dest_icao)
        if airport is None:
            return None, False
        approaches = airport.procedures_query.approaches()
        if not approaches.exists():
            return None, False
        best = approaches.most_precise()
        return (best.approach_type if best is not None else None), True
    except Exception:
        logger.debug(
            "alternate_requirement: destination approach lookup failed for %s",
            dest_icao, exc_info=True,
        )
        return None, False


def run_alternate_requirement(snapshot, airports_db_path: str, *, now: datetime | None = None):
    """Compute and attach the regulatory alternate-requirement assessment.

    Mutates ``snapshot.alternates`` in place (destination trigger +
    per-candidate qualification). No-op when the alternates stage didn't run.
    """
    alternates = getattr(snapshot, "alternates", None)
    if alternates is None:
        return

    now = now or datetime.now(timezone.utc)
    route = snapshot.route
    dest_icao = route.destination.icao
    eta = alternates.eta or now

    # 1. Destination TAF (only present on D-0 via run_route_weather).
    taf_raw = None
    obs = getattr(snapshot, "route_observations", None)
    if obs is not None:
        for a in obs.airports:
            if a.icao == dest_icao and a.taf_raw:
                taf_raw = a.taf_raw
                break

    # 2. Destination window (TAF preferred, else NWP-consensus fallback).
    window = _build_destination_window(
        taf_raw, eta,
        alternates.destination_ceiling_ft,
        alternates.destination_visibility_m,
    )

    # 3. Destination approach class.
    dest_approach_type, dest_has_iap = _destination_approach_class(airports_db_path, dest_icao)
    dest_proxy = proxy_for_approach(dest_approach_type, dest_has_iap)

    # 4. Destination trigger (FAA + EASA).
    caveats = list(_CAVEATS)
    if window.source == "nwp":
        caveats.append(_NWP_CAVEAT)
    if alternates.approach_filter_relaxed:
        caveats.append(_RELAXED_CAVEAT)

    alternates.alternate_requirement = AlternateRequirement(
        destination_icao=dest_icao,
        eta=eta,
        faa=compute_faa_trigger(window),
        easa=compute_easa_trigger(window, dest_proxy),
        caveats=caveats,
        computed_at=now,
    )

    # 5. Per-candidate qualification from each candidate's consensus ceiling/vis.
    for cand in alternates.alternates:
        cand.faa = compute_faa_qual(
            cand.ceiling_ft, cand.visibility_m, cand.best_approach_type,
            cand.has_instrument_approach,
        )
        cand.easa = compute_easa_qual(
            cand.ceiling_ft, cand.visibility_m, cand.best_approach_type,
            cand.has_instrument_approach,
        )
