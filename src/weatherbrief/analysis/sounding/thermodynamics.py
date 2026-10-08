"""MetPy-based thermodynamic computations for sounding analysis.

All MetPy calls are isolated here. Takes a PreparedProfile and returns
ThermodynamicIndices + list[DerivedLevel] with plain-number values.
"""

from __future__ import annotations

import logging
import os
from typing import NamedTuple

import metpy.calc as mpcalc
import numpy as np
from metpy.units import units

from weatherbrief.analysis.sounding import parcel
from weatherbrief.analysis.sounding.prepare import PreparedProfile
from weatherbrief.analysis.sounding.wet_bulb import wet_bulb_degc
from weatherbrief.models import DerivedLevel, ParcelPathPoint, ThermodynamicIndices
from weatherbrief.models.analysis import pressure_hpa_to_altitude_m

logger = logging.getLogger(__name__)

M_TO_FT = 3.28084


def _mag(quantity) -> float | None:
    """Extract magnitude from a pint Quantity, or return None.

    Size-1 arrays are unwrapped. MetPy returns several indices as a
    one-element array rather than a scalar — ``lifted_index`` is the one that
    bit us — and NumPy 2 refuses ``float()`` on any array with ``ndim > 0``.
    That raised inside the caller's ``round()``, was swallowed by its
    ``except Exception``, and left ``sounding_lifted_index`` NULL on *every*
    profile ever written: 110,801 of 110,801 rows on a sampled day, across all
    three models, with nothing in the logs above DEBUG.

    Unwrapping is unambiguous only for size 1 — anything larger is a genuine
    profile and must not be silently collapsed to its first element.
    """
    if quantity is None:
        return None
    try:
        magnitude = quantity.magnitude
    except AttributeError:
        return None
    try:
        size = getattr(magnitude, "size", None)
        if size is not None and getattr(magnitude, "ndim", 0) > 0:
            if size != 1:
                return None
            magnitude = magnitude.reshape(())[()]
        return float(magnitude)
    except Exception:
        return None


def _pressure_to_altitude_ft(pressure_hpa: float) -> float:
    """Approximate pressure to altitude conversion (standard atmosphere)."""
    return pressure_hpa_to_altitude_m(pressure_hpa) * M_TO_FT


def _find_temperature_crossing(
    profile: PreparedProfile, target_c: float,
) -> float | None:
    """Find altitude (ft) where temperature crosses target_c by interpolation."""
    temps = profile.temperature.to("degC").magnitude
    if profile.height is not None:
        alts = profile.height.to("meter").magnitude * M_TO_FT
    else:
        alts = np.array([
            _pressure_to_altitude_ft(p) for p in profile.pressure.to("hPa").magnitude
        ])

    # Walk from surface upward (high pressure to low)
    for i in range(len(temps) - 1):
        t0, t1 = temps[i], temps[i + 1]
        if (t0 - target_c) * (t1 - target_c) <= 0 and t0 != t1:
            # Linear interpolation
            frac = (target_c - t0) / (t1 - t0)
            return float(alts[i] + frac * (alts[i + 1] - alts[i]))
    return None


class CoreIndicesResult(NamedTuple):
    """Result of compute_indices_core: indices + optional parcel path array."""

    indices: ThermodynamicIndices
    parcel_path: list[ParcelPathPoint]


class _MetPyParcel:
    """The parcel computations through MetPy (the reference implementation).

    Every method takes and returns plain numbers (hPa, kelvin, J/kg); the
    profile's own pint arrays are passed to MetPy unchanged, so with
    ``WB_SOUNDING_KERNEL=metpy`` the results are exactly the pre-#704 ones.
    """

    def __init__(self, profile: PreparedProfile) -> None:
        self.p = profile.pressure
        self.t = profile.temperature
        self.td = profile.dewpoint

    def lcl_hpa(self) -> float:
        lcl_p, _ = mpcalc.lcl(self.p[0], self.t[0], self.td[0])
        return _mag(lcl_p.to("hPa"))

    def parcel_k(self) -> np.ndarray:
        return mpcalc.parcel_profile(self.p, self.t[0], self.td[0]).to("kelvin").magnitude

    def lfc_hpa(self, parcel_k: np.ndarray) -> float | None:
        lfc_p, _ = mpcalc.lfc(
            self.p, self.t, self.td, parcel_temperature_profile=parcel_k * units.kelvin,
        )
        return None if lfc_p is None else _mag(lfc_p.to("hPa"))

    def el_hpa(self, parcel_k: np.ndarray) -> float | None:
        el_p, _ = mpcalc.el(
            self.p, self.t, self.td, parcel_temperature_profile=parcel_k * units.kelvin,
        )
        return None if el_p is None else _mag(el_p.to("hPa"))

    def cape_cin(self, parcel_k: np.ndarray) -> tuple[float, float]:
        cape, cin = mpcalc.cape_cin(self.p, self.t, self.td, parcel_k * units.kelvin)
        return _mag(cape.to("J/kg")), _mag(cin.to("J/kg"))

    def mu_cape(self) -> float:
        cape, _ = mpcalc.most_unstable_cape_cin(self.p, self.t, self.td)
        return _mag(cape.to("J/kg"))

    def ml_cape(self) -> float:
        cape, _ = mpcalc.mixed_layer_cape_cin(self.p, self.t, self.td)
        return _mag(cape.to("J/kg"))

    def lifted_index(self, parcel_k: np.ndarray) -> float:
        li = mpcalc.lifted_index(self.p, self.t, parcel_k * units.kelvin)
        return _mag(li.to("delta_degC"))

    def showalter(self) -> float:
        return _mag(mpcalc.showalter_index(self.p, self.t, self.td).to("delta_degC"))


_FALLBACK_WARNED: set[str] = set()


class _KernelParcel:
    """The same computations through the unit-free kernel (``parcel.py``).

    Any kernel exception falls back to MetPy for that one value, so a kernel
    gap can never turn a value MetPy would produce into ``None``; when MetPy
    raises too, the caller's ``try`` reports ``None`` as before.
    """

    def __init__(self, profile: PreparedProfile) -> None:
        self._metpy = _MetPyParcel(profile)
        self.p = profile.pressure.to("hPa").magnitude.astype(float)
        self.t = profile.temperature.to("kelvin").magnitude.astype(float)
        self.td = profile.dewpoint.to("kelvin").magnitude.astype(float)

    def _try(self, name: str, kernel_call, *args):
        try:
            return kernel_call()
        except Exception:
            logger.debug("Parcel kernel %s failed, using MetPy", name, exc_info=True)
            # If MetPy raises too, the profile simply has no such value (high
            # terrain without an 850 hPa parcel, ...) and the caller reports
            # None. Only a kernel failure MetPy does not share is a divergence
            # (or a renamed MetPy private seam) worth surfacing: warn once per
            # value per process, so it shows in prod logs without flooding.
            value = getattr(self._metpy, name)(*args)
            if name not in _FALLBACK_WARNED:
                _FALLBACK_WARNED.add(name)
                logger.warning(
                    "Parcel kernel %s raised where MetPy succeeded; using MetPy "
                    "(logged once per process)", name, exc_info=True,
                )
            return value

    def lcl_hpa(self) -> float:
        return self._try("lcl_hpa", lambda: parcel._lcl(self.p[0], self.t[0], self.td[0])[0])

    def parcel_k(self) -> np.ndarray:
        return self._try(
            "parcel_k", lambda: parcel.parcel_profile(self.p, self.t[0], self.td[0]),
        )

    def lfc_hpa(self, parcel_k: np.ndarray) -> float | None:
        return self._try(
            "lfc_hpa", lambda: parcel.lfc(self.p, self.t, self.td, parcel_k), parcel_k,
        )

    def el_hpa(self, parcel_k: np.ndarray) -> float | None:
        return self._try(
            "el_hpa", lambda: parcel.el(self.p, self.t, self.td, parcel_k), parcel_k,
        )

    def cape_cin(self, parcel_k: np.ndarray) -> tuple[float, float]:
        return self._try(
            "cape_cin", lambda: parcel.cape_cin(self.p, self.t, self.td, parcel_k), parcel_k,
        )

    def mu_cape(self) -> float:
        return self._try(
            "mu_cape", lambda: parcel.most_unstable_cape_cin(self.p, self.t, self.td)[0],
        )

    def ml_cape(self) -> float:
        return self._try(
            "ml_cape", lambda: parcel.mixed_layer_cape_cin(self.p, self.t, self.td)[0],
        )

    def lifted_index(self, parcel_k: np.ndarray) -> float:
        return self._try(
            "lifted_index", lambda: parcel.lifted_index(self.p, self.t, parcel_k), parcel_k,
        )

    def showalter(self) -> float:
        return self._try("showalter", lambda: parcel.showalter_index(self.p, self.t, self.td))


def _parcel_backend(profile: PreparedProfile) -> _MetPyParcel | _KernelParcel:
    """Kernel by default; ``WB_SOUNDING_KERNEL=metpy`` restores pure MetPy (#704).

    Built once per profile and kept on it, so the core and extended passes
    share one backend (and its unit conversions); keyed by the mode so a
    changed env var still takes effect.
    """
    mode = os.environ.get("WB_SOUNDING_KERNEL", "").strip().lower() == "metpy"
    cached = profile.parcel_backend
    if cached is not None and cached[0] == mode:
        return cached[1]
    calc = _MetPyParcel(profile) if mode else _KernelParcel(profile)
    profile.parcel_backend = (mode, calc)
    return calc


def _round_level(value: float | None) -> float | None:
    """Round a level pressure to 0.1 hPa; ``None`` for a missing (NaN) level."""
    if value is None or np.isnan(value):
        return None
    return round(value, 1)


def compute_indices_core(profile: PreparedProfile) -> CoreIndicesResult:
    """Compute core thermodynamic indices needed for ceiling and convective risk.

    This is the lightweight subset: LCL, parcel profile, LFC, EL,
    surface CAPE/CIN, lifted index, and freezing level.  Used by both
    the full briefing pipeline and standalone verification.

    Returns a CoreIndicesResult with the indices and the parcel path array
    (captured for client-side Skew-T CAPE/CIN rendering).
    """
    idx = ThermodynamicIndices()
    calc = _parcel_backend(profile)

    # --- LCL ---
    try:
        idx.lcl_pressure_hpa = round(calc.lcl_hpa(), 1)
        idx.lcl_altitude_ft = round(_pressure_to_altitude_ft(idx.lcl_pressure_hpa))
    except Exception:
        logger.debug("LCL computation failed", exc_info=True)

    # --- Parcel profile (needed for LFC, EL, CAPE/CIN) ---
    try:
        parcel_k = calc.parcel_k()
    except Exception:
        logger.debug("Parcel profile computation failed", exc_info=True)
        parcel_k = None

    # --- LFC ---
    if parcel_k is not None:
        try:
            lfc_hpa = _round_level(calc.lfc_hpa(parcel_k))
            if lfc_hpa is not None:
                idx.lfc_pressure_hpa = lfc_hpa
                idx.lfc_altitude_ft = round(_pressure_to_altitude_ft(lfc_hpa))
        except Exception:
            logger.debug("LFC computation failed", exc_info=True)

    # --- EL ---
    if parcel_k is not None:
        try:
            el_hpa = _round_level(calc.el_hpa(parcel_k))
            if el_hpa is not None:
                idx.el_pressure_hpa = el_hpa
                idx.el_altitude_ft = round(_pressure_to_altitude_ft(el_hpa))
        except Exception:
            logger.debug("EL computation failed", exc_info=True)

    # --- CAPE / CIN (surface-based) ---
    if parcel_k is not None:
        try:
            cape, cin = calc.cape_cin(parcel_k)
            idx.cape_surface_jkg = round(cape, 1)
            idx.cin_surface_jkg = round(cin, 1)
        except Exception:
            logger.debug("Surface CAPE/CIN failed", exc_info=True)

    # --- Most-unstable CAPE (elevated convection) ---
    # In core, not extended: the convective regime tier scores on ML-CAPE and
    # flags elevated instability from MU-CAPE, so both must be available wherever
    # convective risk is assessed (briefing and standalone verification alike).
    try:
        idx.cape_most_unstable_jkg = round(calc.mu_cape(), 1)
    except Exception:
        logger.debug("MU CAPE failed", exc_info=True)

    # --- Mixed-layer CAPE (realizable, well-mixed boundary layer) ---
    try:
        idx.cape_mixed_layer_jkg = round(calc.ml_cape(), 1)
    except Exception:
        logger.debug("ML CAPE failed", exc_info=True)

    # --- Lifted index ---
    if parcel_k is not None:
        try:
            idx.lifted_index = round(calc.lifted_index(parcel_k), 1)
        except Exception:
            logger.debug("Lifted index failed", exc_info=True)

    # --- Temperature crossings ---
    idx.freezing_level_ft = _safe_round(_find_temperature_crossing(profile, 0.0))
    idx.minus10c_level_ft = _safe_round(_find_temperature_crossing(profile, -10.0))
    idx.minus20c_level_ft = _safe_round(_find_temperature_crossing(profile, -20.0))

    # --- Capture parcel path for client-side CAPE/CIN rendering ---
    parcel_path: list[ParcelPathPoint] = []
    if parcel_k is not None:
        try:
            pressures = profile.pressure.to("hPa").magnitude
            for p_val, t_val in zip(pressures, parcel_k - 273.15):
                if not np.isnan(t_val):
                    parcel_path.append(
                        ParcelPathPoint(
                            pressure_hpa=round(float(p_val), 1),
                            temperature_c=round(float(t_val), 2),
                        )
                    )
        except Exception:
            logger.debug("Parcel path capture failed", exc_info=True)

    return CoreIndicesResult(indices=idx, parcel_path=parcel_path)


def compute_indices_extended(
    profile: PreparedProfile,
    idx: ThermodynamicIndices,
) -> None:
    """Compute extended indices on top of core results (mutates *idx* in-place).

    Adds Showalter, K-index, Total Totals, precipitable water, and bulk wind
    shear.  These are used by the full briefing pipeline (icing severity
    modifiers, cloud top uncertainty, Skew-T display) but not needed for
    standalone verification scoring.  (MU-CAPE and ML-CAPE moved to
    ``compute_indices_core`` — the convective tier needs them everywhere.)
    """
    p = profile.pressure
    t = profile.temperature
    td = profile.dewpoint

    # --- Showalter index ---
    try:
        idx.showalter_index = round(_parcel_backend(profile).showalter(), 1)
    except Exception:
        logger.debug("Showalter index failed", exc_info=True)

    # --- K-index ---
    try:
        ki = mpcalc.k_index(p, t, td)
        idx.k_index = round(_mag(ki.to("degC")), 1)
    except Exception:
        logger.debug("K-index failed", exc_info=True)

    # --- Total Totals ---
    try:
        tt = mpcalc.total_totals_index(p, t, td)
        idx.total_totals = round(_mag(tt.to("delta_degC")), 1)
    except Exception:
        logger.debug("Total Totals failed", exc_info=True)

    # --- Precipitable water ---
    try:
        pw = mpcalc.precipitable_water(p, td)
        idx.precipitable_water_mm = round(_mag(pw.to("mm")), 1)
    except Exception:
        logger.debug("Precipitable water failed", exc_info=True)

    # --- Bulk wind shear ---
    if profile.wind_speed is not None and profile.wind_direction is not None:
        u, v = mpcalc.wind_components(profile.wind_speed, profile.wind_direction)
        if profile.height is not None:
            heights_m = profile.height.to("meter").magnitude
        else:
            heights_m = np.array([
                _pressure_to_altitude_ft(p_val) / M_TO_FT
                for p_val in profile.pressure.to("hPa").magnitude
            ])

        idx.bulk_shear_0_6km_kt = _compute_bulk_shear(u, v, heights_m, 0, 6000)
        idx.bulk_shear_0_1km_kt = _compute_bulk_shear(u, v, heights_m, 0, 1000)


def compute_indices(profile: PreparedProfile) -> CoreIndicesResult:
    """Compute all thermodynamic indices (core + extended)."""
    result = compute_indices_core(profile)
    compute_indices_extended(profile, result.indices)
    return result


def _safe_round(val: float | None, ndigits: int = 0) -> float | None:
    """Round if not None."""
    return round(val, ndigits) if val is not None else None


def _compute_bulk_shear(
    u, v, heights_m: np.ndarray, bottom_m: float, top_m: float,
) -> float | None:
    """Compute bulk wind shear magnitude (kt) between two height levels."""
    try:
        # Find levels closest to bottom and top
        bot_idx = int(np.argmin(np.abs(heights_m - bottom_m)))
        top_idx = int(np.argmin(np.abs(heights_m - top_m)))
        if bot_idx == top_idx:
            return None
        du = u[top_idx] - u[bot_idx]
        dv = v[top_idx] - v[bot_idx]
        shear = np.sqrt(du**2 + dv**2)
        shear_kt = float(shear.to("knot").magnitude)
        # prepare_profile NaN-fills wind at levels a partial fetch missed, so an
        # endpoint of the shear layer can be NaN -> NaN shear. Report None (the
        # shear is genuinely unavailable), never a NaN: it would serialise as
        # invalid JSON and read downstream as a number. Required companion to the
        # per-level wind gate in prepare.py — that gate is what lets NaN reach
        # here at all (#478).
        if np.isnan(shear_kt):
            return None
        return round(shear_kt, 1)
    except Exception:
        return None


def compute_derived_levels_core(profile: PreparedProfile) -> list[DerivedLevel]:
    """Compute core per-level values needed for cloud detection and ceiling.

    Produces: pressure, altitude, temperature, dewpoint, dewpoint depression,
    lapse rate, wind speed/direction, and omega (Pa/s — a cheap unit
    conversion the convective loaded-gun trigger needs in the lite path).
    Skips expensive MetPy calls (wet bulb, theta-e, RH, omega→w conversion).
    """
    pressures = profile.pressure.to("hPa").magnitude
    temps = profile.temperature.to("degC").magnitude
    dewpoints = profile.dewpoint.to("degC").magnitude

    if profile.height is not None:
        heights_ft = profile.height.to("meter").magnitude * M_TO_FT
    else:
        heights_ft = np.array([_pressure_to_altitude_ft(p) for p in pressures])

    # Extract wind speed/direction (cheap unit conversion, needed by convective)
    wspd_kt = None
    wdir_deg = None
    if profile.wind_speed is not None and profile.wind_direction is not None:
        wspd_kt = profile.wind_speed.to("knots").magnitude
        wdir_deg = profile.wind_direction.to("degrees").magnitude

    # Omega magnitudes (Pa/s, NaN where missing). Cheap unit conversion — the
    # expensive omega→w (w_fpm) conversion is left to the extended pass. The
    # convective loaded-gun trigger reads omega here, before extended runs.
    omega_vals = None
    if profile.omega is not None:
        omega_vals = profile.omega.to("Pa/s").magnitude

    levels: list[DerivedLevel] = []
    for i in range(len(pressures)):
        p_hpa = int(pressures[i])
        t_c = float(temps[i])
        td_c = float(dewpoints[i])

        dd = round(t_c - td_c, 1)

        lapse = None
        if i < len(pressures) - 1:
            dz_m = (heights_ft[i + 1] - heights_ft[i]) / M_TO_FT
            if dz_m > 0:
                dt = temps[i + 1] - temps[i]
                lapse = round(float(-dt / (dz_m / 1000)), 1)

        ws = round(float(wspd_kt[i]), 1) if wspd_kt is not None and not np.isnan(wspd_kt[i]) else None
        wd = round(float(wdir_deg[i]), 0) if wdir_deg is not None and not np.isnan(wdir_deg[i]) else None
        om = (
            round(float(omega_vals[i]), 4)
            if omega_vals is not None and not np.isnan(omega_vals[i])
            else None
        )

        levels.append(DerivedLevel(
            pressure_hpa=p_hpa,
            altitude_ft=round(float(heights_ft[i])),
            temperature_c=round(t_c, 1),
            dewpoint_c=round(td_c, 1),
            dewpoint_depression_c=dd,
            lapse_rate_c_per_km=lapse,
            wind_speed_kt=ws,
            wind_direction_deg=wd,
            omega_pa_s=om,
        ))

    return levels


def compute_derived_levels_extended(
    profile: PreparedProfile,
    levels: list[DerivedLevel],
) -> None:
    """Enrich derived levels with wet bulb, theta-e, RH, and omega (mutates in-place).

    Called by the full briefing pipeline after ``compute_derived_levels_core``.
    These fields are used by icing, inversions, vertical motion, and Skew-T.
    """
    # Omega magnitudes (Pa/s, NaN where missing per prepare.py contract)
    omega_vals = None
    if profile.omega is not None:
        omega_vals = profile.omega.to("Pa/s").magnitude

    # All MetPy calls below operate on the full profile array at once;
    # MetPy propagates NaN per-level so the per-element guards below are
    # the same shape as the omega/w path.

    rh_vals = None
    try:
        rh_vals = mpcalc.relative_humidity_from_dewpoint(
            profile.temperature, profile.dewpoint,
        ).magnitude * 100
    except Exception:
        logger.debug("RH computation failed", exc_info=True)

    wb_vals = None
    try:
        # Vectorized RK4 descent, same definition/integrand as MetPy's
        # per-level ODE solve but ~6x faster — see wet_bulb.py.
        wb_vals = wet_bulb_degc(
            profile.pressure, profile.temperature, profile.dewpoint,
        )
    except Exception:
        logger.debug("Wet bulb computation failed", exc_info=True)

    te_vals = None
    try:
        te_vals = mpcalc.equivalent_potential_temperature(
            profile.pressure, profile.temperature, profile.dewpoint,
        ).to("kelvin").magnitude
    except Exception:
        logger.debug("Theta-E computation failed", exc_info=True)

    w_vals = None
    if profile.omega is not None:
        try:
            w_vals = mpcalc.vertical_velocity(
                profile.omega, profile.pressure, profile.temperature,
            ).to("m/s").magnitude
        except Exception:
            logger.debug("Omega→w conversion failed", exc_info=True)

    for i, lv in enumerate(levels):
        if wb_vals is not None and not np.isnan(wb_vals[i]):
            lv.wet_bulb_c = round(float(wb_vals[i]), 1)

        if te_vals is not None and not np.isnan(te_vals[i]):
            lv.theta_e_k = round(float(te_vals[i]), 1)

        if omega_vals is not None and not np.isnan(omega_vals[i]):
            lv.omega_pa_s = round(float(omega_vals[i]), 4)
            if w_vals is not None and not np.isnan(w_vals[i]):
                lv.w_fpm = round(float(w_vals[i]) * 196.85, 1)

        if rh_vals is not None:
            lv.relative_humidity_pct = round(float(rh_vals[i]), 1)
        else:
            lv.relative_humidity_pct = None


def compute_derived_levels(profile: PreparedProfile) -> list[DerivedLevel]:
    """Compute all per-level derived values (core + extended)."""
    levels = compute_derived_levels_core(profile)
    compute_derived_levels_extended(profile, levels)
    return levels
