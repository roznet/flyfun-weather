"""Frontal zone detection and front type classification.

Gradient-magnitude thresholding on 850hPa T and θe fields with TFP
(thermal front parameter) proximity filtering for zone-scale frontal
presence detection. Front classification via cross-front wind.

The TFP filter (Hewson 1998) requires frontal points to be near a
gradient peak (TFP zero-crossing), not just in a region of high
gradient. This distinguishes real fronts (sharp gradient line) from
orographic/moisture boundaries (broad uniform gradient).
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import binary_dilation, gaussian_filter

from weatherbrief.frontal.grid import fill_terrain


def _tfp_proximity_mask(tfp: np.ndarray, dilation: int = 2) -> np.ndarray:
    """Mask of cells near TFP zero-crossings (gradient peaks).

    A TFP zero-crossing marks the warm edge of a gradient maximum —
    the front line in Hewson's framework. Points near a zero-crossing
    are near a real front; points far from any zero-crossing are in a
    broad gradient field (orographic, sea-land) with no sharp peak.

    Requires ≥0.25° grid resolution for TFP second derivatives to be
    meaningful. At 0.5°, TFP noise causes zero-crossings everywhere.

    Parameters
    ----------
    tfp : 2D TFP field.
    dilation : how many grid cells to expand around zero-crossings.
        At 0.25° resolution, each cell is ~28km. dilation=3 covers
        ~84km radius, appropriate for zone-scale front width at 850hPa.
    """
    tfp_sign = np.sign(tfp)
    zero_crossing = np.zeros_like(tfp, dtype=bool)

    # Check 4 neighbors for sign change
    for axis, direction in [(0, 1), (0, -1), (1, 1), (1, -1)]:
        shifted = np.roll(tfp_sign, direction, axis=axis)
        zero_crossing |= (tfp_sign * shifted) < 0

    if dilation > 0:
        structure = np.ones((2 * dilation + 1, 2 * dilation + 1))
        return binary_dilation(zero_crossing, structure=structure)

    return zero_crossing


def compute_frontal_zones(
    field: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    smooth_sigma: float = 0.5,
    gradient_threshold: float = 2.0,
    terrain_mask: np.ndarray | None = None,
    tfp_dilation: int = -1,
) -> dict:
    """Detect frontal zones from a 2D scalar field (T850 or θe).

    Parameters
    ----------
    field : 2D array (lat × lon), NaN-free (resolved by prepare_field).
    lat, lon : 1D coordinate arrays (degrees).
    smooth_sigma : Gaussian smoothing in grid points.
    gradient_threshold : K per 100km — frontal zone threshold.
    terrain_mask : boolean (True=valid).
    tfp_dilation : grid cells to expand around TFP zero-crossings.
        Points must be within this distance of a gradient peak to count
        as frontal. Set to -1 to disable TFP filtering (gradient-only).

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

    # Frontal zone mask — gradient threshold + terrain exclusion
    frontal_mask = grad_mag_100km > gradient_threshold
    if terrain_mask is not None:
        frontal_mask = frontal_mask & terrain_mask

    # TFP: -∇|∇T| · (∇T / |∇T|) — Hewson 1998
    # Zero-crossings locate gradient peaks (front lines).
    # Use a more heavily smoothed field for TFP to suppress noise in
    # the second derivatives. The detection gradient (above) uses light
    # smoothing for sensitivity; the TFP needs clean second derivatives
    # for selectivity. σ=4.0 cells ≈ 1° effective Gaussian σ at 0.25°
    # (matches the σ=2.0 × 0.5° choice from the legacy 0.5° grid, and
    # the heavy_sigma=4.0 default used in compute_hewson_diagnostics).
    tfp_sigma = max(smooth_sigma, 4.0)
    T_tfp = gaussian_filter(field_input, sigma=tfp_sigma)
    dT_tfp_dy = np.gradient(T_tfp, dlat_spacing, axis=0)
    dT_tfp_dx = np.gradient(T_tfp, axis=1) / dlon_spacing_per_row[:, np.newaxis]
    grad_mag_tfp = np.sqrt(dT_tfp_dx**2 + dT_tfp_dy**2)

    grad_norm = np.where(grad_mag_tfp > 1e-10, grad_mag_tfp, 1e-10)
    unit_grad_x = dT_tfp_dx / grad_norm
    unit_grad_y = dT_tfp_dy / grad_norm

    d_gradmag_dy = np.gradient(grad_mag_tfp, dlat_spacing, axis=0)
    d_gradmag_dx = np.gradient(grad_mag_tfp, axis=1) / dlon_spacing_per_row[:, np.newaxis]
    tfp = -(d_gradmag_dx * unit_grad_x + d_gradmag_dy * unit_grad_y)

    # TFP proximity filter: require points to be near a gradient peak
    # (TFP zero-crossing), not just in a broad high-gradient region.
    # This filters orographic and sea-land contrast gradients that have
    # high magnitude but no sharp peak.
    if tfp_dilation >= 0:
        frontal_mask = frontal_mask & _tfp_proximity_mask(tfp, tfp_dilation)

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


def theta_e_gradient_components(
    field: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray | None = None,
    smooth_sigma: float = 0.5,
) -> tuple[np.ndarray, np.ndarray]:
    """Light-smoothed horizontal gradient ``(dT_dx, dT_dy)`` of a scalar field.

    Returns components in **K/km** (lat-dependent dlon spacing handled per row).
    Factored out of :func:`compute_hewson_diagnostics` so the snapshot field
    source can re-derive the gradient *direction* from a stored θe grid — the
    precompute NPZ deliberately does not persist ``dT_dx``/``dT_dy`` (design
    §4.7), yet the off-track front extractor needs the unit gradient to measure
    the cross-front θe jump. Sharing this keeps the recomputed direction bit-for
    bit consistent with the precompute path.
    """
    field_input = field
    if terrain_mask is not None:
        field_input = fill_terrain(field, terrain_mask)
    field_smooth = gaussian_filter(field_input, sigma=smooth_sigma)

    dlat_km = 111.0
    dlon_km = 111.0 * np.cos(np.radians(lat))
    dlat_spacing = dlat_km * np.abs(np.diff(lat).mean())
    dlon_col = (dlon_km * np.abs(np.diff(lon).mean()))[:, np.newaxis]

    dT_dy = np.gradient(field_smooth, dlat_spacing, axis=0)
    dT_dx = np.gradient(field_smooth, axis=1) / dlon_col
    return dT_dx, dT_dy


def compute_hewson_diagnostics(
    field: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    terrain_mask: np.ndarray | None = None,
    smooth_sigma: float = 0.5,
    heavy_sigma: float = 4.0,
) -> dict:
    """Compute all Hewson 1998 / Hewson & Titley 2010 diagnostic fields.

    These are the raw ingredients of the full Hewson formula, computed
    without any threshold or mask logic. Intended for visualization and
    threshold calibration before plumbing new criteria into detection.

    Formulas (with τ = field, ∇̂τ = ∇τ/|∇τ|):
        gradient       = |∇τ|                            K / 100km
        tfp            = −∇|∇τ| · ∇̂τ                    K / (100km)²
        neg_laplacian  = −∇²τ                            K / (100km)²
        advection      = −V · ∇τ                          K / h

    Unit contract: u and v MUST be in **km/h** (to match ∇τ in K/km and
    yield advection in K/h). Pass m/s wind and the advection output is
    off by 3.6×. All current callers (ERA5 loader, frontal `wind_to_uv`,
    reshape_to_fields) convert to km/h before calling — future call
    sites must do the same.

    Light smoothing (smooth_sigma) is used for the gradient (sensitivity).
    Heavy smoothing (heavy_sigma) is used for TFP and the Laplacian so
    the second derivatives survive the grid spacing. Defaults target
    the 0.25° grid: σ=4.0 cells ≈ 1° effective σ, which matches the
    1° effective σ that σ=2.0 gave on the legacy 0.5° grid. Callers
    using the 0.5° pipeline should pass heavy_sigma=2.0 explicitly.

    Returned dict keys:
        field_smooth, gradient, tfp, neg_laplacian, advection,
        dT_dx, dT_dy  (in K/km for reuse by callers).
    """
    field_input = field
    if terrain_mask is not None:
        field_input = fill_terrain(field, terrain_mask)

    field_smooth = gaussian_filter(field_input, sigma=smooth_sigma)
    field_heavy = gaussian_filter(field_input, sigma=heavy_sigma)

    dlat_km = 111.0
    dlon_km = 111.0 * np.cos(np.radians(lat))
    dlat_spacing = dlat_km * np.abs(np.diff(lat).mean())
    dlon_spacing_per_row = dlon_km * np.abs(np.diff(lon).mean())
    dlon_col = dlon_spacing_per_row[:, np.newaxis]

    # Light-smoothed gradient (K/km, then /100 → K/100km). Shares
    # theta_e_gradient_components so the snapshot field source re-derives an
    # identical direction from stored θe.
    dT_dx, dT_dy = theta_e_gradient_components(
        field_input, lat, lon, terrain_mask=None, smooth_sigma=smooth_sigma,
    )
    grad_mag = np.sqrt(dT_dx**2 + dT_dy**2)
    grad_mag_100km = grad_mag * 100.0

    # Heavy-smoothed derivatives for TFP and Laplacian
    dH_dy = np.gradient(field_heavy, dlat_spacing, axis=0)
    dH_dx = np.gradient(field_heavy, axis=1) / dlon_col
    grad_heavy = np.sqrt(dH_dx**2 + dH_dy**2)

    grad_norm = np.where(grad_heavy > 1e-10, grad_heavy, 1e-10)
    unit_x = dH_dx / grad_norm
    unit_y = dH_dy / grad_norm

    # TFP = −∇|∇τ| · ∇̂τ, scaled to K / (100km)²
    d_gradmag_dy = np.gradient(grad_heavy, dlat_spacing, axis=0)
    d_gradmag_dx = np.gradient(grad_heavy, axis=1) / dlon_col
    tfp = -(d_gradmag_dx * unit_x + d_gradmag_dy * unit_y) * 100.0 * 100.0

    # −∇²τ on heavy field, scaled to K / (100km)²
    d2_dy2 = np.gradient(dH_dy, dlat_spacing, axis=0)
    d2_dx2 = np.gradient(dH_dx, axis=1) / dlon_col
    neg_laplacian = -(d2_dx2 + d2_dy2) * 100.0 * 100.0

    # Advection −V · ∇τ on the light-smoothed gradient.
    # u, v in km/h; dT_d? in K/km → advection in K/h.
    advection = -(u * dT_dx + v * dT_dy)

    return {
        "field_smooth": field_smooth,
        "gradient": grad_mag_100km,
        "tfp": tfp,
        "neg_laplacian": neg_laplacian,
        "advection": advection,
        "dT_dx": dT_dx,
        "dT_dy": dT_dy,
    }


def classify_front_type(
    dT_dx: np.ndarray,
    dT_dy: np.ndarray,
    u850: np.ndarray,
    v850: np.ndarray,
    frontal_mask: np.ndarray,
    cross_front_threshold: float = 2.0,
    detected_by: np.ndarray | None = None,
) -> np.ndarray:
    """Classify frontal points as cold (1), warm (2), or indeterminate (3).

    Uses the cross-front wind component: the wind projected onto the
    temperature gradient direction. The gradient points cold→warm, so:
    - Positive cross-front wind = blowing from cold to warm side = cold front
    - Negative = blowing from warm to cold side = warm front

    This is more stable than temperature advection (which depends on both
    wind speed AND gradient magnitude). The cross-front component depends
    only on the wind direction relative to the front orientation.

    cross_front_threshold : minimum cross-front wind speed (km/h) to
        classify. ~1 knot — filters truly ambiguous cases while
        classifying ~85-90% of frontal points.

    Points detected only by θe (bit 1 in detected_by) with weak cross-front
    wind are biased toward warm — these are precisely the warm fronts that
    θe was added to catch.

    Returns int array: 0=not front, 1=cold, 2=warm, 3=indeterminate.
    """
    # Cross-front wind: wind component along gradient direction (cold→warm)
    grad_mag = np.sqrt(dT_dx**2 + dT_dy**2)
    grad_norm = np.where(grad_mag > 1e-10, grad_mag, 1e-10)
    cross_front = (u850 * dT_dx + v850 * dT_dy) / grad_norm

    front_type = np.zeros(frontal_mask.shape, dtype=int)

    cold_mask = frontal_mask & (cross_front > cross_front_threshold)
    warm_mask = frontal_mask & (cross_front < -cross_front_threshold)
    indeterminate_mask = frontal_mask & ~cold_mask & ~warm_mask

    # θe-only points with weak cross-front wind → bias toward warm
    if detected_by is not None:
        theta_e_only = detected_by == 2  # bit 1 only
        reclassify = indeterminate_mask & theta_e_only
        indeterminate_mask = indeterminate_mask & ~reclassify
        warm_mask = warm_mask | reclassify

    front_type[cold_mask] = 1
    front_type[warm_mask] = 2
    front_type[indeterminate_mask] = 3

    return front_type
