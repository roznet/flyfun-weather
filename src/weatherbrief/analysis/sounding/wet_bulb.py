"""Fast pseudo-adiabatic wet-bulb temperature.

MetPy's ``wet_bulb_temperature`` integrates the moist adiabat with an
adaptive LSODA solver *per pressure level* — ~28 separate ODE solves per
sounding, ~50 ms, about a third of ``analyze_sounding`` wall time. This
module follows the exact same definition — lift each level to its LCL
(MetPy's own ``lcl``, so adiabat selection is identical), then descend the
pseudoadiabat back to the level pressure — but integrates **all levels
simultaneously** with a fixed-step vectorized RK4 using MetPy's own
integrand (``saturation_mixing_ratio._nounit`` + MetPy constants).

Agreement with ``mpcalc.wet_bulb_temperature`` on a 2,860-point grid
spanning p 150-1013 hPa, T -55..+40 degC, dewpoint depression 0-35 degC:
max |delta| 2.4e-5 degC — zero disagreement after the 0.1 degC rounding
applied by the only consumer (``DerivedLevel.wet_bulb_c``). Verified by
tests/test_wet_bulb.py.

If MetPy's private ``_nounit`` seam disappears in a future release, the
public ``wet_bulb_degc`` falls back to ``mpcalc.wet_bulb_temperature``.
"""

from __future__ import annotations

import logging

import metpy.calc as mpcalc
import numpy as np
from metpy.constants import nounit

logger = logging.getLogger(__name__)

# Fixed RK4 step count for the LCL -> level descent. The descent interval is
# short (typically < 300 hPa) and the integrand smooth; 40 steps puts the
# truncation error around 1e-5 degC, far below the 0.1 degC storage rounding.
_RK4_STEPS = 40

_ZERO_DEGC_K = 273.15


def _moist_lapse_dtdp(t_k: np.ndarray, p_pa: np.ndarray) -> np.ndarray:
    """dT/dp along the pseudoadiabat — same expression MetPy's moist_lapse
    hands to LSODA ([Bakhshaii2013], with MetPy's saturation mixing ratio)."""
    rs = mpcalc.saturation_mixing_ratio._nounit(p_pa, t_k)
    frac = (nounit.Rd * t_k + nounit.Lv * rs) / (
        nounit.Cp_d + (nounit.Lv * nounit.Lv * rs * nounit.epsilon / (nounit.Rd * t_k**2))
    )
    return frac / p_pa


def _wet_bulb_fast(pressure, temperature, dewpoint) -> np.ndarray:
    """Vectorized wet bulb (degC ndarray) from pint-wrapped profile arrays."""
    lcl_p, lcl_t = mpcalc.lcl(pressure, temperature, dewpoint)

    p_pa = np.atleast_1d(pressure.to("Pa").magnitude).astype(float)
    p_cur = np.atleast_1d(lcl_p.to("Pa").magnitude).astype(float).copy()
    t_k = np.atleast_1d(lcl_t.to("kelvin").magnitude).astype(float).copy()

    # Descend from each level's LCL back to the level pressure, all levels in
    # lockstep (each with its own step size; zero for saturated levels).
    dp = (p_pa - p_cur) / _RK4_STEPS
    for _ in range(_RK4_STEPS):
        k1 = _moist_lapse_dtdp(t_k, p_cur)
        k2 = _moist_lapse_dtdp(t_k + 0.5 * dp * k1, p_cur + 0.5 * dp)
        k3 = _moist_lapse_dtdp(t_k + 0.5 * dp * k2, p_cur + 0.5 * dp)
        k4 = _moist_lapse_dtdp(t_k + dp * k3, p_cur + dp)
        t_k = t_k + dp / 6.0 * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
        p_cur = p_cur + dp

    return t_k - _ZERO_DEGC_K


def wet_bulb_degc(pressure, temperature, dewpoint) -> np.ndarray:
    """Wet-bulb temperature in degC for pint-wrapped sounding arrays.

    Drop-in replacement for
    ``mpcalc.wet_bulb_temperature(...).to("degC").magnitude`` on 1D profiles;
    falls back to it if the fast path fails (e.g. MetPy internals changed).
    """
    try:
        return _wet_bulb_fast(pressure, temperature, dewpoint)
    except Exception:
        logger.warning(
            "Fast wet-bulb path failed — falling back to MetPy ODE solver",
            exc_info=True,
        )
        return mpcalc.wet_bulb_temperature(pressure, temperature, dewpoint).to("degC").magnitude
