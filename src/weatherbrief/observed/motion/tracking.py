"""Bounded independent contour translation; diagnostics are not forecast skill.

Arrays use the north-increasing ground-grid rows, so dx/dy are east/north.
Cloud vectors here are *candidate image translations*: publication must apply
the independent geolocation gate. No source decoding or registration is done here.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
import math
import time

import numpy as np
from scipy import ndimage, signal
from shapely import box, distance
from shapely.affinity import translate
from shapely.geometry.base import BaseGeometry

from weatherbrief.models.observed_motion import PairDiagnostics, PatchDiagnostics
from .geometry import footprint
from .history import AnalysisFrame, check_deadline
from .policy import DEFAULT_POLICY


@dataclass(frozen=True)
class TrackSample:
    frame_id: str
    reference_at: datetime
    footprint: BaseGeometry


@dataclass
class Track:
    feature_id: str
    source_id: str
    reference_at: datetime
    footprint: BaseGeometry
    history: list[TrackSample]
    velocity_xy_m_s: tuple[float, float] | None = None
    reason_codes: tuple[str, ...] = ()
    pair_diagnostics: list[PairDiagnostics] = field(default_factory=list)
    fit_rms_residual_cells: float | None = None


@dataclass
class TrackingCount:
    """Latest supplied frame/source counts, NOT sums over history or coverage.

    Full-field = all eight-connected detected-and-known components. Small <9
    cells; eligible >=9. Selected = route-ranked eligible motion candidates
    (<=32). Emitted = actual bounded observed tracks, including small fillers;
    omitted = full-field minus emitted. Null means the stage was not evaluated.
    ``counts_complete`` covers full-field labeling/size counts only;
    ``selection_complete`` covers route ranking and bounded observed emission,
    not matching success or complete weather coverage.
    """
    source_id: str
    reference_frame_id: str
    full_field_detections: int | None = None
    small_detections: int | None = None
    eligible_candidates: int | None = None
    selected_candidates: int | None = None
    emitted_observed_features: int = 0
    omitted_observed_features: int | None = None
    counts_complete: bool = False
    selection_complete: bool = False
    reason_codes: tuple[str, ...] = ()


@dataclass
class TrackingResult:
    tracks: list[Track]
    reason_codes: tuple[str, ...]
    counts: tuple[TrackingCount, ...]


@dataclass
class _Field:
    frame: AnalysisFrame
    labels: np.ndarray
    sizes: np.ndarray
    geometries: dict[int, BaseGeometry] = field(default_factory=dict)
    selected: list[int] = field(default_factory=list)

    def geometry(self, label):
        if label not in self.geometries:
            self.geometries[label] = footprint(self.labels == label, self.frame.grid)
        return self.geometries[label]


def _label(frame, policy, deadline):
    check_deadline(deadline)
    if (frame.grid.width * frame.grid.height > policy.max_domain_cells
            or max(frame.grid.shape) > policy.max_domain_dimension_cells):
        raise ValueError("region_too_large")
    if any(np.shape(a) != frame.grid.shape for a in (frame.known, frame.detected, frame.descriptor)):
        raise ValueError("incompatible_grid")
    labels, total = ndimage.label(np.asarray(frame.detected, bool) & np.asarray(frame.known, bool),
                                 structure=np.ones((3, 3), dtype=bool))
    sizes = np.bincount(labels.ravel(), minlength=total+1)
    sizes[0] = 0
    return _Field(frame, labels, sizes)


def _rank(observed, route, policy, deadline):
    """Exact full-cell-union distance without polygonizing omitted components."""
    labels, grid = observed.labels, observed.frame.grid
    distances = np.full(len(observed.sizes), np.inf)
    rows, cols = np.nonzero(labels)
    for start in range(0, len(rows), 4096):
        check_deadline(deadline)
        r, c = rows[start:start+4096], cols[start:start+4096]
        x, y = grid.origin_x_m+c*grid.cell_size_m, grid.origin_y_m+r*grid.cell_size_m
        values = distance(box(x, y, x+grid.cell_size_m, y+grid.cell_size_m), route)
        np.minimum.at(distances, labels[r, c], values)
    order = sorted(range(1, len(observed.sizes)),
                   key=lambda label: (distances[label], -observed.sizes[label], label))
    observed.selected = [i for i in order if observed.sizes[i] >= policy.min_track_cells][:policy.max_candidates_per_source]
    # Size never changes route ordering within either group. Small observations
    # fill unused work slots and remain explicit positive evidence.
    emitted = set(observed.selected)
    emitted.update([i for i in order if observed.sizes[i] < policy.min_track_cells]
                   [:policy.max_candidates_per_source-len(emitted)])
    return [i for i in order if i in emitted]


def _fixed_support(reference, target, radius):
    # EDT on a one-cell false pad is the exact distance to the closest unknown
    # lattice cell, including outside-domain cells. >radius intersects support
    # under *every* searched integer shift in the circular search region.
    target_known = target.known & np.isfinite(target.descriptor)
    clearance = ndimage.distance_transform_edt(np.pad(target_known, 1))[1:-1, 1:-1]
    return reference.known & np.isfinite(reference.descriptor) & (clearance > radius)


def _centres(reference, mask, support, policy):
    size = policy.template_size_cells
    count = ndimage.uniform_filter(support.astype(float), size, mode="constant") * size**2
    values = np.where(support, reference.descriptor, 0.)
    total = ndimage.uniform_filter(values, size, mode="constant") * size**2
    squares = ndimage.uniform_filter(values**2, size, mode="constant") * size**2
    variance = np.maximum(0., squares / np.maximum(count, 1.) - (total / np.maximum(count, 1.))**2)
    good = (mask & (count >= policy.min_template_support_fraction*size**2-1e-8)
            & (count >= policy.min_template_support_samples-1e-8) & (variance > 1e-12))
    half = size//2
    good[:half] = good[-half:] = False
    good[:, :half] = good[:, -half:] = False
    rows, cols = np.nonzero(good)
    order = np.lexsort((cols, rows, -variance[rows, cols]))
    selected = []
    for i in order:
        r, c = int(rows[i]), int(cols[i])
        if not selected or math.hypot(r-selected[0][0], c-selected[0][1]) >= 2.:
            selected.append((r, c))
            if len(selected) == policy.required_usable_patches_per_feature_pair:
                break
    return selected


def _refine(scores, row, col):
    local = scores[row-1:row+2, col-1:col+2]
    if local.shape != (3, 3) or not np.all(np.isfinite(local)):
        return 0., 0., "integer"
    yy, xx = np.mgrid[-1:2, -1:2]
    design = np.column_stack((xx.ravel()**2, yy.ravel()**2,
                              (xx*yy).ravel(), xx.ravel(), yy.ravel(), np.ones(9)))
    a, b, cross, gx, gy, _ = np.linalg.lstsq(design, local.ravel(), rcond=None)[0]
    hessian = np.array([[2*a, cross], [cross, 2*b]])
    if np.max(np.linalg.eigvalsh(hessian)) >= 0.:
        return 0., 0., "integer"
    offset = -np.linalg.solve(hessian, [gx, gy])
    if not np.all(np.isfinite(offset)) or np.max(np.abs(offset)) > .5:
        return 0., 0., "integer"
    # Numerical noise from an exactly symmetric peak is not resolved movement.
    offset[np.abs(offset) < 1e-12] = 0.
    return float(offset[0]), float(offset[1]), "quadratic"


def _patch(reference, target, centre, support, radius, direction, policy, deadline):
    check_deadline(deadline)
    row, col = centre
    diag = dict(direction=direction, center_column=col, center_row=row,
                status="unavailable", reason_codes=[], support_fraction=None, ncc=None,
                competing_peak_margin=None, dx_cells=None, dy_cells=None, refinement=None)

    def failed(reason):
        return PatchDiagnostics(**{**diag, "reason_codes": [reason]})

    half, extent = policy.template_size_cells//2, math.floor(radius)
    if min(row-half, col-half) < 0 or row+half >= reference.grid.height or col+half >= reference.grid.width:
        return failed("insufficient_template_support")
    crop = np.s_[row-half:row+half+1, col-half:col+half+1]
    mask = support[crop]
    count = int(mask.sum())
    diag["support_fraction"] = float(mask.mean())
    if count < policy.min_template_support_samples or diag["support_fraction"] < policy.min_template_support_fraction:
        return failed("insufficient_template_support")
    template = np.where(mask, reference.descriptor[crop], 0.)
    template = np.where(mask, template-template.sum()/count, 0.)
    energy = float(np.sum(template**2))
    if energy <= 1e-12:
        return failed("low_texture")
    # Padding affects only pixels excluded by the fixed support mask, never NCC.
    padded = np.pad(np.where(target.known & np.isfinite(target.descriptor), target.descriptor, 0.), extent+half)
    window = padded[row:row+2*(extent+half)+1, col:col+2*(extent+half)+1]
    numerator = signal.correlate2d(window, template, mode="valid")
    total = signal.correlate2d(window, mask.astype(float), mode="valid")
    squares = signal.correlate2d(window**2, mask.astype(float), mode="valid")
    denominator = np.sqrt(energy*np.maximum(0., squares-total**2/count))
    scores = np.full(numerator.shape, -np.inf)
    yy, xx = np.mgrid[-extent:extent+1, -extent:extent+1]
    searched = xx**2+yy**2 <= radius**2
    good = searched & (denominator > 1e-12)
    np.divide(numerator, denominator, out=scores, where=good)
    scores[good] = np.clip(scores[good], -1., 1.)
    if not np.any(np.isfinite(scores)):
        return failed("low_texture")
    peak = np.unravel_index(np.argmax(scores), scores.shape)
    py, px = int(peak[0]), int(peak[1])
    diag["ncc"] = float(scores[peak])
    # A discrete boundary pixel has a neighbour outside the searched disk.
    if py in (0, 2*extent) or px in (0, 2*extent) or not np.all(searched[py-1:py+2, px-1:px+2]):
        return failed("search_boundary")
    if diag["ncc"] < policy.min_ncc:
        return failed("low_ncc")
    competitor = (xx-xx[peak])**2+(yy-yy[peak])**2 > policy.competing_peak_neighborhood_cells**2
    other = scores[competitor & np.isfinite(scores)]
    if not len(other):
        return failed("competing_peak_not_evaluated")
    diag["competing_peak_margin"] = float(scores[peak]-other.max())
    if diag["competing_peak_margin"] < policy.min_competing_peak_margin:
        return failed("ambiguous_peak")
    dx, dy, refinement = _refine(scores, py, px)
    # NCC already at its Cauchy--Schwarz upper bound cannot be improved by
    # displacing it toward the maximum of an asymmetric approximate quadratic.
    # This absolute float64 roundoff guard is numerical, not a speed threshold.
    if math.isclose(diag["ncc"], 1., rel_tol=0., abs_tol=1e-12):
        dx, dy, refinement = 0., 0., "integer"
    diag.update(dx_cells=float(px-extent+dx), dy_cells=float(py-extent+dy), refinement=refinement)
    if math.hypot(diag["dx_cells"], diag["dy_cells"]) >= radius:
        return failed("search_boundary")
    return PatchDiagnostics(**{**diag, "status": "available"})


def _overlaps(mask, labels, dx, dy, deadline):
    """Exact translated-cell intersection counts (continuous fractional areas).

    A translated square overlaps at most four target squares. Weighted bincounts
    include every labeled competitor without materializing omitted polygons.
    """
    rows, cols = np.nonzero(mask)
    ix, iy = math.floor(dx), math.floor(dy)
    fx, fy = dx-ix, dy-iy
    counts = np.zeros(int(labels.max())+1)
    for ox, wx in ((ix, 1.-fx), (ix+1, fx)):
        for oy, wy in ((iy, 1.-fy), (iy+1, fy)):
            check_deadline(deadline)
            if wx*wy == 0.:
                continue
            r, c = rows+oy, cols+ox
            inside = (r >= 0) & (c >= 0) & (r < labels.shape[0]) & (c < labels.shape[1])
            counts += np.bincount(labels[r[inside], c[inside]], minlength=len(counts))*wx*wy
    counts[0] = 0.
    return counts


def _plausible(overlaps, own_size, other_sizes, policy):
    return np.flatnonzero((other_sizes > 0) &
                          (overlaps >= policy.lineage_ambiguity_overlap_fraction*np.minimum(own_size, other_sizes)))


def _pair(earlier, later, label, policy, deadline, support0, support1):
    first, last = earlier.frame, later.frame
    dt = float((last.reference_at-first.reference_at).total_seconds())
    diag = dict(from_frame_id=first.frame_id, to_frame_id=last.frame_id, elapsed_seconds=dt,
                status="unavailable", reason_codes=[], patches=[], forward_dx_cells=None,
                forward_dy_cells=None, patch_disagreement_cells=None, reverse_residual_cells=None,
                next_observation_residual_cells=None, common_support_iou=None, area_ratio=None,
                plausible_parent_count=None, plausible_child_count=None, lineage_complete=False)
    children = []

    def finish(reason=None):
        diag["reason_codes"] = [reason] if reason else []
        diag["status"] = "unavailable" if reason else "available"
        return children, PairDiagnostics(**diag)

    radius = policy.max_search_speed_mps*dt/first.grid.cell_size_m
    forward_support = _fixed_support(first, last, radius)
    centres = _centres(first, earlier.labels == label, forward_support, policy)
    if len(centres) != 2:
        known_values = first.descriptor[first.known & np.isfinite(first.descriptor)]
        reason = "low_texture" if len(known_values) and np.ptp(known_values) <= 1e-12 else "insufficient_usable_patches"
        return finish(reason)
    for centre in centres:
        patch = _patch(first, last, centre, forward_support, radius, "forward", policy, deadline)
        diag["patches"].append(patch)
        if patch.status != "available":
            return finish(patch.reason_codes[0])
    forward = np.array([(p.dx_cells, p.dy_cells) for p in diag["patches"]])
    d = forward.mean(axis=0)
    diag.update(forward_dx_cells=float(d[0]), forward_dy_cells=float(d[1]),
                patch_disagreement_cells=float(np.linalg.norm(forward[1]-forward[0])))
    if diag["patch_disagreement_cells"] > math.sqrt(2):
        return finish("patch_disagreement")

    # Lineage precedes the final match and considers the entire labeled fields.
    child_overlap = _overlaps(earlier.labels == label, later.labels, *d, deadline)
    children = [int(i) for i in _plausible(child_overlap, earlier.sizes[label], later.sizes, policy)]
    diag["plausible_child_count"] = len(children)
    parents = []
    for child in children:
        overlap = _overlaps(later.labels == child, earlier.labels, *(-d), deadline)
        parents.append(_plausible(overlap, later.sizes[child], earlier.sizes, policy))
    diag["plausible_parent_count"] = max((len(p) for p in parents), default=0)
    diag["lineage_complete"] = True
    if len(children) != 1 or len(parents[0]) != 1 or parents[0][0] != label:
        return finish("lineage_ambiguous" if children else "no_match")
    child = children[0]
    reverse_support = _fixed_support(last, first, radius)
    reverse = []
    for row, col in centres:
        centre = (int(round(row+d[1])), int(round(col+d[0])))
        patch = _patch(last, first, centre, reverse_support, radius, "reverse", policy, deadline)
        diag["patches"].append(patch)
        if patch.status != "available":
            return finish(patch.reason_codes[0])
        reverse.append((patch.dx_cells, patch.dy_cells))
    if np.linalg.norm(np.subtract(reverse[1], reverse[0])) > math.sqrt(2):
        return finish("patch_disagreement")
    diag["reverse_residual_cells"] = float(np.linalg.norm(d+np.mean(reverse, axis=0)))
    if diag["reverse_residual_cells"] > policy.max_reverse_error_cell_diagonals*math.sqrt(2):
        return finish("reverse_inconsistent")
    check_deadline(deadline)
    cell = first.grid.cell_size_m
    a = translate(earlier.geometry(label), xoff=d[0]*cell, yoff=d[1]*cell)
    b = later.geometry(child)
    common = translate(support0, xoff=d[0]*cell, yoff=d[1]*cell).intersection(support1)
    for feature in (a, b):
        rim = feature.buffer(cell, join_style="mitre")
        if not first.grid.domain.covers(rim):
            return finish("domain_clipped")
        if not common.covers(rim):
            return finish("coverage_clipped")
    a, b = a.intersection(common), b.intersection(common)
    diag["common_support_iou"] = float(a.intersection(b).area/a.union(b).area)
    diag["area_ratio"] = float(b.area/a.area)
    if diag["common_support_iou"] < policy.min_common_support_iou:
        return finish("insufficient_overlap")
    if not policy.min_common_support_area_ratio <= diag["area_ratio"] <= policy.max_common_support_area_ratio:
        return finish("area_change")
    return finish()


def _observed_track(observed, label, policy):
    f = observed.frame
    shape = observed.geometry(label)
    reason = "small_feature" if observed.sizes[label] < policy.min_track_cells else "insufficient_history"
    return Track(f"{f.source_id}:{f.frame_id}:{label}", f.source_id, f.reference_at, shape,
                 [TrackSample(f.frame_id, f.reference_at, shape)], reason_codes=(reason,))


def _resolve_pairs(candidates, nearby_failures):
    """Resolve all proposed parents before accepting any reciprocal match.

    A forward-supported lineage claim remains a competitor when reverse/support
    checks subsequently fail. A merely nearby failed search is not such a claim.
    """
    claims = {}
    for label, children, diag in candidates:
        for child in children:
            claims.setdefault(child, []).append((label, diag))
    matched, failures = {}, dict(nearby_failures)
    for child, parents in claims.items():
        label, diag = parents[0]
        if len(parents) > 1:
            failures[child] = diag.model_copy(update={
                "status": "unavailable", "reason_codes": ["lineage_ambiguous"],
                "plausible_parent_count": max(len(parents), diag.plausible_parent_count or 0),
            })
        elif diag.status == "available":
            matched[child] = (label, diag)
            failures.pop(child, None)
        else:
            failures[child] = diag
    return matched, failures


def _fit_track(track, label, fields, pairs, failures, policy):
    current, samples, diagnostics = label, [track.history[-1]], []
    for index in range(len(fields)-2, -1, -1):
        if current not in pairs[index]:
            if current in failures[index]:
                diagnostics.insert(0, failures[index][current])
            break
        parent, diag = pairs[index][current]
        diagnostics.insert(0, diag)
        previous = fields[index]
        samples.insert(0, TrackSample(previous.frame.frame_id, previous.frame.reference_at, previous.geometry(parent)))
        current = parent
    track.history, track.pair_diagnostics = samples, diagnostics
    accepted = [p for p in diagnostics if p.status == "available"]
    # A failure before an otherwise clean three-frame newest suffix is not a
    # member of the used chain. A failed newest pair can never be skipped.
    if len(samples) >= policy.min_primary_valid_times:
        diagnostics = accepted
        displacements = np.array([(p.forward_dx_cells, p.forward_dy_cells) for p in diagnostics])
        elapsed = np.array([p.elapsed_seconds for p in diagnostics])
        for index in range(len(displacements)-1):
            residual = float(np.linalg.norm(displacements[index+1]-displacements[index]*elapsed[index+1]/elapsed[index]))
            diagnostics[index] = diagnostics[index].model_copy(update={"next_observation_residual_cells": residual})
            if residual > policy.max_next_observation_residual_cell_diagonals*math.sqrt(2):
                diagnostics[index] = diagnostics[index].model_copy(update={"status": "unavailable", "reason_codes": ["next_observation_inconsistent"]})
                track.reason_codes = ("next_observation_inconsistent",)
                track.pair_diagnostics = diagnostics
                return
        t = np.r_[0., np.cumsum(elapsed)]
        xy = np.vstack((np.zeros(2), np.cumsum(displacements, axis=0)))
        design = np.column_stack((t, np.ones(len(t))))
        fit = np.linalg.lstsq(design, xy, rcond=None)[0]
        vector = fit[0]*fields[-1].frame.grid.cell_size_m
        track.velocity_xy_m_s = (float(vector[0]), float(vector[1]))
        track.fit_rms_residual_cells = float(np.sqrt(np.mean(np.sum((xy-design@fit)**2, axis=1))))
        track.reason_codes = ()
        track.pair_diagnostics = diagnostics
    elif diagnostics:
        track.reason_codes = tuple(dict.fromkeys(reason for p in diagnostics for reason in p.reason_codes)) or ("insufficient_history",)


def _source(frames, route, policy, deadline):
    newest = frames[-1]
    count = TrackingCount(newest.source_id, newest.frame_id)
    tracks, reasons = [], []
    try:
        latest = _label(newest, policy, deadline)
        count.full_field_detections = len(latest.sizes)-1
        count.small_detections = int(np.sum((latest.sizes > 0) & (latest.sizes < policy.min_track_cells)))
        count.eligible_candidates = count.full_field_detections-count.small_detections
        count.counts_complete = True
        emitted = _rank(latest, route, policy, deadline)
        count.selected_candidates = len(latest.selected)
        for label in emitted:
            check_deadline(deadline)
            tracks.append(_observed_track(latest, label, policy))
        count.selection_complete = True
        if len(tracks) < count.full_field_detections:
            reasons.append("selection_limit")
        if len(frames) < policy.min_primary_valid_times:
            reasons.append("insufficient_history")
            return tracks, count, reasons
        if any(f.grid != newest.grid for f in frames):
            raise ValueError("incompatible_grid")
        gaps = [(b.reference_at-a.reference_at).total_seconds() for a, b in zip(frames, frames[1:])]
        if any(dt <= 0 for dt in gaps):
            raise ValueError("invalid_time")
        max_gap = (policy.max_dbzh_adjacent_gap_minutes if newest.source_id == "opera_dbzh" else policy.max_ctth_adjacent_gap_minutes)*60
        if max(gaps) > max_gap or sum(gaps) > policy.max_history_span_minutes*60:
            raise ValueError("history_gap")
        fields = []
        for frame in frames[:-1]:
            observed = _label(frame, policy, deadline)
            _rank(observed, route, policy, deadline)
            fields.append(observed)
        fields.append(latest)
        pairs, failures = [], []
        for earlier, later in zip(fields, fields[1:]):
            check_deadline(deadline)
            support0, support1 = footprint(earlier.frame.known, newest.grid), footprint(later.frame.known, newest.grid)
            candidates, nearby_failures = [], {}
            for label in earlier.selected:
                check_deadline(deadline)
                children, diag = _pair(earlier, later, label, policy, deadline, support0, support1)
                candidates.append((label, children, diag))
                if diag.status != "available" and not children:
                    # Without a usable displacement we cannot assert a parent,
                    # but can retain failed-search diagnostics on bounded nearby
                    # observations. This never creates a match/lineage claim.
                    radius_m = policy.max_search_speed_mps*diag.elapsed_seconds
                    parent_shape = earlier.geometry(label)
                    for child in later.selected:
                        check_deadline(deadline)
                        if parent_shape.distance(later.geometry(child)) <= radius_m:
                            nearby_failures.setdefault(child, diag)
            matched, failed = _resolve_pairs(candidates, nearby_failures)
            pairs.append(matched)
            failures.append(failed)
        for label, track in zip(emitted, tracks):
            if latest.sizes[label] >= policy.min_track_cells:
                _fit_track(track, label, fields, pairs, failures, policy)
    except ValueError as exc:
        reason = str(exc)
        if reason not in ("compute_deadline", "region_too_large", "incompatible_grid", "invalid_time", "history_gap"):
            raise
        reasons.append(reason)
        if reason == "compute_deadline":
            reasons.append("lineage_not_evaluated")
        for track in tracks:
            track.velocity_xy_m_s = None
            track.reason_codes = tuple(dict.fromkeys((*track.reason_codes, *reasons)))
    finally:
        count.emitted_observed_features = len(tracks)
        count.omitted_observed_features = (None if count.full_field_detections is None
                                           else count.full_field_detections-len(tracks))
        count.reason_codes = tuple(reasons)
    return tracks, count, reasons


def track_history(frames: Sequence[AnalysisFrame], *, route_geometry: BaseGeometry,
                  policy=DEFAULT_POLICY, deadline=None) -> TrackingResult:
    """Track each source independently, retaining bounded newest observations.

    Input is chronological selected history; no bad middle frame is skipped.
    ``deadline`` is an absolute monotonic timestamp (default: now + policy's
    cooperative budget), checked between bounded stages/patches, not a wall-time
    interrupt. Source inventory is known from supplied frames even on timeout.
    """
    if deadline is None:
        deadline = time.monotonic()+policy.compute_budget_seconds
    if route_geometry.is_empty or not route_geometry.is_valid:
        raise ValueError("invalid_route")
    groups = {}
    for frame in frames:
        groups.setdefault(frame.source_id, []).append(frame)
    tracks, counts, reasons = [], [], []
    for source in sorted(groups):
        selected = groups[source][-policy.max_primary_frames_per_source:]
        found, count, source_reasons = _source(selected, route_geometry, policy, deadline)
        tracks.extend(found)
        counts.append(count)
        reasons.extend(source_reasons)
    return TrackingResult(tracks, tuple(dict.fromkeys(reasons)), tuple(counts))


__all__ = ["Track", "TrackSample", "TrackingCount", "TrackingResult", "track_history"]
