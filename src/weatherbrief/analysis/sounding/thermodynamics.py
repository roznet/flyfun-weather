"""MetPy-based thermodynamic computations for sounding analysis.

All MetPy calls are isolated here. Takes a PreparedProfile and returns
ThermodynamicIndices + list[DerivedLevel] with plain-number values.
"""

from __future__ import annotations

import logging

import metpy.calc as mpcalc
import numpy as np
from metpy.units import units

from weatherbrief.analysis.sounding.prepare import PreparedProfile
from weatherbrief.models import DerivedLevel, ThermodynamicIndices

logger = logging.getLogger(__name__)

M_TO_FT = 3.28084


def _mag(quantity) -> float | None:
    """Extract magnitude from a pint Quantity, or return None."""
    if quantity is None:
        return None
    try:
        return float(quantity.magnitude)
    except Exception:
        return None


def _pressure_to_altitude_ft(pressure_hpa: float) -> float:
    """Approximate pressure to altitude conversion (standard atmosphere)."""
    # ISA: h = (T0/L) * (1 - (P/P0)^(R*L/(g*M)))
    P0, T0, L = 1013.25, 288.15, 0.0065
    g, M, R = 9.80665, 0.0289644, 8.31447
    exp = R * L / (g * M)
    altitude_m = (T0 / L) * (1 - (pressure_hpa / P0) ** exp)
    return altitude_m * M_TO_FT


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


def compute_indices(profile: PreparedProfile) -> ThermodynamicIndices:
    """Compute thermodynamic indices from a prepared sounding profile."""
    idx = ThermodynamicIndices()
    p = profile.pressure
    t = profile.temperature
    td = profile.dewpoint

    # --- LCL ---
    try:
        lcl_p, lcl_t = mpcalc.lcl(p[0], t[0], td[0])
        idx.lcl_pressure_hpa = round(_mag(lcl_p.to("hPa")), 1)
        idx.lcl_altitude_ft = round(_pressure_to_altitude_ft(idx.lcl_pressure_hpa))
    except Exception:
        logger.debug("LCL computation failed", exc_info=True)

    # --- Parcel profile (needed for LFC, EL, CAPE/CIN) ---
    try:
        parcel = mpcalc.parcel_profile(p, t[0], td[0])
    except Exception:
        logger.debug("Parcel profile computation failed", exc_info=True)
        parcel = None

    # --- LFC ---
    if parcel is not None:
        try:
            lfc_p, lfc_t = mpcalc.lfc(p, t, td, parcel_temperature_profile=parcel)
            if lfc_p is not None and not np.isnan(_mag(lfc_p)):
                idx.lfc_pressure_hpa = round(_mag(lfc_p.to("hPa")), 1)
                idx.lfc_altitude_ft = round(_pressure_to_altitude_ft(idx.lfc_pressure_hpa))
        except Exception:
            logger.debug("LFC computation failed", exc_info=True)

    # --- EL ---
    if parcel is not None:
        try:
            el_p, el_t = mpcalc.el(p, t, td, parcel_temperature_profile=parcel)
            if el_p is not None and not np.isnan(_mag(el_p)):
                idx.el_pressure_hpa = round(_mag(el_p.to("hPa")), 1)
                idx.el_altitude_ft = round(_pressure_to_altitude_ft(idx.el_pressure_hpa))
        except Exception:
            logger.debug("EL computation failed", exc_info=True)

    # --- CAPE / CIN (surface-based) ---
    if parcel is not None:
        try:
            cape, cin = mpcalc.cape_cin(p, t, td, parcel)
            idx.cape_surface_jkg = round(_mag(cape.to("J/kg")), 1)
            idx.cin_surface_jkg = round(_mag(cin.to("J/kg")), 1)
        except Exception:
            logger.debug("Surface CAPE/CIN failed", exc_info=True)

    # --- Most-unstable CAPE ---
    try:
        mu_cape, _ = mpcalc.most_unstable_cape_cin(p, t, td)
        idx.cape_most_unstable_jkg = round(_mag(mu_cape.to("J/kg")), 1)
    except Exception:
        logger.debug("MU CAPE failed", exc_info=True)

    # --- Mixed-layer CAPE ---
    try:
        ml_cape, _ = mpcalc.mixed_layer_cape_cin(p, t, td)
        idx.cape_mixed_layer_jkg = round(_mag(ml_cape.to("J/kg")), 1)
    except Exception:
        logger.debug("ML CAPE failed", exc_info=True)

    # --- Lifted index ---
    try:
        li = mpcalc.lifted_index(p, t, parcel)
        idx.lifted_index = round(_mag(li.to("delta_degC")), 1)
    except Exception:
        logger.debug("Lifted index failed", exc_info=True)

    # --- Showalter index ---
    try:
        si = mpcalc.showalter_index(p, t, td)
        idx.showalter_index = round(_mag(si.to("delta_degC")), 1)
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

    # --- Temperature crossings ---
    idx.freezing_level_ft = _safe_round(_find_temperature_crossing(profile, 0.0))
    idx.minus10c_level_ft = _safe_round(_find_temperature_crossing(profile, -10.0))
    idx.minus20c_level_ft = _safe_round(_find_temperature_crossing(profile, -20.0))

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

    return idx


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
        return round(float(shear.to("knot").magnitude), 1)
    except Exception:
        return None


def compute_derived_levels(profile: PreparedProfile) -> list[DerivedLevel]:
    """Compute per-level derived values from a prepared sounding profile."""
    pressures = profile.pressure.to("hPa").magnitude
    temps = profile.temperature.to("degC").magnitude
    dewpoints = profile.dewpoint.to("degC").magnitude

    if profile.height is not None:
        heights_ft = profile.height.to("meter").magnitude * M_TO_FT
    else:
        heights_ft = np.array([_pressure_to_altitude_ft(p) for p in pressures])

    # Extract omega values (may contain NaN for missing levels)
    omega_vals = None
    if profile.omega is not None:
        omega_vals = profile.omega.to("Pa/s").magnitude

    # RH for each level
    try:
        rh_vals = mpcalc.relative_humidity_from_dewpoint(
            profile.temperature, profile.dewpoint
        ).magnitude * 100
    except Exception:
        rh_vals = [None] * len(pressures)

    levels: list[DerivedLevel] = []
    for i in range(len(pressures)):
        p_hpa = int(pressures[i])
        t_c = float(temps[i])
        td_c = float(dewpoints[i])

        # Wet-bulb temperature
        wet_bulb = None
        try:
            wb = mpcalc.wet_bulb_temperature(
                pressures[i] * units.hPa, temps[i] * units.degC, dewpoints[i] * units.degC
            )
            wet_bulb = round(float(wb.to("degC").magnitude), 1)
        except Exception:
            pass

        # Dewpoint depression
        dd = round(t_c - td_c, 1)

        # Theta-E
        theta_e = None
        try:
            te = mpcalc.equivalent_potential_temperature(
                pressures[i] * units.hPa, temps[i] * units.degC, dewpoints[i] * units.degC
            )
            theta_e = round(float(te.to("kelvin").magnitude), 1)
        except Exception:
            pass

        # Lapse rate between this level and the next (C/km)
        lapse = None
        if i < len(pressures) - 1:
            dz_m = (heights_ft[i + 1] - heights_ft[i]) / M_TO_FT
            if dz_m > 0:
                dt = temps[i + 1] - temps[i]
                lapse = round(float(-dt / (dz_m / 1000)), 1)  # -dT/dz in C/km

        # Omega → w conversion
        omega_pa_s = None
        w_fpm = None
        if omega_vals is not None and not np.isnan(omega_vals[i]):
            omega_pa_s = round(float(omega_vals[i]), 4)
            try:
                w = mpcalc.vertical_velocity(
                    omega_vals[i] * units("Pa/s"),
                    pressures[i] * units.hPa,
                    temps[i] * units.degC,
                )
                w_fpm = round(float(w.to("m/s").magnitude) * 196.85, 1)  # m/s → ft/min
            except Exception:
                logger.debug("Omega→w conversion failed at %s hPa", pressures[i], exc_info=True)

        rh_pct = round(float(rh_vals[i]), 1) if rh_vals[i] is not None else None

        levels.append(DerivedLevel(
            pressure_hpa=p_hpa,
            altitude_ft=round(float(heights_ft[i])),
            temperature_c=round(t_c, 1),
            dewpoint_c=round(td_c, 1),
            relative_humidity_pct=rh_pct,
            wet_bulb_c=wet_bulb,
            dewpoint_depression_c=dd,
            theta_e_k=theta_e,
            lapse_rate_c_per_km=lapse,
            omega_pa_s=omega_pa_s,
            w_fpm=w_fpm,
        ))

    return levels
