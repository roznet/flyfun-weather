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
from dataclasses import asdict, dataclass, field


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


# Every way a cell's position is projected forward (#662), scored side by side.
MOTION_VARIANTS: tuple[str, ...] = ("raw", "smoothed", "track", "field", "field_anchored")


@dataclass(frozen=True)
class ClutterPolicy:
    """Non-meteorological echo evidence (#696): thresholds and weights.

    Every number here was read off the #696 reference frames against all 335
    core41 cells in Europe at 2026-10-07 00:25Z — see ``clutter.py`` for the
    table and ``designs/meteorology-decisions.md`` §42 for the decision.  They
    are **provisional**, like everything in this module: the point of
    recording the features on every cell is that a replay set can settle them.

    Thresholds sit well clear of the genuine population rather than snugly
    around the one known case: ``ring_rain_bare`` at 0.15 against a genuine
    10th percentile of 0.83, ``onset_sudden_db`` at 25 against a genuine 99th
    percentile of 19.5.  Picking them to make this one case disappear is
    exactly what the issue warns against.
    """

    # Off switches the whole assessment: no features measured, no block on any
    # cell, and the per-frame cost goes back to what it was before #696.  It
    # is a policy number, so a run with it off carries its own
    # ``policy_version`` — which is right: those catalogues answer a different
    # question and must not be mistaken for assessed ones.
    enabled: bool = True

    # --- Isolation: the enclosing precipitation ------------------------------
    # A genuine >= 41 dBZ core is embedded in its own >= 20 dBZ rain.  20 dBZ is
    # the rain20 tier's own threshold, so the two agree by construction.
    rain_dbz: float = 20.0
    # The ring is 2 px wide because the French contribution arrives on a 2 km
    # grid duplicated 2x2 onto the 1 km composite: a 1 px ring measures the
    # duplication, not the weather.
    ring_px: int = 2
    # Below this many *covered* ring pixels there is no isolation evidence.
    min_ring_px: int = 8
    # Measured over all 11,967 cores of the 17 #696 frames, against the cells
    # corroborated by lightning or a cloud top ("weather-labelled"):
    #   <= 0.15 catches 0.6-1.1 % of cores and **0** weather-labelled ones
    #   <= 0.30 catches 1.0-1.5 % and still 0;  <= 0.40 starts catching them.
    ring_rain_bare: float = 0.15
    ring_rain_thin: float = 0.30
    # The first pass read these off one frame's core41 cells (p10 = 23) and
    # was wrong: over 17 frames and both tiers, <= 8 catches more than a tenth
    # of the weather-labelled cores, because a core35 is large relative to its
    # own rain area and the ratio is not tier-free.  <= 1.5 catches 0.5-0.8 %
    # of cores and 0 weather-labelled; <= 2.0 catches 1.3-1.5 % and 3.
    rain_ratio_bare: float = 1.5
    rain_ratio_thin: float = 2.0
    # When the rain20 tier dropped the region containing a core (under its own
    # 64 km2 minimum), the ratio is measured on a crop this many pixels wider
    # than the core.  Big enough to hold any region that tier would have
    # dropped, so a region reaching the crop edge really is large and the
    # feature stays silent rather than guessing.
    rain_crop_px: int = 12

    # --- Onset at the cell's own pixels -------------------------------------
    # Dilated by 2 px so a cell that drifted a pixel is not read as newborn.
    onset_dilate_px: int = 2
    # Where the radar looked and saw nothing, the earlier field reads as this
    # rather than as the NaN sentinel: "below the detection floor" is an
    # observation, and differencing against the sentinel gave 1000 dB onsets.
    onset_floor_dbz: float = 0.0
    onset_sudden_db: float = 25.0  # 0.9-1.1 % of cores, 1 weather-labelled
    onset_fast_db: float = 20.0    # 15.0 caught 7 weather-labelled core41s

    # --- Single-pixel peaks --------------------------------------------------
    median_px: int = 3
    robust_drop_db: float = 6.0    # genuine p90 = 2.5-3.5; the case 4.5-10.0

    # --- Gabella spatial texture --------------------------------------------
    gabella_window: int = 5        # Dutch operational Cartesian settings
    gabella_db: float = 6.0
    gabella_min_neighbours: int = 6
    continuity_low: float = 0.80   # 1.7-2.0 % of cores, <= 1 weather-labelled

    # --- Supporting ----------------------------------------------------------
    # Only where lightning was really observed as zero, never where it is
    # merely pending (#666).
    silent_peak_dbz: float = 50.0
    # A satellite cloud top at or above this clears the cell outright: a strong
    # echo under no cloud is not possible.  Only bites where CTTH is collected.
    veto_top_fl: float = 100.0

    # --- Weights and levels --------------------------------------------------
    # Isolation carries the separation; the rest corroborate.  Isolation alone
    # reaches `suspect`; isolation plus one corroborator reaches `confirmed`.
    w_ring_bare: float = 3.0
    w_ring_thin: float = 1.0
    w_ratio_bare: float = 2.0
    w_ratio_thin: float = 1.0
    w_onset_sudden: float = 2.0
    w_onset_fast: float = 1.0
    w_robust_drop: float = 1.0
    w_continuity: float = 1.0
    # Recorded with the other reasons but **weighted at zero**: plenty of real
    # convection never sparks, lightning coverage and latency vary, and the
    # #696 review is explicit that no lightning is not grounds to reject a
    # shower.  Kept visible so an evaluation can weigh it rather than
    # rediscover it.
    w_silent: float = 0.0
    suspect_score: float = 3.0
    confirmed_score: float = 5.0


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

    # --- Velocity over the lineage (#662) -------------------------------------
    # Smoothed velocity: exponentially weighted mean (weight exp(-age/tau)) of
    # the raw single-pair vectors the cell received within the window,
    # including this frame.  ``track``: least-squares line through the
    # centroids in the same window.  Both start afresh at a split or merge, and
    # both fall back to the raw vector below their minimum count, so every
    # variant is scored on the same cells.  Provisional, like everything here.
    smooth_window_minutes: int = 20
    smooth_tau_minutes: float = 10.0
    smooth_min_vectors: int = 2
    track_min_centroids: int = 3

    # --- Advection along the motion field (#662) -----------------------------
    # Matched tile vectors are smoothed into a field by normalised convolution
    # on the tile lattice (Gaussian, sigma in lattice steps) and reach at most
    # ``field_fill_tiles`` lattice steps past a matched tile: never far beyond
    # what was measured.  Trajectories are integrated (midpoint rule) in steps
    # of ``advect_step_minutes``.
    field_sigma_tiles: float = 1.0
    field_fill_tiles: int = 1
    advect_step_minutes: float = 5.0
    # Which motion the map arrow uses: raw | smoothed | track | field |
    # field_anchored.  Raw until the scores say otherwise (issue #662: a
    # variant becomes the default only if it beats the raw straight line).
    display_motion: str = "raw"

    # --- Self-scoring --------------------------------------------------------
    score_leads_minutes: tuple[int, ...] = (30, 60)
    # Verification area: within this distance of an issued or persisted
    # footprint.  Beyond a 60-minute move at 100 kt it would be unfair to the
    # tracker; short of it, the misses would be someone else's cells.
    score_margin_km: float = 100.0

    # --- Non-meteorological echo evidence (#696) ------------------------------
    # Measured and recorded on every core; acting on it is the droplet's
    # decision and is off by default (WB_CELLS_CLUTTER_SUPPRESS).
    clutter: ClutterPolicy = field(default_factory=ClutterPolicy)

    name: str = "cells-3"

    def __post_init__(self) -> None:
        if self.display_motion not in MOTION_VARIANTS:
            raise ValueError(f"display_motion must be one of {MOTION_VARIANTS}, not {self.display_motion!r}")
        if self.smooth_window_minutes > self.trend_window_minutes + 5:
            # The lineage history is trimmed to the trend window (+5 min).
            raise ValueError("smooth_window_minutes cannot exceed trend_window_minutes + 5")

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
