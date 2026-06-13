"""Pure functions for the regulatory alternate-requirement assessment (issue #249).

No I/O — every function here is unit-testable in isolation. The euro_aip TAF
parsing and the snapshot wiring live in ``tasks/alternate_requirement.py``; this
module only reasons over already-extracted ceiling/visibility values and the
approach-class proxy table.

Two regimes:

* **FAA (14 CFR 91.169)** — fixed regulatory thresholds, so every band
  collapses (``req_lo == req_hi``) and verdicts are only ever the two extremes
  (Yes/No, never Marginal). The only proxy is the precision-vs-non-precision
  classification, kept conservative (ILS-only = precision).
* **EASA Part-NCO** — derives thresholds from the (unknown) plate minima plus
  margins, expressed as a range; the band yields Likely / Marginal / Unlikely.

The forecast ceiling/visibility *inputs* are always real. Only the *plate-minima
thresholds* (which drive the EASA band) are estimated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from weatherbrief.models.alternate_requirement import (
    AlternateQual,
    BandVerdict,
    CriterionAssessment,
    RegAlternateTrigger,
    TriggerVerdict,
)
from weatherbrief.units import M_PER_SM

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning surface: estimated plate minima per approach class (issue #249).
#
# We lack the published minima, so each approach class's DH/MDH and published
# visibility are estimated as a RANGE [lo, hi]. The lo ends are the PANS-OPS /
# regulatory best case; the hi ends are conservative category estimates. The
# Marginal band's width is set entirely by these ranges — widen upward when
# unsure, never narrow so a marginal case reads as Likely.
#
# Keyed on the values that actually exist in nav.db (procedures.approach_type).
# There is NO LPV/LNAV granularity in the data (approach_type is the ICAO chart
# family; minima lines live inside the chart), so the GNSS bucket deliberately
# spans LPV-best to LNAV-only-worst and the Marginal band absorbs the ambiguity.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApproachProxy:
    """Estimated plate-minima range for one approach class."""

    dh_lo: float  # decision/minimum height, best case (ft)
    dh_hi: float  # decision/minimum height, worst case (ft)
    vis_lo: float  # published visibility, best case (m)
    vis_hi: float  # published visibility, worst case (m)
    faa_precision: bool  # FAA 600-2 (precision) vs 800-2 (non-precision) branch


# Precision (ILS only — the sole FAA precision branch).
_PRECISION = ApproachProxy(dh_lo=200, dh_hi=300, vis_lo=550, vis_hi=1500, faa_precision=True)
# RNP / RNAV (GNSS) — no confirmable LPV line in the data, so a wide bucket.
_GNSS = ApproachProxy(dh_lo=250, dh_hi=600, vis_lo=1000, vis_hi=2000, faa_precision=False)
# Non-precision (VOR / NDB / LOC) and the unknown/other fallback.
_NONPRECISION = ApproachProxy(dh_lo=400, dh_hi=900, vis_lo=1500, vis_hi=3000, faa_precision=False)

APPROACH_CLASS_PROXY: dict[str, ApproachProxy] = {
    "ILS": _PRECISION,
    "RNP": _GNSS,
    "RNAV": _GNSS,
    "VOR": _NONPRECISION,
    "NDB": _NONPRECISION,
    "LOC": _NONPRECISION,
}
# Empty / NULL / TACAN / any unmapped string → most-demanding (non-precision).
_DEFAULT_PROXY = _NONPRECISION

# EASA NCO.OP.143 alternate-SELECTION planning minima (does candidate X qualify
# as a destination alternate? — see compute_easa_qual). Tiered on the approach
# DH; visibility is a FIXED value per tier (not the published-vis range):
#   DH  < 250 ft (ILS):                 ceiling >= DH + 200 ft, vis >= 1500 m
#   DH >= 250 ft (RNP / non-precision): ceiling >= DH + 400 ft, vis >= 3000 m
#   no IAP:                             ceiling >= 2000 ft,      vis >= 5000 m
# We tier by class (ILS is the only <250 ft class; everything else is treated as
# the more-demanding >=250 ft tier, conservative given LPV/LNAV ambiguity).
EASA_ALT_LOWDH_CEILING_MARGIN_FT = 200.0
EASA_ALT_LOWDH_VIS_M = 1500.0
EASA_ALT_HIGHDH_CEILING_MARGIN_FT = 400.0
EASA_ALT_HIGHDH_VIS_M = 3000.0
EASA_ALT_NOIAP_CEILING_FT = 2000.0  # or min safe IFR altitude (unavailable) if higher
EASA_ALT_NOIAP_VIS_M = 5000.0

# EASA NCO.OP.140 destination-alternate TRIGGER (is an alternate required at the
# destination?). A DIFFERENT, higher bar than selection minima: no alternate is
# required only if ceiling >= DH/MDH + 1000 ft AND visibility >= 5000 m over the
# ETA window; with no IAP the field must be VMC. Do NOT confuse with the +200
# selection margin above — using the selection minima here lets IFR destinations
# wrongly pass (issue: EASA said "not required" for an IFR field).
EASA_DEST_CEILING_MARGIN_FT = 1000.0  # ceiling >= DH/MDH + 1000 ft
EASA_DEST_VIS_M = 5000.0  # visibility >= 5000 m (fixed)
EASA_DEST_NOIAP_CEILING_FT = 1500.0  # no IAP -> require VMC (proxy ceiling)

# FAA fixed thresholds (14 CFR 91.169). Statute miles for visibility.
FAA_TRIGGER_CEILING_FT = 2000.0
FAA_TRIGGER_VIS_SM = 3.0
FAA_ALT_PRECISION_CEILING_FT = 600.0  # ILS only
FAA_ALT_NONPRECISION_CEILING_FT = 800.0
FAA_ALT_VIS_SM = 2.0
FAA_ALT_VFR_CEILING_FT = 1000.0  # no-IAP VFR proxy
FAA_ALT_VFR_VIS_SM = 3.0

# A ceiling forecast of ``None`` means "no ceiling layer" (clear / good); map it
# to +inf for the band comparison so it clears every requirement.
_NO_CEILING = float("inf")


def proxy_for_approach(approach_type: str | None, has_iap: bool = True) -> ApproachProxy | None:
    """Return the estimated plate-minima range for an approach class.

    ``None`` means "no instrument approach" — the caller applies the VFR proxy.
    An unknown/unmapped ``approach_type`` (with an IAP present) degrades to the
    most-demanding non-precision range, never a more permissive one.
    """
    if not has_iap:
        return None
    if not approach_type:
        return _DEFAULT_PROXY
    return APPROACH_CLASS_PROXY.get(approach_type.strip().upper(), _DEFAULT_PROXY)


def easa_alt_ceiling_band(proxy: ApproachProxy) -> tuple[float, float]:
    """EASA NCO.OP.143 alternate ceiling band (ft): DH + 200 (ILS) or + 400."""
    margin = (
        EASA_ALT_LOWDH_CEILING_MARGIN_FT
        if proxy.faa_precision
        else EASA_ALT_HIGHDH_CEILING_MARGIN_FT
    )
    return (proxy.dh_lo + margin, proxy.dh_hi + margin)


def easa_alt_vis(proxy: ApproachProxy) -> float:
    """EASA NCO.OP.143 alternate visibility (m): 1500 (ILS) or 3000 (otherwise)."""
    return EASA_ALT_LOWDH_VIS_M if proxy.faa_precision else EASA_ALT_HIGHDH_VIS_M


# ---------------------------------------------------------------------------
# Verdict primitives. ``req_lo == req_hi`` collapses the band (FAA), so only the
# two extremes are reachable — never Marginal.
# ---------------------------------------------------------------------------


def band_qualification(forecast: float | None, req_lo: float, req_hi: float) -> BandVerdict:
    """Qualification verdict (higher forecast is better — alternate eligible?).

    ``forecast=None`` is a missing/unparseable value → conservative fail.
    """
    if forecast is None:
        return BandVerdict.UNLIKELY
    if forecast >= req_hi:
        return BandVerdict.LIKELY
    if forecast < req_lo:
        return BandVerdict.UNLIKELY
    return BandVerdict.MARGINAL


def band_trigger(forecast: float | None, req_lo: float, req_hi: float) -> TriggerVerdict:
    """Trigger verdict (lower forecast forces an alternate — alternate required?).

    ``forecast=None`` is a missing/unparseable value → conservative require.
    """
    if forecast is None:
        return TriggerVerdict.REQUIRED
    if forecast >= req_hi:
        return TriggerVerdict.NOT_REQUIRED
    if forecast < req_lo:
        return TriggerVerdict.REQUIRED
    return TriggerVerdict.MARGINAL


_QUAL_RANK = {BandVerdict.LIKELY: 0, BandVerdict.MARGINAL: 1, BandVerdict.UNLIKELY: 2}
_TRIG_RANK = {
    TriggerVerdict.NOT_REQUIRED: 0,
    TriggerVerdict.MARGINAL: 1,
    TriggerVerdict.REQUIRED: 2,
}


def combine_qual(verdicts: list[BandVerdict]) -> BandVerdict:
    """Worst-of-criteria: any Unlikely → Unlikely; else any Marginal → Marginal."""
    if not verdicts:
        return BandVerdict.UNLIKELY
    return max(verdicts, key=lambda v: _QUAL_RANK[v])


def combine_trigger(verdicts: list[TriggerVerdict]) -> TriggerVerdict:
    """Worst-of-criteria: any Required → Required; else any Marginal → Marginal."""
    if not verdicts:
        return TriggerVerdict.REQUIRED
    return max(verdicts, key=lambda v: _TRIG_RANK[v])


def _ceiling_for_band(ceiling_ft: float | None) -> float | None:
    """Map a window ceiling to the value used for the band comparison.

    ``None`` here means "no ceiling layer" (clear) and clears every requirement.
    The genuine "no forecast at all" case is handled upstream (``has_forecast``),
    so ``None`` is never the missing-data case once we reach a criterion.
    """
    return _NO_CEILING if ceiling_ft is None else ceiling_ft


# ---------------------------------------------------------------------------
# Forecast window (ceiling + visibility over the ETA window). Pure: the euro_aip
# TAF → TrendView extraction is in tasks/, and feeds ``build_window`` here.
# ---------------------------------------------------------------------------


@dataclass
class TrendView:
    """euro_aip-agnostic view of one TAF trend group needed for windowing."""

    ceiling_ft: float | None = None  # None = no BKN/OVC layer (clear, good)
    visibility_m: float | None = None
    cavok: bool = False  # CAVOK / SKC / NSC → no ceiling constraint, vis good
    trend_type: str | None = None  # None/"BASE"/"FM"/"BECMG" (prevailing) | "TEMPO"/"PROB"/"INTER"
    probability: int | None = None  # PROB %
    validity_start: datetime | None = None  # used to pick the latest prevailing group


@dataclass
class CeilingVisWindow:
    """Worst-case ceiling/visibility over the ETA window.

    ``ceiling_ft=None`` means no ceiling layer over the whole window (clear,
    good); a finite value is the lowest ceiling. ``visibility_m=None`` means the
    visibility could not be determined (conservative fail) — distinct from a
    clear ceiling. ``has_forecast=False`` short-circuits to Required/Unlikely.
    """

    ceiling_ft: float | None
    visibility_m: float | None
    source: str  # "taf" | "nwp" | "none"
    triggered_by_tempo: bool = False
    has_forecast: bool = True
    # Steady-state (prevailing main body / FM / BECMG) worst over the window,
    # before TEMPO/PROB are applied. Lets callers show "main body X, but a
    # temporary group brings it to Y". None on the NWP / no-forecast path.
    main_body_ceiling_ft: float | None = None
    main_body_visibility_m: float | None = None

    @property
    def visibility_sm(self) -> float | None:
        """Visibility in statute miles (for FAA), or ``None`` if undetermined."""
        return None if self.visibility_m is None else self.visibility_m / M_PER_SM


_TEMPORARY_TYPES = {"TEMPO", "PROB", "PROB30", "PROB40", "INTER", "PROB30 TEMPO", "PROB40 TEMPO"}


def _is_temporary(trend: TrendView) -> bool:
    """A TEMPO / PROB / INTER group (candidate-worse), not a prevailing line."""
    tt = (trend.trend_type or "").strip().upper()
    if tt in _TEMPORARY_TYPES:
        return True
    if "TEMPO" in tt or tt.startswith("PROB") or "INTER" in tt:
        return True
    # A bare PROB group with no trend_type but a probability set.
    return trend.probability is not None and tt in ("", "BASE")


def _prob_allowed(trend: TrendView, include_prob30: bool) -> bool:
    """PROB policy: TEMPO and PROB40 honoured; PROB30 disregarded by default."""
    if trend.probability is None:
        return True
    if trend.probability <= 30:
        return include_prob30
    return True


def _eff_ceiling(trend: TrendView) -> float:
    """Effective ceiling for the band comparison (clear / no layer → +inf)."""
    if trend.cavok or trend.ceiling_ft is None:
        return _NO_CEILING
    return trend.ceiling_ft


def _eff_visibility(trend: TrendView) -> float | None:
    """Effective visibility (CAVOK → at-least-9999 m; else the reported value)."""
    if trend.cavok:
        return max(trend.visibility_m or 0.0, 9999.0)
    return trend.visibility_m


def _instant_worst(
    trends: list[TrendView], include_prob30: bool
) -> tuple[float, float | None, float, float | None]:
    """Worst-case at one instant as ``(ceiling, vis, ceiling_prev, vis_prev)``.

    ``ceiling``/``vis`` are the overall (TEMPO-inclusive) worst; ``ceiling_prev``/
    ``vis_prev`` are the prevailing-only worst. The prevailing condition is the
    latest-by-validity FM/BECMG/base group; TEMPO/PROB groups can only make it
    *worse* (never improve it). Returning both lets the window builder decide
    whether the binding value is owed to a temporary group — by comparing the
    overall worst against the worst prevailing line, rather than latching a
    per-instant flag that can go stale across instants (#250 round-3 review).
    """
    prevailing = [t for t in trends if not _is_temporary(t)]
    temporary = [
        t for t in trends if _is_temporary(t) and _prob_allowed(t, include_prob30)
    ]

    gov = None
    if prevailing:
        gov = max(prevailing, key=lambda t: t.validity_start or datetime.min)

    c_prev = _eff_ceiling(gov) if gov is not None else _NO_CEILING
    v_prev = _eff_visibility(gov) if gov is not None else None
    ceiling = c_prev
    vis = v_prev

    for t in temporary:
        ct = _eff_ceiling(t)
        if ct < ceiling:
            ceiling = ct
        vt = _eff_visibility(t)
        if vt is not None and (vis is None or vt < vis):
            vis = vt

    return ceiling, vis, c_prev, v_prev


def build_window(
    instant_trends: list[list[TrendView]],
    *,
    source: str = "taf",
    include_prob30: bool = False,
) -> CeilingVisWindow:
    """Reduce per-instant applicable trends to the worst-case window.

    ``instant_trends`` is the list of applicable trends at each sample time
    (ETA−60/−30/0/+30/+60 min, say). Tracks the lowest ceiling and lowest
    visibility across the window and whether the binding value came from a
    temporary (TEMPO/PROB) group.
    """
    has_forecast = any(instant_trends)
    if not has_forecast:
        return CeilingVisWindow(
            ceiling_ft=None, visibility_m=None, source="none",
            triggered_by_tempo=False, has_forecast=False,
        )

    best_c = _NO_CEILING  # overall (TEMPO-inclusive) worst ceiling
    best_c_prev = _NO_CEILING  # worst prevailing-only ceiling
    best_v: float | None = None  # overall worst visibility
    best_v_prev: float | None = None  # worst prevailing-only visibility

    for trends in instant_trends:
        if not trends:
            continue
        c, v, c_prev, v_prev = _instant_worst(trends, include_prob30)
        if c < best_c:
            best_c = c
        if c_prev < best_c_prev:
            best_c_prev = c_prev
        if v is not None and (best_v is None or v < best_v):
            best_v = v
        if v_prev is not None and (best_v_prev is None or v_prev < best_v_prev):
            best_v_prev = v_prev

    ceiling_out = None if best_c == _NO_CEILING else best_c
    # A criterion is "temporary" only when the window's worst value is strictly
    # worse than the worst *prevailing* line — i.e. a TEMPO/PROB group drives it,
    # not the prevailing condition. Deriving the flag from the two aggregates
    # (instead of latching it per-instant) means a later equally-bad prevailing
    # instant correctly clears a stale TEMPO tag (#250 round-3 review).
    ceiling_tempo = ceiling_out is not None and best_c < best_c_prev
    vis_tempo = best_v is not None and (best_v_prev is None or best_v < best_v_prev)
    triggered_by_tempo = ceiling_tempo or vis_tempo
    return CeilingVisWindow(
        ceiling_ft=ceiling_out,
        visibility_m=best_v,
        source=source,
        triggered_by_tempo=triggered_by_tempo,
        has_forecast=True,
        main_body_ceiling_ft=None if best_c_prev == _NO_CEILING else best_c_prev,
        main_body_visibility_m=best_v_prev,
    )


def nwp_window(ceiling_ft: float | None, visibility_m: float | None) -> CeilingVisWindow:
    """Single-point NWP-consensus fallback window (no TAF available).

    A ``None`` ceiling here means "no ceiling layer" (clear). When both the
    ceiling and visibility are missing we have no usable forecast at all.
    """
    has_forecast = ceiling_ft is not None or visibility_m is not None
    return CeilingVisWindow(
        ceiling_ft=ceiling_ft,
        visibility_m=visibility_m,
        source="nwp" if has_forecast else "none",
        triggered_by_tempo=False,
        has_forecast=has_forecast,
    )


def no_forecast_window() -> CeilingVisWindow:
    """Window for "no TAF and no NWP fallback" → both triggers Required."""
    return CeilingVisWindow(
        ceiling_ft=None, visibility_m=None, source="none",
        triggered_by_tempo=False, has_forecast=False,
    )


# ---------------------------------------------------------------------------
# Formatting helpers for reason strings.
# ---------------------------------------------------------------------------


def _fmt_req(lo: float, hi: float, unit: str) -> str:
    """Format a requirement band: "600 ft" if collapsed, else "~600–1100 ft"."""
    if lo == hi:
        return f"{lo:.0f} {unit}"
    return f"~{lo:.0f}–{hi:.0f} {unit}"


def _fmt_forecast(value: float | None, unit: str, *, is_ceiling: bool) -> str:
    """Format a forecast value, distinguishing "no ceiling" from "missing"."""
    if value is None:
        return "no ceiling" if is_ceiling else "missing"
    return f"{value:.0f} {unit}"


def _criterion(
    label: str,
    unit: str,
    forecast: float | None,
    req_lo: float,
    req_hi: float,
    verdict_value: str,
) -> CriterionAssessment:
    return CriterionAssessment(
        label=label,
        unit=unit,
        forecast=forecast,
        required_min=req_lo,
        required_max=req_hi,
        verdict=verdict_value,
    )


# ---------------------------------------------------------------------------
# Destination triggers (is an alternate required?).
# ---------------------------------------------------------------------------


def _build_trigger(
    regime: str,
    window: CeilingVisWindow,
    *,
    ceiling_lo: float,
    ceiling_hi: float,
    vis_lo: float,
    vis_hi: float,
    vis_unit: str,
) -> RegAlternateTrigger:
    """Assemble a destination trigger for one regime from its requirement bands."""
    vis_forecast = window.visibility_sm if vis_unit == "SM" else window.visibility_m

    if not window.has_forecast:
        ceiling_c = _criterion(
            "ceiling", "ft", None, ceiling_lo, ceiling_hi, TriggerVerdict.REQUIRED.value
        )
        vis_c = _criterion(
            "visibility", vis_unit, None, vis_lo, vis_hi, TriggerVerdict.REQUIRED.value
        )
        return RegAlternateTrigger(
            regime=regime,
            status=TriggerVerdict.REQUIRED,
            reason="no forecast available",
            source="none",
            triggered_by_tempo=False,
            ceiling=ceiling_c,
            visibility=vis_c,
        )

    cverd = band_trigger(_ceiling_for_band(window.ceiling_ft), ceiling_lo, ceiling_hi)
    vverd = band_trigger(vis_forecast, vis_lo, vis_hi)
    status = combine_trigger([cverd, vverd])

    ceiling_c = _criterion(
        "ceiling", "ft", window.ceiling_ft, ceiling_lo, ceiling_hi, cverd.value
    )
    vis_c = _criterion(
        "visibility", vis_unit, vis_forecast, vis_lo, vis_hi, vverd.value
    )
    reason = _trigger_reason(status, window, ceiling_c, vis_c, vis_unit)
    return RegAlternateTrigger(
        regime=regime,
        status=status,
        reason=reason,
        # Invariant: a forecast-present window's source is "taf" or "nwp" (the
        # "none" source only reaches the no-forecast branch above). Pydantic's
        # Literal validation makes a broken invariant loud rather than silent.
        source=window.source,
        triggered_by_tempo=window.triggered_by_tempo,
        ceiling=ceiling_c,
        visibility=vis_c,
    )


def _trigger_reason(
    status: TriggerVerdict,
    window: CeilingVisWindow,
    ceiling_c: CriterionAssessment,
    vis_c: CriterionAssessment,
    vis_unit: str,
) -> str:
    src = "model estimate" if window.source == "nwp" else "forecast"
    if status == TriggerVerdict.NOT_REQUIRED:
        return f"ceiling/visibility comfortably above minima ({src})"
    # Name the binding criterion(s).
    bits: list[str] = []
    if ceiling_c.verdict != TriggerVerdict.NOT_REQUIRED.value:
        bits.append(
            f"ceiling {_fmt_forecast(ceiling_c.forecast, 'ft', is_ceiling=True)} "
            f"vs {_fmt_req(ceiling_c.required_min, ceiling_c.required_max, 'ft')}"
        )
    if vis_c.verdict != TriggerVerdict.NOT_REQUIRED.value:
        bits.append(
            f"vis {_fmt_forecast(vis_c.forecast, vis_unit, is_ceiling=False)} "
            f"vs {_fmt_req(vis_c.required_min, vis_c.required_max, vis_unit)}"
        )
    tempo = " (temporary)" if window.triggered_by_tempo else ""
    return f"{'; '.join(bits)}{tempo} ({src})"


def compute_faa_trigger(window: CeilingVisWindow) -> RegAlternateTrigger:
    """FAA 14 CFR 91.169 destination trigger (2000 ft / 3 SM, binary)."""
    return _build_trigger(
        "faa",
        window,
        ceiling_lo=FAA_TRIGGER_CEILING_FT,
        ceiling_hi=FAA_TRIGGER_CEILING_FT,
        vis_lo=FAA_TRIGGER_VIS_SM,
        vis_hi=FAA_TRIGGER_VIS_SM,
        vis_unit="SM",
    )


def compute_easa_trigger(
    window: CeilingVisWindow, proxy: ApproachProxy | None
) -> RegAlternateTrigger:
    """EASA NCO.OP.140 destination trigger (is an alternate required?).

    No alternate is required only if, over the ETA window, the ceiling is at
    least ``DH/MDH + 1000 ft`` AND visibility is at least 5000 m. With no IAP the
    field must be VMC (proxy ceiling 1500 ft / 5000 m). This is the *need-an-
    alternate* test and is deliberately a higher bar than the alternate-selection
    minima in ``compute_easa_qual`` (DH+200) — an IFR destination requires an
    alternate. The proxied DH range yields a small Marginal zone around the bar.
    """
    if proxy is None:
        cl = ch = EASA_DEST_NOIAP_CEILING_FT
    else:
        cl = proxy.dh_lo + EASA_DEST_CEILING_MARGIN_FT
        ch = proxy.dh_hi + EASA_DEST_CEILING_MARGIN_FT
    vl = vh = EASA_DEST_VIS_M
    return _build_trigger(
        "easa",
        window,
        ceiling_lo=cl,
        ceiling_hi=ch,
        vis_lo=vl,
        vis_hi=vh,
        vis_unit="m",
    )


# ---------------------------------------------------------------------------
# Per-candidate qualification (does this alternate meet alternate minima?).
# ---------------------------------------------------------------------------


def _qual_reason(
    verdict: BandVerdict,
    ceiling_c: CriterionAssessment,
    vis_c: CriterionAssessment,
    vis_unit: str,
    *,
    vfr_only: bool,
) -> str:
    note = "VFR only (no instrument approach); " if vfr_only else ""
    return (
        f"{note}ceiling {_fmt_forecast(ceiling_c.forecast, 'ft', is_ceiling=True)} "
        f"vs {_fmt_req(ceiling_c.required_min, ceiling_c.required_max, 'ft')}; "
        f"vis {_fmt_forecast(vis_c.forecast, vis_unit, is_ceiling=False)} "
        f"vs {_fmt_req(vis_c.required_min, vis_c.required_max, vis_unit)} "
        f"→ {verdict.value}"
    )


def compute_faa_qual(
    ceiling_ft: float | None,
    visibility_m: float | None,
    approach_type: str | None,
    has_iap: bool,
) -> AlternateQual:
    """FAA per-candidate alternate minima (ILS→600-2, else→800-2, no-IAP→VFR)."""
    proxy = proxy_for_approach(approach_type, has_iap)
    if not has_iap or proxy is None:
        cl = ch = FAA_ALT_VFR_CEILING_FT
        vl = vh = FAA_ALT_VFR_VIS_SM
        vfr_only = True
    elif proxy.faa_precision:
        cl = ch = FAA_ALT_PRECISION_CEILING_FT
        vl = vh = FAA_ALT_VIS_SM
        vfr_only = False
    else:
        cl = ch = FAA_ALT_NONPRECISION_CEILING_FT
        vl = vh = FAA_ALT_VIS_SM
        vfr_only = False

    vis_sm = None if visibility_m is None else visibility_m / M_PER_SM
    cverd = band_qualification(_ceiling_for_band(ceiling_ft), cl, ch)
    vverd = band_qualification(vis_sm, vl, vh)
    verdict = combine_qual([cverd, vverd])

    ceiling_c = _criterion("ceiling", "ft", ceiling_ft, cl, ch, cverd.value)
    vis_c = _criterion("visibility", "SM", vis_sm, vl, vh, vverd.value)
    return AlternateQual(
        regime="faa",
        verdict=verdict,
        reason=_qual_reason(verdict, ceiling_c, vis_c, "SM", vfr_only=vfr_only),
        ceiling=ceiling_c,
        visibility=vis_c,
    )


def compute_easa_qual(
    ceiling_ft: float | None,
    visibility_m: float | None,
    approach_type: str | None,
    has_iap: bool,
) -> AlternateQual:
    """EASA NCO.OP.143 alternate planning minima.

    DH<250 ft (ILS) → ceiling DH+200 / vis 1500 m; DH>=250 ft (RNP / non-
    precision) → ceiling DH+400 / vis 3000 m; no IAP → ceiling 2000 ft / vis
    5000 m. The DH proxy range yields a ceiling band; visibility is a fixed bar.
    """
    proxy = proxy_for_approach(approach_type, has_iap)
    if not has_iap or proxy is None:
        cl = ch = EASA_ALT_NOIAP_CEILING_FT
        vl = vh = EASA_ALT_NOIAP_VIS_M
        vfr_only = True
    else:
        cl, ch = easa_alt_ceiling_band(proxy)
        vl = vh = easa_alt_vis(proxy)
        vfr_only = False

    cverd = band_qualification(_ceiling_for_band(ceiling_ft), cl, ch)
    vverd = band_qualification(visibility_m, vl, vh)
    verdict = combine_qual([cverd, vverd])

    ceiling_c = _criterion("ceiling", "ft", ceiling_ft, cl, ch, cverd.value)
    vis_c = _criterion("visibility", "m", visibility_m, vl, vh, vverd.value)
    return AlternateQual(
        regime="easa",
        verdict=verdict,
        reason=_qual_reason(verdict, ceiling_c, vis_c, "m", vfr_only=vfr_only),
        ceiling=ceiling_c,
        visibility=vis_c,
    )
