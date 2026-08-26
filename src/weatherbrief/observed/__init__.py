"""Observed conditions along the route (issue #574, phase 1).

Radar reflectivity and rain rate from the EUMETNET OPERA composite, total
lightning and satellite cloud tops from EUMETSAT MTG — sampled around every
corridor station so a pilot sees what is actually there next to what the
models forecast.

The package splits along one seam: :mod:`collect` is the only module that
touches the network, and everything else reads the local frame store.  That is
what makes "zero network fetches inside a briefing request" structural rather
than a rule someone has to remember.

    collect  →  frames (store on disk)  →  readers  →  sampler  →  payload

Phase 1 displays observations only.  It computes no verdict and touches no
advisory; the cross-check is visual, with ``observed-tops`` rendered over the
NWP cloud bands.  Computing that comparison is phase 2.
"""

from .frames import (  # noqa: F401
    ALL_SOURCES,
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    SOURCE_SPECS,
    FlashFrame,
    FrameStore,
    GridFrame,
    SourceSpec,
    StoredFrame,
)
from .grid import GridSpec, GridWindow, compute_window  # noqa: F401
from .sampler import DEFAULT_RADII_NM, SampleStation, sample, sample_flashes  # noqa: F401

__all__ = [
    "ALL_SOURCES",
    "DEFAULT_RADII_NM",
    "FlashFrame",
    "FrameStore",
    "GridFrame",
    "GridSpec",
    "GridWindow",
    "SOURCE_EUMETSAT_CTTH",
    "SOURCE_EUMETSAT_LI",
    "SOURCE_OPERA_DBZH",
    "SOURCE_OPERA_RATE",
    "SOURCE_SPECS",
    "SampleStation",
    "SourceSpec",
    "StoredFrame",
    "compute_window",
    "sample",
    "sample_flashes",
]
