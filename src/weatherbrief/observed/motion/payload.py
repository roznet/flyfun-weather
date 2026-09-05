"""Build the bounded observed-motion envelope from local retained frames."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import time
from threading import Lock
from uuid import uuid4

import numpy as np
from shapely.affinity import translate
from shapely.geometry import Point, box
from pydantic import ValidationError

from weatherbrief.models import RouteConfig
from weatherbrief.models.observed_motion import (
    COMPLETENESS_CATEGORIES,
    CompletenessRecord,
    ContourDefinition,
    FeatureLightningEvidence,
    FeatureRecord,
    GeometryRecord,
    Interval,
    METHOD_VERSION,
    MotionRecord,
    ObservedMotion,
    PlannedOverlapResult,
    ProjectionRecord,
    ScalarObservation,
    SupportRecord,
    TrailSample,
    empty_motion,
)
from weatherbrief.observed.collect import observed_enabled
from weatherbrief.observed.frames import FrameStore
from weatherbrief.observed.motion.association import AssociationContext, associate_tracks
from weatherbrief.observed.motion.geometry import display_geometry
from weatherbrief.observed.motion.history import AnalysisFrame, HistoryResult, check_deadline, load_history
from weatherbrief.observed.motion.policy import DEFAULT_POLICY, MotionPolicy
from weatherbrief.observed.motion.route import (
    build_route_geometry,
    ground_velocity,
    route_identities,
    route_relationships,
)
from weatherbrief.observed.motion.tracking import TrackingCount, track_history

_ADMISSION_LOCK = Lock()


def _enabled_value(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes")


def motion_enabled() -> bool:
    """Shared serve-time capability gate; performs no source analysis."""
    import os

    return observed_enabled() and _enabled_value(os.environ.get("WB_OBSERVED_MOTION_ENABLED"))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("invalid_time")
    return value.astimezone(timezone.utc)


def _completeness(category: str, considered, emitted: int, omitted, reasons=()) -> CompletenessRecord:
    status = "complete" if not reasons else "partial"
    return CompletenessRecord(
        category=category,
        status=status,
        reason_codes=[] if status == "complete" else list(dict.fromkeys(reasons)),
        considered_count=considered,
        emitted_count=emitted,
        omitted_count=omitted,
    )


def _sum_counts(values):
    if any(value is None for value in values):
        return None
    return sum(values)


def _coverage(frame: AnalysisFrame, shape, scope: str = "feature_contour") -> SupportRecord:
    x, y = frame.grid.centres()
    mask = np.vectorize(lambda xx, yy: shape.covers(Point(float(xx), float(yy))))(x, y)
    total = int(mask.sum())
    if total == 0:
        return SupportRecord(
            status="unavailable",
            reason_codes=["unknown_support"],
            scope=scope,
            known_cells=None,
            total_cells=None,
            known_fraction=None,
        )
    known = int((frame.known & mask).sum())
    return SupportRecord(
        status="available" if known else "unavailable",
        reason_codes=[] if known else ["unknown_support"],
        scope=scope,
        known_cells=known,
        total_cells=total,
        known_fraction=known / total if known else None,
    )


def _unavailable_support(scope: str = "feature_contour") -> SupportRecord:
    return SupportRecord(
        status="unavailable",
        reason_codes=["unknown_support"],
        scope=scope,
        known_cells=None,
        total_cells=None,
        known_fraction=None,
    )


def _blank_lightning() -> FeatureLightningEvidence:
    return FeatureLightningEvidence(
        status="unavailable",
        reason_codes=["missing_source"],
        source_id=None,
        frame_ids=[],
        evaluated_window=None,
        reported_detection_count=None,
        emitted_marker_count=0,
        evaluation_complete=False,
    )


def _empty_planned(reason: str) -> PlannedOverlapResult:
    return PlannedOverlapResult(
        status="unavailable",
        reason_codes=[reason],
        method="relative_segment_contour_intersection",
        planned_time_method="distance_proportional_planned",
        evaluated_interval=None,
        intervals=[],
        complete=False,
    )


def _sample_mask(frame: AnalysisFrame, shape):
    x, y = frame.grid.centres()
    return np.vectorize(lambda xx, yy: shape.covers(Point(float(xx), float(yy))))(x, y)


def _scalar(
    kind: str,
    frame: AnalysisFrame | None,
    shape,
    comparison_at: datetime,
    *,
    temperature=False,
    alignment_method: str | None = None,
) -> ScalarObservation:
    if alignment_method is None:
        alignment_method = "observed" if frame is not None and frame.reference_at == comparison_at else "in_history_translation"
    units = {"reflectivity_max": "dBZ", "rain_rate_max": "mm_h", "cloud_top_max": "m_msl"}
    if frame is None:
        return ScalarObservation(
            kind=kind,
            status="unavailable",
            reason_codes=["missing_source"],
            value=None,
            unit=units[kind],
            source_id=None,
            frame_id=None,
            observed_at=None,
            comparison_at=None,
            acquisition_window=None,
            alignment_method=None,
            sample_id=None,
            sample_position=None,
            paired_temperature_k=None,
            coverage=_unavailable_support(),
        )
    mask = _sample_mask(frame, shape) & frame.known & frame.detected & np.isfinite(frame.values)
    if not np.any(mask):
        return ScalarObservation(
            kind=kind,
            status="unavailable",
            reason_codes=["no_positive_sample"],
            value=None,
            unit=units[kind],
            source_id=frame.source_id,
            frame_id=frame.frame_id,
            observed_at=frame.reference_at,
            comparison_at=comparison_at,
            acquisition_window=frame.source_record.acquisition_window,
            alignment_method=None,
            sample_id=None,
            sample_position=None,
            paired_temperature_k=None,
            coverage=_coverage(frame, shape),
        )
    masked_values = np.where(mask, frame.values, -np.inf)
    row, col = np.unravel_index(np.argmax(masked_values), masked_values.shape)
    sample_id = str(int(frame.sample_ids[row, col])) if frame.sample_ids is not None else f"{row}:{col}"
    sample_position = None
    if frame.sample_positions is not None and np.isfinite(frame.sample_positions[row, col]).all():
        sample_position = (float(frame.sample_positions[row, col, 0]), float(frame.sample_positions[row, col, 1]))
    paired_temp = None
    if temperature and frame.temperature_k is not None and np.isfinite(frame.temperature_k[row, col]):
        paired_temp = float(frame.temperature_k[row, col])
    return ScalarObservation(
        kind=kind,
        status="available",
        reason_codes=[],
        value=float(frame.values[row, col]),
        unit=units[kind],
        source_id=frame.source_id,
        frame_id=frame.frame_id,
        observed_at=frame.reference_at,
        comparison_at=comparison_at,
        acquisition_window=frame.source_record.acquisition_window,
        alignment_method=alignment_method,
        sample_id=sample_id,
        sample_position=sample_position,
        paired_temperature_k=paired_temp,
        coverage=_coverage(frame, shape),
    )


def _track_shape_at(track, at: datetime):
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
    for left, right in zip(samples, samples[1:]):
        if _utc(left.reference_at) <= at <= _utc(right.reference_at):
            seconds = (at - _utc(left.reference_at)).total_seconds()
            vx, vy = track.velocity_xy_m_s
            return translate(left.footprint, xoff=vx * seconds, yoff=vy * seconds)
    return None


def _rate_scalar(history: HistoryResult, track) -> ScalarObservation:
    first = _utc(track.history[0].reference_at) if track.history else _utc(track.reference_at)
    eligible = [
        frame
        for frame in history.rate_frames
        if first <= _utc(frame.reference_at) <= _utc(track.reference_at)
    ]
    frame = eligible[-1] if eligible else None
    if frame is None:
        return _scalar("rain_rate_max", None, track.footprint, track.reference_at)
    shape = _track_shape_at(track, frame.reference_at)
    if shape is None:
        return _scalar("rain_rate_max", None, track.footprint, frame.reference_at)
    alignment = "observed" if _utc(frame.reference_at) == _utc(track.reference_at) else "in_history_translation"
    return _scalar("rain_rate_max", frame, shape, frame.reference_at, alignment_method=alignment)


def _definition(source_id: str) -> ContourDefinition:
    if source_id == "eumetsat_ctth":
        return ContourDefinition(
            quantity="geometric_cloud_top_height",
            operator="gte",
            threshold=DEFAULT_POLICY.cloud_threshold_m_msl,
            unit="m_msl",
        )
    return ContourDefinition(
        quantity="reflectivity",
        operator="gte",
        threshold=DEFAULT_POLICY.radar_threshold_dbz,
        unit="dBZ",
    )


def _motion(track, frame: AnalysisFrame, grid, cutoff_at: datetime, policy: MotionPolicy) -> MotionRecord:
    reasons = list(track.reason_codes)
    if frame.geolocation.status != "validated":
        reasons.extend(frame.geolocation.reason_codes or ["geolocation_unverified"])
    if _utc(cutoff_at) - _utc(track.reference_at) > timedelta(minutes=policy.max_reference_age_minutes):
        reasons.append("stale_reference")
    if track.velocity_xy_m_s is None:
        reasons.extend(track.reason_codes or ["insufficient_history"])
    if reasons:
        return MotionRecord(
            status="unavailable",
            reason_codes=list(dict.fromkeys(reasons)),
            ground_speed_kt=None,
            bearing_deg_true=None,
            velocity_reference_point=None,
            velocity_method=None,
            pair_diagnostics=track.pair_diagnostics,
            fit_rms_residual_cells=None,
        )
    speed, bearing, point = ground_velocity(track, grid)
    return MotionRecord(
        status="accepted",
        reason_codes=[],
        ground_speed_kt=speed,
        bearing_deg_true=bearing,
        velocity_reference_point=point,
        velocity_method="inverse_aeqd_geodesic_1s",
        pair_diagnostics=track.pair_diagnostics,
        fit_rms_residual_cells=track.fit_rms_residual_cells,
    )


def _projection_times(cutoff_at: datetime, policy: MotionPolicy) -> list[datetime]:
    first = cutoff_at.replace(second=0, microsecond=0)
    if first <= cutoff_at:
        first += timedelta(minutes=policy.projection_tick_minutes)
    while first.minute % policy.projection_tick_minutes:
        first += timedelta(minutes=1)
    return [first + timedelta(minutes=policy.projection_tick_minutes * index) for index in range(policy.max_projection_times)]


def _geometry_unavailable(reason: str) -> GeometryRecord:
    return GeometryRecord(
        status="unavailable",
        reason_codes=[reason],
        geometry=None,
        provenance="grid_contour",
        simplification_tolerance_m=0.0,
    )


def _supported_geometry(shape, frame: AnalysisFrame, grid, policy: MotionPolicy) -> GeometryRecord:
    rim = grid.cell_size_m
    x0, y0, x1, y1 = shape.bounds
    rim_shape = box(x0 - rim, y0 - rim, x1 + rim, y1 + rim)
    if not grid.domain.covers(rim_shape):
        return _geometry_unavailable("outside_analysis_domain")
    support = _coverage(frame, rim_shape)
    if support.status != "available" or not math.isclose(support.known_fraction or 0.0, 1.0, abs_tol=1e-12):
        return _geometry_unavailable("unknown_support")
    return display_geometry(shape, grid, policy)


def _project(track, frame: AnalysisFrame, at: datetime, grid, policy: MotionPolicy) -> GeometryRecord:
    if track.velocity_xy_m_s is None:
        return _geometry_unavailable("insufficient_history")
    seconds = (at - track.reference_at).total_seconds()
    vx, vy = track.velocity_xy_m_s
    return _supported_geometry(translate(track.footprint, xoff=vx * seconds, yoff=vy * seconds), frame, grid, policy)


def _feature(track, history: HistoryResult, grid, route: RouteConfig, departure_time, cutoff_at, projection_times, deadline, policy):
    source_frames = history.frames_by_source.get(track.source_id, ())
    if not source_frames:
        return None
    frame_by_id = {frame.frame_id: frame for frame in source_frames}
    frame = frame_by_id.get(track.history[-1].frame_id, source_frames[-1])
    family = "high_cloud_top" if track.source_id == "eumetsat_ctth" else "radar_echo"
    display = _supported_geometry(track.footprint, frame, grid, policy)
    coverage = _coverage(frame, track.footprint)
    motion = _motion(track, frame, grid, cutoff_at, policy)
    projection_end_at = track.reference_at + timedelta(minutes=policy.projection_horizon_minutes) if motion.status == "accepted" else None
    projections = []
    for at in projection_times:
        geom = _project(track, frame, at, grid, policy) if motion.status == "accepted" and at <= projection_end_at else _geometry_unavailable("no_future_lead")
        projections.append(
            ProjectionRecord(
                at=at,
                status=geom.status,
                reason_codes=geom.reason_codes,
                display_geometry=geom,
            )
        )
    observations = []
    if track.source_id == "opera_dbzh":
        observations.append(_scalar("reflectivity_max", frame, track.footprint, track.reference_at))
        observations.append(_rate_scalar(history, track))
    else:
        observations.append(_scalar("cloud_top_max", frame, track.footprint, track.reference_at, temperature=True))
    route_rows = []
    planned_overlap = _empty_planned("not_evaluated")
    if motion.status == "accepted":
        applicable_projection_times = [at for at in projection_times if at <= projection_end_at]
        route_rows, planned_overlap = route_relationships(
            track,
            route,
            grid,
            departure_time,
            cutoff_at,
            applicable_projection_times,
            policy=policy,
            deadline=deadline,
        )
    return FeatureRecord(
        feature_id=track.feature_id,
        source_id=track.source_id,
        family=family,
        definition=_definition(track.source_id),
        reference_at=track.reference_at,
        reference_frame_id=frame.frame_id,
        frame_ids=[sample.frame_id for sample in track.history],
        display_geometry=display,
        trail=[
            TrailSample(
                frame_id=sample.frame_id,
                observed_at=sample.reference_at,
                center=tuple(map(float, grid.inverse(sample.footprint.centroid.x, sample.footprint.centroid.y))),
            )
            for sample in track.history[-policy.max_trail_samples_per_feature :]
        ],
        observations=observations,
        lightning_evidence=_blank_lightning(),
        coverage=coverage,
        geolocation=frame.geolocation,
        motion=motion,
        projection_end_at=projection_end_at,
        projections=projections,
        route_rows=route_rows,
        planned_overlap=planned_overlap,
        reason_codes=list(track.reason_codes),
    )


def _cap_features(features, policy: MotionPolicy):
    radar = [feature for feature in features if feature.family == "radar_echo"]
    cloud = [feature for feature in features if feature.family == "high_cloud_top"]
    selected_ids: set[str] = set()
    selected = []
    for family_features in (radar, cloud):
        for feature in family_features[: policy.initial_features_per_family]:
            selected.append(feature)
            selected_ids.add(feature.feature_id)
    for feature in features:
        if len(selected) >= policy.max_features:
            break
        if feature.feature_id not in selected_ids:
            selected.append(feature)
            selected_ids.add(feature.feature_id)
    return selected[: policy.max_features]


def _trim_projection_times(features, projection_times):
    accepted = [feature for feature in features if feature.motion.status == "accepted"]
    expires_at = max((feature.projection_end_at for feature in accepted), default=None)
    retained_times = [at for at in projection_times if expires_at is not None and at <= expires_at]
    retained_time_set = set(retained_times)
    trimmed_features = [
        feature.model_copy(
            update={
                "projections": [projection for projection in feature.projections if projection.at in retained_time_set],
                "route_rows": [
                    row
                    for row in feature.route_rows
                    if row.at == feature.reference_at or row.at in retained_time_set
                ],
            }
        )
        for feature in features
    ]
    return retained_times, trimmed_features


def _apply_evidence(features, evidence):
    return [
        feature.model_copy(update={"lightning_evidence": evidence.get(feature.feature_id, feature.lightning_evidence)})
        for feature in features
    ]


def _geometry_positions(record: GeometryRecord) -> int:
    return record.geometry.position_count if record.geometry is not None else 0


def _limited_geometry() -> GeometryRecord:
    return GeometryRecord(
        status="unavailable",
        reason_codes=["geometry_limit"],
        geometry=None,
        provenance="grid_contour",
        simplification_tolerance_m=0.0,
    )


def _cap_geometry_total(features, policy: MotionPolicy):
    used = 0
    output = []
    for feature in features:
        display = feature.display_geometry
        display_positions = _geometry_positions(display)
        if used + display_positions <= policy.max_total_geometry_positions:
            used += display_positions
        else:
            display = _limited_geometry()
        projections = []
        for projection in feature.projections:
            geom = projection.display_geometry
            positions = _geometry_positions(geom)
            if used + positions <= policy.max_total_geometry_positions:
                used += positions
            else:
                geom = _limited_geometry()
            projections.append(
                projection.model_copy(
                    update={
                        "status": geom.status,
                        "reason_codes": geom.reason_codes,
                        "display_geometry": geom,
                    }
                )
            )
        output.append(feature.model_copy(update={"display_geometry": display, "projections": projections}))
    return output


def _counts(
    history: HistoryResult,
    tracking_counts: tuple[TrackingCount, ...],
    features,
    associations,
    lightning,
    projection_times,
    route_leg_count: int,
    association_considered: int,
):
    input_considered = _sum_counts([count.considered_count for count in history.input_counts])
    input_omitted = _sum_counts([count.omitted_count for count in history.input_counts])
    track_reasons = [reason for count in tracking_counts for reason in count.reason_codes]
    feature_considered = _sum_counts([count.full_field_detections for count in tracking_counts])
    tracking_emitted = sum(count.emitted_observed_features for count in tracking_counts)
    feature_reasons = [*track_reasons]
    if len(features) < tracking_emitted:
        feature_reasons.append("feature_limit")
    feature_omitted = None if feature_considered is None else feature_considered - len(features)
    selected = _sum_counts([count.selected_candidates for count in tracking_counts])
    eligible = _sum_counts([count.eligible_candidates for count in tracking_counts])
    small = _sum_counts([count.small_detections for count in tracking_counts])
    lightning_reported = _lightning_reported_count(history)
    lightning_reasons = _lightning_reasons(history, lightning_reported, len(lightning))
    lightning_omitted = None if lightning_reported is None else max(0, lightning_reported - len(lightning))
    route_rows = sum(len(feature.route_rows) for feature in features)
    overlap_intervals = sum(len(feature.planned_overlap.intervals) for feature in features)
    planned_refusal_reasons = [
        reason
        for feature in features
        if feature.motion.status == "accepted" and feature.planned_overlap.status == "unavailable"
        for reason in feature.planned_overlap.reason_codes
    ]
    route_row_refusal_reasons = [
        reason for reason in planned_refusal_reasons if reason == "selection_limit"
    ]
    overlap_refusal_reasons = [
        reason
        for reason in planned_refusal_reasons
        if reason in ("selection_limit", "overlap_interval_limit")
    ]
    route_rows_considered = None if route_row_refusal_reasons else route_rows
    route_rows_omitted = None if route_row_refusal_reasons else 0
    overlap_intervals_considered = None if overlap_refusal_reasons else overlap_intervals
    overlap_intervals_omitted = None if overlap_refusal_reasons else 0
    geometry_considered = len(features) * (1 + len(projection_times))
    geometry_emitted = sum(
        1
        for feature in features
        for geom in [feature.display_geometry, *(p.display_geometry for p in feature.projections)]
        if geom.status == "available"
    )
    geometry_omitted = geometry_considered - geometry_emitted
    geometry_reasons = [
        reason
        for feature in features
        for geom in [feature.display_geometry, *(p.display_geometry for p in feature.projections)]
        if geom.status != "available"
        for reason in geom.reason_codes
    ]
    return [
        _completeness("regions", 1 if history.grid is not None else None, 1 if history.grid is not None else 0, 0 if history.grid is not None else None, history.reason_codes if history.grid is None else ()),
        _completeness("input_frames", input_considered, sum(count.emitted_count for count in history.input_counts), input_omitted, [reason for count in history.input_counts for reason in count.reason_codes]),
        _completeness("small_detections", small, small or 0, 0 if small is not None else None, track_reasons),
        _completeness("candidates", eligible, selected or 0, None if eligible is None or selected is None else eligible - selected, track_reasons),
        _completeness("features", feature_considered, len(features), feature_omitted, feature_reasons),
        _completeness("geometry", geometry_considered, geometry_emitted, geometry_omitted, geometry_reasons),
        _completeness(
            "associations",
            association_considered,
            len(associations),
            max(0, association_considered - len(associations)),
            [
                *(["association_limit"] if association_considered > len(associations) else []),
                *[
                    reason
                    for item in associations
                    if item.status != "available"
                    for reason in item.reason_codes
                ],
            ],
        ),
        _completeness("lightning", lightning_reported, len(lightning), lightning_omitted, lightning_reasons),
        _completeness("legs", route_leg_count, route_leg_count, 0, ()),
        _completeness("route_rows", route_rows_considered, route_rows, route_rows_omitted, route_row_refusal_reasons),
        _completeness(
            "overlap_intervals",
            overlap_intervals_considered,
            overlap_intervals,
            overlap_intervals_omitted,
            overlap_refusal_reasons,
        ),
    ]


def _lightning_reported_count(history: HistoryResult) -> int | None:
    if not history.lightning_frames:
        return None
    return sum(len(frame_input.frame.lons) for frame_input in history.lightning_frames)


def _lightning_reasons(history: HistoryResult, reported: int | None, emitted: int) -> list[str]:
    reasons: list[str] = []
    if reported is None:
        for source in history.sources:
            if source.source_id == "eumetsat_li" and source.status == "unavailable":
                reasons.extend(source.reason_codes)
        return list(dict.fromkeys(reasons or ["missing_source"]))
    if emitted < reported:
        reasons.append("lightning_marker_limit")
    return reasons


def _association_considered(tracks) -> int:
    radar = sum(1 for track in tracks if track.source_id == "opera_dbzh")
    cloud = sum(1 for track in tracks if track.source_id == "eumetsat_ctth")
    return radar * cloud


def _enforce_aggregate_limits(features, policy: MotionPolicy) -> None:
    if sum(len(feature.route_rows) for feature in features) > policy.max_route_rows:
        raise ValueError("payload_limit")
    if sum(len(feature.planned_overlap.intervals) for feature in features) > policy.max_overlap_intervals:
        raise ValueError("payload_limit")


def _failure_reason(exc: BaseException) -> str:
    text = str(exc)
    if "compute_deadline" in text:
        return "compute_deadline"
    if "payload_limit" in text or "payload limit exceeded" in text:
        return "payload_limit"
    for code in (
        "region_too_large",
        "incompatible_grid",
        "invalid_time",
        "invalid_route",
        "history_gap",
        "frame_changed",
        "source_window_limit",
        "unsupported_grid_spacing",
    ):
        if code in text:
            return code
    return "compute_failed"


def _empty(route: RouteConfig, departure_time, cutoff_at, revision: int, status: str, reasons: list[str]) -> ObservedMotion:
    route_geometry_id, planned_timing_id = route_identities(route, departure_time)
    return empty_motion(
        route_geometry_id=route_geometry_id,
        planned_timing_id=planned_timing_id,
        cutoff_at=cutoff_at,
        revision=revision,
        status=status,
        reason_codes=reasons,
    )


def build_observed_motion(
    route: RouteConfig,
    *,
    departure_time: datetime | None,
    cutoff_at: datetime,
    revision: int,
    store: FrameStore | None = None,
) -> ObservedMotion:
    """Assemble a validated experimental motion envelope from local storage."""
    cutoff_at = _utc(cutoff_at)
    if not observed_enabled():
        return _empty(route, departure_time, cutoff_at, revision, "disabled", ["observed_disabled"])
    if not motion_enabled():
        return _empty(route, departure_time, cutoff_at, revision, "disabled", ["feature_disabled"])
    policy = DEFAULT_POLICY
    if not _ADMISSION_LOCK.acquire(blocking=False):
        return _empty(route, departure_time, cutoff_at, revision, "unavailable", ["busy"])
    deadline = time.monotonic() + policy.compute_budget_seconds
    route_geometry_id, planned_timing_id = route_identities(route, departure_time)
    try:
        store = store or FrameStore()
        history = load_history(store, route, cutoff_at, policy, deadline=deadline)
        if history.grid is None:
            return _empty(route, departure_time, cutoff_at, revision, "unavailable", list(history.reason_codes) or ["missing_source"])
        route_geometry = build_route_geometry(route, history.grid, policy=policy, deadline=deadline)
        primary = [frame for frames in history.frames_by_source.values() for frame in frames]
        tracked = track_history(primary, route_geometry=route_geometry, policy=policy, deadline=deadline)
        projection_times = _projection_times(cutoff_at, policy)
        features = [
            feature
            for track in tracked.tracks
            if (feature := _feature(track, history, history.grid, route, departure_time, cutoff_at, projection_times, deadline, policy)) is not None
        ]
        features = _cap_features(features, policy)
        projection_times, features = _trim_projection_times(features, projection_times)
        features = _cap_geometry_total(features, policy)
        retained_feature_ids = {feature.feature_id for feature in features}
        retained_tracks = [track for track in tracked.tracks if track.feature_id in retained_feature_ids]
        associations, lightning, evidence = associate_tracks(
            retained_tracks,
            history.frames_by_source,
            history.grid,
            AssociationContext(lightning_frames=history.lightning_frames, policy=policy, deadline=deadline),
        )
        features = _apply_evidence(features, evidence)
        _enforce_aggregate_limits(features, policy)
        accepted = [feature for feature in features if feature.motion.status == "accepted"]
        status = "available" if accepted else "unavailable"
        if status == "unavailable":
            projection_times = []
            features = [
                feature.model_copy(update={
                    "projections": [],
                    "route_rows": [],
                    "planned_overlap": _empty_planned("not_evaluated"),
                })
                for feature in features
            ]
        if status == "unavailable":
            feature_reasons = [
                reason
                for feature in features
                for reason in feature.motion.reason_codes
            ]
            reason_codes = list(dict.fromkeys((*history.reason_codes, *tracked.reason_codes, *feature_reasons))) or ["insufficient_history"]
        else:
            reason_codes = []
        motion = ObservedMotion(
            schema_version=1,
            status=status,
            reason_codes=reason_codes,
            revision=revision,
            run_id=f"observed-motion-{uuid4().hex}" if status == "available" else None,
            route_geometry_id=route_geometry_id,
            planned_timing_id=planned_timing_id,
            computed_at=datetime.now(timezone.utc),
            cutoff_at=cutoff_at,
            expires_at=max((feature.projection_end_at for feature in accepted), default=None),
            method_version=METHOD_VERSION,
            policy_version=policy.policy_version,
            analysis_domain=history.grid.to_record(),
            sources=list(history.sources),
            features=features,
            associations=associations if status == "available" else [],
            lightning=lightning,
            projection_times=projection_times if status == "available" else [],
            completeness=_counts(
                history,
                tracked.counts,
                features,
                associations if status == "available" else [],
                lightning,
                projection_times,
                len(route.waypoints) - 1,
                _association_considered(retained_tracks) if status == "available" else 0,
            ),
        )
        check_deadline(deadline)
        if len(motion.model_dump_json().encode("utf-8")) > policy.max_serialized_bytes:
            raise ValueError("payload_limit")
        return ObservedMotion.model_validate(motion.model_dump(mode="python"))
    except (ValueError, RuntimeError, OSError, TimeoutError, ValidationError) as exc:
        return _empty(route, departure_time, cutoff_at, revision, "unavailable", [_failure_reason(exc)])
    finally:
        _ADMISSION_LOCK.release()


__all__ = ["build_observed_motion", "motion_enabled"]
