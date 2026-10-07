"""Non-meteorological echo evidence per cell (#696).

We detect and track **reflectivity objects**, then use them as **storms**.
Nothing in between asks whether the echo is weather at all, so a wind-farm or
anomalous-propagation return becomes a core41 cell and the live layer says
*"Extreme cell (57 dBZ) 9 NM left of track"*.  This module measures evidence
about each core and records it; it never deletes a pixel and never changes a
dBZ value.

## What it measures, and why these features

Measured on the reference case of #696 (2026-10-07 00:10–00:40Z, a block of
45–57 dBZ over the Somme wind farms) against **every** core41 cell in Europe
on the same frames — 335 cores at 00:25Z, a convective night over France:

| feature | the clutter core | the 334 others |
|---|---|---|
| ``rain_margin`` ring >= 20 dBZ | **0.00** | p10 0.83, median 1.00 |
| ``rain_ratio`` enclosing rain20 / core | **1.0** | p10 23 |
| ``onset_db`` at its own pixels | **+30.0** (00:10Z) | p99 +19.5 |
| ``robust_drop`` peak - 3x3-median peak | **4.5 … 10.0** | p90 2.5 … 3.5 |
| ``continuity`` Gabella 5x5 | 0.88 | p10 1.00 |
| ``quality_zero`` OPERA ``qi_total`` == 0 | 1.00 | **median 1.00 — no signal** |

So the discriminator that actually works on our data is **isolation**: a
genuine 41 dBZ core is physically embedded in its own ≥ 20 dBZ precipitation
— 0.83 of its surroundings at the 10th percentile — while ground clutter is a
bare block sitting in air the radar looked at and found empty.  Everything
else is corroboration.

Two published approaches were weighed first (see
``designs/meteorology-decisions.md`` §42):

* **Gabella** continuity *and* area-to-perimeter geometry.  Continuity carries
  a little signal; **the geometry term does not and would do harm** — the
  clutter core's area/perimeter is 1.42 against a median of 1.24 for genuine
  small cores, so the published 1.3 threshold flags real showers in preference
  to the clutter.  Continuity is recorded and weighted lightly; the geometry
  ratio is recorded and weighted at zero.
* **OPERA's own quality index.**  Recorded, weighted at zero.  ~95 % of all
  European echo pixels carry a literal 0 because most nodes publish no index,
  so "quality 0" means "unmeasured", not "clutter" (``opera._find_quality_group``).

## The rules the score obeys

1. **Isolation is necessary.**  No cell is suspect without the rain-margin
   evidence firing, whatever the other features say.  That is the feature with
   measured separation; the rest cannot convict on their own.
2. **Missing evidence is never support.**  A feature that could not be
   computed scores zero and is named in ``unknown``.  A cell whose ring is
   mostly *nodata* has no isolation evidence — "we cannot see there" is not
   "there is no rain there" — so it can never be suspect.
3. **Physical corroboration vetoes.**  Observed lightning in the cell, or a
   cloud top where CTTH is running, clears it outright.  Lightning that is
   merely *pending* (#666) is not an observation of zero flashes.
4. **Nothing is deleted.**  The level and its reasons ride along with the
   cell; acting on them is the droplet's decision, off by default
   (``WB_CELLS_CLUTTER_SUPPRESS``).

The French contribution to the composite arrives on a **2 km** grid, duplicated
2x2 onto the 1 km composite, so every small French cell has hard 1 km edges by
construction.  Hence the ring is taken at 2 px and the continuity window is
5x5: both reach past the duplication.  A 1 px ring would call half of France
clutter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from ..frames import GridFrame
from .detect import TierDetection
from .policy import ClutterPolicy

_S8 = np.ones((3, 3), dtype=bool)
_S4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)

#: Levels, weakest first.  ``clear`` is "no evidence of clutter", **not**
#: "this is weather" — most cells are clear because nothing was measured
#: against them.
CLEAR = "clear"
SUSPECT = "suspect"
CONFIRMED = "confirmed"

#: Features are rounded to this many decimals **before** they are scored, not
#: on the way to the wire.  The lightning amend (#666) re-scores a published
#: cell from the features stored in its catalogue — lightning is a veto, so a
#: frame whose flashes land late must lose its suspicion — and that re-score
#: has to agree with what a replay computes from the raw frame.  Rounding
#: first makes the two identical instead of merely close, which is what keeps
#: "an amended catalogue equals its replay byte for byte" true.
FEATURE_DIGITS = 3


@dataclass
class CellEvidence:
    """One cell's clutter evidence: every number, the score, and the reasons."""

    level: str = CLEAR
    score: float = 0.0
    reasons: list[dict] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        from .catalogue import r

        out = {
            "level": self.level,
            "score": r(self.score, 1),
            "features": dict(sorted(self.features.items())),
        }
        if self.reasons:
            out["reasons"] = self.reasons
        if self.unknown:
            out["unknown"] = sorted(self.unknown)
        return out


#: Stand-in for NaN inside the neighbourhood filters, far below any
#: reflectivity so it can never win a maximum.  Anything at or under
#: ``_SENTINEL_FLOOR`` after a filter came from the sentinel rather than from
#: the radar and must not be read as a measurement.
_SENTINEL = -999.0
_SENTINEL_FLOOR = -900.0


def _finite(values: np.ndarray) -> np.ndarray:
    """``values`` with NaN replaced by a sentinel far below any dBZ."""
    return np.where(np.isfinite(values), values, _SENTINEL).astype(np.float32)


def _crop(det: TierDetection, cell, pad: int) -> tuple[slice, slice]:
    """The cell's bounding box grown by ``pad``, clipped to the window.

    Every feature is computed on this crop rather than the whole composite:
    cores cover a tiny share of a 3800 x 4400 grid, and a full-grid median or
    neighbour count would cost seconds a frame against the ~3 s budget #666
    bought.  ``pad`` must cover the filter's own reach or the cell's edge
    pixels get a truncated neighbourhood.
    """
    ny, nx = det.labels.shape
    return (
        slice(max(0, cell.slice_rows.start - pad), min(ny, cell.slice_rows.stop + pad)),
        slice(max(0, cell.slice_cols.start - pad), min(nx, cell.slice_cols.stop + pad)),
    )


def rain_margin(
    det: TierDetection, rain_labels: np.ndarray | None, frame: GridFrame,
    policy: ClutterPolicy,
) -> list[dict]:
    """Per cell: how much precipitation surrounds it, and how big that is.

    ``ring_rain`` is the share of the cell's outer ring (dilated
    ``ring_px``, so it reaches past the French 2 km duplication) that carries
    at least ``rain_dbz``, counted **only over ring pixels the radar covered**.
    A ring that is mostly ``nodata`` yields ``None``: we cannot see there, which
    is not evidence of anything.  ``rain_ratio`` is the area of the enclosing
    ``rain20`` component divided by the cell's own area — 1.0 means the cell
    *is* the whole rain area, with nothing around it.
    """
    values = np.asarray(frame.values)
    covered = ~np.asarray(frame.nodata)
    rain_sizes = np.bincount(rain_labels.ravel()) if rain_labels is not None else None
    out: list[dict] = []
    for cell in det.cells:
        sr, sc = _crop(det, cell, policy.ring_px + 1)
        own = det.labels[sr, sc] == cell.label
        ring = ndimage.binary_dilation(own, _S8, iterations=policy.ring_px) & ~own
        ring_covered = ring & covered[sr, sc]
        n_ring = int(ring_covered.sum())
        rain = _finite(values[sr, sc]) >= policy.rain_dbz
        ring_rain = float(rain[ring_covered].mean()) if n_ring >= policy.min_ring_px else None

        ratio = None
        if rain_sizes is not None:
            under = rain_labels[sr, sc][own]
            labelled = under[under > 0]
            if labelled.size:
                enclosing = int(np.bincount(labelled).argmax())
                ratio = float(rain_sizes[enclosing]) / max(cell.area_px, 1)
            else:
                # No rain20 label over a >= 41 dBZ core means that tier dropped
                # the region for being under its own minimum area — so the
                # precipitation around this core is *bounded* by that minimum,
                # which is the isolation this feature is looking for.  Leaving
                # it unknown would lose the evidence on exactly the cells that
                # matter: 292 of 11,967 cores on the #696 frames, the bare ones.
                # Measure it on the crop, where by construction it fits.
                br, bc = _crop(det, cell, policy.rain_crop_px)
                local, _ = ndimage.label(_finite(values[br, bc]) >= policy.rain_dbz,
                                         structure=_S8)
                mine = local[det.labels[br, bc] == cell.label]
                mine = mine[mine > 0]
                if mine.size:
                    region = local == int(np.bincount(mine).argmax())
                    # Clipped at the crop edge: the region is *at least* this
                    # big, so it is not evidence of isolation.  Claiming a
                    # ratio from a truncated measurement is how a genuine core
                    # at the corner of a crop would get flagged.
                    edge = (region[0, :].any() or region[-1, :].any()
                            or region[:, 0].any() or region[:, -1].any())
                    if not edge:
                        ratio = float(int(region.sum())) / max(cell.area_px, 1)
        out.append({
            "ring_rain": ring_rain,
            "ring_px": n_ring,
            "ring_covered_frac": float(n_ring / max(int(ring.sum()), 1)),
            "rain_ratio": ratio,
        })
    return out


def onset_db(det: TierDetection, frame: GridFrame, earlier: GridFrame | None,
             policy: ClutterPolicy) -> list[float | None]:
    """Per cell: peak now minus the strongest echo at its **own pixels** before.

    Keyed on pixels, never on a lineage id: in the reference case the object
    dropped below the minimum area at 00:15Z and restarted its id while the
    echo under it never went away, so an age- or lineage-based onset test is
    blind to exactly this shape (#696 review).  The earlier frame is taken
    through a ``onset_dilate_px`` maximum filter, so a cell that merely drifted
    a pixel or two is not read as newborn.
    """
    n = len(det.cells)
    if earlier is None or earlier.grid != frame.grid or earlier.window != frame.window:
        return [None] * n
    now = np.asarray(frame.values)
    before = np.asarray(earlier.values)
    was_covered = ~np.asarray(earlier.nodata)
    pad = policy.onset_dilate_px
    out: list[float | None] = []
    for cell in det.cells:
        sr, sc = _crop(det, cell, pad)
        own = det.labels[sr, sc] == cell.label
        seen = own & was_covered[sr, sc]
        if not seen.any():
            out.append(None)  # the radar did not cover these pixels last frame
            continue
        # Floor the earlier field: where the radar looked and saw nothing the
        # value is NaN, and differencing against the NaN sentinel produced
        # onsets of ~1000 dB (max 1063 over the #696 frames).  "Below the
        # detection floor" is a real observation worth a real number, so it
        # reads as `onset_floor_dbz` rather than as minus infinity.
        earlier_values = np.maximum(_finite(before[sr, sc]), policy.onset_floor_dbz)
        dilated = ndimage.maximum_filter(earlier_values, size=2 * pad + 1)
        out.append(float(_finite(now[sr, sc])[seen].max() - dilated[seen].max()))
    return out


def robust_peaks(det: TierDetection, frame: GridFrame, policy: ClutterPolicy) -> list[float | None]:
    """Per cell: the peak of a ``median_px`` median-filtered field.

    ``peak_dbz`` is one pixel out of the whole composite, and CIRRUS is a
    *maximum* over contributing radars and a 10-minute window, so a single
    spurious bin becomes the headline — "Extreme cell (57 dBZ)" came from one
    pixel whose median-filtered value is 52.5, and the 00:10Z frame's 57.5
    drops to 47.5.  Recorded alongside ``peak_dbz``; nothing displays or
    alerts on it yet (meteorology-decisions §42).
    """
    values = np.asarray(frame.values)
    pad = policy.median_px // 2 + 1
    out: list[float | None] = []
    for cell in det.cells:
        sr, sc = _crop(det, cell, pad)
        own = det.labels[sr, sc] == cell.label
        median = ndimage.median_filter(_finite(values[sr, sc]), size=policy.median_px)
        # A window where most neighbours are NaN medians to the sentinel, not
        # to a reflectivity.  One core in 60,973 on the 2026-10-03/04 replay —
        # a 2-pixel-wide filament — had that at *every* one of its pixels, and
        # wrote `robust_peak_dbz: -999` into its catalogue and a +1 from a
        # 1044 dB `robust_drop`.  Such a cell has no robust peak: report it
        # unknown, which costs it the corroborator rather than inventing one.
        usable = median[own] > _SENTINEL_FLOOR
        out.append(float(median[own][usable].max()) if usable.any() else None)
    return out


def continuity(det: TierDetection, frame: GridFrame, policy: ClutterPolicy) -> list[float]:
    """Gabella local echo continuity per cell: share of pixels with enough peers.

    A pixel is *continuous* when at least ``gabella_min_neighbours`` of the
    neighbours in its ``gabella_window`` square carry an echo within
    ``gabella_db`` of its own value.  The 5x5 window and 6-neighbour count are
    the Dutch operational product's Cartesian settings (ESSD 2025 §3.6), which
    also happen to reach past the French 2 km duplication: a lone 2 km pixel
    gives its four 1 km copies only three similar neighbours and fails, while a
    2x2 block of 2 km pixels passes comfortably.

    Weak on our composite (genuine cores sit at 1.00, the clutter core at
    0.88) — supporting evidence, not a test.
    """
    values = np.asarray(frame.values)
    reach = policy.gabella_window // 2
    out: list[float] = []
    for cell in det.cells:
        sr, sc = _crop(det, cell, reach)
        crop = values[sr, sc]
        finite = np.isfinite(crop)
        filled = _finite(crop)
        count = np.zeros(crop.shape, dtype=np.int16)
        for dr in range(-reach, reach + 1):
            for dc in range(-reach, reach + 1):
                if dr == 0 and dc == 0:
                    continue
                shifted = np.roll(np.roll(filled, dr, axis=0), dc, axis=1)
                count += ((np.abs(shifted - filled) <= policy.gabella_db)
                          & finite & (shifted > _SENTINEL_FLOOR)).astype(np.int16)
        own = det.labels[sr, sc] == cell.label
        out.append(float((count[own] >= policy.gabella_min_neighbours).mean()))
    return out


def area_perimeter(det: TierDetection) -> list[float]:
    """Gabella's geometry term: cell area over 4-connected perimeter, in pixels.

    Recorded for the archive and **weighted at zero**: measured on #696's
    frames it points the wrong way (the clutter core 1.42, genuine small cores
    a median 1.24), so the published 1.3 threshold would flag real showers
    first.  Kept so a later evaluation can revisit it rather than re-derive it.
    """
    out: list[float] = []
    for cell in det.cells:
        own = det.labels[cell.slice_rows, cell.slice_cols] == cell.label
        perimeter = int((own & ~ndimage.binary_erosion(own, _S4)).sum())
        out.append(float(cell.area_px) / max(perimeter, 1))
    return out


def quality_stats(det: TierDetection, quality: np.ndarray | None) -> list[dict]:
    """Per cell: the OPERA quality index's distribution over its pixels.

    The whole distribution, not a minimum: an aggregate index whose components
    we cannot see is only interpretable in bulk, and it is kept as evidence for
    a later evaluation (#696 recommendation (3)).  ``missing`` is the share of
    pixels where no node published an index at all.
    """
    n = len(det.cells)
    if quality is None:
        return [{}] * n
    out: list[dict] = []
    for cell in det.cells:
        own = det.labels[cell.slice_rows, cell.slice_cols] == cell.label
        values = quality[cell.slice_rows, cell.slice_cols][own]
        present = values[np.isfinite(values)]
        out.append({
            "quality_missing": float(1.0 - present.size / max(values.size, 1)),
            "quality_zero": float((present == 0.0).mean()) if present.size else None,
            "quality_median": float(np.median(present)) if present.size else None,
        })
    return out


def score_cell(features: dict, policy: ClutterPolicy, *, peak_dbz: float,
               flashes: int | None, top_fl: float | None) -> CellEvidence:
    """Combine one cell's features into a level, with its reasons recorded.

    A transparent additive score rather than a fuzzy-logic fusion: with one
    feature carrying the separation and the rest corroborating, weights we can
    read off the catalogue are worth more than a combination rule we cannot.
    It is **not a probability** and must not be presented as one.
    """
    evidence = CellEvidence(features=dict(features))
    reasons: list[dict] = []
    unknown: list[str] = []

    def add(name: str, value, points: float, note: str) -> None:
        reasons.append({"feature": name, "value": value, "points": points, "note": note})

    # --- Vetoes: physical corroboration outranks every statistical feature ---
    if flashes:
        evidence.reasons = [{"feature": "flashes", "value": flashes, "points": 0.0,
                             "note": "lightning observed in the cell"}]
        return evidence
    if top_fl is not None and top_fl >= policy.veto_top_fl:
        evidence.reasons = [{"feature": "top_fl", "value": top_fl, "points": 0.0,
                             "note": "satellite cloud top above the veto level"}]
        return evidence

    # --- Isolation: the one feature with measured separation ----------------
    ring = features.get("ring_rain")
    ratio = features.get("rain_ratio")
    isolated = False
    if ring is None:
        unknown.append("ring_rain")
    elif ring <= policy.ring_rain_bare:
        isolated = True
        add("ring_rain", ring, policy.w_ring_bare, "no precipitation around the core")
    elif ring <= policy.ring_rain_thin:
        isolated = True
        add("ring_rain", ring, policy.w_ring_thin, "little precipitation around the core")
    if ratio is None:
        unknown.append("rain_ratio")
    elif ratio <= policy.rain_ratio_bare:
        isolated = True
        add("rain_ratio", ratio, policy.w_ratio_bare, "the core is the whole rain area")
    elif ratio <= policy.rain_ratio_thin:
        isolated = True
        add("rain_ratio", ratio, policy.w_ratio_thin, "barely any rain area around the core")

    if not isolated:
        # Rule 1: nothing convicts without the isolation evidence.  Features
        # are still recorded on the cell — the archive needs them whatever the
        # verdict — but they add no points and the cell stays clear.
        evidence.unknown = unknown
        return evidence

    # --- Corroboration ------------------------------------------------------
    onset = features.get("onset_db")
    if onset is None:
        unknown.append("onset_db")
    elif onset >= policy.onset_sudden_db:
        add("onset_db", onset, policy.w_onset_sudden, "reached full strength in one frame")
    elif onset >= policy.onset_fast_db:
        add("onset_db", onset, policy.w_onset_fast, "grew unusually fast in one frame")

    drop = features.get("robust_drop")
    if drop is None:
        unknown.append("robust_drop")
    elif drop >= policy.robust_drop_db:
        add("robust_drop", drop, policy.w_robust_drop, "the peak is one pixel, not a core")

    cont = features.get("continuity")
    if cont is None:
        unknown.append("continuity")
    elif cont <= policy.continuity_low:
        add("continuity", cont, policy.w_continuity, "the echo is not locally continuous")

    # Supporting only, and only where lightning really was observed as zero:
    # a pending frame (#666) has no lightning observation to speak of.
    if flashes == 0 and peak_dbz >= policy.silent_peak_dbz:
        add("flashes", 0, policy.w_silent, "no lightning under a very strong echo")

    evidence.reasons = reasons
    evidence.unknown = unknown
    evidence.score = float(sum(x["points"] for x in reasons))
    if evidence.score >= policy.confirmed_score:
        evidence.level = CONFIRMED
    elif evidence.score >= policy.suspect_score:
        evidence.level = SUSPECT
    return evidence


def assessable(tier, policy: ClutterPolicy) -> bool:
    """Whether clutter evidence means anything for this tier.

    Only tiers **above** the rain threshold.  For ``rain20`` itself the
    isolation feature is vacuous by construction — the ring around a 20 dBZ
    area is below 20 dBZ because that is where the area ends — so every rain
    area in Europe would read as isolated.  A clutter block does also raise a
    small bare rain20 area; it stays far below ``RAIN_MIN_AREA_KM2`` so it
    never becomes a displayed cell, though it can still contribute a tiny
    floor-intensity rain band on the ribbon (documented limitation, #696).
    """
    return tier.threshold_dbz > policy.rain_dbz


def assess(
    det: TierDetection,
    frame: GridFrame,
    *,
    rain_labels: np.ndarray | None,
    earlier: GridFrame | None,
    quality: np.ndarray | None,
    flashes: list[int | None],
    tops: list[float | None],
    policy: ClutterPolicy,
) -> list[CellEvidence]:
    """Evidence for every cell of one tier on one frame.

    ``rain_labels`` is the ``rain20`` tier's label array on the same window
    (``None`` where that tier was not detected, which costs the ``rain_ratio``
    feature only).  Everything here is per-frame and pixel-keyed: no lineage,
    no rolling state, so a replay of the same frames reproduces it exactly.
    """
    n = len(det.cells)
    if n == 0 or not policy.enabled:
        return [] if n == 0 else [CellEvidence() for _ in range(n)]
    margins = rain_margin(det, rain_labels, frame, policy)
    onsets = onset_db(det, frame, earlier, policy)
    robust = robust_peaks(det, frame, policy)
    conts = continuity(det, frame, policy)
    aps = area_perimeter(det)
    quals = quality_stats(det, quality)
    out: list[CellEvidence] = []
    for k, cell in enumerate(det.cells):
        features = round_features({
            **margins[k],
            "onset_db": onsets[k],
            "robust_peak_dbz": robust[k],
            "robust_drop": None if robust[k] is None else float(cell.peak_dbz) - robust[k],
            "continuity": conts[k],
            "area_perimeter": aps[k],
            **quals[k],
        })
        out.append(score_cell(
            features, policy, peak_dbz=float(cell.peak_dbz),
            flashes=flashes[k] if k < len(flashes) else None,
            top_fl=tops[k] if k < len(tops) else None,
        ))
    return out


def round_features(features: dict) -> dict:
    """Features at wire precision, which is also scoring precision.

    See ``FEATURE_DIGITS``.  ``None`` stays ``None`` (unknown), and a
    non-finite value becomes ``None`` rather than reaching JSON.
    """
    from .catalogue import r

    return {k: (r(v, FEATURE_DIGITS) if isinstance(v, float) else v)
            for k, v in features.items()}


def rescore(cell: dict, policy: ClutterPolicy) -> dict | None:
    """Re-run the score over a published cell's stored features (#666 amend).

    Only the inputs that can arrive *after* a frame is published change the
    verdict — lightning, and a cloud top where CTTH is collected — and both
    are vetoes, so a late flash can only ever clear a suspicion, never create
    one.  Returns the new ``clutter`` block, or ``None`` for a cell that never
    carried one (a lower tier, or a pre-#696 catalogue).
    """
    stored = cell.get("clutter")
    if not stored:
        return None
    evidence = score_cell(
        stored.get("features") or {}, policy,
        peak_dbz=float(cell.get("peak_dbz") or 0.0),
        flashes=cell.get("flashes"),
        top_fl=cell.get("top_fl"),
    )
    return evidence.as_dict()


def suspect(cell: dict) -> bool:
    """Whether a catalogue or display cell carries clutter suspicion.

    One reader for every consumer — the droplet's storm and band filters, the
    review map and the evaluation CLI — so "what counts as suspect" cannot
    drift between them.  A cell from before #696 has no ``clutter`` block and
    is never suspect.
    """
    level = (cell.get("clutter") or {}).get("level")
    return level in (SUSPECT, CONFIRMED)
