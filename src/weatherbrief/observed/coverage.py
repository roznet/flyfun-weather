"""Where each observed source can see at all.

Every source has a fixed geographic *domain*: OPERA composites a European
radar network onto one fixed grid, and the MTG imagers look at the Earth disc
from 0° longitude.  Outside that domain the product does not merely lack a
value — it has never looked, and a sample there would either crash the reader
(an empty window) or, worse, report an observation that was never made (zero
lightning flashes over Arizona, which MTG cannot see).

This module answers "does source X cover this place?" without opening a frame,
so it can gate sampling *before* any file is read, and so any other caller
(the forecast map, a future non-European radar source, an MCP tool) can ask
"is there radar here?" from coordinates alone.

Domain is one level above the per-pixel ``nodata`` mask, not a replacement for
it: inside the OPERA domain, sea and radar shadow are still ``nodata``.  The
domain says "this product could have a value here"; the mask says "for this
pixel, in this frame, it does".

Adding a source (e.g. a US radar mosaic) means adding a domain here and to
:data:`SOURCE_DOMAINS` — the gate in :mod:`.payload` needs no change.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
)
from .grid import GridSpec

# Sources that answer "is it raining here" — the radar family.
RADAR_SOURCES: tuple[str, ...] = (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE)


@dataclass(frozen=True)
class GridDomain:
    """Domain = the footprint of a fixed projected grid.

    Exact rather than a lat/lon box: OPERA's Lambert grid is a rectangle in
    projected metres, which a lat/lon box can only over- or under-state.
    """

    name: str
    grid: GridSpec

    def covers(self, lats, lons) -> np.ndarray:
        lats = np.atleast_1d(np.asarray(lats, dtype=float))
        lons = np.atleast_1d(np.asarray(lons, dtype=float))
        x, y = self.grid.lonlat_to_xy(lons, lats)
        cols = (np.asarray(x, dtype=float) - self.grid.x0) / self.grid.dx
        rows = (np.asarray(y, dtype=float) - self.grid.y0) / self.grid.dy
        with np.errstate(invalid="ignore"):
            return (
                np.isfinite(cols)
                & np.isfinite(rows)
                & (cols >= -0.5)
                & (cols <= self.grid.nx - 0.5)
                & (rows >= -0.5)
                & (rows <= self.grid.ny - 0.5)
            )


@dataclass(frozen=True)
class DiscDomain:
    """Domain = a geostationary imager's usable disc.

    Bounded by great-circle angle from the sub-satellite point rather than by
    the geometric limb: near the limb the view is so oblique that the pixels
    are tens of km long and the parallax pad outgrows any read window, so
    "technically on the disc" is not "observed".
    """

    name: str
    sub_lon: float
    max_arc_deg: float

    def covers(self, lats, lons) -> np.ndarray:
        lat = np.radians(np.atleast_1d(np.asarray(lats, dtype=float)))
        dlon = np.radians(np.atleast_1d(np.asarray(lons, dtype=float)) - self.sub_lon)
        cos_arc = np.cos(lat) * np.cos(dlon)
        return cos_arc >= np.cos(np.radians(self.max_arc_deg))


# The OPERA composite grid, as every frame's own ``/where`` group declares it.
# Static by design — it is what "OPERA covers this place" means without a
# frame on disk.  ``test_collect_live.py`` catches a re-cut domain.
OPERA_DOMAIN = GridDomain(
    name="Europe (OPERA radar network)",
    grid=GridSpec(
        proj4=(
            "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 "
            "+y_0=-2100000.0 +units=m +ellps=WGS84"
        ),
        nx=3800,
        ny=4400,
        x0=500.0,
        y0=-500.0,
        dx=1000.0,
        dy=-1000.0,
    ),
)

# MTG-I1 at 0°.  75° of arc keeps northern Norway (~73°) and Iceland (~66°)
# and drops the Americas: New York is ~78°, Arizona ~108°.
MTG_DOMAIN = DiscDomain(name="MTG satellite disc (Europe/Africa)", sub_lon=0.0, max_arc_deg=75.0)

SOURCE_DOMAINS: dict[str, GridDomain | DiscDomain] = {
    SOURCE_OPERA_DBZH: OPERA_DOMAIN,
    SOURCE_OPERA_RATE: OPERA_DOMAIN,
    SOURCE_EUMETSAT_LI: MTG_DOMAIN,
    SOURCE_EUMETSAT_CTTH: MTG_DOMAIN,
}


def covers(source: str, lats, lons) -> np.ndarray:
    """Per-point mask: does ``source``'s domain include each (lat, lon)?

    A source with no declared domain is treated as covering nothing — a new
    source must say where it can see before it is sampled anywhere.
    """
    domain = SOURCE_DOMAINS.get(source)
    if domain is None:
        return np.zeros(np.atleast_1d(np.asarray(lats)).shape, dtype=bool)
    return domain.covers(lats, lons)


def covering_sources(lat: float, lon: float) -> tuple[str, ...]:
    """Every observed source whose domain includes this point."""
    return tuple(s for s in SOURCE_DOMAINS if bool(covers(s, lat, lon)[0]))


def has_radar(lat: float, lon: float) -> bool:
    """Is there an observed radar source covering this point?"""
    return any(bool(covers(s, lat, lon)[0]) for s in RADAR_SOURCES)


def domain_name(source: str) -> str | None:
    domain = SOURCE_DOMAINS.get(source)
    return domain.name if domain else None
