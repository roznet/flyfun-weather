"""Cell identity across frames, and the lifecycle trend it makes possible.

**Matching.**  Last frame's cells are shifted by their own measured motion (or
not at all if they had none) and overlapped with this frame's cells.  A link
needs ``lineage_overlap_fraction`` of the smaller of the two areas.  Advecting
first is what makes overlap matching robust: a 40 kt cell moves 6 km between
5-minute frames, larger than many cores.

**Identity.**  A cell inherits its parent's id only when each is the other's
dominant link (largest overlap).  So on a split the largest child carries the
storm on, and on a merge the cell keeps the id of its largest parent; every
other child starts a new id with ``parents`` recorded.  Without the dominant
rule a convective complex, which splits and merges constantly, would never
build the 30-minute history the trend needs.

**Splits and merges withhold velocity** on the frame where they happen
(brainstorm §7, "lineage ambiguity → withhold"): the footprint jumped, so
"how did this object move" has no single answer that frame.

**Ids are deterministic** — ``<tier>-<birth stamp>-<label>`` — so a replay of
the same frames under the same policy reproduces them exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from ..frames import frame_stamp
from .detect import TierDetection
from .policy import CellPolicy


@dataclass
class PreviousCell:
    """What the lineage step needs from last frame's catalogue entry."""

    id: str
    label: int
    born_at: str
    history: list
    drow_per_min: float | None
    dcol_per_min: float | None


@dataclass
class Lineage:
    id: str
    born_at: str
    event: str  # born | continued | split | merge
    parents: list[str] = field(default_factory=list)
    history: list = field(default_factory=list)


def link(
    current: TierDetection,
    previous: TierDetection | None,
    previous_cells: dict[int, PreviousCell] | None,
    gap_minutes: float,
    valid_time: datetime,
    policy: CellPolicy,
) -> dict[int, Lineage]:
    stamp = frame_stamp(valid_time)
    born_iso = valid_time.isoformat()
    tier = current.tier.name
    result: dict[int, Lineage] = {}

    def fresh(label: int, event: str = "born", parents: list[str] | None = None) -> Lineage:
        return Lineage(f"{tier}-{stamp}-{label:04d}", born_iso, event, parents or [], [])

    if previous is None or not previous_cells or not current.cells:
        return {c.label: fresh(c.label) for c in current.cells}

    # Overlap counts between advected previous cells and current cells.
    ny, nx = current.labels.shape
    pairs: dict[tuple[int, int], int] = {}
    prev_area: dict[int, int] = {}
    for pc in previous.cells:
        meta = previous_cells.get(pc.label)
        if meta is None:
            continue
        sub = previous.labels[pc.slice_rows, pc.slice_cols] == pc.label
        r, c = np.nonzero(sub)
        r = r + pc.slice_rows.start + previous.row0 - current.row0
        c = c + pc.slice_cols.start + previous.col0 - current.col0
        if meta.drow_per_min is not None and meta.dcol_per_min is not None:
            r = r + int(round(meta.drow_per_min * gap_minutes))
            c = c + int(round(meta.dcol_per_min * gap_minutes))
        prev_area[pc.label] = int(sub.sum())
        inside = (r >= 0) & (r < ny) & (c >= 0) & (c < nx)
        hit = current.labels[r[inside], c[inside]]
        hit = hit[hit > 0]
        if hit.size == 0:
            continue
        labels, counts = np.unique(hit, return_counts=True)
        for lab, cnt in zip(labels.tolist(), counts.tolist()):
            pairs[(pc.label, lab)] = cnt

    cur_area = {c.label: c.area_px for c in current.cells}
    links = {
        k: v
        for k, v in pairs.items()
        if v >= policy.lineage_overlap_fraction * min(prev_area[k[0]], cur_area[k[1]])
    }
    parents_of: dict[int, list[tuple[int, int]]] = {}
    children_of: dict[int, list[tuple[int, int]]] = {}
    for (p, c), v in links.items():
        parents_of.setdefault(c, []).append((v, p))
        children_of.setdefault(p, []).append((v, c))

    def dominant(options: list[tuple[int, int]]) -> int:
        # Largest overlap; ties broken by the lower label for determinism.
        return sorted(options, key=lambda vc: (-vc[0], vc[1]))[0][1]

    for cell in current.cells:
        parents = parents_of.get(cell.label)
        if not parents:
            result[cell.label] = fresh(cell.label)
            continue
        parent_ids = [previous_cells[p].id for _, p in sorted(parents, key=lambda vp: vp[1])]
        top_parent = dominant(parents)
        siblings = children_of[top_parent]
        merged = len(parents) > 1
        split = len(siblings) > 1
        event = "merge" if merged else ("split" if split else "continued")
        if dominant(siblings) == cell.label:
            prev = previous_cells[top_parent]
            result[cell.label] = Lineage(prev.id, prev.born_at, event, parent_ids, list(prev.history))
        else:
            result[cell.label] = fresh(cell.label, "split", parent_ids)
    return result


def trim_history(history: list, valid_time: datetime, policy: CellPolicy) -> list:
    """Keep entries inside the trend window (plus one frame of slack)."""
    keep_s = (policy.trend_window_minutes + 5) * 60
    out = []
    for entry in history:
        t = datetime.fromisoformat(entry[0])
        if (valid_time - t).total_seconds() <= keep_s:
            out.append(entry)
    return out


def trend(history: list, now_entry: list, valid_time: datetime, policy: CellPolicy) -> dict:
    """Lifecycle over the trend window from the cell's own history.

    ``history`` entries are ``velocity.history_entry`` lists; only the first
    four fields (``iso_time, peak_dbz, area_km2, flashes|None``) are read here.
    Compares now against the entry nearest ``trend_window_minutes`` ago that is
    at least ``trend_min_history_minutes`` old.  Provisional thresholds — the
    archive is what will calibrate them.
    """
    candidates = []
    for entry in history:
        age = (valid_time - datetime.fromisoformat(entry[0])).total_seconds() / 60.0
        if policy.trend_min_history_minutes <= age <= policy.trend_window_minutes + 5:
            candidates.append((abs(age - policy.trend_window_minutes), age, entry))
    if not candidates:
        return {"state": "new", "window_min": None}
    _, age, then = sorted(candidates, key=lambda x: (x[0], x[1]))[0]
    d_peak = now_entry[1] - then[1]
    ratio = now_entry[2] / then[2] if then[2] > 0 else None
    d_flash = (
        now_entry[3] - then[3]
        if now_entry[3] is not None and then[3] is not None
        else None
    )
    up = int(d_peak >= policy.trend_peak_db) + int(ratio is not None and ratio >= policy.trend_area_grow)
    down = int(d_peak <= -policy.trend_peak_db) + int(ratio is not None and ratio <= policy.trend_area_shrink)
    # Opposite signals (area up while the peak drops, or the reverse) are
    # reported as "mixed", not averaged into "steady": a spreading, weakening
    # cell and a contracting, intensifying one are both real stories.
    if up and down:
        state = "mixed"
    else:
        state = "developing" if up else "decaying" if down else "steady"
    return {
        "state": state,
        "window_min": round(age, 1),
        "d_peak_db": round(d_peak, 1),
        "area_ratio": round(ratio, 2) if ratio is not None else None,
        "d_flashes": d_flash,
    }
