"""Unit-free parcel kernel: LCL, parcel path, LFC/EL, CAPE/CIN, MU/ML, LI, Showalter.

MetPy's parcel functions cost ~18 ms per profile (#704), and almost none of
that is arithmetic: profiles have 20-28 levels, so the time goes to pint
unit handling, MetPy's argument-checking decorators, and ``solve_ivp``
(LSODA) calling a pint-wrapped integrand a few hundred times per ascent.
This module is a line-by-line port of the MetPy 1.7 algorithms on plain
floats, so it makes the same choices (which LFC, which EL, where the LCL is
inserted, how zero crossings are appended, the ``isclose`` tolerances)
without the overhead:

- the LCL is MetPy's own ``lcl._nounit`` (Romps 2017, exact);
- the moist adiabat integrates MetPy's own integrand (``moist_lapse``'s
  ``dt``) with fixed-step RK4 in ln p, in plain Python floats — the same
  move ``wet_bulb.py`` made for the wet-bulb descent;
- everything else (``find_intersections``, ``lfc``, ``el``, ``cape_cin``,
  ``most_unstable_cape_cin``, ``mixed_layer_cape_cin``, ``lifted_index``,
  ``showalter_index``) is ported with the same branch structure.

Units: pressures in hPa, temperatures in kelvin, CAPE/CIN in J/kg. Where
MetPy returns NaN this returns NaN, and where MetPy raises this raises, so
the caller's per-index ``try`` keeps its meaning. Parity against MetPy is
pinned by tests/test_parcel_kernel.py; the agreed tolerances are recorded in
designs/meteorology-decisions.md.
"""

from __future__ import annotations

import math

import metpy.calc as mpcalc
import numpy as np
from metpy.constants import nounit as _c

_RD = _c.Rd
_RV = _c.Rv
_LV = _c.Lv
_CP_D = _c.Cp_d
_CP_L = _c.Cp_l
_CP_V = _c.Cp_v
_EPS = _c.epsilon
_T0 = _c.T0  # 273.16 K: the reference for MetPy's latent heat and e_s
_ES0 = _c.sat_pressure_0c  # Pa
_ZERO_DEGC = _c.zero_degc  # 273.15 K
_KAPPA = _c.kappa
_HEAT_POWER = (_CP_L - _CP_V) / _RV
_LV_T0 = _LV / _T0

# RK4 step cap in ln p for the moist adiabat: ~5% of pressure per step,
# about 46 steps from 1000 to 100 hPa. Against MetPy's LSODA (rtol 1.5e-8)
# the parcel temperature agrees to ~1e-4 K on real packs, 1000x inside the
# 0.1 degC parcel-path tolerance; a coarser step saves little, because by
# then the time is in numpy overhead rather than the ascent.
_MAX_DLNP = 0.05


# ---------------------------------------------------------------------------
# Moisture (vectorised, same expressions as MetPy's _nounit functions)
# ---------------------------------------------------------------------------


def _sat_vapor_pressure(t_k):
    """Saturation vapour pressure over liquid water (Pa), MetPy's formula."""
    latent = _LV - (_CP_L - _CP_V) * (t_k - _T0)
    return _ES0 * (_T0 / t_k) ** _HEAT_POWER * np.exp((_LV_T0 - latent / t_k) / _RV)


def _sat_mixing_ratio(p_hpa, t_k):
    """Saturation mixing ratio (kg/kg); NaN where e_s >= p, as in MetPy."""
    p_pa = np.asarray(p_hpa, dtype=float) * 100.0
    e_s = _sat_vapor_pressure(np.asarray(t_k, dtype=float))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(e_s >= p_pa, np.nan, _EPS * e_s / (p_pa - e_s))


def _virtual_temperature(t_k, mixing_ratio):
    return t_k * ((mixing_ratio + _EPS) / (_EPS * (1 + mixing_ratio)))


def _lcl(p_hpa: float, t_k: float, td_k: float) -> tuple[float, float]:
    """LCL (hPa, K) — MetPy's own unit-free ``lcl``."""
    p_lcl, t_lcl = mpcalc.lcl._nounit(p_hpa * 100.0, t_k, td_k)
    return float(p_lcl) / 100.0, float(t_lcl)


# ---------------------------------------------------------------------------
# Moist adiabat
# ---------------------------------------------------------------------------


def _dt_dlnp(p_pa: float, t: float) -> float:
    """dT/dln p along the pseudoadiabat: p times MetPy's ``moist_lapse`` integrand."""
    latent = _LV - (_CP_L - _CP_V) * (t - _T0)
    e_s = _ES0 * (_T0 / t) ** _HEAT_POWER * math.exp((_LV_T0 - latent / t) / _RV)
    if e_s >= p_pa:
        return math.nan
    rs = _EPS * e_s / (p_pa - e_s)
    return (_RD * t + _LV * rs) / (_CP_D + (_LV * _LV * rs * _EPS / (_RD * t * t)))


def _moist_lapse(p_hpa: np.ndarray, t_ref: float) -> np.ndarray:
    """Parcel temperature (K) along the pseudoadiabat through (p_hpa[0], t_ref).

    *p_hpa* is ordered away from the reference (MetPy's ``moist_lapse`` with
    the default reference pressure); levels ``isclose`` to the reference take
    the reference temperature, as MetPy's do.
    """
    out = np.empty(len(p_hpa))
    if math.isnan(t_ref) or math.isnan(p_hpa[0]):
        out[:] = np.nan
        return out
    ref = float(p_hpa[0])
    close = _isclose(p_hpa, ref)
    t = t_ref
    lnp = math.log(ref * 100.0)
    for i, p in enumerate(p_hpa):
        if close[i]:
            out[i] = t_ref
            continue
        target = math.log(float(p) * 100.0)
        span = target - lnp
        n = max(1, math.ceil(abs(span) / _MAX_DLNP))
        h = span / n
        for _ in range(n):
            k1 = _dt_dlnp(math.exp(lnp), t)
            k2 = _dt_dlnp(math.exp(lnp + 0.5 * h), t + 0.5 * h * k1)
            k3 = _dt_dlnp(math.exp(lnp + 0.5 * h), t + 0.5 * h * k2)
            k4 = _dt_dlnp(math.exp(lnp + h), t + h * k3)
            t = t + h / 6.0 * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
            lnp += h
        lnp = target
        out[i] = t
    return out


# ---------------------------------------------------------------------------
# Parcel profile
# ---------------------------------------------------------------------------


def _isclose(a, b):
    """``np.isclose`` with its defaults (rtol 1e-5, atol 1e-8, NaN never close).

    Inlined because ``np.isclose`` costs ~8 us a call on these tiny arrays
    and the parcel chain makes ~25 of them per profile.
    """
    with np.errstate(invalid="ignore"):
        return np.abs(a - b) <= 1e-8 + 1e-5 * np.abs(b)


def _greater_or_close(a, value):
    return (a > value) | _isclose(a, value)


def _less_or_close(a, value):
    return (a < value) | _isclose(a, value)


def _parcel_profile_helper(p: np.ndarray, t0: float, td0: float):
    """MetPy's ``_parcel_profile_helper``: (p_lower, p_lcl, p_upper, t_lower, t_lcl, t_upper)."""
    if not np.all(p[:-1] >= p[1:]):
        raise ValueError("Pressure increases between at least two points")
    p_lcl, t_lcl = _lcl(float(p[0]), t0, td0)

    p_lower = np.append(p[p >= p_lcl], p_lcl)
    t_lower = t0 * (p_lower / p_lower[0]) ** _KAPPA

    if _greater_or_close(np.nanmin(p), p_lcl):
        return p_lower[:-1], p_lcl, np.array([]), t_lower[:-1], t_lcl, np.array([])

    p_upper = np.append(p_lcl, p[p < p_lcl])
    unique, inverse = np.unique(p_upper, return_inverse=True)
    t_upper = _moist_lapse(unique[::-1], float(t_lower[-1]))[::-1][inverse]
    return p_lower[:-1], p_lcl, p_upper[1:], t_lower[:-1], t_lcl, t_upper[1:]


def parcel_profile(p: np.ndarray, t0: float, td0: float) -> np.ndarray:
    """Parcel temperature (K) at each level of *p*, LCL not inserted."""
    _, _, _, t_l, _, t_u = _parcel_profile_helper(p, t0, td0)
    return np.concatenate((t_l, t_u))


def _interp_1d(x: float, xp: np.ndarray, *arrays: np.ndarray) -> list[float]:
    """MetPy ``interpolate_1d`` at one point: linear, NaN out of range."""
    order = np.argsort(xp)
    xs = xp[order]
    i = int(np.searchsorted(xs, x))
    n = len(xs)
    out_of_range = i == n
    i2 = min(max(i, 1), n - 1)
    lo, hi = xs[i2 - 1], xs[i2]
    if x < lo:
        out_of_range = True
    res = []
    for arr in arrays:
        a = arr[order]
        v = a[i2 - 1] + (a[i2] - a[i2 - 1]) * ((x - lo) / (hi - lo))
        res.append(math.nan if out_of_range else float(v))
    return res


def _insert_lcl_level(p: np.ndarray, values: np.ndarray, p_lcl: float) -> np.ndarray:
    (v,) = _interp_1d(p_lcl, p, values)
    loc = p.size - p[::-1].searchsorted(p_lcl)
    return np.insert(values, loc, v)


def parcel_profile_with_lcl(p: np.ndarray, t: np.ndarray, td: np.ndarray):
    """MetPy's ``parcel_profile_with_lcl``: (p, t_env, td_env, t_parcel) with the LCL inserted."""
    p_l, p_lcl, p_u, t_l, t_lcl, t_u = _parcel_profile_helper(p, float(t[0]), float(td[0]))
    new_p = np.concatenate((p_l, [p_lcl], p_u))
    prof = np.concatenate((t_l, [t_lcl], t_u))
    return new_p, _insert_lcl_level(p, t, p_lcl), _insert_lcl_level(p, td, p_lcl), prof


# ---------------------------------------------------------------------------
# Intersections, LFC, EL
# ---------------------------------------------------------------------------


def _find_intersections(x, a, b, direction="all", log_x=False):
    """MetPy ``find_intersections`` (no masked-array support needed here)."""
    if log_x:
        x = np.log(x)
    nearest = np.nonzero(np.diff(np.sign(a - b)))[0]
    nxt = nearest + 1
    sign_change = np.sign(a[nxt] - b[nxt])
    x0, x1 = x[nearest], x[nxt]
    a0, a1 = a[nearest], a[nxt]
    dy0 = a0 - b[nearest]
    dy1 = a1 - b[nxt]
    with np.errstate(invalid="ignore", divide="ignore"):
        ix = (dy1 * x0 - dy0 * x1) / (dy1 - dy0)
        iy = ((ix - x0) / (x1 - x0)) * (a1 - a0) + a0
    if len(ix) == 0:
        return ix, iy
    if log_x:
        ix = np.exp(ix)
    keep = np.ediff1d(ix, to_end=1) != 0
    if direction == "increasing":
        keep &= sign_change > 0
    elif direction == "decreasing":
        keep &= sign_change < 0
    return ix[keep], iy[keep]


def _remove_nans(*arrays):
    mask = np.zeros(len(arrays[0]), dtype=bool)
    for a in arrays:
        mask |= np.isnan(a)
    return [a[~mask] for a in arrays]


def _pick(x, y, valid, which):
    xs, ys = x[valid], y[valid]
    if which == "bottom":
        return float(xs[0]), float(ys[0])
    if which == "top":
        return float(xs[-1]), float(ys[-1])
    raise ValueError(f"unsupported which={which!r}")


def lfc(p, t, td, parcel, which="top", dewpoint_start=None) -> float:
    """LFC pressure (hPa) or NaN — MetPy ``lfc`` with a parcel profile given."""
    p, t, td, parcel = _remove_nans(p, t, td, parcel)
    if dewpoint_start is None:
        dewpoint_start = float(td[0])
    # MetPy compares the first point in the environment's units (degC).
    if _isclose(parcel[0] - _ZERO_DEGC, t[0] - _ZERO_DEGC):
        x, y = _find_intersections(p[1:], parcel[1:], t[1:], "increasing", log_x=True)
    else:
        x, y = _find_intersections(p, parcel, t, "increasing", log_x=True)
    lcl_p, _ = _lcl(float(p[0]), float(parcel[0]), dewpoint_start)

    if len(x) == 0:
        mask = p < lcl_p
        if np.all(_less_or_close(parcel[mask], t[mask])):
            return math.nan
        return lcl_p

    idx = x < lcl_p
    if not np.any(idx):
        el_p, _ = _find_intersections(p[1:], parcel[1:], t[1:], "decreasing", log_x=True)
        if el_p.size and np.min(el_p) > lcl_p:
            return math.nan
        return lcl_p
    return _pick(x, y, idx, which)[0]


def el(p, t, td, parcel, which="top") -> float:
    """EL pressure (hPa) or NaN — MetPy ``el`` with a parcel profile given."""
    p, t, td, parcel = _remove_nans(p, t, td, parcel)
    if parcel[-1] > t[-1]:
        return math.nan
    x, y = _find_intersections(p[1:], parcel[1:], t[1:], "decreasing", log_x=True)
    lcl_p, _ = _lcl(float(p[0]), float(t[0]), float(td[0]))
    if len(x) > 0 and x[-1] < lcl_p:
        return _pick(x, y, x < lcl_p, which)[0]
    return math.nan


# ---------------------------------------------------------------------------
# CAPE / CIN
# ---------------------------------------------------------------------------


def _find_append_zero_crossings(x, y):
    cx, cy = _find_intersections(x[1:], y[1:], np.zeros_like(y[1:]), log_x=True)
    x = np.concatenate((x, cx))
    y = np.concatenate((y, cy))
    order = np.argsort(x)
    x, y = x[order], y[order]
    keep = np.ediff1d(x, to_end=[1]) > 1e-6
    return x[keep], y[keep]


def cape_cin(p, t, td, parcel, which_lfc="bottom", which_el="top") -> tuple[float, float]:
    """(CAPE, CIN) in J/kg — MetPy ``cape_cin`` (virtual-temperature corrected)."""
    p, t, td, parcel = _remove_nans(p, t, td, parcel)
    lcl_p, _ = _lcl(float(p[0]), float(t[0]), float(td[0]))
    below_lcl = p > lcl_p
    parcel_mr = np.where(
        below_lcl,
        _sat_mixing_ratio(p[0], td[0]),
        _sat_mixing_ratio(p, parcel),
    )
    tv = _virtual_temperature(t, _sat_mixing_ratio(p, td))
    parcel_v = _virtual_temperature(parcel, parcel_mr)

    lfc_p = lfc(p, tv, td, parcel_v, which=which_lfc)
    if math.isnan(lfc_p):
        return 0.0, 0.0
    el_p = el(p, tv, td, parcel_v, which=which_el)
    if math.isnan(el_p):
        el_p = float(p[-1])

    x, y = _find_append_zero_crossings(np.copy(p), parcel_v - tv)

    mask = _less_or_close(x, lfc_p) & _greater_or_close(x, el_p)
    cape = _RD * float(np.trapezoid(y[mask], np.log(x[mask])))

    mask = _greater_or_close(x, lfc_p)
    cin = _RD * float(np.trapezoid(y[mask], np.log(x[mask])))
    return cape, min(cin, 0.0)


def _theta_e(p_hpa, t_k, td_k):
    """MetPy ``equivalent_potential_temperature`` (Bolton 1980), kelvin."""
    r = _sat_mixing_ratio(p_hpa, td_k)
    e = _sat_vapor_pressure(td_k) / 100.0
    t_l = 56 + 1.0 / (1.0 / (td_k - 56) + np.log(t_k / td_k) / 800.0)
    th_l = t_k / ((p_hpa - e) / 1000.0) ** _KAPPA * (t_k / t_l) ** (0.28 * r)
    return th_l * np.exp(r * (1 + 0.448 * r) * (3036.0 / t_l - 1.78))


def most_unstable_cape_cin(p, t, td, depth_hpa: float = 300.0) -> tuple[float, float]:
    """MU CAPE/CIN — highest-theta-e parcel in the lowest *depth_hpa* (MetPy defaults)."""
    p, t, td = _remove_nans(p, t, td)
    # get_layer(interpolate=False): the top bound snaps to the nearest level.
    top = p[int(np.abs(p - (np.nanmax(p) - depth_hpa)).argmin())]
    in_layer = _less_or_close(p, np.nanmax(p)) & _greater_or_close(p, top)
    layer_idx = np.nonzero(in_layer)[0]
    theta_e = _theta_e(p[layer_idx], t[layer_idx], td[layer_idx])
    start = int(np.argmax(theta_e))
    pp, tt, tdd, prof = parcel_profile_with_lcl(p[start:], t[start:], td[start:])
    return cape_cin(pp, tt, tdd, prof)


def _log_interp(x_new: np.ndarray, xp: np.ndarray, values: np.ndarray) -> np.ndarray:
    """MetPy ``log_interpolate_1d`` for in-range points (xp ascending)."""
    lx, lxp = np.log(x_new), np.log(xp)
    idx = np.clip(np.searchsorted(lxp, lx), 1, len(lxp) - 1)
    lo, hi = idx - 1, idx
    return values[lo] + (values[hi] - values[lo]) * ((lx - lxp[lo]) / (lxp[hi] - lxp[lo]))


def mixed_layer_cape_cin(p, t, td, depth_hpa: float = 100.0) -> tuple[float, float]:
    """ML CAPE/CIN — parcel mixed over the lowest *depth_hpa* (MetPy defaults)."""
    start_p = float(p[0])
    bottom = float(np.nanmax(p))
    top = bottom - depth_hpa
    if not (_greater_or_close(top, np.nanmin(p)) and _less_or_close(top, np.nanmax(p))):
        raise ValueError("Specified bound is outside pressure range.")

    theta = t / (p / 1000.0) ** _KAPPA
    mr = _sat_mixing_ratio(p, td)

    order = np.argsort(p)
    ps = p[order]
    in_layer = _less_or_close(ps, bottom) & _greater_or_close(ps, top)
    p_layer = ps[in_layer]
    if not np.any(_isclose(top, p_layer)):
        p_layer = np.sort(np.append(p_layer, top))
    if not np.any(_isclose(bottom, p_layer)):
        p_layer = np.sort(np.append(p_layer, bottom))
    p_layer = p_layer[::-1]
    depth = abs(p_layer[0] - p_layer[-1])
    mean_theta = np.trapezoid(_log_interp(p_layer, ps, theta[order]), p_layer) / -depth
    mean_mr = np.trapezoid(_log_interp(p_layer, ps, mr[order]), p_layer) / -depth

    mean_t = mean_theta * (start_p / 1000.0) ** _KAPPA
    e = start_p * 100.0 * mean_mr / (_EPS + mean_mr)
    val = math.log(e / _ES0)
    mean_td = _ZERO_DEGC + 243.5 * val / (17.67 - val)

    above = p < (start_p - depth_hpa)
    pp, tt, tdd, prof = parcel_profile_with_lcl(
        np.concatenate(([start_p], p[above])),
        np.concatenate(([mean_t], t[above])),
        np.concatenate(([mean_td], td[above])),
    )
    return cape_cin(pp, tt, tdd, prof)


# ---------------------------------------------------------------------------
# Stability indices
# ---------------------------------------------------------------------------


def lifted_index(p, t, parcel) -> float:
    """T_env(500) - T_parcel(500), linear in p; NaN when 500 hPa is out of range."""
    t500, tp500 = _interp_1d(500.0, p, t, parcel)
    return t500 - tp500


def showalter_index(p, t, td) -> float:
    """T_env(500) - T(850 hPa parcel lifted to 500); NaN when out of range."""
    t850, td850 = _interp_1d(850.0, p, t, td)
    (t500,) = _interp_1d(500.0, p, t)
    prof = parcel_profile(np.array([850.0, 500.0]), t850, td850)
    return t500 - float(prof[-1])
