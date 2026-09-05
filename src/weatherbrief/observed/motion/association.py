"""Timed radar/cloud/lightning associations for observed-motion features."""
from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Sequence
from datetime import datetime, timezone

from shapely.affinity import translate
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from weatherbrief.models.observed_motion import (
    AssociationRecord,
    FeatureLightningEvidence,
    Interval,
    LightningRecord,
)
from weatherbrief.observed.motion.history import AnalysisFrame, LightningInput, check_deadline
from weatherbrief.observed.motion.policy import DEFAULT_POLICY, MotionPolicy

NM_M = 1852.0


@dataclass(frozen=True)
class AssociationContext:
    lightning_frames: Sequence[LightningInput] = ()
    policy: MotionPolicy = DEFAULT_POLICY
    deadline: float | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("invalid_time")
    return value.astimezone(timezone.utc)


def _history_times(track) -> list[datetime]:
    return [_utc(sample.reference_at) for sample in track.history]


def _track_shape_at(track, at: datetime) -> BaseGeometry | None:
    at = _utc(at)
    samples = sorted(track.history, key=lambda sample: sample.reference_at)
    for sample in samples:
        if _utc(sample.reference_at) == at:
            return sample.footprint
    if track.velocity_xy_m_s is None or not samples:
        return None
    times = [_utc(sample.reference_at) for sample in samples]
    if at < times[0] or at > times[-1]:
        return None
    newest = samples[-1]
    seconds = (at - _utc(newest.reference_at)).total_seconds()
    vx, vy = track.velocity_xy_m_s
    return translate(newest.footprint, xoff=vx * seconds, yoff=vy * seconds)


def _frame_records(track, at: datetime) -> tuple[list[str], Interval | None, str | None]:
    at = _utc(at)
    samples = sorted(track.history, key=lambda sample: sample.reference_at)
    exact = [sample for sample in samples if _utc(sample.reference_at) == at]
    if exact:
        return [exact[0].frame_id], Interval(start_at=at, end_at=at), "simultaneous_observed"
    for left, right in zip(samples, samples[1:]):
        start, end = _utc(left.reference_at), _utc(right.reference_at)
        if start <= at <= end:
            return [left.frame_id, right.frame_id], Interval(start_at=start, end_at=end), "in_history_translation"
    return [], None, None


def _source_geolocation(frames_by_source: dict[str, tuple[AnalysisFrame, ...]], source_id: str):
    frames = frames_by_source.get(source_id, ())
    return frames[-1].geolocation if frames else None


def _unavailable_association(index: int, radar, cloud, reasons: list[str]) -> AssociationRecord:
    return AssociationRecord(
        association_id=f"association-{index}",
        radar_feature_id=radar.feature_id,
        cloud_feature_id=cloud.feature_id,
        status="unavailable",
        reason_codes=list(dict.fromkeys(reasons)) or ["not_evaluated"],
        relation=None,
        comparison_at=None,
        alignment_method=None,
        radar_frame_ids=[],
        cloud_frame_ids=[],
        radar_window=None,
        cloud_window=None,
        intersection_area_km2=None,
        radar_overlap_fraction=None,
        cloud_overlap_fraction=None,
        edge_distance_nm=None,
        measurement_basis="analysis_grid_contours",
    )


def _association(index: int, radar, cloud, frames_by_source, policy: MotionPolicy, deadline: float | None) -> AssociationRecord:
    reasons: list[str] = []
    for source_id in (radar.source_id, cloud.source_id):
        geolocation = _source_geolocation(frames_by_source, source_id)
        if geolocation is None:
            reasons.append("missing_source")
        elif geolocation.status != "validated":
            reasons.extend(geolocation.reason_codes or ["geolocation_unverified"])
    try:
        radar_times = _history_times(radar)
        cloud_times = _history_times(cloud)
    except ValueError as exc:
        return _unavailable_association(index, radar, cloud, [str(exc)])
    if not radar_times or not cloud_times:
        reasons.append("insufficient_history")
        return _unavailable_association(index, radar, cloud, reasons)
    comparison_at = min(radar_times[-1], cloud_times[-1])
    if comparison_at < max(radar_times[0], cloud_times[0]):
        reasons.append("no_common_history")
    radar_shape = _track_shape_at(radar, comparison_at) if not reasons else None
    cloud_shape = _track_shape_at(cloud, comparison_at) if not reasons else None
    radar_ids, radar_window, radar_method = _frame_records(radar, comparison_at)
    cloud_ids, cloud_window, cloud_method = _frame_records(cloud, comparison_at)
    if not reasons and (radar_shape is None or cloud_shape is None or radar_window is None or cloud_window is None):
        reasons.append("outside_observed_history")
    if reasons:
        return _unavailable_association(index, radar, cloud, reasons)
    check_deadline(deadline)
    intersection = radar_shape.intersection(cloud_shape)
    area_km2 = float(intersection.area / 1_000_000.0)
    distance_nm = float(radar_shape.distance(cloud_shape) / NM_M)
    relation = "overlap" if area_km2 > 0.0 else "nearby"
    if relation == "nearby" and distance_nm > policy.route_capture_corridor_nm:
        return _unavailable_association(index, radar, cloud, ["no_spatial_association"])
    method = "simultaneous_observed" if radar_method == cloud_method == "simultaneous_observed" else "in_history_translation"
    radar_area = max(float(radar_shape.area), 0.0)
    cloud_area = max(float(cloud_shape.area), 0.0)
    return AssociationRecord(
        association_id=f"association-{index}",
        radar_feature_id=radar.feature_id,
        cloud_feature_id=cloud.feature_id,
        status="available",
        reason_codes=[],
        relation=relation,
        comparison_at=comparison_at,
        alignment_method=method,
        radar_frame_ids=radar_ids,
        cloud_frame_ids=cloud_ids,
        radar_window=radar_window,
        cloud_window=cloud_window,
        intersection_area_km2=area_km2,
        radar_overlap_fraction=area_km2 * 1_000_000.0 / radar_area if radar_area else 0.0,
        cloud_overlap_fraction=area_km2 * 1_000_000.0 / cloud_area if cloud_area else 0.0,
        edge_distance_nm=0.0 if relation == "overlap" else distance_nm,
        measurement_basis="analysis_grid_contours",
    )


def _point_feature_ids(lon: float, lat: float, at: datetime, tracks, frames_by_source, grid, deadline: float | None) -> list[str]:
    x, y = grid.project(lon, lat)
    if not math.isfinite(float(x)) or not math.isfinite(float(y)):
        return []
    point = Point(float(x), float(y))
    result: list[str] = []
    for track in tracks:
        check_deadline(deadline)
        geolocation = _source_geolocation(frames_by_source, track.source_id)
        if geolocation is None or geolocation.status != "validated":
            continue
        shape = _track_shape_at(track, at)
        if shape is not None and shape.covers(point):
            result.append(track.feature_id)
    return result


def _feature_window(tracks, frame: LightningInput) -> Interval:
    starts = [_utc(sample.reference_at) for track in tracks for sample in track.history]
    source_start = frame.source_record.acquisition_window.start_at
    source_end = frame.source_record.acquisition_window.end_at
    if not starts:
        return Interval(start_at=source_start, end_at=source_end)
    return Interval(start_at=max(min(starts), source_start), end_at=min(max(starts), source_end))


def _lightning(tracks, frames: Sequence[LightningInput], frames_by_source, grid, policy: MotionPolicy, deadline: float | None):
    records: list[LightningRecord] = []
    reported: dict[str, int] = {}
    frame_ids: dict[str, set[str]] = {}
    source_id: dict[str, str] = {}
    windows: dict[str, Interval] = {}
    truncated = False
    for frame_input in frames:
        frame = frame_input.frame
        window = frame_input.source_record.acquisition_window
        count = len(frame.lons)
        for index in range(count):
            check_deadline(deadline)
            precision = frame.time_precision[index] if index < len(frame.time_precision) else "window_only"
            reasons = list(frame.time_reason_codes[index] if index < len(frame.time_reason_codes) else ("window_only_time",))
            event_at = frame.event_times[index] if index < len(frame.event_times) else None
            associated: list[str] = []
            assoc_reasons = reasons[:]
            if precision == "individual_time" and event_at is not None:
                event_at = _utc(event_at)
                associated = _point_feature_ids(
                    float(frame.lons[index]),
                    float(frame.lats[index]),
                    event_at,
                    tracks,
                    frames_by_source,
                    grid,
                    deadline,
                )
                assoc_reasons = [] if associated else ["no_spatial_association"]
            else:
                event_at = None
                assoc_reasons = reasons or ["window_only_time"]
            for feature_id in associated:
                reported[feature_id] = reported.get(feature_id, 0) + 1
                frame_ids.setdefault(feature_id, set()).add(frame_input.source_record.frame_id)
                source_id[feature_id] = "eumetsat_li"
                windows[feature_id] = _feature_window([track for track in tracks if track.feature_id == feature_id], frame_input)
            if len(records) >= policy.max_lightning_records:
                truncated = True
                continue
            records.append(
                LightningRecord(
                    detection_id=f"{frame_input.source_record.frame_id}:{frame.sample_ids[index] if index < len(frame.sample_ids) else index}",
                    source_id="eumetsat_li",
                    frame_id=frame_input.source_record.frame_id,
                    position=(float(frame.lons[index]), float(frame.lats[index])),
                    time_precision=precision,
                    event_at=event_at,
                    acquisition_window=window,
                    reason_codes=reasons,
                    association_status="available" if associated else "unavailable",
                    association_reason_codes=[] if associated else assoc_reasons,
                    associated_feature_ids=associated or None,
                )
            )
    emitted = {feature_id: 0 for feature_id in reported}
    for record in records:
        if record.associated_feature_ids:
            for feature_id in record.associated_feature_ids:
                emitted[feature_id] = emitted.get(feature_id, 0) + 1
    evidence = {
        feature_id: FeatureLightningEvidence(
            status="available",
            reason_codes=["lightning_marker_limit"] if truncated and emitted.get(feature_id, 0) < count else [],
            source_id=source_id.get(feature_id, "eumetsat_li"),
            frame_ids=sorted(frame_ids.get(feature_id, ())),
            evaluated_window=windows.get(feature_id),
            reported_detection_count=count,
            emitted_marker_count=emitted.get(feature_id, 0),
            evaluation_complete=not (truncated and emitted.get(feature_id, 0) < count),
        )
        for feature_id, count in reported.items()
    }
    return records, evidence


def associate_tracks(tracks, frames_by_source, grid, context: AssociationContext):
    """Return cross-source associations, capped lightning records and summaries."""
    policy = context.policy
    radar = [track for track in tracks if track.source_id == "opera_dbzh"]
    cloud = [track for track in tracks if track.source_id == "eumetsat_ctth"]
    associations: list[AssociationRecord] = []
    for r in radar:
        for c in cloud:
            if len(associations) >= policy.max_associations:
                break
            associations.append(_association(len(associations) + 1, r, c, frames_by_source, policy, context.deadline))
    lightning, evidence = _lightning(list(tracks), context.lightning_frames, frames_by_source, grid, policy, context.deadline)
    return associations, lightning, evidence


__all__ = ["AssociationContext", "associate_tracks"]
