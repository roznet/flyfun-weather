"""Convective risk assessment from thermodynamic indices and NWP diagnostics.

Pure threshold logic — no MetPy dependency. Takes ThermodynamicIndices
and returns ConvectiveAssessment.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal, NamedTuple

from weatherbrief.analysis.route_geometry import (
    EMPTY_EXTENT,
    RouteExtent,
    cell_edges,
    route_extent,
)
from weatherbrief.models import (
    ConvectiveAssessment,
    ConvectiveCharacter,
    ConvectiveRegime,
    ConvectiveRisk,
    NWPCloudDiagnostics,
    NWPExplicitConvectiveDiagnostics,
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


# Lifted-index threshold for the realized-vs-potential gate. LI ≤ −2 marks
# moderate+ instability (0…−2 = stable/weak), the standard weak/moderate boundary.
LI_REALIZED = -2.0


def convection_realized(
    *,
    method: str | None,
    cin_jkg: float | None,
    ml_cape_jkg: float | None,
    lifted_index: float | None,
) -> bool:
    """True when convection is *realized*, not just CIN-capped potential (#216).

    Distinct from the convective *tier* (``risk_level`` / :func:`classify_regime`):
    the tier deliberately keeps a moderate cap's risk at its potential level —
    WEAK_INSTABILITY only suppresses at CIN < ``CIN_CAP_THRESHOLD`` (−200) — because
    a moderate-CAPE air mass is worth flagging on the route. A consumer that must
    know whether convection is *actually realized* — e.g. front co-location, which
    would otherwise read the parcel equilibrium level as a real tower a pilot meets
    — needs a stricter bar: realized unless strongly capped (CIN ≤
    ``REGIME_CIN_CAP``, −50) with no countervailing instability (ML-CAPE ≥
    ``REGIME_CAPE_LOW`` (300) or a lifted index ≤ ``LI_REALIZED`` (−2)).

    Native NWP convective cloud (a model-native ``method`` — "nwp" / "nwp_hybrid"
    / "nwp_lcl_top" / "nwp_precip") is realized by construction. The CAPE-derived fallback
    (``"nwp_cape_fallback"``, #283) is treated like ``"thermo"`` — it is parcel
    CAPE under another name, so it goes through the same realized-vs-potential
    gate rather than being trusted as a native firing signal.
    Unknown data defaults to realized — we downgrade only on *positive* evidence
    the instability is weak, so a real signal is never silently hidden. Callers own
    data extraction (and any DD→NWP lifted-index fallback); this owns the logic and
    the shared thresholds. See ``meteorology-decisions.md`` §4 (tier) and §6 (gate).
    """
    if (method or "thermo") not in ("thermo", "nwp_cape_fallback"):
        return True
    if not (cin_jkg is not None and cin_jkg <= REGIME_CIN_CAP):
        return True
    if ml_cape_jkg is None and lifted_index is None:
        return True
    return (
        (ml_cape_jkg is not None and ml_cape_jkg >= REGIME_CAPE_LOW)
        or (lifted_index is not None and lifted_index <= LI_REALIZED)
    )


_RISK_LEVELS = tuple(ConvectiveRisk)


def _down_one(risk: ConvectiveRisk) -> ConvectiveRisk:
    """Drop risk by one ordinal level (clamped at NONE)."""
    idx = _RISK_LEVELS.index(risk)
    return _RISK_LEVELS[idx - 1] if idx > 0 else risk


def _up_one(risk: ConvectiveRisk) -> ConvectiveRisk:
    """Raise risk by one ordinal level (clamped at EXTREME)."""
    idx = _RISK_LEVELS.index(risk)
    return _RISK_LEVELS[idx + 1] if idx < len(_RISK_LEVELS) - 1 else risk


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
      designs/meteorology-decisions.md §4).

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


# Convective tower-top → severity. ``convective_top_ft`` is the model's own
# convective cloud-top height (AMSL feet on NWPCloudDiagnostics); /100 → FL.
# This is the one native field common to all three GRIB models (GFS, ECMWF,
# ICON), it is resolution-robust (unlike convective-precip *rate*), and it
# separates shallow Cu from a mature Cb — so it is the primary native scale.
# Note: these are *approximate* FLs — convective_top_ft is geometric AMSL height,
# not pressure altitude, so in non-ISA conditions the true FL can differ by
# ~100–200 ft. Acceptable for severity tiering at this calibration stage.
# (#283; thresholds are a defensible v1 pending PIREP/eval-digest calibration.)
_CONV_TOP_FL_THRESHOLDS = [
    (380, ConvectiveRisk.EXTREME),   # overshooting / severe
    (280, ConvectiveRisk.HIGH),      # mature Cb / thunderstorm
    (200, ConvectiveRisk.MODERATE),  # deep Cu / small Cb
    (120, ConvectiveRisk.LOW),       # towering Cu
    (0,   ConvectiveRisk.MARGINAL),  # convective cloud present but shallow (<FL120)
]

# Convective areal-cover bands (%). Used as the base scale only when a model
# reports cover but no tower top (the depth is then unknown, so even numerous
# cells cap at MODERATE), and — via ``_COVER_NUMEROUS_PCT`` — as an up-one
# corroboration on the top-derived tier.
_CONV_COVER_PCT_THRESHOLDS = [
    (60, ConvectiveRisk.MODERATE),   # widespread
    (35, ConvectiveRisk.LOW),        # numerous
    (15, ConvectiveRisk.MARGINAL),   # scattered
]

# Cover at/above this is "numerous": bumps the tower-top tier up one level
# (more cells along the route), capped at HIGH — areal cover alone never
# implies EXTREME, which requires an overshooting (≥FL380) tower.
_COVER_NUMEROUS_PCT = 35.0


def _risk_from_conv_top(top_ft: float) -> ConvectiveRisk:
    """Native risk tier from a model convective tower top (feet AMSL → FL)."""
    fl = top_ft / 100.0
    for min_fl, level in _CONV_TOP_FL_THRESHOLDS:
        if fl >= min_fl:
            return level
    return ConvectiveRisk.NONE  # unreachable (0-FL threshold catches top_ft >= 0)


def _risk_from_conv_cover(cover_pct: float) -> ConvectiveRisk:
    """Native risk tier from convective areal cover when no tower top is known."""
    for min_pct, level in _CONV_COVER_PCT_THRESHOLDS:
        if cover_pct >= min_pct:
            return level
    return ConvectiveRisk.NONE  # isolated / none


def _native_convective_risk(
    top_ft: float | None,
    cover_pct: float | None,
) -> ConvectiveRisk:
    """Model-native convective risk: tower top primary, cover secondary.

    Driven by the model's own convective-scheme output (#283), NOT by CAPE —
    so the NWP track is a genuinely independent assessment from the DD
    (parcel-CAPE) track. When a top is present it sets the tier; numerous cover
    (≥35%) bumps it up one level (capped at HIGH). When only cover is present it
    sets a depth-unknown tier (capped at MODERATE). A quiet scheme (no top, no
    meaningful cover) is NONE — the model says it is not producing convection.
    """
    if top_ft is not None:
        risk = _risk_from_conv_top(top_ft)
        if (
            cover_pct is not None
            and cover_pct >= _COVER_NUMEROUS_PCT
            and _RISK_LEVELS.index(risk) < _RISK_LEVELS.index(ConvectiveRisk.HIGH)
        ):
            risk = _up_one(risk)
        return risk
    if cover_pct is not None:
        return _risk_from_conv_cover(cover_pct)
    return ConvectiveRisk.NONE


def _nwp_cape_fallback_risk(cape: float | None) -> ConvectiveRisk:
    """CAPE-threshold risk for models with no native convective scheme output.

    Mirrors the pre-#283 NWP behaviour (and the DD thresholds) for the fallback
    path only — a model that emits neither a convective top nor cover. Note the
    MARGINAL bar here (``cape >= 10``) is intentionally looser than
    ``_risk_from_cape``'s (which also requires LFC and EL): the fallback has no
    sounding geometry to gate on, so it keys on CAPE alone. Practical impact is
    nil — production never reaches this path (build_* return None for empty
    diagnostics).
    """
    if cape is None:
        return ConvectiveRisk.NONE
    for threshold, level in _CAPE_THRESHOLDS:
        if cape >= threshold:
            return level
    if cape >= 10:
        return ConvectiveRisk.MARGINAL
    return ConvectiveRisk.NONE


# Firing gate (#283 Phase 2). A MODERATE+ tower is only kept there when the
# model's own scheme *realized* convection — measurable convective precip OR
# meaningful convective cover. A deep-but-dry tower (precip≈0 / no cover — the
# capped or elevated case) is held down one level. This is the native-side
# mirror of the parcel-EL over-read fix (meteorology-decisions §6). Missing data
# never holds down: we suppress only on positive evidence of no firing (safety
# asymmetry), so a model that simply doesn't emit precip keeps its tower tier.
_FIRING_PRECIP_MM_H = 0.1
_FIRING_COVER_PCT = 15.0

# Native corroboration (#283 Phase 2). A *realized* MODERATE+ cell whose own
# model-native instability indices are strong is bumped up one level (capped at
# HIGH — only an overshooting ≥FL380 tower yields EXTREME). Thresholds are a
# defensible v1, pending calibration. Note these are the model's NATIVE kx /
# totalx / conv-precip (on NWPCloudDiagnostics), not the DD-derived indices.
_CORROB_K_INDEX = 35.0
_CORROB_TOTAL_TOTALS = 50.0
_CORROB_PRECIP_MM_H = 0.5

# Convective-precip-rate → tier (§14 Phase 3, #283 follow-up). Used ONLY when a
# native model is precipitating convectively but emitted neither a tower top nor
# a cover fraction — the ECMWF marine / elevated-convection case, where `cp`
# lands but `hcct` is sentinel/absent. The Phase-1 design collapsed "no geometry"
# to NONE, which hid a plainly-firing scheme (e.g. 4 mm/h convective rain over
# the Channel read NONE). Tower top stays the primary, resolution-robust scale
# whenever it IS present; this ladder is the geometry-absent fallback only.
#
# Depth is unknown from rate alone, so the ladder is capped at MODERATE (same
# rationale as the cover-only scale). Precip *rate* is resolution-dependent (§14
# reasoning 1: 0.8 mm/h at ECMWF ~9–25 km ≈ 3–5 mm/h at convection-permitting
# scale), so these thresholds are calibrated for synoptic-scale GRIB; a finer
# model whose convective precip gets wired later needs its own ladder. Defensible
# v1 — tune against the eval-digest corpus.
_CONV_PRECIP_MM_H_THRESHOLDS = [
    (2.0, ConvectiveRisk.MODERATE),               # active shower / storm core
    (0.5, ConvectiveRisk.LOW),                    # light convective showers
    (_FIRING_PRECIP_MM_H, ConvectiveRisk.MARGINAL),  # MARGINAL lower bound (entry gate requires precip > 0.1)
]


def _risk_from_conv_precip(precip_mm_h: float) -> ConvectiveRisk:
    """Depth-unknown native risk tier from convective precip rate (§14 Phase 3).

    Fallback for a native model firing convective precip with no tower top and no
    cover fraction (ECMWF marine / elevated convection). NOT used when geometry is
    present — tower top remains the primary, resolution-robust scale. Capped at
    MODERATE by ``_CONV_PRECIP_MM_H_THRESHOLDS`` (depth unknown).

    Mirrors the caller's firing gate (``precip > _FIRING_PRECIP_MM_H``) so the
    function is self-consistent when called in isolation: at/below the firing
    floor → NONE, above it → the ladder (MARGINAL/LOW/MODERATE, boundaries
    inclusive). The explicit floor guard means the 0.1 ladder row reads as the
    MARGINAL *upper-open* lower bound without making exactly-0.1 return MARGINAL.
    """
    if precip_mm_h <= _FIRING_PRECIP_MM_H:
        return ConvectiveRisk.NONE
    for min_mm_h, level in _CONV_PRECIP_MM_H_THRESHOLDS:
        if precip_mm_h >= min_mm_h:
            return level
    return ConvectiveRisk.NONE


def _convection_realized_nwp(diag: NWPCloudDiagnostics) -> bool:
    """True when the model's convective scheme actually fired here (#283).

    Realized = measurable convective precip OR meaningful convective cover.
    Unknown (both absent) is NOT realized — but see ``_apply_firing_gate``: a
    not-realized tower is only held down on *positive* dry evidence, never on
    missing data.
    """
    precip = diag.convective_precip_mm_h
    cover = diag.convective_cover_pct
    return (precip is not None and precip > _FIRING_PRECIP_MM_H) or (
        cover is not None and cover > _FIRING_COVER_PCT
    )


def _apply_firing_gate(
    risk: ConvectiveRisk, diag: NWPCloudDiagnostics
) -> tuple[ConvectiveRisk, str | None, bool]:
    """Gate a MODERATE+ tower on realized convection, then corroborate (#283).

    Returns ``(risk, note, is_driver)``: the adjusted risk, an optional note, and
    whether that note is a *driver* (corroboration, True) or *suppressor* (gate
    hold-down, False) — so the caller routes it without matching on wording. The
    gate only acts on MODERATE+ (a shallow tower is already low); corroboration
    only acts on a *realized* cell (strong instability confirms severity, it does
    not create a cell from nothing).
    """
    if _RISK_LEVELS.index(risk) < _RISK_LEVELS.index(ConvectiveRisk.MODERATE):
        return risk, None, False

    precip = diag.convective_precip_mm_h
    cover = diag.convective_cover_pct
    realized = _convection_realized_nwp(diag)

    if not realized:
        # Hold down one level only on positive dry evidence (precip ~0 or cover
        # ≤ threshold). Missing data → keep the tower tier (conservative).
        dry = (precip is not None and precip <= _FIRING_PRECIP_MM_H) or (
            cover is not None and cover <= _FIRING_COVER_PCT
        )
        if dry:
            return _down_one(risk), (
                "Deep tower but the model's convective scheme is dry here "
                "(no convective precip / cover) — held down one level"
            ), False
        return risk, None, False

    # Realized cell — strong native instability corroborates one level up.
    corroborators: list[str] = []
    if diag.k_index is not None and diag.k_index > _CORROB_K_INDEX:
        corroborators.append(f"K-index {diag.k_index:.0f}")
    if diag.total_totals is not None and diag.total_totals > _CORROB_TOTAL_TOTALS:
        corroborators.append(f"Total Totals {diag.total_totals:.0f}")
    if precip is not None and precip > _CORROB_PRECIP_MM_H:
        corroborators.append(f"convective precip {precip:.1f} mm/h")
    if corroborators and _RISK_LEVELS.index(risk) < _RISK_LEVELS.index(
        ConvectiveRisk.HIGH
    ):
        return _up_one(risk), (
            "Model-native severity corroborated ("
            + ", ".join(corroborators)
            + ")"
        ), True
    return risk, None, False


def _has_native_cloud_content(diag: NWPCloudDiagnostics) -> bool:
    """True when GRIB enrichment populated *any* native cloud diagnostic.

    A native GRIB model (GFS/ECMWF/ICON) always emits cloud-cover / ceiling /
    freezing-level content even where its convective scheme is quiet, so a diag
    carrying any of these is from a native model — a quiet convective scheme
    there genuinely means "no convection", not "no scheme". A completely empty
    diagnostics object (build_* returns None for that in production, so this is
    a defensive/synthetic case) is treated as "no native scheme" → CAPE
    fallback. (#283)
    """
    return any(
        v is not None
        for v in (
            diag.convective_cover_pct,
            diag.convective_base_ft,
            diag.convective_top_ft,
            diag.total_cover_pct,
            diag.boundary_cover_pct,
            diag.ceiling_ft,
            diag.freezing_level_ft,
            diag.low.cover_pct,
            diag.mid.cover_pct,
            diag.high.cover_pct,
        )
    )


def assess_convective_nwp(
    indices: ThermodynamicIndices,
    nwp_diagnostics: NWPCloudDiagnostics | None,
) -> ConvectiveAssessment | None:
    """Assess convective risk from the model's own convective-scheme output.

    Returns None when ``nwp_diagnostics`` is None (no GRIB enrichment for this
    model — e.g. AROME / UKMO / Météo-France, which are Open-Meteo-only).

    For GRIB models (GFS, ECMWF, ICON-EU) the risk level is **model-native**
    (#283): driven by the convective tower top (``convective_top_ft``) and, where
    available, the convective cover fraction — NOT by CAPE. This makes the NWP
    track independent of the DD (parcel-CAPE) track, so ``dd_nwp_agreement`` can
    fire on real divergence and a quiet model (capped / not firing) reads NONE
    even where DD reads HIGH. Thermodynamic indices are preserved for context
    and severity modifiers, and the existing strong-CIN suppression still
    partially handles capped towers.

    A CAPE-threshold fallback (``method="nwp_cape_fallback"``) is used only when
    the diagnostics carry no native cloud content at all; the distinct method
    lets ``dd_nwp_agreement`` skip the (then-circular) DD-vs-NWP comparison.
    """
    if nwp_diagnostics is None:
        return None

    cover = nwp_diagnostics.convective_cover_pct
    base = nwp_diagnostics.convective_base_ft
    top = nwp_diagnostics.convective_top_ft
    lcl = indices.lcl_altitude_ft
    cape = _effective_cape(indices)
    cin = indices.cin_surface_jkg
    modifiers = _severity_modifiers(indices, cape)

    # Fallback: no native convective scheme output — either no native cloud
    # content at all, or the diag explicitly declares its source exposes no
    # convective-realization channel (``convective_scheme_absent``, #457:
    # HRRR is convection-allowing with no parameterized scheme output
    # ingested, so its generic band covers must NOT read as "scheme present
    # but quiet" — that graded a CAPE-2000 column NONE). Score on CAPE, as
    # the DD track does, but mark it distinctly so the now-circular
    # DD-vs-NWP comparison is skipped downstream.
    if nwp_diagnostics.convective_scheme_absent or not _has_native_cloud_content(
        nwp_diagnostics
    ):
        # Prefer the model's OWN mixed-layer parcel pair where it delivers one
        # (HRRR's 90-0 mb MLCAPE/MLCIN — PR #508 review). This path exists
        # because the source has no convective-realization channel, but that
        # does NOT mean it has no parcel diagnostic: grading HRRR on
        # MetPy-derived CAPE made the "NWP" track a relabelled copy of the DD
        # track and, when MetPy could not solve the profile at all, scored a
        # delivered 2500 J/kg column NONE. Each of CAPE and CIN falls back
        # independently to the sounding-derived value, so a source that
        # publishes only one still contributes it.
        native_cape = nwp_diagnostics.ml_cape_jkg
        native_cin = nwp_diagnostics.ml_cin_jkg
        if native_cape is not None or native_cin is not None:
            cape = native_cape if native_cape is not None else cape
            cin = native_cin if native_cin is not None else cin
            # Modifiers were computed above against the sounding CAPE; recompute
            # so they describe whichever value actually grades this assessment.
            modifiers = _severity_modifiers(indices, cape)
        risk = _nwp_cape_fallback_risk(cape)
        if cin is not None and cin < CIN_CAP_THRESHOLD and risk != ConvectiveRisk.NONE:
            risk = _down_one(risk)
        return ConvectiveAssessment(
            risk_level=risk,
            cape_jkg=cape,
            cin_jkg=cin,
            lcl_altitude_ft=lcl,
            lfc_altitude_ft=indices.lfc_altitude_ft,
            el_altitude_ft=indices.el_altitude_ft,
            bulk_shear_0_6km_kt=indices.bulk_shear_0_6km_kt,
            lifted_index=indices.lifted_index,
            k_index=indices.k_index,
            total_totals=indices.total_totals,
            severe_modifiers=modifiers,
            base_ft=None,
            top_ft=None,
            cover_pct=None,
            method="nwp_cape_fallback",
        )

    precip = nwp_diagnostics.convective_precip_mm_h
    drivers: list[str] = []
    suppressors: list[str] = []

    # Native path. Determine which convective signal this model exposes
    # (preserving the method strings consumed by dd_nwp_agreement / front
    # co-location) and the base/top envelope to attach.
    if cover is not None:
        # GFS: convective cover always present (0% when quiet).
        method, base_ft, top_ft = "nwp", base, top
    elif base is not None and top is not None:
        # ICON-EU: convective base + top, no cover fraction.
        method, base_ft, top_ft = "nwp_hybrid", base, top
    elif top is not None and (lcl is None or top > lcl):
        # ECMWF hcct: convective top only — use LCL as the base proxy. The
        # top > LCL guard rejects the rare sub-LCL hcct artefact. When LCL is
        # unavailable (e.g. a very dry profile where MetPy can't compute it) we
        # still keep the tower rather than silently discard a real cell (#283
        # review) — the base is then unknown (None), but the tower drives risk.
        method, base_ft, top_ft = "nwp_lcl_top", lcl, top
    elif precip is not None and precip > _FIRING_PRECIP_MM_H:
        # §14 Phase 3: no tower top and no cover fraction, but the scheme is
        # precipitating convectively (ECMWF marine / elevated convection — `cp`
        # present, `hcct` sentinel). Tier from the precip rate rather than the
        # old false NONE. Realized by construction, so it skips the firing-gate
        # hold-down and the precip corroboration (which would double-count `cp`).
        method, base_ft, top_ft = "nwp_precip", None, None
    else:
        # Native model, quiet convective scheme at this point (no cover, no
        # usable geometry, no convective precip). Keep a real NONE assessment —
        # not None — so the DD-vs-NWP comparison can still fire.
        method, base_ft, top_ft = "nwp", None, None

    if method == "nwp_precip":
        risk = _risk_from_conv_precip(precip)
        drivers.append(
            f"Model convective scheme precipitating ({precip:.1f} mm/h) with no "
            "diagnosed tower top — tier from precip rate (depth unknown, capped MODERATE)"
        )
    else:
        # Risk from the validated native geometry (NOT CAPE).
        risk = _native_convective_risk(top_ft, cover)

        # Firing gate + native corroboration (#283 Phase 2): hold a deep-but-dry
        # tower down one level, or bump a realized cell up on strong native indices.
        risk, gate_note, gate_is_driver = _apply_firing_gate(risk, nwp_diagnostics)
        if gate_note is not None:
            (drivers if gate_is_driver else suppressors).append(gate_note)

    # Keep the strong-CIN suppression (#283: partially handles a capped tower the
    # scheme reports but won't realize; CIN < -200 → one level down). Prefer the
    # model's own ML-CIN when present, else the DD surface CIN.
    #
    # This stacks with the firing gate above: a deep-but-dry tower under a strong
    # cap is held down TWICE (gate → one level for "not firing", CIN → one level
    # for "capped"). That two-level drop is intentional — they are independent
    # physical suppressors (no realized convection AND a strong inhibition) — and
    # is bounded at NONE. Tune either threshold with the combined effect in mind.
    # See tests/test_convective.py::test_nwp_firing_gate_and_cin_double_suppression.
    #
    # NOT applied on the "nwp_precip" path (§14 Phase 3): `cp` proves the scheme
    # already fired, so penalising it on surface/ML CIN is circular — the
    # parameterisation overcame whatever inhibition existed at the (typically
    # elevated) triggering level, which a negative surface/ML CIN does not
    # describe. CIN suppression is for the deep-but-dry tower, not a firing cell.
    if method != "nwp_precip":
        eff_cin = nwp_diagnostics.ml_cin_jkg if nwp_diagnostics.ml_cin_jkg is not None else cin
        if eff_cin is not None and eff_cin < CIN_CAP_THRESHOLD and risk != ConvectiveRisk.NONE:
            risk = _down_one(risk)
            suppressors.append(f"Strong cap (CIN {eff_cin:.0f} J/kg) holds the tower down")

    return ConvectiveAssessment(
        drivers=drivers,
        suppressors=suppressors,
        risk_level=risk,
        cape_jkg=cape,
        cin_jkg=cin,
        lcl_altitude_ft=lcl,
        lfc_altitude_ft=indices.lfc_altitude_ft,
        el_altitude_ft=indices.el_altitude_ft,
        bulk_shear_0_6km_kt=indices.bulk_shear_0_6km_kt,
        lifted_index=indices.lifted_index,
        k_index=indices.k_index,
        total_totals=indices.total_totals,
        severe_modifiers=modifiers,
        base_ft=base_ft,
        top_ft=top_ft,
        cover_pct=cover,
        convective_precip_mm_h=nwp_diagnostics.convective_precip_mm_h,
        method=method,
    )


# --- Explicit-convection assessment (ICON-D2, issue #462) --------------------
#
# D2 is convection-permitting: deep convection lives in explicit storm fields
# (simulated reflectivity, echo top, LPI, updrafts), not in a
# parameterized scheme's diagnosed tower. This is a different KIND of signal
# from assess_convective_nwp's tower-top track, so it gets its own assessment
# with its own method string ("nwp_explicit") — the two are never blended.
#
# v2 decision table (meteorology-decisions §19, 2026-07-21 amendment #466/#467).
# The firing signal is the corridor-max hour-max reflectivity DBZ_CTMAX; a
# storm-process corroborator count |C| confirms it is real convection:
#
#   corridor dbz_ctmax | |C| = 0                    | |C| = 1  | |C| >= 2
#   -------------------+----------------------------+----------+---------
#   < 35 dBZ           | no fire                    | no fire  | no fire
#   35–44 dBZ          | no fire (stratiform note)  | MARGINAL | MODERATE
#   45–49 dBZ          | MODERATE                   | MODERATE | HIGH
#   >= 50 dBZ          | MODERATE                   | HIGH     | HIGH
#
# Two changes from v1 (both driven by the #467 validation, where the v1 table
# false-alarmed HIGH at 4 of 8 route points in widespread stratiform rain):
#   1. The corroborator ALGEBRA is now LPI-primary (see _explicit_corroborators).
#      DWD's LPI ≈ ∫ ε w² dz weighted by mixed-phase (graupel/ice) content, so it
#      already embeds updraft × graupel — counting lpi_max + w_ctmax + grau_gsp
#      as three independent votes triple-counted one mixed-phase updraft. CAPE
#      (environment, and it duplicates the independent thermo track) and UH are
#      narrative-only — neither is ever a vote.
#   2. The >= 50 row no longer bypasses corroboration. dbz_ctmax is a COLUMN
#      max, and a strong melting-layer bright band in heavy stratiform rain
#      reaches 50+ dBZ with no convection in the column. |C| = 0 now caps at
#      MODERATE (was HIGH), and the bright-band gate suppresses it to no-fire
#      entirely when the echo is shallow — see assess_convective_explicit.
#
# The v2 vote set is therefore lpi_max (primary) with w_ctmax as its only
# substitute. grau_gsp was dropped outright in #468 — it measures snow pellets
# reaching the GROUND, ~always 0 under warm-season route-corridor cores, not the
# column mixed-phase property #462 intended, so it could never realistically
# fire. That lands in the same place this amendment does (graupel was never an
# independent vote to begin with); its column replacement (tcond10_mx) is
# deferred pending calibration (#468).
#
# Completeness (rule 5): |C| counts only COMPLETE channels (value non-None — a
# valid quiet channel decodes to 0.0, #421 None ≠ 0) AND over threshold.
# Incomplete channels never move the tier in either direction; a zero on one
# channel never suppresses positive evidence on another.
_EXPLICIT_DBZ_FIRE = 35.0        # below → no fire (environment tracks unaffected)
_EXPLICIT_DBZ_CONVECTIVE = 45.0  # 35–44 band may be stratiform/melting-band
_EXPLICIT_DBZ_SEVERE = 50.0      # >= 50 still needs |C| >= 1 for HIGH (bright band)
_EXPLICIT_LPI_CORROB_JKG = 1.0
_EXPLICIT_LPI_STRONG_JKG = 5.0   # LPI (the primary channel) >= 5 counts as 2
_EXPLICIT_UPDRAFT_CORROB_MS = 10.0
# Narrative-only threshold: CAPE is ENVIRONMENT, never a corroborator vote
# (v2, #466/#467) — hence NARRATIVE, not CORROB, unlike
# _EXPLICIT_UPDRAFT_CORROB_MS below which is a genuine substitute vote.
_EXPLICIT_CAPE_NARRATIVE_JKG = 500.0
_EXPLICIT_UH_NOTE_M2S2 = 25.0    # rotation NOTE only, never a tier input
# Bright-band gate (#466/#467): the 18 dBZ echo top of a melting-layer bright
# band sits only a few kft above the freezing level, whereas a real convective
# core carries hydrometeors far higher. #467 measured echo_top − freezing_level
# at 6.5–7.8 kft (stratiform false alarm) vs 24–27 kft (real storms). Below this
# margin, AND with no storm-process corroboration, high reflectivity is treated
# as melting-layer bright band, not convection, at any dBZ.
_EXPLICIT_BRIGHT_BAND_DELTA_FT = 10000.0


def _explicit_freezing_level_ft(
    indices: ThermodynamicIndices,
    nwp_diagnostics: NWPCloudDiagnostics | None,
) -> float | None:
    """First available freezing level for the bright-band gate.

    Prefer the sounding's own (DD/MetPy) 0 °C crossing — the same column the
    echo top's altitude conversion uses — then the Open-Meteo / model-native
    freezing level, then the NWP cloud-diagnostics one.

    All three are MSL, so the chain compares like with like against the echo
    top. That was not free: ECMWF publishes ``deg0l`` as metres ABOVE GROUND,
    and it reaches BOTH of the last two candidates (the diagnostics field
    directly, and ``nwp_freezing_level_ft`` via the
    ``attach_nwp_diagnostics`` mirror onto ``HourlyForecast.freezing_level_m``
    — so dropping the third candidate would have fixed nothing). It is
    normalized to MSL at the decode boundary instead, using the model's own
    orography; see ``build_ecmwf_cloud_diagnostics`` (#487).
    """
    candidates = [indices.freezing_level_ft, indices.nwp_freezing_level_ft]
    if nwp_diagnostics is not None:
        candidates.append(nwp_diagnostics.freezing_level_ft)
    for value in candidates:
        if value is not None:
            return value
    return None


def _explicit_corroborators(
    explicit: NWPExplicitConvectiveDiagnostics,
    nwp_diagnostics: NWPCloudDiagnostics | None,
) -> tuple[int, list[str], int]:
    """Count storm-process corroborators, LPI-primary (§19 amendment #466).

    Returns ``(count, descriptions, incomplete_channels)``.

    DWD's LPI ≈ ``∫ ε w² dz`` weighted by mixed-phase content, so it already
    embeds updraft × graupel. Counting ``lpi_max`` + ``w_ctmax`` + ``grau_gsp``
    as three independent votes triple-counts one mixed-phase updraft, letting a
    moderately-electrified (or a stratiform bright-band) cell reach |C| >= 2 on
    physically one signal. So:

    - **LPI is the primary storm-process channel.** When present (complete) it
      votes alone: ``>= 5`` counts 2, ``>= 1`` counts 1. ``w_ctmax`` is then
      narrative detail only — NOT additive (it is an ingredient LPI already
      integrates).
    - **w_ctmax substitutes only when LPI does not vote** — i.e. LPI is missing
      OR present-but-below-floor. The substitution is IDENTICAL in both those
      cases, so an incomplete LPI channel never silently promotes w beyond what
      a present-but-quiet LPI would give (issue #466 note). It still counts only
      when its OWN channel is complete-and-over-threshold (rule 5) — a masked
      w_ctmax (None) contributes nothing.
    - **CAPE is environment, not storm process** — it duplicates the independent
      thermo track and let a bright band upgrade on ML-CAPE alone (#467). It is
      narrative-only now, never a vote. **UH** stays a rotation NOTE only.

    Note the consequence of #468 dropping ``grau_gsp`` (the other v1 substitute):
    the substitute path can now contribute at most 1, so **|C| >= 2 is reachable
    only via LPI >= 5**. The decision table's ``|C| >= 2`` column is therefore an
    electrification column in practice — deliberate, since LPI is the one channel
    that actually integrates the mixed-phase updraft the tier is trying to name.
    """
    count = 0
    notes: list[str] = []
    incomplete = 0

    lpi = explicit.lightning_potential_hour_max_jkg
    w = explicit.updraft_hour_max_ms

    lpi_voted = False
    if lpi is None:
        incomplete += 1
    elif lpi >= _EXPLICIT_LPI_STRONG_JKG:
        count += 2
        lpi_voted = True
        notes.append(f"strong lightning potential (LPI {lpi:.1f} J/kg)")
    elif lpi >= _EXPLICIT_LPI_CORROB_JKG:
        count += 1
        lpi_voted = True
        notes.append(f"lightning potential (LPI {lpi:.1f} J/kg)")

    if lpi_voted:
        # LPI already integrates the mixed-phase updraft — surface w as detail,
        # but never add it to |C| (the v1 double/triple-count this amendment
        # kills). grau_gsp, the other v1 vote, no longer exists (#468).
        if w is not None and w >= _EXPLICIT_UPDRAFT_CORROB_MS:
            notes.append(f"strong updraft ({w:.0f} m/s) — embedded in LPI, not additive")
    else:
        # LPI missing or present-but-quiet: w may substitute (1), only when its
        # own channel is complete-and-over-threshold.
        if w is None:
            incomplete += 1
        elif w >= _EXPLICIT_UPDRAFT_CORROB_MS:
            count += 1
            notes.append(f"strong updraft ({w:.0f} m/s)")

    # CAPE: environment, narrative-only (never a vote) — see the docstring.
    cape_ml = nwp_diagnostics.ml_cape_jkg if nwp_diagnostics is not None else None
    if cape_ml is not None and cape_ml >= _EXPLICIT_CAPE_NARRATIVE_JKG:
        notes.append(
            f"unstable environment (ML-CAPE {cape_ml:.0f} J/kg) — environment, "
            "narrative only"
        )

    return count, notes, incomplete


def _explicit_risk_from_table(
    dbz: float | None, corroborators: int
) -> ConvectiveRisk:
    """The v2 dbz × |C| decision table (see the constants block above)."""
    if dbz is None or dbz < _EXPLICIT_DBZ_FIRE:
        return ConvectiveRisk.NONE
    if dbz >= _EXPLICIT_DBZ_SEVERE:
        # >= 50 dBZ no longer self-certifies (v1 bypass removed, #466/#467):
        # dbz_ctmax is a column max and a stratiform melting-layer bright band
        # reaches 50+ with no convection. Uncorroborated → MODERATE, not HIGH
        # (and the bright-band gate downstream may drop it further to no-fire).
        return ConvectiveRisk.HIGH if corroborators >= 1 else ConvectiveRisk.MODERATE
    if dbz >= _EXPLICIT_DBZ_CONVECTIVE:
        return ConvectiveRisk.HIGH if corroborators >= 2 else ConvectiveRisk.MODERATE
    # 35–44 dBZ: echo present but possibly stratiform rain / melting-band
    # bright-band — only corroborated echoes fire.
    if corroborators >= 2:
        return ConvectiveRisk.MODERATE
    if corroborators == 1:
        return ConvectiveRisk.MARGINAL
    return ConvectiveRisk.NONE


def assess_convective_explicit(
    indices: ThermodynamicIndices,
    nwp_diagnostics: NWPCloudDiagnostics | None,
    explicit: NWPExplicitConvectiveDiagnostics,
) -> ConvectiveAssessment | None:
    """Assess convective risk from a convection-permitting model's storm fields.

    The explicit-convection sibling of :func:`assess_convective_nwp` (#462):
    consumes the :class:`NWPExplicitConvectiveDiagnostics` payload (corridor
    extrema — see the payload docstring) and returns the standard convective-
    assessment contract with ``method="nwp_explicit"``.

    Structural safety properties:
    - ``top_ft`` is ALWAYS ``None`` — D2 cells have unresolved vertical
      geometry for clearance purposes, so the overfly-clearance filter
      structurally cannot consume the 18 dBZ echo top as a cloud top. The echo
      top travels only as the ``echo_top_18dbz_ft`` character/detail field.
    - ``detection_complete=False`` returns ``None`` — "explicit assessment
      unavailable". It must NOT become an NWP ``NONE`` (unknown is not quiet),
      nor a CAPE-only fallback presented as D2's explicit verdict; the caller
      records the unavailability (``convective_explicit_unavailable``) and the
      DD/thermo track remains fully usable.
    - No CIN suppression: a simulated echo IS realized convection — the model
      already convected, so penalising it on surface/ML CIN would be circular
      (same reasoning as the ``nwp_precip`` path).
    - A quiet-but-complete hour returns a real ``NONE`` assessment (not
      ``None``) so DD-vs-model cross-checks can still fire.
    - Bright-band gate (§19 amendment #466/#467): when the 18 dBZ echo top sits
      < ``_EXPLICIT_BRIGHT_BAND_DELTA_FT`` above the freezing level AND no
      storm-process corroborator fired (|C| = 0), the high reflectivity is a
      melting-layer bright band, not convection — the fire is suppressed to
      ``NONE``. Gated on |C| = 0 so it can never override positive
      electrification / updraft evidence (a corroborated cell is never hidden —
      the codebase's under-warning-is-worse safety asymmetry).
    """
    if not explicit.detection_complete:
        return None

    dbz = explicit.reflectivity_hour_max_dbz
    cape = _effective_cape(indices)
    cin = indices.cin_surface_jkg
    # Hard wording rule (#462 rule 4): the explicit track never says "hail" —
    # D2's mixed-phase signal is graupel, which does not discriminate hail.
    # _severity_modifiers' freezing-level+CAPE heuristic emits "hail risk", so
    # rephrase it here to the graupel/mixed-phase framing (#463 review).
    modifiers = [
        m.replace("hail risk", "graupel / strong mixed-phase core potential")
        for m in _severity_modifiers(indices, cape)
    ]

    corrob_count, corrob_notes, incomplete = _explicit_corroborators(
        explicit, nwp_diagnostics,
    )
    risk = _explicit_risk_from_table(dbz, corrob_count)

    # Bright-band gate (§19 amendment #466/#467). A shallow 18 dBZ echo top
    # relative to the freezing level is a melting-layer bright band, not a
    # convective core. Only with |C| = 0 (no storm-process corroboration) and
    # only when both the echo top (complete) and a freezing level are available
    # — an unevaluable gate never downgrades (rule 5, safety asymmetry).
    freezing_ft = _explicit_freezing_level_ft(indices, nwp_diagnostics)
    echo_top_ft = explicit.echo_top_18dbz_ft
    bright_band = (
        risk is not ConvectiveRisk.NONE
        and corrob_count == 0
        and echo_top_ft is not None
        and explicit.echo_top_complete
        and freezing_ft is not None
        and (echo_top_ft - freezing_ft) < _EXPLICIT_BRIGHT_BAND_DELTA_FT
    )
    if bright_band:
        risk = ConvectiveRisk.NONE

    drivers: list[str] = []
    suppressors: list[str] = []

    if dbz is not None and dbz >= _EXPLICIT_DBZ_FIRE:
        if bright_band:
            # echo_top_ft / freezing_ft are both non-None here (gate condition).
            suppressors.append(
                f"Corridor-max reflectivity {dbz:.0f} dBZ, but the 18 dBZ echo "
                f"top (~{echo_top_ft:.0f} ft) sits only ~{echo_top_ft - freezing_ft:.0f} ft "
                f"above the freezing level (~{freezing_ft:.0f} ft) with no "
                "storm-process corroboration — melting-layer bright band in "
                "stratiform rain, not convection"
            )
        else:
            drivers.append(
                f"Explicitly simulated storm echo — corridor-max reflectivity "
                f"{dbz:.0f} dBZ over the past hour (ICON-D2, convection-permitting)"
            )
            if dbz < _EXPLICIT_DBZ_CONVECTIVE and corrob_count == 0:
                suppressors.append(
                    "Echo present but uncorroborated at 35–44 dBZ — likely "
                    "stratiform rain / melting-band bright-band, not convection"
                )
            drivers.extend(corrob_notes)
            if incomplete and corrob_count < 2:
                drivers.append(
                    f"{incomplete} corroborator channel(s) unavailable — tier from "
                    "the reflectivity row alone (missing data never downgrades)"
                )
            if explicit.echo_top_18dbz_ft is not None:
                # Depth/character AFTER firing — explicitly NOT a cloud top: the
                # physical storm top (weakly-reflecting anvil ice) sits higher.
                drivers.append(
                    f"18 dBZ echo top ~{explicit.echo_top_18dbz_ft:.0f} ft "
                    "(storm depth indicator — the cloud top is higher; not for "
                    "overfly planning)"
                )
            uh = explicit.updraft_helicity_2_8km_hour_max_m2s2
            if uh is not None and abs(uh) >= _EXPLICIT_UH_NOTE_M2S2:
                sense = "cyclonic" if uh > 0 else "anticyclonic"
                drivers.append(
                    f"Rotating updraft signature (updraft helicity {uh:.0f} m²/s², "
                    f"{sense}) — organized-storm character"
                )

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
        drivers=drivers,
        suppressors=suppressors,
        base_ft=None,
        top_ft=None,   # structural: echo top must never reach the clearance filter
        cover_pct=None,
        convective_precip_mm_h=None,
        method="nwp_explicit",
        explicit_source=explicit.source,
        reflectivity_hour_max_dbz=dbz,
        echo_top_18dbz_ft=explicit.echo_top_18dbz_ft,
    )


# Convective DD-vs-model cross-check thresholds (tunable module constants).
# These gate the "does the model's own convective scheme corroborate the DD
# (CAPE-derived) risk" signal surfaced in the advisory popup and LLM digest.
# They never affect the grade (details-only; safety asymmetry — a quiet model
# may comment but must never pull a DD RED down). Keyed on the model's native
# *firing* signal (#283 follow-up): convective precip, meaningful cover, or a
# deep tower — NOT bare convective-geometry presence (which over-fires on
# shallow Cu).
_XCHECK_MODEL_QUIET_COVER_PCT = 10.0   # cover <= this counts toward "quiet"
_XCHECK_MODEL_ACTIVE_COVER_PCT = 25.0  # cover >= this => model "active"
_XCHECK_DEEP_TOP_FL = 200              # tower >= FL200 => deep cell ("active")
_XCHECK_QUIET_TOP_FL = 120             # tower < FL120 (or none) counts toward "quiet"


def _explicit_cross_check(
    thermo: "ConvectiveAssessment",
    nwp: "ConvectiveAssessment",
) -> "ConvectiveCrossCheck | None":
    """DD-vs-explicit divergence for the ICON-D2 mode (#462).

    Same two material divergences as the parameterized check, keyed on the
    explicit track's own firing verdict (the decision table already folds in
    reflectivity + corroborators):
    - thermo MODERATE+ but the simulated radar is quiet/uncorroborated →
      ``dd_not_corroborated``;
    - thermo NONE/MARGINAL but D2 explicitly develops a cell (MODERATE+) →
      ``model_active_dd_quiet``.
    MARGINAL explicit (a single-corroborator 35–44 dBZ echo) sits in neither
    band, mirroring the parameterized gap between quiet and active.
    """
    model_active = _RISK_LEVELS.index(nwp.risk_level) >= _RISK_LEVELS.index(
        ConvectiveRisk.MODERATE
    )
    model_quiet = nwp.risk_level == ConvectiveRisk.NONE

    thermo_high = _RISK_LEVELS.index(thermo.risk_level) >= _RISK_LEVELS.index(
        ConvectiveRisk.MODERATE
    )
    thermo_low = thermo.risk_level in (ConvectiveRisk.NONE, ConvectiveRisk.MARGINAL)

    dbz = nwp.reflectivity_hour_max_dbz
    if thermo_high and model_quiet:
        cape_txt = f" (CAPE {thermo.cape_jkg:.0f})" if thermo.cape_jkg is not None else ""
        echo_txt = (
            f"corridor-max reflectivity {dbz:.0f} dBZ"
            if dbz is not None else "no simulated echo"
        )
        note = (
            f"Thermo Convective shows {thermo.risk_level.value.upper()} instability"
            f"{cape_txt}, but ICON-D2's explicit convection is quiet ({echo_txt})"
        )
        return ConvectiveCrossCheck(direction="dd_not_corroborated", note=note)

    if thermo_low and model_active:
        echo_txt = f"{dbz:.0f} dBZ corridor-max echo" if dbz is not None else "a storm cell"
        dd_txt = (
            "little instability"
            if thermo.risk_level == ConvectiveRisk.NONE
            else "only marginal instability"
        )
        note = (
            f"ICON-D2 explicitly develops {echo_txt}, but Thermo Convective "
            f"shows {dd_txt}"
        )
        return ConvectiveCrossCheck(direction="model_active_dd_quiet", note=note)

    return None


class ConvectiveCrossCheck(NamedTuple):
    """Material divergence between the thermo (CAPE) risk and the model scheme.

    ``direction`` names the kind of disagreement; ``note`` is a human-readable
    explanation. Only returned when the two derivations disagree materially —
    otherwise the caller gets ``None``.
    """

    direction: Literal["dd_not_corroborated", "model_active_dd_quiet"]
    note: str


def convective_cross_check(
    thermo: ConvectiveAssessment | None,
    nwp: ConvectiveAssessment | None,
) -> ConvectiveCrossCheck | None:
    """Cross-check the chosen thermo (DD) risk against the model's native scheme.

    The genuinely independent signal is whether the model's own scheme *fired* a
    cell here (#283 follow-up): convective precip > the firing gate, meaningful
    convective cover (≥ ``_XCHECK_MODEL_ACTIVE_COVER_PCT``), or a deep tower
    (≥ ``_XCHECK_DEEP_TOP_FL``). Bare convective-geometry presence is NOT enough
    — a shallow Cu top would otherwise spuriously read "active". The model's
    ``risk_level`` is not compared directly here; this stays a DD-vs-firing
    cross-check (the risk-level comparison lived in ``dd_nwp_agreement``, now
    removed for convective — see ``designs/advisories.md``).

    Returns ``None`` (silent) unless one of two material divergences fires:
    - ``dd_not_corroborated`` — thermo MODERATE+ but the model scheme is quiet
      (no precip, low/no cover, no deep tower) — the capped / loaded-gun
      false-alarm (e.g. ECMWF over Reims, CIN −360, dry till afternoon).
    - ``model_active_dd_quiet`` — thermo NONE/MARGINAL but the model fired
      (e.g. GFS Sun morning over Reims: cover 46.8%, top FL332, DD only marginal).
    """
    if nwp is None or thermo is None:
        return None
    if nwp.method == "nwp_cape_fallback":
        # Circular: the fallback is CAPE-scored like thermo (no native scheme
        # output), so a quiet/active comparison would re-derive thermo against
        # itself. Skip it — same reasoning the dd_nwp_agreement convective block
        # used before it was removed. (#283 review) Reachable when a diagnostic
        # carries only stability indices (ml_cape/kx/...) and no cloud content.
        return None
    if nwp.method == "nwp_explicit":
        # Explicit-convection mode (#462): the parameterized firing channels
        # (precip / cover / tower) are structurally None here, so the generic
        # quiet/active detection below would misread a FIRING explicit cell as
        # "quiet". Active/quiet come from the explicit assessment itself —
        # which exists only when detection is complete, so its NONE is a
        # genuine simulated-radar quiet, not missing data (an incomplete
        # detection never produces an assessment at all).
        return _explicit_cross_check(thermo, nwp)

    precip = nwp.convective_precip_mm_h
    cover = nwp.cover_pct
    top_fl = nwp.top_ft / 100.0 if nwp.top_ft is not None else None

    model_active = (
        (precip is not None and precip > _FIRING_PRECIP_MM_H)
        or (cover is not None and cover >= _XCHECK_MODEL_ACTIVE_COVER_PCT)
        or (top_fl is not None and top_fl >= _XCHECK_DEEP_TOP_FL)
    )
    # Quiet requires positive evidence of no firing on every available channel:
    # no convective precip, low/no cover, and no all-but-shallow tower. The gap
    # between the quiet and active bands (e.g. cover 10–25%, tower FL120–200) is
    # intentionally *neither* — only material divergences fire.
    model_quiet = (
        not model_active
        and (precip is None or precip <= _FIRING_PRECIP_MM_H)
        and (cover is None or cover <= _XCHECK_MODEL_QUIET_COVER_PCT)
        and (top_fl is None or top_fl < _XCHECK_QUIET_TOP_FL)
    )

    # LOW is intentionally in neither thermo band: too weak to call a quiet model
    # a "missed" high risk, yet not weak enough for an active model to surprise.
    thermo_high = _RISK_LEVELS.index(thermo.risk_level) >= _RISK_LEVELS.index(
        ConvectiveRisk.MODERATE
    )
    thermo_low = thermo.risk_level in (ConvectiveRisk.NONE, ConvectiveRisk.MARGINAL)

    if thermo_high and model_quiet:
        cape_txt = f" (CAPE {thermo.cape_jkg:.0f})" if thermo.cape_jkg is not None else ""
        if cover is not None:
            cover_txt = f"cover {cover:.0f}%"
        elif precip is not None:
            # precip present but ≤ the firing gate — say so rather than imply absence
            cover_txt = f"conv precip {precip:.1f} mm/h, no cover"
        else:
            cover_txt = "no convective precip/cover"
        note = (
            f"Thermo Convective shows {thermo.risk_level.value.upper()} instability"
            f"{cape_txt}, but the model's own NWP Convective forecast is quiet "
            f"({cover_txt})"
        )
        return ConvectiveCrossCheck(direction="dd_not_corroborated", note=note)

    if thermo_low and model_active:
        bits: list[str] = []
        if precip is not None and precip > _FIRING_PRECIP_MM_H:
            bits.append(f"{precip:.1f} mm/h conv precip")
        if cover is not None and cover >= _XCHECK_MODEL_ACTIVE_COVER_PCT:
            bits.append(f"{cover:.0f}% cover")
        if top_fl is not None and top_fl >= _XCHECK_DEEP_TOP_FL:
            # Round to a conventional FL for display (top_fl is geometric AMSL).
            bits.append(f"tops FL{round(top_fl / 10) * 10:.0f}")
        desc = " / ".join(bits) if bits else "convection"
        dd_txt = (
            "little instability"
            if thermo.risk_level == ConvectiveRisk.NONE
            else "only marginal instability"
        )
        note = (
            f"NWP Convective shows convection ({desc}), but Thermo Convective "
            f"shows {dd_txt}"
        )
        return ConvectiveCrossCheck(direction="model_active_dd_quiet", note=note)

    return None


# --- Convective character (VFR avoidability) — issue #294 -------------------
# A second axis, orthogonal to the severity tier above: does route convection
# stay circumnavigable VFR (isolated / scattered) or is it genuinely
# VFR-impractical (widespread / organized / embedded)? Severity owns the colour;
# this owns the narrative + a dedicated graded advisory. Pure logic over
# per-point inputs — the advisory layer extracts the inputs from the soundings.

# Realized-coverage bands: % of *all* route points with realized convection.
CHAR_ISOLATED_MAX_PCT = 15.0   # ≤ ⇒ isolated (discrete cells, wide gaps)
CHAR_SCATTERED_MAX_PCT = 40.0  # ≤ ⇒ scattered; above ⇒ widespread
# Longest *contiguous* run of embedded cells (nm) at/above which the route is
# EMBEDDED (#568). Replaces the old ``CHAR_EMBED_PCT`` fraction of convective
# points, which had no population floor: one convective point under a deck
# scored 100 % and turned a 582 nm route RED "embedded — VFR impractical" off
# 9 nm of it.
#
# Contiguous rather than total, because EMBEDDED is RED for *"you cannot get
# around it"*. Scattered embedded points summing to 50 nm are not a barrier —
# they are cells with gaps between them, which is what the coverage band below
# already grades. Measured with the midpoint-owned-cell convention
# (``route_geometry.cell_edges``), the same one ``format_extent`` uses, so the
# extent this gate fires on and the "(Xnm/Ynm)" the card prints agree.
CHAR_EMBED_MIN_NM = 50.0
# 0–6 km bulk shear (kt) at/above which a *widespread* band is called ORGANIZED.
CHAR_ORGANIZED_SHEAR_KT = 35.0
# K-index / Total Totals "numerous storms" thresholds — bump the band up one.
CHAR_K_NUMEROUS = 40.0
CHAR_TT_NUMEROUS = 55.0
# Minimum NWP convective cover (%) counting a GFS point as realized convection.
CHAR_COVER_REALIZED_PCT = 25.0


class ConvCharPoint(NamedTuple):
    """Per-route-point inputs to the convective-character classifier."""

    is_convective: bool       # severity ≥ the min risk that counts (MODERATE+)
    realized: bool            # model realizes convection here (showers/cover/geom)
    embedded: bool            # a BKN/OVC deck contains cruise here (cells hidden)
    k_index: float | None     # native preferred, else MetPy
    total_totals: float | None
    # Below-base avoidability geometry (#298) — consumed only by the annotate-only
    # clearance note in the evaluator, never by classify_convective_character().
    convective_base_ft: float | None = None  # model-native cell base; None = depth/base unresolved
    convective_top_ft: float | None = None   # model-native cell top; None = no diagnosed tower
    vmc_below_base: bool = True               # layer cruise→base free of a BKN/OVC deck (VMC see-and-avoid)
    # Along-route position (nm from origin), for the contiguous-extent embedded
    # gate (#568). ``None`` on hand-built points in tests; the classifier then
    # falls back to even spacing over ``total_distance_nm``.
    distance_nm: float | None = None
    # Could this model be graded here at all? False for a route point where the
    # model has no sounding. Such a point must still be PRESENT so the midpoint
    # cell edges tile the real route — dropping it donates its miles to the
    # nearest survivor, which let one realized cell at 40 nm of a 200 nm route
    # claim 82% coverage and force WIDESPREAD/RED (#571 review) — but it is
    # excluded from the denominator, the #391 rule every other evaluator follows.
    assessed: bool = True


_CHAR_BAND_ORDER = (
    ConvectiveCharacter.ISOLATED,
    ConvectiveCharacter.SCATTERED,
    ConvectiveCharacter.WIDESPREAD,
)


def _char_up_one(band: ConvectiveCharacter) -> ConvectiveCharacter:
    """Bump a coverage band up one step (clamped at WIDESPREAD)."""
    try:
        i = _CHAR_BAND_ORDER.index(band)
    except ValueError:
        return band
    return _CHAR_BAND_ORDER[min(i + 1, len(_CHAR_BAND_ORDER) - 1)]


def _char_distances(
    points: Sequence[ConvCharPoint], total_distance_nm: float
) -> list[float]:
    """Along-route positions of the character points, evenly spaced as a fallback.

    Hand-built test points carry no ``distance_nm``; treating them as evenly
    spaced keeps a fixture graded rather than silently ungradeable.
    """
    n = len(points)
    return [
        p.distance_nm
        if p.distance_nm is not None
        else (total_distance_nm * i / (n - 1) if n > 1 else 0.0)
        for i, p in enumerate(points)
    ]


def character_extent(
    points: Sequence[ConvCharPoint],
    total_distance_nm: float | None,
    predicate: Callable[[ConvCharPoint], bool],
    speed_kt: float | None = None,
) -> RouteExtent:
    """Extent of a character sub-population over the shared route geometry (#571).

    One reducer for both the realized-coverage band and the EMBEDDED contiguity
    gate, so the band, the gate and the ``(Xnm/Ynm)`` the card prints are the
    same measurement.

    With no route length ``route_extent`` treats the points as evenly spaced over
    a **unit** route, so the coverage *share* stays measurable — under even
    spacing a distance ratio is exactly the point ratio, which is what the band
    used to be — and marks the result ``distance_known=False``. The resulting
    ``nm`` / ``longest_run_nm`` are then unitless and must not be gated on; that
    is why :func:`longest_embedded_run_nm` keeps its own explicit guard and
    returns 0.0, so an unmeasurable barrier is never asserted.
    """
    if not points:
        return EMPTY_EXTENT
    # ``total_distance_nm or 0.0`` hands the degenerate case to route_extent's
    # own unit fallback rather than repeating it here (#571 review).
    return route_extent(
        _char_distances(points, total_distance_nm or 0.0),
        total_distance_nm or 0.0,
        [predicate(p) for p in points],
        [p.assessed for p in points],
        speed_kt=speed_kt,
    )


class ConvCluster(NamedTuple):
    """One contiguous along-track run of realized convective cells (#593).

    A route is not one weather system. On LFMD→EGTF 2026-08-27 a mid-route
    system was genuinely flyable underneath while a *different* system hours
    later at the arrival end was not, and any route-wide below-base test
    answers "no" for the whole flight on account of the second one. A cluster
    is the unit that question can honestly be asked about.

    ``indices`` index into the ``points`` the cluster was built from (and so,
    for points built by ``build_character_points``, into ``ctx.analyses``).
    ``dist_from_nm`` / ``dist_to_nm`` are the run's extent on the shared
    :func:`cell_edges` geometry — the same convention the coverage band and the
    EMBEDDED contiguity gate measure on, so the miles a per-cluster mitigation
    prints cannot describe different geometry from the miles that graded it.
    """

    indices: tuple[int, ...]
    dist_from_nm: float
    dist_to_nm: float


def convective_clusters(
    points: Sequence[ConvCharPoint], total_distance_nm: float | None
) -> list[ConvCluster]:
    """Split the realized cells into maximal contiguous along-track runs (#593).

    Contiguity is the *index* run of consecutive realized cells, the same
    convention :func:`route_extent` measures ``longest_run_nm`` with — one
    definition of "contiguous along-track run" in this module rather than two.
    Deliberately no gap tolerance: a bridged gap would be miles the cluster
    claims but was never tested, and fragmenting a system is the conservative
    direction (each fragment must clear the below-base geometry on its own).
    Callers that dislike the fragmentation fold neighbouring runs back together
    *after* testing them, which asserts nothing untested.

    Returns ``[]`` when nothing realizes, matching the classifier's
    ``realized == 0`` short-circuit: no cells, no clusters.
    """
    if not points:
        return []
    lefts, rights = cell_edges(
        _char_distances(points, total_distance_nm or 0.0), total_distance_nm or 0.0
    )
    clusters: list[ConvCluster] = []
    run: list[int] = []
    for i, p in enumerate(points):
        if p.is_convective and p.realized:
            run.append(i)
            continue
        if run:
            clusters.append(
                ConvCluster(tuple(run), lefts[run[0]], rights[run[-1]])
            )
            run = []
    if run:
        clusters.append(ConvCluster(tuple(run), lefts[run[0]], rights[run[-1]]))
    return clusters


def _is_embedded_cell(p: ConvCharPoint) -> bool:
    """A **realized** cell whose cruise level sits inside a deck.

    Realization is part of the predicate for the same reason the
    ``realized == 0`` short-circuit exists below: "cells hidden in a deck"
    presupposes cells, and the old gate counted merely-thermodynamically-flagged
    points.
    """
    return p.is_convective and p.realized and p.embedded


def longest_embedded_run_nm(
    points: Sequence[ConvCharPoint], total_distance_nm: float | None
) -> float:
    """Longest *contiguous* along-route run of embedded cells, in nm (#568).

    Now a thin read of :func:`character_extent`'s ``longest_run_nm`` (#571):
    contiguity is a reducer on the shared geometry, not a convention of its own.

    Returns 0.0 when the route length is unknown or non-positive — without a
    real distance there is no extent to gate on, and the caller falls through to
    the coverage band rather than asserting an unmeasurable barrier.
    """
    if total_distance_nm is None or total_distance_nm <= 0:
        return 0.0
    return character_extent(points, total_distance_nm, _is_embedded_cell).longest_run_nm


def classify_convective_character(
    points: Sequence[ConvCharPoint],
    *,
    shear_kt: float | None = None,
    front_present: bool = False,
    synoptic_ascent: bool = False,
    isolated_max_pct: float = CHAR_ISOLATED_MAX_PCT,
    scattered_max_pct: float = CHAR_SCATTERED_MAX_PCT,
    embed_min_nm: float = CHAR_EMBED_MIN_NM,
    total_distance_nm: float | None = None,
    organized_shear_kt: float = CHAR_ORGANIZED_SHEAR_KT,
    k_numerous: float = CHAR_K_NUMEROUS,
    tt_numerous: float = CHAR_TT_NUMEROUS,
) -> ConvectiveCharacter:
    """Classify route convective character (VFR avoidability) for one model.

    Coverage-first: the *realized* extent (showers / model cover / convective
    geometry) sets the band, K/TT nudges it up one step (potential numerosity),
    and forcing (front / synoptic ascent / strong shear) only relabels a
    *widespread* band as ORGANIZED. This ordering is deliberate — forcing with
    only a few realized cells (a capped loaded gun strung along a trough) stays
    avoidable, matching the EDQT→EDDS 2026-06-16 "few but nasty" ground truth
    (issue #294).

    EMBEDDED (cells hidden in a deck) is checked first, and fires on the longest
    *contiguous* run of realized embedded points reaching ``embed_min_nm`` —
    a route extent, not a fraction of convective points (#568). ``points`` must
    therefore carry ``distance_nm`` and the caller must pass ``total_distance_nm``;
    without a route length the gate cannot fire at all.

    Severity owns the colour; this never downgrades it. Bands map to the
    advisory colour in the evaluator (ISOLATED/SCATTERED→AMBER, the rest→RED).
    """
    # Assessed points only: a route point this model cannot grade is present for
    # the geometry but is not part of the population being described.
    total = sum(1 for p in points if p.assessed)
    if total == 0:
        return ConvectiveCharacter.NONE
    conv = [p for p in points if p.is_convective]
    if not conv:
        return ConvectiveCharacter.NONE

    realized = sum(1 for p in conv if p.realized)

    # 0. Nothing realized anywhere → there is no *character* to describe (§22).
    # Without this, zero realized cells fell into the coverage band below
    # (realized_pct = 0 <= isolated_max_pct) and classified ISOLATED → AMBER,
    # rendering "Isolated cells — circumnavigable VFR with see-and-avoid, expect
    # possible diversions (0nm/600nm (0%))" — a colour and a promise of cells
    # over an extent of nothing. Seen on UKMO/MétéoFrance (no model-native
    # convective scheme, so no precip/cover/geometry realizes) sitting beside a
    # RED "peak HIGH" from the severity advisory on the same model.
    #
    # This is deliberately checked BEFORE the embedded test: "cells hidden under
    # a deck" presupposes cells: with nothing realizing, EMBEDDED would assert
    # hidden convection that no signal supports. Severity still owns the colour
    # for a loaded-but-unrealized air mass — the convective advisory grades it
    # (dd_trigger AMBER, §18); character simply has nothing to add.
    if realized == 0:
        return ConvectiveCharacter.NONE

    # 1. Embedded — cells you cannot see to avoid because a deck hides them.
    #
    # Gated on CONTIGUOUS ALONG-ROUTE EXTENT, not on a fraction of convective
    # points (#568). The old fraction had ``len(conv)`` as its denominator and no
    # population floor, so a single flagged point under a deck scored 100 % and
    # called a 582 nm route EMBEDDED/RED off 9 nm of it — the same "a colour over
    # an extent of nothing" failure the ``realized == 0`` short-circuit above
    # exists to prevent.
    #
    # Contiguous, because EMBEDDED is RED for "you cannot get around it": cells
    # with gaps between them are exactly what the coverage band below grades, and
    # scattered embedded points summing to the threshold are not a barrier.
    # Below the threshold we fall through to that band, which grades the cells on
    # their realized extent. Severity is untouched — a dangerous single cell
    # still owns its own colour there.
    #
    # ``run > 0`` is load-bearing at the parameter's own minimum: ``embed_min_nm``
    # is tunable down to 0, and without it a route with NO embedded points would
    # satisfy ``0 >= 0`` and grade EMBEDDED. A run has to exist to be measured.
    embedded_run_nm = longest_embedded_run_nm(points, total_distance_nm)
    if embedded_run_nm > 0 and embedded_run_nm >= embed_min_nm:
        return ConvectiveCharacter.EMBEDDED

    # 2. Realized-coverage band — the share of the ROUTE, in miles, carrying
    #    realized convection (#571). It was ``realized / len(points)``, a point
    #    ratio: on an unevenly spaced route the same weather banded differently
    #    depending on where ``interpolate_route`` happened to insert waypoints.
    realized_pct = character_extent(
        points, total_distance_nm, lambda p: p.is_convective and p.realized,
    ).pct
    if realized_pct <= isolated_max_pct:
        band = ConvectiveCharacter.ISOLATED
    elif realized_pct <= scattered_max_pct:
        band = ConvectiveCharacter.SCATTERED
    else:
        band = ConvectiveCharacter.WIDESPREAD

    # 3. Potential-numerosity nudge: a moist, numerous-storm environment bumps
    #    the band up one step (never down). Uses the peak K/TT among cells.
    k_max = max((p.k_index for p in conv if p.k_index is not None), default=None)
    tt_max = max((p.total_totals for p in conv if p.total_totals is not None), default=None)
    if (k_max is not None and k_max >= k_numerous) or (
        tt_max is not None and tt_max >= tt_numerous
    ):
        band = _char_up_one(band)

    # 4. Forcing relabels a widespread band as an organized system (both RED).
    if band is ConvectiveCharacter.WIDESPREAD and (
        front_present
        or synoptic_ascent
        or (shear_kt is not None and shear_kt >= organized_shear_kt)
    ):
        return ConvectiveCharacter.ORGANIZED
    return band
