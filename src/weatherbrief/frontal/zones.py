"""Analysis zone definitions, route templates, and zone intersection logic.

18 geographic boxes covering European GA chokepoints. Each zone is at least
3×4 degrees, giving ≥384 grid points at 0.25° resolution for reliable
gradient analysis and fractional coverage thresholding.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Zone definitions
# ---------------------------------------------------------------------------

ZONES: dict[str, dict] = {
    # Atlantic approach — early warning for incoming fronts
    "atlantic_north": {"lat": (52, 60), "lon": (-20, -10), "display": "North Atlantic Approach"},
    "atlantic_south": {"lat": (43, 52), "lon": (-20, -8), "display": "South Atlantic / Biscay Approach"},
    # British Isles
    "uk_south": {"lat": (49, 53), "lon": (-6, 3), "display": "Southern England & Channel"},
    "uk_north_ireland": {"lat": (53, 59), "lon": (-10, 0), "display": "Northern UK & Ireland"},
    # Low Countries / North Sea
    "benelux_north_sea": {"lat": (50, 55), "lon": (2, 8), "display": "Benelux & North Sea"},
    # Germany
    "n_germany_baltic": {"lat": (52, 57), "lon": (8, 16), "display": "Northern Germany & Baltic"},
    "central_germany": {"lat": (48, 52), "lon": (7, 14), "display": "Central Germany"},
    # Scandinavia
    "scandinavia_south": {"lat": (55, 60), "lon": (8, 20), "display": "Southern Scandinavia"},
    # France
    "north_france": {"lat": (47, 51), "lon": (-2, 6), "display": "Northern France"},
    "south_france": {"lat": (43, 47), "lon": (-1, 7), "display": "Southern France"},
    # Alps
    "alps": {"lat": (45, 49), "lon": (6, 16), "display": "Alps & Bavaria"},
    # Biscay & Iberia
    "bay_of_biscay": {"lat": (43, 48), "lon": (-8, -1), "display": "Bay of Biscay"},
    "iberia_north": {"lat": (40, 44), "lon": (-9, 1), "display": "Northern Iberia & Pyrenees"},
    "iberia_south": {"lat": (36, 40), "lon": (-9, 0), "display": "Central & Southern Iberia"},
    # Mediterranean
    "western_med": {"lat": (40, 44), "lon": (3, 10), "display": "Western Mediterranean"},
    "balearics": {"lat": (37, 41), "lon": (-1, 5), "display": "Balearic Islands"},
    # Italy
    "po_valley": {"lat": (43, 47), "lon": (8, 14), "display": "Po Valley & Northern Italy"},
    "central_south_italy": {"lat": (37, 43), "lon": (11, 17), "display": "Central & Southern Italy"},
    # Adriatic & Balkans
    "adriatic": {"lat": (41, 46), "lon": (13, 19), "display": "Adriatic"},
    "balkans": {"lat": (38, 46), "lon": (19, 27), "display": "Balkans & Greece"},
}

# ---------------------------------------------------------------------------
# Route templates
# ---------------------------------------------------------------------------

ROUTE_TEMPLATES: dict[str, list[str]] = {
    # UK departures — include Atlantic approach for early warning
    "uk_alps": ["atlantic_north", "uk_south", "north_france", "south_france", "alps"],
    "uk_western_med": ["atlantic_north", "uk_south", "north_france", "south_france", "western_med"],
    "uk_iberia": ["atlantic_north", "uk_south", "north_france", "bay_of_biscay", "iberia_north"],
    "uk_balearics": ["atlantic_north", "uk_south", "north_france", "south_france", "balearics"],
    "uk_italy": ["atlantic_north", "uk_south", "north_france", "alps", "po_valley"],
    "uk_greece": ["atlantic_north", "uk_south", "north_france", "alps", "adriatic", "balkans"],
    # Germany/Benelux departures
    "germany_alps": ["central_germany", "alps"],
    "germany_italy": ["central_germany", "alps", "po_valley"],
    "germany_adriatic": ["central_germany", "alps", "adriatic"],
    "germany_med": ["central_germany", "south_france", "western_med"],
    "benelux_med": ["benelux_north_sea", "north_france", "south_france", "western_med"],
    "benelux_iberia": ["benelux_north_sea", "north_france", "bay_of_biscay", "iberia_north"],
    # Atlantic approach routes
    "atlantic_uk": ["atlantic_north", "uk_north_ireland", "uk_south"],
    "atlantic_iberia": ["atlantic_south", "bay_of_biscay", "iberia_north"],
    # Scandinavian departures
    "scandinavia_uk": ["scandinavia_south", "benelux_north_sea", "uk_south"],
    "scandinavia_alps": ["scandinavia_south", "n_germany_baltic", "central_germany", "alps"],
    # Within-Mediterranean / southern routes
    "iberia_balearics": ["iberia_north", "iberia_south", "balearics"],
    "france_med": ["south_france", "western_med"],
    "italy_greece": ["po_valley", "adriatic", "balkans"],
}

# ---------------------------------------------------------------------------
# Orientation labeling
# ---------------------------------------------------------------------------

_COMPASS_LABELS = [
    (0, "N-S"),
    (22.5, "NNE-SSW"),
    (45, "NE-SW"),
    (67.5, "ENE-WSW"),
    (90, "E-W"),
    (112.5, "ESE-WNW"),
    (135, "SE-NW"),
    (157.5, "SSE-NNW"),
]


def _orientation_label(
    front_orientation: np.ndarray, frontal_in_region: np.ndarray
) -> str:
    """Compute mean front orientation over a zone's frontal points.

    Uses circular mean with axial doubling to handle wraparound
    (orientation is axial: 0° ≡ 180°).
    """
    bearings = front_orientation[frontal_in_region]
    if len(bearings) == 0:
        return "N-S"
    # Axial mean: double angles, average as vectors, halve result
    rad2 = np.radians(2 * bearings)
    mean_angle = (
        np.degrees(np.arctan2(np.mean(np.sin(rad2)), np.mean(np.cos(rad2)))) / 2
    )
    mean_angle = mean_angle % 180  # normalize to [0, 180)

    # Find closest compass label
    best = min(
        _COMPASS_LABELS,
        key=lambda c: min(abs(mean_angle - c[0]), 180 - abs(mean_angle - c[0])),
    )
    return best[1]


# ---------------------------------------------------------------------------
# Zone intersection
# ---------------------------------------------------------------------------

# Minimum fraction of zone grid points that must be frontal to count
# as "front present". A real front is 1-2 cells wide at 0.5° (or 2-4 at
# 0.25°), touching ~10-15% of a typical zone. Fraction is
# grid-resolution-agnostic.
_MIN_FRONTAL_FRACTION = 0.08

# Absolute minimum alongside the fraction — prevents large zones from
# systematically under-detecting. Scaled for 0.25° grid where a minimum
# 3×4° zone holds ~384 valid points (vs ~96 at 0.5°); 32 preserves the
# ~8% discriminating threshold that 8 points gave at 0.5°.
_MIN_FRONTAL_POINTS = 32


def find_fronts_in_regions(
    frontal_mask: np.ndarray,
    front_type: np.ndarray,
    gradient: np.ndarray,
    front_orientation: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray | None = None,
    regions: dict | None = None,
) -> dict:
    """Check each region for frontal activity.

    Returns dict keyed by region name with keys:
        present, type, intensity, orientation, coverage_fraction
    terrain_mask adjusts the denominator so terrain-heavy zones
    aren't penalised by masked-out points.
    """
    if regions is None:
        regions = ZONES

    results = {}
    lat_grid, lon_grid = np.meshgrid(lat, lon, indexing="ij")

    for region_name, bounds in regions.items():
        region_mask = (
            (lat_grid >= bounds["lat"][0])
            & (lat_grid <= bounds["lat"][1])
            & (lon_grid >= bounds["lon"][0])
            & (lon_grid <= bounds["lon"][1])
        )

        # Use unmasked points as denominator
        if terrain_mask is not None:
            valid_region = region_mask & terrain_mask
        else:
            valid_region = region_mask

        n_region_points = int(valid_region.sum())
        if n_region_points == 0:
            results[region_name] = {"present": False}
            continue

        frontal_in_region = frontal_mask & region_mask
        n_frontal_points = int(frontal_in_region.sum())
        frontal_fraction = n_frontal_points / n_region_points

        if (
            frontal_fraction < _MIN_FRONTAL_FRACTION
            or n_frontal_points < _MIN_FRONTAL_POINTS
        ):
            results[region_name] = {"present": False}
            continue

        types_in_region = front_type[frontal_in_region]
        type_counts = np.bincount(
            types_in_region[types_in_region > 0], minlength=4
        )
        dominant_type = int(np.argmax(type_counts[1:]) + 1)

        type_names = {1: "cold", 2: "warm", 3: "indeterminate"}

        results[region_name] = {
            "present": True,
            "type": type_names.get(dominant_type, "unknown"),
            "intensity": float(gradient[frontal_in_region].max()),
            "orientation": _orientation_label(front_orientation, frontal_in_region),
            "coverage_fraction": float(frontal_fraction),
        }

    return results


def find_route_zones(waypoints: list[tuple[float, float]]) -> list[str]:
    """Given route waypoints [(lat, lon), ...], return ordered unique zone list."""
    route_zones: list[str] = []
    for lat, lon in waypoints:
        for zone_name, bounds in ZONES.items():
            if (
                bounds["lat"][0] <= lat <= bounds["lat"][1]
                and bounds["lon"][0] <= lon <= bounds["lon"][1]
            ):
                if not route_zones or route_zones[-1] != zone_name:
                    route_zones.append(zone_name)
                break
    return route_zones
