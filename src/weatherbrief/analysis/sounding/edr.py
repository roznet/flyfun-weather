"""EDR (Eddy Dissipation Rate) scale via the Sharman & Pearson (2017) remap.

EDR (ε^1/3) is the ICAO-standard, aircraft-independent turbulence intensity
that pilots see in tools like ForeFlight, banded roughly: <0.1 smooth,
0.1–0.2 light, 0.2–0.45 moderate, 0.45–0.75 severe, >0.75 extreme.

Rather than compute EDR from scratch (a GTG-style ensemble), we use the
published **statistical remapping** of Sharman & Pearson (2017): any
turbulence diagnostic ``D`` that is positive and *increases* with turbulence
is mapped onto the lognormal EDR climatology with a log-linear fit::

    ln(EDR) = a + b·ln(D)
        b = C2 / SD[ln D]
        a = C1 − b·⟨ln D⟩

- ``⟨ln D⟩`` and ``SD[ln D]`` are the climatological mean and std of ``ln(D)``
  computed from *our own* model output (pure moment-matching — no turbulence
  ground truth / PIREPs needed). These are accumulated offline by
  :class:`EdrAccumulator` as standalone soundings stream past, and persisted
  as running sums in the ``edr_calibration_accumulator`` table.
- ``C1``, ``C2`` are the published universal climatology of EDR (mean and std
  of ``ln(EDR)`` from in-situ aircraft, 2009–2014); they are altitude-band
  dependent. Values below are primary-source-verified from Kim et al. 2020
  (AMT 13, 1373–1385), reproducing the Sharman & Pearson 2017 table.

The calibration is a one-time / periodic *offline* exercise over a large
multi-day, multi-airport sample — NOT recomputed per briefing. At runtime the
coefficients ``a, b`` are fixed and we just apply
``EDR = exp(a + b·ln D)``, clipped to ~[0, 1].

Diagnostic for Richardson number
--------------------------------
Richardson number is the *inverse* of turbulence intensity (low Ri ⇒ turbulent)
and ``compute_stability_indicators`` only assigns Ri when ``N² >= 0`` (so
Ri ≥ 0). We therefore define the positive, turbulence-increasing diagnostic::

    D_ri = 1 / max(Ri, RI_FLOOR)

``RI_FLOOR`` caps ``D`` so a near-zero Ri does not blow up to infinity.

Scope
-----
v0 covers the calibration accumulator + the Richardson remap. The C1/C2
lognormal climatology is only valid down to 10 kft; below that (boundary
layer) the operational method (Pearson & Sharman 2018) switches to a
log-Weibull distribution for daytime/convective conditions. That regime is
**out of scope** for v0 — the lowest band (0–10 kft) here uses the lognormal
pair below and is therefore *approximate* for the convective boundary layer.

References
----------
- Sharman & Pearson 2017, J. Appl. Meteor. Climatol. 56, 229–243.
- Kim et al. 2020, AMT 13, 1373–1385 (open-access reproduction of C1/C2).
- Pearson & Sharman 2018, JAMC 57(6) (boundary-layer log-Weibull, follow-up).
"""

from __future__ import annotations

import math
import threading

# Diagnostic identifiers (room for "e_shear" in a follow-up).
DIAGNOSTIC_RICHARDSON = "richardson"

# Floor applied to Ri before inverting, so D = 1/max(Ri, RI_FLOOR) stays bounded.
RI_FLOOR = 0.01

# Published universal EDR climatology (mean, SD of ln EDR), per altitude band.
# Kim et al. 2020 / Sharman & Pearson 2017. The ">45 kft" regime is clipped to
# the 20–45 kft constants for now (see band_for_altitude_ft).
C1_C2_BY_BAND: dict[str, tuple[float, float]] = {
    "0_10kft": (-2.248, 0.4235),   # NOTE: approximate — convective BL wants log-Weibull (out of scope v0)
    "10_20kft": (-2.578, 0.557),
    "20_45kft": (-2.953, 0.602),
    "all": (-2.572, 0.5067),       # all-altitude (>0 ft), sanity cross-check band
}

# Ordered list of altitude bands (excludes the "all" cross-check band).
ALTITUDE_BANDS = ("0_10kft", "10_20kft", "20_45kft")
ALL_BAND = "all"

# Lower-bound on variance so SD never collapses to 0 (and b never explodes).
# Public so the CLI readout can floor its display SD identically.
VAR_EPS = 1e-12


def band_for_altitude_ft(altitude_ft: float | None) -> str | None:
    """Return the altitude-band key for a level's geometric altitude (ft).

    Bands: 0–10 kft, 10–20 kft, 20–45 kft. Levels above 45 kft are clipped
    into the 20–45 kft band (its constants are the closest published pair).
    Returns ``None`` only when altitude is unknown.
    """
    if altitude_ft is None or not math.isfinite(altitude_ft):
        return None
    kft = altitude_ft / 1000.0
    if kft < 10.0:
        return "0_10kft"
    if kft < 20.0:
        return "10_20kft"
    return "20_45kft"  # includes >45 kft, clipped per v0 scope


def richardson_to_d(ri: float | None) -> float | None:
    """Map a Richardson number to the positive turbulence diagnostic ``D``.

    ``D = 1 / max(Ri, RI_FLOOR)``. Returns ``None`` for missing/non-finite Ri
    so the caller skips the level (no ``ln(D)`` is taken).
    """
    if ri is None or not math.isfinite(ri):
        return None
    return 1.0 / max(ri, RI_FLOOR)


def coefficients_from_accumulator(
    n: int, sum_ln: float, sum_ln2: float, c1: float, c2: float,
) -> tuple[float, float] | None:
    """Derive the remap coefficients ``(a, b)`` from accumulated ln-moments.

    ``b = C2 / SD[ln D]``, ``a = C1 − b·⟨ln D⟩``. Returns ``None`` when there
    are too few samples to estimate a variance (``n < 2``).
    """
    if n < 2:
        return None
    mean = sum_ln / n
    var = sum_ln2 / n - mean * mean
    sd = math.sqrt(max(var, VAR_EPS))
    b = c2 / sd
    a = c1 - b * mean
    return a, b


def diagnostic_to_edr(d: float | None, a: float, b: float) -> float | None:
    """Apply the frozen remap: ``EDR = exp(a + b·ln D)``, clipped to [0, 1].

    Returns ``None`` for non-positive / non-finite ``D``.
    """
    if d is None or not math.isfinite(d) or d <= 0.0:
        return None
    edr = math.exp(a + b * math.log(d))
    return min(max(edr, 0.0), 1.0)


class EdrAccumulator:
    """Thread-safe streaming accumulator of ``ln(D)`` moments for EDR calibration.

    Holds running ``(n, Σ ln D, Σ (ln D)²)`` keyed by
    ``(model, diagnostic, band)`` — constant memory, no per-sample archive.
    The accumulated moments are independent of ``C1/C2``: if the target
    climatology is later refined (e.g. log-Weibull low band), ``a, b`` are
    re-derived from the *same* stored moments without re-accumulation.

    Designed to be fed concurrently from the standalone fetch thread pool:
    :meth:`observe_richardson_levels` computes one snapshot's partial sums
    lock-free, then merges them under a single lock acquisition.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> [n, sum_ln, sum_ln2]
        self._data: dict[tuple[str, str, str], list[float]] = {}

    def observe_richardson_levels(self, model: str, derived_levels) -> None:
        """Accumulate ln(D_ri) moments for every valid level of one sounding.

        Samples the *full* distribution — every adjacent-level Ri, not only
        layers flagged as turbulent — so the climatological mean is unbiased.
        Each valid level contributes to both its altitude band and the ``all``
        cross-check band. Non-finite / non-positive ``D`` levels are dropped.
        """
        partials: dict[tuple[str, str, str], list[float]] = {}
        for lv in derived_levels:
            ri = getattr(lv, "richardson_number", None)
            d = richardson_to_d(ri)
            if d is None or d <= 0.0:
                continue
            ln_d = math.log(d)
            if not math.isfinite(ln_d):
                continue
            ln_d2 = ln_d * ln_d
            band = band_for_altitude_ft(getattr(lv, "altitude_ft", None))
            for key_band in (band, ALL_BAND):
                if key_band is None:
                    continue
                key = (model, DIAGNOSTIC_RICHARDSON, key_band)
                slot = partials.get(key)
                if slot is None:
                    partials[key] = [1.0, ln_d, ln_d2]
                else:
                    slot[0] += 1.0
                    slot[1] += ln_d
                    slot[2] += ln_d2

        if not partials:
            return
        with self._lock:
            for key, (n, s1, s2) in partials.items():
                tot = self._data.get(key)
                if tot is None:
                    self._data[key] = [n, s1, s2]
                else:
                    tot[0] += n
                    tot[1] += s1
                    tot[2] += s2

    def rows(self) -> list[tuple[str, str, str, int, float, float]]:
        """Snapshot the accumulated moments as ``(model, diagnostic, band, n, sum_ln, sum_ln2)``."""
        with self._lock:
            return [
                (model, diag, band, int(vals[0]), vals[1], vals[2])
                for (model, diag, band), vals in self._data.items()
            ]
