"""Frontal zone detection and front type classification.

Gradient-magnitude thresholding on 850hPa T and θe fields for zone-scale
frontal presence detection. Front classification via temperature advection.
TFP computed for CLI plotting only — not used in detection pipeline.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter

from weatherbrief.frontal.grid import fill_terrain


def compute_frontal_zones(
    field: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    smooth_sigma: float = 0.5,
    gradient_threshold: float = 2.0,
    terrain_mask: np.ndarray | None = None,
) -> dict:
    """Detect frontal zones from a 2D scalar field (T850 or θe).

    Parameters
    ----------
    field : 2D array (lat × lon), NaN-free (resolved by prepare_field).
        For T850: Celsius (gradient units K/100km = °C/100km).
        For θe: Kelvin (same gradient units).
    lat, lon : 1D coordinate arrays (degrees).
    smooth_sigma : Gaussian smoothing in grid points. 0.5 at 0.5° resolution
        removes single-cell noise without blurring narrow fronts.
    gradient_threshold : K per 100km — frontal zone threshold.
        The plan's initial 0.8 is too low — background T850 gradients across
        Europe are typically ~1 K/100km in spring (median ~0.83). At 0.8,
        over 50% of the domain exceeds threshold. 2.0 captures ~8-10% of
        the domain, which matches real frontal coverage.
    terrain_mask : boolean (True=valid). Terrain cells should be filled
        before calling this function; the mask is applied to results only.

    Returns
    -------
    dict with gradient, frontal_mask, tfp, T_smooth, front_orientation,
    dT_dx, dT_dy.
    """
    # Fill terrain cells before smoothing
    field_input = field
    if terrain_mask is not None:
        field_input = fill_terrain(field, terrain_mask)

    # Smooth to remove single-cell noise
    T_smooth = gaussian_filter(field_input, sigma=smooth_sigma)

    # Grid spacing in km — dlat constant, dlon varies with latitude
    dlat_km = 111.0
    dlon_km = 111.0 * np.cos(np.radians(lat))  # 1D array per latitude row

    # Compute spacing for np.gradient
    dlat_spacing = dlat_km * np.abs(np.diff(lat).mean())  # scalar (km)
    dlon_spacing_per_row = dlon_km * np.abs(np.diff(lon).mean())  # 1D (n_lat,)

    # Temperature gradient components (K per km)
    dT_dy = np.gradient(T_smooth, dlat_spacing, axis=0)
    dT_dx = np.gradient(T_smooth, axis=1) / dlon_spacing_per_row[:, np.newaxis]

    # Gradient magnitude (K per 100km)
    grad_mag = np.sqrt(dT_dx**2 + dT_dy**2)
    grad_mag_100km = grad_mag * 100.0

    # Frontal zone mask — exclude terrain from results
    frontal_mask = grad_mag_100km > gradient_threshold
    if terrain_mask is not None:
        frontal_mask = frontal_mask & terrain_mask

    # TFP: -∇|∇T| · (∇T / |∇T|) — for CLI plotting only
    grad_norm = np.where(grad_mag > 1e-10, grad_mag, 1e-10)
    unit_grad_x = dT_dx / grad_norm
    unit_grad_y = dT_dy / grad_norm

    d_gradmag_dy = np.gradient(grad_mag, dlat_spacing, axis=0)
    d_gradmag_dx = np.gradient(grad_mag, axis=1) / dlon_spacing_per_row[:, np.newaxis]
    tfp = -(d_gradmag_dx * unit_grad_x + d_gradmag_dy * unit_grad_y)

    # Front orientation: gradient direction + 90° gives front line bearing
    grad_direction = np.degrees(np.arctan2(dT_dx, dT_dy))
    front_orientation = (grad_direction + 90.0) % 360.0

    return {
        "gradient": grad_mag_100km,
        "frontal_mask": frontal_mask,
        "tfp": tfp,
        "T_smooth": T_smooth,
        "front_orientation": front_orientation,
        "dT_dx": dT_dx,
        "dT_dy": dT_dy,
    }


def compute_frontal_zones_dual(
    T850: np.ndarray,
    theta_e: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray | None = None,
    t_gradient_threshold: float = 2.0,
    te_gradient_threshold: float = 4.0,
    **kwargs,
) -> dict:
    """Detect frontal zones using both T850 and θe gradients.

    A grid point is frontal if EITHER gradient exceeds its threshold.
    Cold fronts show up in both; warm fronts primarily in θe.
    """
    t_result = compute_frontal_zones(
        T850, lat, lon,
        terrain_mask=terrain_mask,
        gradient_threshold=t_gradient_threshold,
        **kwargs,
    )
    te_result = compute_frontal_zones(
        theta_e, lat, lon,
        terrain_mask=terrain_mask,
        gradient_threshold=te_gradient_threshold,
        **kwargs,
    )

    # Union of both masks
    combined_mask = t_result["frontal_mask"] | te_result["frontal_mask"]

    # Track which method triggered detection
    detected_by = np.zeros_like(T850, dtype=np.uint8)
    detected_by[t_result["frontal_mask"]] |= 1  # bit 0 = T850
    detected_by[te_result["frontal_mask"]] |= 2  # bit 1 = θe

    return {
        **t_result,
        "frontal_mask": combined_mask,
        "te_gradient": te_result["gradient"],
        "te_frontal_mask": te_result["frontal_mask"],
        "detected_by": detected_by,
    }


def classify_front_type(
    dT_dx: np.ndarray,
    dT_dy: np.ndarray,
    u850: np.ndarray,
    v850: np.ndarray,
    frontal_mask: np.ndarray,
    advection_threshold: float = 0.5,
    detected_by: np.ndarray | None = None,
) -> np.ndarray:
    """Classify frontal points as cold (1), warm (2), or indeterminate (3).

    Uses temperature advection: -(u·dT/dx + v·dT/dy).
    u850/v850 in km/h, dT/dx in K/km → advection in K/hr.

    Points detected only by θe (bit 1 in detected_by) with weak T850
    advection are biased toward warm — these are precisely the warm
    fronts that θe was added to catch.

    Returns int array: 0=not front, 1=cold, 2=warm, 3=indeterminate.
    """
    T_adv = -(u850 * dT_dx + v850 * dT_dy)

    front_type = np.zeros(frontal_mask.shape, dtype=int)

    cold_mask = frontal_mask & (T_adv < -advection_threshold)
    warm_mask = frontal_mask & (T_adv > advection_threshold)
    indeterminate_mask = frontal_mask & ~cold_mask & ~warm_mask

    # θe-only points with weak advection → bias toward warm
    if detected_by is not None:
        theta_e_only = detected_by == 2  # bit 1 only
        reclassify = indeterminate_mask & theta_e_only
        indeterminate_mask = indeterminate_mask & ~reclassify
        warm_mask = warm_mask | reclassify

    front_type[cold_mask] = 1
    front_type[warm_mask] = 2
    front_type[indeterminate_mask] = 3

    return front_type
