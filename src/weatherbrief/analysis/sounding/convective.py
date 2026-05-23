"""Convective risk assessment from thermodynamic indices and NWP diagnostics.

Pure threshold logic — no MetPy dependency. Takes ThermodynamicIndices
and returns ConvectiveAssessment.
"""

from __future__ import annotations

from typing import NamedTuple

from weatherbrief.models import (
    ConvectiveAssessment,
    ConvectiveRegime,
    ConvectiveRisk,
    NWPCloudDiagnostics,
    ThermodynamicIndices,
)

# CAPE thresholds (J/kg) for risk classification — European-calibrated.
# European convection produces severe weather at lower CAPE than the US;
# CAPE > 2000 J/kg is already exceptional over western Europe.
_CAPE_THRESHOLDS = [
    (2000, ConvectiveRisk.EXTREME),
    (1000, ConvectiveRisk.HIGH),
    (300, ConvectiveRisk.MODERATE),
    (50, ConvectiveRisk.LOW),
]

# CIN threshold above which convection is capped
CIN_CAP_THRESHOLD = -200  # J/kg (strong cap)

# Regime classification boundaries.
# CAPE (J/kg): below LOW → thermal-dominated; at/above HIGH → high-instability.
# CIN (J/kg): at/below CAP → a capping inversion significant enough that
# initiation depends on whether large-scale ascent erodes the cap.
REGIME_CAPE_LOW = 300
REGIME_CAPE_HIGH = 800
REGIME_CIN_CAP = -50

# 700 hPa omega trigger thresholds (Pa/s; negative = ascent).
# Tuned for synoptic-scale fields (ECMWF/GFS). High-resolution models (e.g.
# ICON-EU) can show larger mesoscale omega from orographic/sea-breeze lift that
# doesn't erode a synoptic cap — to be revisited with the calibration work.
OMEGA_ASCENT = -0.1      # clear large-scale ascent
OMEGA_SUBSIDENCE = 0.05  # large-scale subsidence

# Minimum MU−SB excess (J/kg) to call instability "elevated" — the most-unstable
# parcel originates meaningfully above the surface (e.g. warm air overrunning a
# stable/marine boundary layer). MU ≥ SB always, so a small excess is just the
# surface parcel; a large one means a separate unstable layer aloft that matters
# to an aircraft in cruise even when surface-based convection is unlikely.
ELEVATED_MU_SB_EXCESS = 200


def effective_cape(indices: ThermodynamicIndices) -> float | None:
    """Return the most relevant CAPE for convective risk.

    Uses max of MetPy-computed variants (SB-CAPE, MU-CAPE, ML-CAPE).
    Falls back to NWP raw CAPE only when no MetPy variant is available,
    because model-native CAPE can diverge significantly from sounding-
    derived values (different parcel selection, virtual temperature
    corrections, etc.) and including it in the max pool could inflate
    convective risk when the sounding doesn't support it.
    """
    metpy_values = [
        v for v in (
            indices.cape_surface_jkg,
            indices.cape_most_unstable_jkg,
            indices.cape_mixed_layer_jkg,
        )
        if v is not None
    ]
    if metpy_values:
        return max(metpy_values)
    # Fallback: NWP raw CAPE when no MetPy variants available
    return indices.nwp_cape_jkg


# Backward-compatible alias (private name used by callers during transition)
_effective_cape = effective_cape


def _severity_modifiers(indices: ThermodynamicIndices, cape: float | None) -> list[str]:
    """Compute severity modifier strings from indices."""
    modifiers: list[str] = []
    shear_06 = indices.bulk_shear_0_6km_kt

    if shear_06 is not None:
        if shear_06 > 40:
            modifiers.append("strong shear (>40kt 0-6km): organized/supercell potential")
        elif shear_06 > 25:
            modifiers.append("moderate shear (>25kt 0-6km): multicell potential")

    if (
        indices.freezing_level_ft is not None
        and indices.freezing_level_ft > 11500  # ~3500m
        and cape is not None
        and cape > 1000
    ):
        modifiers.append("high freezing level + CAPE: hail risk")

    if indices.k_index is not None and indices.k_index > 35:
        modifiers.append(f"high K-index ({indices.k_index}): thunderstorm potential")

    if indices.total_totals is not None and indices.total_totals > 55:
        modifiers.append(f"high Total Totals ({indices.total_totals}): severe thunderstorm potential")

    if indices.lifted_index is not None and indices.lifted_index < -6:
        modifiers.append(f"strongly negative LI ({indices.lifted_index}): extreme instability")

    return modifiers


def _risk_from_cape(cape: float | None, indices: ThermodynamicIndices) -> ConvectiveRisk:
    """Base risk purely from effective CAPE (with MARGINAL for shallow convection)."""
    if cape is None:
        return ConvectiveRisk.NONE
    for threshold, level in _CAPE_THRESHOLDS:
        if cape >= threshold:
            return level
    # Marginal: any CAPE > 0 with a defined LFC/EL → shallow convection
    if cape > 0 and indices.lfc_altitude_ft is not None and indices.el_altitude_ft is not None:
        return ConvectiveRisk.MARGINAL
    return ConvectiveRisk.NONE


def classify_regime(cape: float | None, cin: float | None) -> ConvectiveRegime | None:
    """Classify the dominant convective regime from CAPE and CIN.

    Returns None when CAPE is unavailable (insufficient data to characterise
    the regime). The boundaries are deliberately simple, hard cutoffs — the
    per-regime scoring below, not the classification, carries the nuance.
    """
    if cape is None:
        return None
    if cape >= REGIME_CAPE_HIGH:
        if cin is not None and cin <= REGIME_CIN_CAP:
            return ConvectiveRegime.LOADED_GUN
        return ConvectiveRegime.ACTIVE
    if cape < REGIME_CAPE_LOW:
        return ConvectiveRegime.THERMAL
    return ConvectiveRegime.WEAK_INSTABILITY


_RISK_LEVELS = tuple(ConvectiveRisk)


def _down_one(risk: ConvectiveRisk) -> ConvectiveRisk:
    """Drop risk by one ordinal level (clamped at NONE)."""
    idx = _RISK_LEVELS.index(risk)
    return _RISK_LEVELS[idx - 1] if idx > 0 else risk


def _elevated_instability(indices: ThermodynamicIndices) -> bool:
    """True when the most-unstable parcel sits meaningfully above the surface.

    MU-CAPE ≥ SB-CAPE by construction; a large excess means the instability is
    not surface-rooted (elevated convection). Relevant to aviation even when
    surface-based storms are unlikely — an aircraft in cruise can still meet it.
    """
    sb = indices.cape_surface_jkg
    mu = indices.cape_most_unstable_jkg
    if sb is None or mu is None:
        return False
    return mu >= REGIME_CAPE_LOW and (mu - sb) >= ELEVATED_MU_SB_EXCESS


def _realizable_risk(
    potential: float | None,
    indices: ThermodynamicIndices,
    omega_700_pa_s: float | None,
) -> tuple[ConvectiveRisk, str | None]:
    """Severity for the ACTIVE regime, scored on the *realizable* parcel.

    Surface-based / most-unstable CAPE (``potential``) is the latent instability;
    mixed-layer CAPE is what a well-mixed daytime boundary layer can actually
    realize, and is the Europe-preferred measure for surface-based convection.
    We score on ML-CAPE but never drop more than one level below the
    potential-based tier — a high-potential air mass is not dismissed on ML
    alone (it can still go up if something lifts it). Large-scale ascent keeps
    the full potential tier, since ascent can realize the latent instability.

    Returns the risk and, when ML pulled the tier down, a suppressor string.
    """
    risk_potential = _risk_from_cape(potential, indices)
    ml = indices.cape_mixed_layer_jkg
    ascent = omega_700_pa_s is not None and omega_700_pa_s <= OMEGA_ASCENT
    if ml is None or ascent:
        return risk_potential, None

    risk_ml = _risk_from_cape(ml, indices)
    floor = _down_one(risk_potential)
    risk = max(risk_ml, floor, key=_RISK_LEVELS.index)

    note: str | None = None
    if _RISK_LEVELS.index(risk) < _RISK_LEVELS.index(risk_potential):
        sb = indices.cape_surface_jkg
        sb_txt = f" vs SB {sb:.0f}" if sb is not None else ""
        pot_txt = f"{potential:.0f}" if potential is not None else "?"
        note = (
            f"Realizable mixed-layer CAPE {ml:.0f} J/kg{sb_txt} well below the "
            f"potential {pot_txt} J/kg — poorly mixed / shallow low-level "
            "moisture limits convection"
        )
    return risk, note


def _shared_annotations(indices: ThermodynamicIndices) -> tuple[list[str], list[str]]:
    """Moisture / geometry drivers and suppressors common to all regimes."""
    drivers: list[str] = []
    suppressors: list[str] = []

    # Bounded above by the severity-modifier threshold (K > 35) so a single
    # K-index value isn't surfaced twice (driver here + modifier there).
    if indices.k_index is not None and 30 <= indices.k_index <= 35:
        drivers.append(f"Moist mid-levels (K-index {indices.k_index:.0f})")

    if indices.lcl_altitude_ft is not None and indices.lfc_altitude_ft is not None:
        gap = indices.lfc_altitude_ft - indices.lcl_altitude_ft
        # LFC >= LCL is a physical invariant, but MetPy on coarse pressure
        # levels can occasionally return LFC < LCL — guard the lower bound.
        if gap == 0:
            drivers.append("LCL = LFC: no barrier to initiation")
        elif 0 < gap <= 500:
            drivers.append(f"Small LCL–LFC gap (~{gap:.0f} ft): low barrier to initiation")

    if indices.lcl_altitude_ft is not None and indices.lcl_altitude_ft > 8000:
        suppressors.append(
            f"High-based (LCL {indices.lcl_altitude_ft:.0f} ft): reduced surface impact"
        )

    return drivers, suppressors


def _regime_explanation(
    regime: ConvectiveRegime | None,
    risk: ConvectiveRisk,
    cape: float | None,
    cin: float | None,
    omega_700_pa_s: float | None,
    tempered: bool = False,
) -> tuple[ConvectiveRisk, list[str], list[str]]:
    """Apply regime-aware risk adjustment and build drivers/suppressors.

    Only the LOADED_GUN regime adjusts the risk level here (trigger-gated cap
    erosion); the other regimes keep the risk the caller computed (the caller
    scores them on realizable ML-CAPE and applies the generic strong-CIN
    suppression). ``cape`` is the potential (SB/MU) CAPE used for narration.
    ``tempered`` says the caller already pulled an ACTIVE point's tier down on
    ML-CAPE, so the driver text shouldn't also claim "convection readily
    initiates". Returns the (possibly adjusted) risk plus drivers/suppressors.
    """
    if regime is None or cape is None:
        return risk, [], []

    drivers: list[str] = []
    suppressors: list[str] = []
    ascent = omega_700_pa_s is not None and omega_700_pa_s <= OMEGA_ASCENT
    subsidence = omega_700_pa_s is not None and omega_700_pa_s >= OMEGA_SUBSIDENCE

    if regime is ConvectiveRegime.LOADED_GUN:
        # classify_regime guarantees cape >= REGIME_CAPE_HIGH and cin <= REGIME_CIN_CAP,
        # so cin is non-None here — assert it so a future inconsistent caller fails
        # loudly rather than hitting a TypeError in the cin format strings below.
        assert cin is not None
        drivers.append(f"High instability (CAPE {cape:.0f} J/kg)")
        if ascent:
            drivers.append(
                f"Large-scale ascent (ω₇₀₀ {omega_700_pa_s:.2f} Pa/s) may erode the "
                f"cap (CIN {cin:.0f} J/kg)"
            )
        elif omega_700_pa_s is None:
            # No ascent data. A very strong cap (CIN < -200) holds regardless,
            # so it still suppresses; a moderate cap we leave unjudged rather
            # than downgrade on missing data — an unassessable loaded gun is
            # not safely "low risk".
            if cin is not None and cin < CIN_CAP_THRESHOLD:
                risk = _down_one(risk)
                suppressors.append(
                    f"Very strong cap (CIN {cin:.0f} J/kg) inhibits convection"
                )
            else:
                suppressors.append(
                    f"Capping inversion (CIN {cin:.0f} J/kg) — no ascent data to "
                    "assess cap erosion (loaded gun)"
                )
        else:
            # Omega present and not ascending → cap likely holds.
            risk = _down_one(risk)
            suppressors.append(
                f"Capping inversion (CIN {cin:.0f} J/kg), no large-scale ascent "
                f"(ω₇₀₀ {omega_700_pa_s:.2f} Pa/s) — initiation inhibited (loaded gun)"
            )
    elif regime is ConvectiveRegime.ACTIVE:
        if tempered:
            # Tier already pulled down on realizable ML-CAPE; the caller adds the
            # suppressor that explains why. Don't claim ready initiation here.
            drivers.append(f"High potential instability (CAPE {cape:.0f} J/kg)")
        else:
            cap_note = f", weak/absent cap (CIN {cin:.0f} J/kg)" if cin is not None else ""
            drivers.append(
                f"High instability (CAPE {cape:.0f} J/kg){cap_note} — convection readily initiates"
            )
        if ascent:
            drivers.append(
                f"Large-scale ascent (ω₇₀₀ {omega_700_pa_s:.2f} Pa/s) reinforces lift"
            )
    elif regime is ConvectiveRegime.WEAK_INSTABILITY:
        drivers.append(f"Moderate instability (CAPE {cape:.0f} J/kg)")
        if ascent:
            drivers.append(
                f"Large-scale ascent (ω₇₀₀ {omega_700_pa_s:.2f} Pa/s) aids initiation"
            )
        elif subsidence:
            suppressors.append(
                f"Large-scale subsidence (ω₇₀₀ {omega_700_pa_s:.2f} Pa/s) suppresses development"
            )
    elif regime is ConvectiveRegime.THERMAL:
        if cape > 0:
            drivers.append(
                "Thermal-dominated regime — daytime heating / orographic lift may "
                "trigger isolated convection"
            )

    return risk, drivers, suppressors


def assess_convective_thermo(
    indices: ThermodynamicIndices,
    omega_700_pa_s: float | None = None,
) -> ConvectiveAssessment:
    """Assess convective risk from thermodynamic indices.

    The *regime* (THERMAL / WEAK_INSTABILITY / LOADED_GUN / ACTIVE) is classified
    from the potential CAPE (max of MetPy SB/MU/ML variants) and CIN, then drives
    regime-appropriate scoring:

    - LOADED_GUN (high potential CAPE under a strong cap): scored on the
      *potential* CAPE and held down one level unless 700 hPa omega shows
      large-scale ascent that could erode the cap. A capped loaded gun is never
      dismissed on mixed-layer CAPE — that is exactly the dangerous case.
    - ACTIVE (high potential CAPE, weak cap): scored on *realizable* mixed-layer
      CAPE (the Europe-preferred surface-convection measure), floored at one
      level below the potential tier so a high-potential air mass is tempered
      but not dismissed. Large-scale ascent keeps the full potential tier. This
      is where surface-based CAPE over-reads a dry, poorly-mixed column.
    - WEAK_INSTABILITY / THERMAL: scored on potential CAPE with the generic
      strong-CIN suppression (intentionally not ML-tempered — see
      designs/future/meteorology-decision.md).

    Independently, ``elevated_convection`` flags a most-unstable parcel sitting
    well above the surface (MU ≫ SB) — convection possible aloft even when the
    surface tier is low. The ``drivers``/``suppressors`` lists make the reasoning
    explicit; ``severe_modifiers`` flag the character of convection if it fires.
    """
    potential = _effective_cape(indices)
    cin = indices.cin_surface_jkg
    regime = classify_regime(potential, cin)

    # Only ACTIVE (high potential, weak cap) is scored on realizable mixed-layer
    # CAPE — that is where surface-based CAPE over-reads a dry / poorly-mixed
    # column the model won't convect. LOADED_GUN scores on potential (the cap,
    # not poor mixing, holds it, and it must not be hidden); WEAK_INSTABILITY /
    # THERMAL keep the potential tier with the generic strong-CIN suppression.
    # Scoping to ACTIVE avoids a route-wide one-level softening (ML ≪ SB almost
    # everywhere) and targets the false-HIGH case directly.
    realizable_note: str | None = None
    if regime is ConvectiveRegime.ACTIVE:
        risk, realizable_note = _realizable_risk(potential, indices, omega_700_pa_s)
    else:
        risk = _risk_from_cape(potential, indices)

    risk, drivers, suppressors = _regime_explanation(
        regime, risk, potential, cin, omega_700_pa_s,
        tempered=realizable_note is not None,
    )
    if realizable_note is not None:
        suppressors.append(realizable_note)

    # Generic strong-CIN suppression for regimes that don't model the cap
    # themselves (LOADED_GUN already gated initiation on the cap above; ACTIVE
    # is weak-cap by definition).
    if regime not in (ConvectiveRegime.LOADED_GUN, ConvectiveRegime.ACTIVE):
        if cin is not None and cin < CIN_CAP_THRESHOLD and risk != ConvectiveRisk.NONE:
            risk = _down_one(risk)
            suppressors.insert(0, f"Strong cap (CIN {cin:.0f} J/kg) suppresses convection")

    # Elevated instability is an additive warning — surfaced regardless of the
    # surface-based tier, since it matters to an aircraft in cruise.
    elevated = _elevated_instability(indices)
    if elevated:
        mu = indices.cape_most_unstable_jkg
        drivers.append(
            f"Elevated instability (MU-CAPE {mu:.0f} J/kg above the surface parcel) "
            "— convection possible aloft"
        )

    shared_drivers, shared_suppressors = _shared_annotations(indices)
    drivers.extend(shared_drivers)
    suppressors.extend(shared_suppressors)

    modifiers = _severity_modifiers(indices, potential)

    # Unified interface: base from LFC (fallback LCL), top from EL
    base_ft = indices.lfc_altitude_ft if indices.lfc_altitude_ft is not None else indices.lcl_altitude_ft
    top_ft = indices.el_altitude_ft

    return ConvectiveAssessment(
        risk_level=risk,
        cape_jkg=potential,
        cin_jkg=cin,
        lcl_altitude_ft=indices.lcl_altitude_ft,
        lfc_altitude_ft=indices.lfc_altitude_ft,
        el_altitude_ft=indices.el_altitude_ft,
        bulk_shear_0_6km_kt=indices.bulk_shear_0_6km_kt,
        lifted_index=indices.lifted_index,
        k_index=indices.k_index,
        total_totals=indices.total_totals,
        severe_modifiers=modifiers,
        regime=regime,
        drivers=drivers,
        suppressors=suppressors,
        elevated_convection=elevated,
        base_ft=base_ft,
        top_ft=top_ft,
        cover_pct=None,
        method="thermo",
    )


# Keep backward-compatible alias
assess_convective = assess_convective_thermo


def assess_convective_nwp(
    indices: ThermodynamicIndices,
    nwp_diagnostics: NWPCloudDiagnostics | None,
) -> ConvectiveAssessment | None:
    """Assess convective risk from NWP model convective cloud parameterization.

    Returns None when nwp_diagnostics is None (GRIB2 data unavailable).
    Risk is driven by convective_cover_pct thresholds. Thermodynamic indices
    are preserved for context. Severity modifiers are computed from the same
    indices as the thermo method.
    """
    if nwp_diagnostics is None:
        return None

    cover = nwp_diagnostics.convective_cover_pct
    cape = _effective_cape(indices)
    modifiers = _severity_modifiers(indices, cape)

    if cover is not None:
        # Full NWP path: risk from CAPE (same as thermo), cover is informational.
        # NWP provides better convective geometry (base/top) than thermo (LFC/EL).
        risk = ConvectiveRisk.NONE
        if cape is not None:
            for threshold, level in _CAPE_THRESHOLDS:
                if cape >= threshold:
                    risk = level
                    break
            if risk == ConvectiveRisk.NONE and cape >= 10:
                risk = ConvectiveRisk.MARGINAL
        method = "nwp"
    elif (
        nwp_diagnostics.convective_base_ft is not None
        and nwp_diagnostics.convective_top_ft is not None
    ):
        # Hybrid path (e.g. ICON-EU): no cover_pct but GRIB base/top exist.
        # Derive risk from CAPE thresholds (same as thermo) and pair with
        # NWP geometric bounds for a more accurate convective envelope.
        risk = ConvectiveRisk.NONE
        if cape is not None:
            for threshold, level in _CAPE_THRESHOLDS:
                if cape >= threshold:
                    risk = level
                    break
            # Marginal: meaningful CAPE with defined base/top.
            # ICON can report convective geometry with negligible CAPE (<10 J/kg);
            # filter that noise — 10 J/kg is well below the LOW threshold (50).
            if risk == ConvectiveRisk.NONE and cape >= 10:
                risk = ConvectiveRisk.MARGINAL
        method = "nwp_hybrid"
    elif (
        nwp_diagnostics.convective_top_ft is not None
        and indices.lcl_altitude_ft is not None
        and nwp_diagnostics.convective_top_ft > indices.lcl_altitude_ft
    ):
        # LCL-anchored (e.g. ECMWF hcct): model gives convective top height
        # but no base — use LCL as the convective base proxy. Risk from CAPE
        # thresholds; base = LCL, top = hcct. Guard against hcct ≤ LCL
        # (rare elevated-convection artefact) so downstream never sees
        # base ≥ top.
        risk = ConvectiveRisk.NONE
        if cape is not None:
            for threshold, level in _CAPE_THRESHOLDS:
                if cape >= threshold:
                    risk = level
                    break
            if risk == ConvectiveRisk.NONE and cape >= 10:
                risk = ConvectiveRisk.MARGINAL
        method = "nwp_lcl_top"
    else:
        return None

    # Suppress by one level if strong CIN cap (same as thermo path)
    cin = indices.cin_surface_jkg
    if cin is not None and cin < CIN_CAP_THRESHOLD and risk != ConvectiveRisk.NONE:
        risk = _down_one(risk)

    # LCL-anchored path uses LCL as the convective base proxy
    base_ft = nwp_diagnostics.convective_base_ft
    if method == "nwp_lcl_top":
        base_ft = indices.lcl_altitude_ft

    return ConvectiveAssessment(
        risk_level=risk,
        cape_jkg=cape,
        cin_jkg=cin,
        lcl_altitude_ft=indices.lcl_altitude_ft,
        lfc_altitude_ft=indices.lfc_altitude_ft,
        el_altitude_ft=indices.el_altitude_ft,
        bulk_shear_0_6km_kt=indices.bulk_shear_0_6km_kt,
        lifted_index=indices.lifted_index,
        k_index=indices.k_index,
        total_totals=indices.total_totals,
        severe_modifiers=modifiers,
        base_ft=base_ft,
        top_ft=nwp_diagnostics.convective_top_ft,
        cover_pct=cover,
        method=method,
    )


# Convective DD-vs-model cross-check thresholds (tunable module constants).
# These gate the "does the model's own convective scheme corroborate the
# CAPE-derived risk" signal surfaced in the advisory popup and LLM digest.
# They never affect the grade.
_XCHECK_MODEL_QUIET_COVER_PCT = 10.0   # cover <= this (and no convective geom) => model "quiet"
_XCHECK_MODEL_ACTIVE_COVER_PCT = 25.0  # cover >= this => model "active"


class ConvectiveCrossCheck(NamedTuple):
    """Material divergence between the thermo (CAPE) risk and the model scheme.

    ``direction`` names the kind of disagreement; ``note`` is a human-readable
    explanation. Only returned when the two derivations disagree materially —
    otherwise the caller gets ``None``.
    """

    direction: str  # "dd_not_corroborated" | "model_active_dd_quiet"
    note: str


def convective_cross_check(
    thermo: ConvectiveAssessment | None,
    nwp: ConvectiveAssessment | None,
) -> ConvectiveCrossCheck | None:
    """Cross-check the chosen thermo risk against the model's convective scheme.

    The genuinely independent signal is the model's convective-cover diagnostic
    (``cover_pct``) or, for models that only emit convective geometry (ICON-EU
    hybrid, ECMWF hcct), the presence of a convective base/top. The model's own
    ``risk_level`` is deliberately NOT used: it is derived from the same CAPE
    thresholds as thermo, so comparing the two would be near-circular.

    Returns ``None`` (silent) unless one of two material divergences fires:
    - ``dd_not_corroborated`` — thermo MODERATE+ but the model scheme is quiet
      (low cover, no convective geometry).
    - ``model_active_dd_quiet`` — thermo NONE/MARGINAL but the model scheme is
      active (meaningful cover, or convective geometry present).

    A model that reports convective base/top but no ``cover_pct`` (ECMWF/ICON)
    counts as model-active.
    """
    if nwp is None or thermo is None:
        return None

    model_has_geom = nwp.base_ft is not None and nwp.top_ft is not None
    model_active = (
        nwp.cover_pct is not None and nwp.cover_pct >= _XCHECK_MODEL_ACTIVE_COVER_PCT
    ) or (nwp.cover_pct is None and model_has_geom)
    model_quiet = (
        nwp.cover_pct is not None
        and nwp.cover_pct <= _XCHECK_MODEL_QUIET_COVER_PCT
        and not model_has_geom
    )

    thermo_high = _RISK_LEVELS.index(thermo.risk_level) >= _RISK_LEVELS.index(
        ConvectiveRisk.MODERATE
    )
    thermo_low = thermo.risk_level in (ConvectiveRisk.NONE, ConvectiveRisk.MARGINAL)

    if thermo_high and model_quiet:
        cape_txt = f" (CAPE {thermo.cape_jkg:.0f})" if thermo.cape_jkg is not None else ""
        note = (
            f"DD {thermo.risk_level.value.upper()}{cape_txt} but model convective "
            f"cover {nwp.cover_pct:.0f}% — not corroborated by model scheme"
        )
        return ConvectiveCrossCheck(direction="dd_not_corroborated", note=note)

    if thermo_low and model_active:
        bits: list[str] = []
        if nwp.cover_pct is not None:
            bits.append(f"{nwp.cover_pct:.0f}% cover")
        if nwp.top_ft is not None:
            bits.append(f"tops {nwp.top_ft:.0f}ft")
        desc = " / ".join(bits) if bits else "model scheme"
        note = f"model convective scheme active ({desc}) despite weak DD instability"
        return ConvectiveCrossCheck(direction="model_active_dd_quiet", note=note)

    return None
