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
    CeilingVisWindow,
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
from weatherbrief.models.alternate_requirement import (
    AlternateRequirement,
    ConditionalGroup,
)

logger = logging.getLogger(__name__)

# Sample offsets within the ETA window (ETA−60 .. ETA+60 min) for the TAF path.
_WINDOW_OFFSETS_MIN = (-60, -30, 0, 30, 60)

# Mandatory display caveats (issue #249). The UI/digest surface these verbatim.
_CAVEATS = [
    "EASA requirements are computed from estimated plate minima expressed as a "
    "range; the Likely/Marginal/Unlikely band reflects that uncertainty. "
    "Forecast ceiling/visibility inputs are real.",
    "Per-candidate qualification uses a TAF when one covers the candidate's ETA "
    "window (D-0), and NWP consensus otherwise.",
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


def _fmt_validity(vs, ve) -> str | None:
    """Format a trend's validity as the TAF token (e.g. "0406/0408", "FM0409")."""
    def dh(x):
        d = getattr(x, "day", None)
        h = getattr(x, "hour", None)
        if d is None or h is None:
            return None
        try:
            return f"{int(d):02d}{int(h):02d}"
        except (ValueError, TypeError):
            return None
    a = dh(vs) if vs is not None else None
    b = dh(ve) if ve is not None else None
    if a and b:
        return f"{a}/{b}"
    if a:
        return f"FM{a}"
    return None


def _conditional_kind(trend_type: str, prob: int | None) -> str:
    """Label a conditional group: TEMPO / PROB30 / PROB40 / PROB.. TEMPO."""
    has_tempo = "TEMPO" in trend_type or "INTER" in trend_type
    if prob is not None:
        return f"PROB{prob} TEMPO" if has_tempo else f"PROB{prob}"
    return "TEMPO" if has_tempo else "OTHER"


def _extract_conditionals(taf, eta: datetime) -> list[ConditionalGroup]:
    """Descriptive list of TEMPO/PROB groups overlapping the ETA window.

    Does not change the verdict (that comes from ``build_window``); it lets the
    UI show each conditional and how it was treated. ``counted`` mirrors the
    conservative policy used by the window builder (TEMPO and PROB>30 count;
    PROB30 is noted only).
    """
    trends = getattr(taf, "trends", None) or []
    w_start = (eta - timedelta(hours=1)).replace(tzinfo=None)
    w_end = (eta + timedelta(hours=1)).replace(tzinfo=None)
    out: list[ConditionalGroup] = []
    for tr in trends:
        tt = (getattr(tr, "trend_type", None) or "").strip().upper()
        prob = getattr(tr, "probability", None)
        is_conditional = "TEMPO" in tt or tt.startswith("PROB") or "INTER" in tt or prob is not None
        if not is_conditional:
            continue
        vs = getattr(tr, "validity_start", None)
        ve = getattr(tr, "validity_end", None)
        cs = _coerce_dt(vs, eta)
        ce = _coerce_dt(ve, eta)
        # Drop groups wholly outside the window; keep when validity is unknown.
        if cs is not None and ce is not None and (ce < w_start or cs > w_end):
            continue
        out.append(ConditionalGroup(
            kind=_conditional_kind(tt, prob),
            probability=prob,
            ceiling_ft=None if getattr(tr, "cavok", False) else getattr(tr, "ceiling_ft", None),
            visibility_m=getattr(tr, "visibility_meters", None),
            validity=_fmt_validity(vs, ve),
            counted=prob is None or prob > 30,
        ))
    return out


def _build_destination_window(
    taf_raw: str | None,
    eta: datetime,
    nwp_ceiling: float | None,
    nwp_vis: float | None,
) -> tuple[CeilingVisWindow, list[ConditionalGroup]]:
    """Build the destination window (prefer a TAF covering the ETA, else NWP).

    Returns the window plus the descriptive conditional groups (empty on the NWP
    / no-forecast path).
    """
    if taf_raw:
        try:
            from euro_aip.briefing.weather.models import WeatherReport

            taf = WeatherReport.from_taf(taf_raw)
            sample_times = [eta + timedelta(minutes=m) for m in _WINDOW_OFFSETS_MIN]
            instants = _taf_instant_trends(taf, sample_times, ref=eta)
            window = build_window(instants, source="taf")
            if window.has_forecast:
                return window, _extract_conditionals(taf, eta)
        except Exception:
            logger.warning(
                "alternate_requirement: TAF parse/window failed; falling back to NWP",
                exc_info=True,
            )

    if nwp_ceiling is not None or nwp_vis is not None:
        return nwp_window(nwp_ceiling, nwp_vis), []
    return no_forecast_window(), []


def _candidate_taf_window(taf_raw: str | None, eta: datetime) -> CeilingVisWindow | None:
    """Build a TAF-only window for a divert candidate at the divert ETA.

    Reuses the destination window builder (same TEMPO/PROB worst-case policy) but
    ignores the NWP fallback: returns the window only when a TAF actually covered
    the ETA (``source == "taf"``), else ``None`` so the caller keeps the
    NWP-consensus ceiling/vis. A parse failure or an empty TAF also yields
    ``None``.
    """
    if not taf_raw:
        return None
    window, _ = _build_destination_window(taf_raw, eta, None, None)
    # Require the TAF window to carry at least one usable value. A string that
    # parses but yields neither ceiling nor visibility (e.g. an unparseable TAF
    # body) must not flip a candidate to a failing verdict — keep the valid NWP
    # consensus in that case. Real TAFs covering the ETA always carry a base
    # visibility (or CAVOK, which sets a high visibility).
    if (
        window.source == "taf"
        and window.has_forecast
        and (window.ceiling_ft is not None or window.visibility_m is not None)
    ):
        return window
    return None


def _fetch_candidate_tafs(icaos: list[str], airports_db_path: str) -> dict[str, str]:
    """Fetch raw TAFs for candidate ICAOs not already covered by the route corridor.

    Uses the same euro_aip ``RouteWeatherService`` path as ``run_route_weather``
    with a minimal corridor (just enough to resolve the named airports — the
    pattern ``tasks/verification.py`` uses for arbitrary ICAO lists). Returns an
    ``icao -> taf_raw`` map. Never raises: a network/source failure degrades the
    affected candidates to the NWP-consensus path.
    """
    if not icaos:
        return {}
    try:
        from euro_aip.briefing.weather.route_weather import RouteWeatherService

        from weatherbrief.airports import _load_airport_model

        model = _load_airport_model(airports_db_path)
        service = RouteWeatherService()
        result = service.fetch_route_weather(
            route_icaos=list(icaos),
            corridor_nm=1,  # minimal — just resolve the named airports themselves
            model=model,
        )
        out: dict[str, str] = {}
        for raw in result.airports:
            taf = raw.latest_taf
            if taf is not None and taf.raw_text:
                out[raw.icao] = taf.raw_text
        return out
    except Exception:
        logger.warning(
            "alternate_requirement: candidate TAF fetch failed; candidates fall "
            "back to NWP consensus", exc_info=True,
        )
        return {}


def _collect_candidate_tafs(obs, candidate_icaos: set[str], airports_db_path: str) -> dict[str, str]:
    """Assemble ``icao -> taf_raw`` for the candidates: reuse first, fetch the gaps.

    TAFs are only meaningful at D-0; ``obs`` (``route_observations``) is present
    only then, so its absence gates the whole TAF path off for D-1/D-2 (where a
    current TAF would not cover the ETA window anyway).
    """
    if obs is None or not candidate_icaos:
        return {}
    taf_by_icao: dict[str, str] = {}
    # Reuse TAFs already fetched along the route corridor (zero extra network).
    for a in obs.airports:
        if a.icao in candidate_icaos and a.taf_raw:
            taf_by_icao[a.icao] = a.taf_raw
    # Explicitly fetch the candidates the corridor didn't cover.
    gap = [icao for icao in candidate_icaos if icao not in taf_by_icao]
    if gap:
        taf_by_icao.update(_fetch_candidate_tafs(gap, airports_db_path))
    return taf_by_icao


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
    window, conditionals = _build_destination_window(
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
        easa=compute_easa_trigger(window, dest_proxy, approach_label=dest_approach_type),
        caveats=caveats,
        main_body_ceiling_ft=window.main_body_ceiling_ft,
        main_body_visibility_m=window.main_body_visibility_m,
        conditionals=conditionals,
        computed_at=now,
    )

    # 5. Per-candidate qualification. Prefer a TAF covering the candidate's ETA
    #    (D-0 only; reuse the route-corridor TAFs, fetch the gaps), exactly as the
    #    destination trigger above does — falling back to the NWP-consensus
    #    ceiling/vis that run_alternates assembled. `source` records which path
    #    fed the verdict so the UI/digest can badge "(forecast)" vs "(model)".
    taf_by_icao = _collect_candidate_tafs(
        obs, {c.icao for c in alternates.alternates}, airports_db_path,
    )
    for cand in alternates.alternates:
        taf_window = _candidate_taf_window(taf_by_icao.get(cand.icao), eta)
        if taf_window is not None:
            ceiling_ft, vis_m, source = taf_window.ceiling_ft, taf_window.visibility_m, "taf"
        else:
            ceiling_ft, vis_m, source = cand.ceiling_ft, cand.visibility_m, "nwp"
        cand.faa = compute_faa_qual(
            ceiling_ft, vis_m, cand.best_approach_type,
            cand.has_instrument_approach, source=source,
        )
        cand.easa = compute_easa_qual(
            ceiling_ft, vis_m, cand.best_approach_type,
            cand.has_instrument_approach, source=source,
        )
