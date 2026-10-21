"""Geometric vertical velocity w → omega, shared by every fetch path.

``PressureLevelData.vertical_velocity_pa_s`` is omega (Pa/s, negative = ascent)
everywhere downstream. Sources that deliver geometric w (m/s, up positive) —
ICON GRIB ``w`` and Open-Meteo's ``vertical_velocity_*`` (#833) — convert here
so the formula and constants exist once.
"""

from __future__ import annotations

G = 9.80665  # gravitational acceleration, m/s²
RD_DRY_AIR = 287.05  # specific gas constant for dry air, J/(kg·K)


def w_to_omega_pa_s(w_ms: float, pressure_hpa: float, temperature_k: float) -> float:
    """ω = −ρ·g·w, with ρ = p / (Rd·T) at the level (hydrostatic)."""
    rho = (pressure_hpa * 100.0) / (RD_DRY_AIR * temperature_k)
    return -rho * G * w_ms
