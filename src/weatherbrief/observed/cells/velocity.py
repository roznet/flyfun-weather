"""A cell's velocity over its own lineage, not just this frame's pair (#662).

The raw velocity (``motion.py``) comes from one DBZH pair and is forgotten by
the next frame.  TITAN (Dixon & Wiener 1993) and SCIT (Johnson et al. 1998)
fit motion over the last several positions instead.  Two such estimates are
recorded next to the raw one, from the per-frame lineage ``history``:

* **smoothed** — exponentially weighted mean of the raw vectors the cell
  received in the last ``smooth_window_minutes`` (this frame included),
  weight ``exp(-age / smooth_tau_minutes)``;
* **track** — least-squares straight line through the centroids in the same
  window (the TITAN/SCIT form).

Both respect the raw gates: a frame whose motion was unsupported or withheld
adds no vector, and they exist only when this frame's raw vector does.  **A
split or merge starts a fresh window** — the footprint jumped, so neither
vectors nor centroids are combined across it.  Below the minimum count each
falls back to the raw vector (``n`` says how many went in), so every variant
is defined on exactly the cells the raw one is, and scoring compares like
with like.

History entries are ``[iso_time, peak_dbz, area_km2, flashes, drow_per_min,
dcol_per_min, row, col, break]`` (``break`` = 1 on a split/merge frame).
Entries written before #662 have only the first four fields and read as "no
vector, no centroid".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .policy import CellPolicy


def history_entry(valid_time: datetime, peak_dbz, area_km2, flashes, drow, dcol, row, col,
                  is_break: bool) -> list:
    return [valid_time.isoformat(), peak_dbz, area_km2, flashes, drow, dcol, row, col,
            1 if is_break else 0]


def entry_vector(entry: list) -> tuple[float, float] | None:
    if len(entry) < 6 or entry[4] is None or entry[5] is None:
        return None
    return float(entry[4]), float(entry[5])


def entry_centroid(entry: list) -> tuple[float, float] | None:
    if len(entry) < 8 or entry[6] is None or entry[7] is None:
        return None
    return float(entry[6]), float(entry[7])


def entry_is_break(entry: list) -> bool:
    return len(entry) >= 9 and bool(entry[8])


@dataclass
class Estimate:
    drow_per_min: float
    dcol_per_min: float
    n: int  # vectors (smoothed) or centroids (track) used; 1 = raw fallback


def window(history: list, now_entry: list, valid_time: datetime, policy: CellPolicy) -> list[tuple[float, list]]:
    """``(age_min, entry)`` since the last split/merge and inside the window, oldest first.

    ``history`` excludes ``now_entry``, which is always the last item.  A
    split/merge entry opens the window (its own centroid is the new footprint);
    everything before it is dropped.
    """
    entries = list(history) + [now_entry]
    start = 0
    for i, entry in enumerate(entries):
        if entry_is_break(entry):
            start = i
    out = []
    for entry in entries[start:]:
        age = (valid_time - datetime.fromisoformat(entry[0])).total_seconds() / 60.0
        if 0.0 <= age <= policy.smooth_window_minutes:
            out.append((age, entry))
    return out


def smoothed(entries: list[tuple[float, list]], raw: tuple[float, float], policy: CellPolicy) -> Estimate:
    vectors = [(age, v) for age, e in entries if (v := entry_vector(e)) is not None]
    if len(vectors) < max(1, policy.smooth_min_vectors):
        return Estimate(raw[0], raw[1], 1)
    weights = [math.exp(-age / policy.smooth_tau_minutes) for age, _ in vectors]
    total = sum(weights)
    return Estimate(
        sum(w * v[0] for w, (_, v) in zip(weights, vectors)) / total,
        sum(w * v[1] for w, (_, v) in zip(weights, vectors)) / total,
        len(vectors),
    )


def track(entries: list[tuple[float, list]], raw: tuple[float, float], policy: CellPolicy) -> Estimate:
    points = [(-age, c) for age, e in entries if (c := entry_centroid(e)) is not None]
    if len(points) < max(2, policy.track_min_centroids):
        return Estimate(raw[0], raw[1], 1)
    t_mean = sum(t for t, _ in points) / len(points)
    var = sum((t - t_mean) ** 2 for t, _ in points)
    if var <= 0:
        return Estimate(raw[0], raw[1], 1)

    def slope(k: int) -> float:
        mean = sum(c[k] for _, c in points) / len(points)
        return sum((t - t_mean) * (c[k] - mean) for t, c in points) / var

    return Estimate(slope(0), slope(1), len(points))
