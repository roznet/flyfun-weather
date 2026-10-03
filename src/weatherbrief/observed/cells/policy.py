"""Every tunable number of the cell analysis, in one versioned object.

The catalogue carries :attr:`CellPolicy.policy_version` — a readable name plus
a digest of *all* the values below — so a stored catalogue stays interpretable
after a threshold moves, and ``--replay`` under a changed policy can never be
confused with the original.  Nothing outside this module hard-codes a
threshold.

All values are **provisional**.  Settling them is what the archive and the
self-scoring loop are for (designs/observed-cells.md, "Open numbers").
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TierPolicy:
    """One reflectivity contour, detected and tracked on its own.

    ``block_km`` is the footprint encoding resolution: ``0`` stores the
    footprint at the grid's own pixels (cores), rain areas use 8 km blocks
    because a frontal band at 1 km pixels would dominate the catalogue size
    while "how far off the route" does not need 1 km precision for a 20 dBZ
    area.  Distances are in km throughout the policy and converted with the
    grid's own pixel size — the OPERA DBZH composite is a **1 km** grid
    (3800 × 4400), RATE a 2 km one.
    """

    name: str
    threshold_dbz: float
    min_area_km2: float
    block_km: float = 0.0

    def block_px(self, pixel_km: float) -> int:
        return px(self.block_km, pixel_km) if self.block_km > 0 else 1


def px(km: float, pixel_km: float) -> int:
    """Whole grid pixels for a distance, at least one."""
    return max(1, int(round(km / pixel_km)))


DEFAULT_TIERS: tuple[TierPolicy, ...] = (
    # Rain areas: extent, frontal bands, onset/clearance timing.  VIP 1 sits
    # at 18 dBZ; 20 is the floor the imagery already treats as "worth routing
    # around" (imagery.FAINT_ECHO_DBZ), which keeps clear-air and bright-band
    # residue out of the objects.
    TierPolicy("rain20", 20.0, 64.0, 8.0),
    # Convective cores, recorded at both candidate thresholds so the history
    # settles the choice: 35 detects cells earlier, 41 matches the live
    # layer's RADAR_SIGNIFICANT_DBZ (VIP 3 "heavy", the AIM line).
    TierPolicy("core35", 35.0, 12.0),
    TierPolicy("core41", 41.0, 8.0),
)


@dataclass(frozen=True)
class CellPolicy:
    tiers: tuple[TierPolicy, ...] = DEFAULT_TIERS

    # --- Motion field --------------------------------------------------------
    # Reflectivity below this contributes nothing to the correlation field, so
    # clear-air returns and drizzle do not drive the match.
    flow_floor_dbz: float = 10.0
    # A tile needs this much echo (>= tile_echo_dbz) to be worth matching.
    tile_echo_dbz: float = 20.0
    tile_km: float = 128.0
    tile_stride_km: float = 64.0  # half-overlapping tiles
    # 1 % of a 128 km tile is ~160 km² of echo: enough to match on, low enough
    # that an isolated cell — the case that matters most — gets its tile tried.
    min_tile_echo_fraction: float = 0.01
    min_tile_valid_fraction: float = 0.5  # covered (not nodata) share of the tile
    # Search radius follows from the fastest motion we accept over the pair.
    max_speed_kt: float = 100.0
    # Pair spacing, preferred first.  DBZH is a rolling 10-minute maximum, so
    # consecutive 5-minute frames share half their window and move ~2 px:
    # too little displacement to measure well.  10 first, then 15, then 5.
    pair_minutes: tuple[int, ...] = (10, 15, 5)
    min_ncc: float = 0.5
    # Forward and reverse match must agree to within this many pixels.
    max_reciprocity_px: float = 1.5
    # Share of a cell's pixels that must sit in matched tiles before it gets a
    # velocity at all.
    min_motion_support: float = 0.5

    # --- Lineage -------------------------------------------------------------
    lineage_max_gap_minutes: int = 10
    # #600 used an unmeasured 20 %; same starting point, now in the policy.
    lineage_overlap_fraction: float = 0.2

    # --- Attributes ----------------------------------------------------------
    # Flashes are matched to the nearest cell pixel within this distance: the
    # LI frame and the DBZH frame are up to 10 minutes apart, and a cell moves
    # ~5 km in that time at 30 kt.
    flash_buffer_km: float = 5.0

    # --- Lifecycle trend -----------------------------------------------------
    trend_window_minutes: int = 30
    trend_min_history_minutes: int = 15
    trend_peak_db: float = 5.0
    trend_area_grow: float = 1.5
    trend_area_shrink: float = 0.6

    # --- Self-scoring --------------------------------------------------------
    score_leads_minutes: tuple[int, ...] = (30, 60)
    # Verification area: within this distance of an issued or persisted
    # footprint.  Beyond a 60-minute move at 100 kt it would be unfair to the
    # tracker; short of it, the misses would be someone else's cells.
    score_margin_km: float = 100.0

    name: str = "cells-1"

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def policy_version(self) -> str:
        digest = hashlib.sha1(
            json.dumps(self.as_dict(), sort_keys=True).encode("utf-8")
        ).hexdigest()[:8]
        return f"{self.name}+{digest}"

    def tier(self, name: str) -> TierPolicy:
        for tier in self.tiers:
            if tier.name == name:
                return tier
        raise KeyError(name)


DEFAULT_POLICY = CellPolicy()
