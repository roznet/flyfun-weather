"""Observed-motion payload assembly validates gates, scalars and caps."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
from threading import Event
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from shapely.geometry import box

from weatherbrief.models import RouteConfig, Waypoint
from weatherbrief.models.observed_motion import (
    GeolocationRecord,
    GeometryRecord,
    Interval,
    ObservedMotion,
    PairDiagnostics,
    PatchDiagnostics,
    PlannedOverlapResult,
    RouteRow,
)
from weatherbrief.observed.motion.geometry import AnalysisGrid
from weatherbrief.observed.motion.history import AnalysisFrame, HistoryResult, InputCount
from tests.observed.test_motion_association import _geo, _lightning, _record


T0 = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(("cutoff", "expected"), [
    ("12:00:00", "12:05:00"),
    ("12:00:01", "12:05:00"),
    ("12:01:17", "12:05:00"),
    ("12:06:00", "12:10:00"),
    ("12:11:59", "12:15:00"),
    ("12:58:32", "13:00:00"),
])
def test_projection_uses_strictly_next_absolute_tick(cutoff, expected):
    from weatherbrief.observed.motion.payload import _projection_times
    from weatherbrief.observed.motion.policy import DEFAULT_POLICY

    times = _projection_times(datetime.fromisoformat(f"2026-09-05T{cutoff}+00:00"), DEFAULT_POLICY)
    assert times[0] == datetime.fromisoformat(f"2026-09-05T{expected}+00:00")
    if cutoff == "12:11:59":
        assert [t.strftime("%H:%M") for t in times if t <= T0 + timedelta(minutes=15)] == ["12:15"]

GRID = AnalysisGrid(
    "+proj=aeqd +lat_0=50 +lon_0=0 +datum=WGS84 +units=m",
    (0.0, 50.0),
    0.0,
    0.0,
    80,
    80,
    2_000.0,
)


def route() -> RouteConfig:
    lon0, lat0 = GRID.inverse(0.0, 20_000.0)
    lon1, lat1 = GRID.inverse(120_000.0, 20_000.0)
    return RouteConfig(
        name="literal",
        flight_duration_hours=1.0,
        waypoints=[
            Waypoint(icao="A", name="A", lon=lon0, lat=lat0),
            Waypoint(icao="B", name="B", lon=lon1, lat=lat1),
        ],
    )


def _analysis(source: str, at: datetime, *, values: float, geolocation: GeolocationRecord | None = None) -> AnalysisFrame:
    known = np.ones(GRID.shape, dtype=bool)
    detected = np.zeros(GRID.shape, dtype=bool)
    descriptor = np.zeros(GRID.shape)
    field_values = np.full(GRID.shape, np.nan)
    frame = AnalysisFrame(
        source,
        f"{source}-{at:%H%M}",
        at,
        GRID,
        descriptor,
        known,
        detected,
        field_values,
        None,
        _record(source, at),
        geolocation or _geo(),
    )
    frame.detected[10:18, 10:18] = True
    frame.descriptor[10:18, 10:18] = 1.0
    frame.values[10:18, 10:18] = values
    frame.sample_ids = np.arange(GRID.width * GRID.height).reshape(GRID.shape)
    lon, lat = GRID.inverse(*GRID.centres())
    frame.sample_positions = np.dstack([lon, lat])
    if source == "eumetsat_ctth":
        frame.temperature_k = np.full(GRID.shape, np.nan)
        frame.temperature_k[10:18, 10:18] = 225.0
    return frame


def _history(*, cloud_geo: GeolocationRecord | None = None, lightning_count: int = 0) -> HistoryResult:
    times = [T0 - timedelta(minutes=10), T0 - timedelta(minutes=5), T0]
    radar = tuple(_analysis("opera_dbzh", at, values=42.0) for at in times)
    cloud = tuple(_analysis("eumetsat_ctth", at, values=9_000.0, geolocation=cloud_geo or _geo("unverified")) for at in times)
    rate = (_analysis("opera_rate", T0, values=7.5),)
    flashes = (_payload_lightning(lightning_count),) if lightning_count else ()
    sources = tuple(frame.source_record for group in (radar, cloud, rate) for frame in group[-1:])
    return HistoryResult(
        GRID,
        {"opera_dbzh": radar, "eumetsat_ctth": cloud},
        (
            _source("opera_dbzh", radar, _geo()),
            _source("eumetsat_ctth", cloud, cloud_geo or _geo("unverified")),
            _source("opera_rate", rate, _geo()),
            _source("eumetsat_li", tuple(input.source_record for input in flashes), _geo("unverified"), point=True),
        ),
        (),
        rate_frames=rate,
        lightning_frames=flashes,
        input_counts=(
            InputCount("opera_dbzh", 3, 3, 3, 3, 0, True),
            InputCount("eumetsat_ctth", 3, 3, 3, 3, 0, True),
            InputCount("opera_rate", 1, 1, 1, 1, 0, True),
            InputCount("eumetsat_li", 1 if lightning_count else None, 1 if lightning_count else 0, 1 if lightning_count else 0, 1 if lightning_count else 0, 0 if lightning_count else None, True),
        ),
    )


def _history_with_times(
    radar_times: list[datetime],
    *,
    cloud_geo: GeolocationRecord | None = None,
    rate_frames: tuple[AnalysisFrame, ...] | None = None,
) -> HistoryResult:
    radar = tuple(_analysis("opera_dbzh", at, values=42.0) for at in radar_times)
    cloud_times = [T0 - timedelta(minutes=10), T0 - timedelta(minutes=5), T0]
    cloud = tuple(_analysis("eumetsat_ctth", at, values=9_000.0, geolocation=cloud_geo or _geo("unverified")) for at in cloud_times)
    rate = rate_frames if rate_frames is not None else (_analysis("opera_rate", radar_times[-1], values=7.5),)
    return HistoryResult(
        GRID,
        {"opera_dbzh": radar, "eumetsat_ctth": cloud},
        (
            _source("opera_dbzh", radar, _geo()),
            _source("eumetsat_ctth", cloud, cloud_geo or _geo("unverified")),
            _source("opera_rate", rate, _geo()),
        ),
        (),
        rate_frames=rate,
        lightning_frames=(),
        input_counts=(
            InputCount("opera_dbzh", len(radar), len(radar), len(radar), len(radar), 0, True),
            InputCount("eumetsat_ctth", len(cloud), len(cloud), len(cloud), len(cloud), 0, True),
            InputCount("opera_rate", len(rate), len(rate), len(rate), len(rate), 0, True),
        ),
    )


def _payload_lightning(count: int):
    frame_input = _lightning(count)
    lon, lat = GRID.inverse(np.full(count, 21_000.0), np.full(count, 21_000.0))
    frame_input.frame.lons = np.asarray(lon)
    frame_input.frame.lats = np.asarray(lat)
    return frame_input


def _source(source: str, frames, geolocation: GeolocationRecord, *, point: bool = False):
    from weatherbrief.models.observed_motion import SourceRecord, SupportRecord

    records = [item.source_record if isinstance(item, AnalysisFrame) else item for item in frames]
    scope = "point_detections" if point else "analysis_domain"
    return SourceRecord(
        source_id=source,
        status="available" if records else "unavailable",
        reason_codes=[] if records else ["missing_source"],
        frames=records[:4],
        gaps=[],
        attribution="synthetic",
        coverage=SupportRecord(
            status="unavailable" if point else "available",
            reason_codes=["point_coverage_unknown"] if point else [],
            scope=scope,
            known_cells=None if point else 100,
            total_cells=None if point else 100,
            known_fraction=None if point else 1.0,
        ),
        geolocation=geolocation,
    )


def _install_payload_inputs(monkeypatch, history: HistoryResult) -> None:
    from weatherbrief.observed.motion import payload

    def fake_history(*args, **kwargs):
        return history

    monkeypatch.setattr(payload, "load_history", fake_history)
    route_geometry = box(0, 0, 160_000, 160_000)

    def fake_route_geometry(*args, **kwargs):
        return route_geometry

    monkeypatch.setattr(payload, "build_route_geometry", fake_route_geometry)
    monkeypatch.setattr(payload, "track_history", lambda *args, **kwargs: _tracking(history))


@dataclass
class _Sample:
    frame_id: str
    reference_at: datetime
    footprint: object


@dataclass
class _Track:
    feature_id: str
    source_id: str
    reference_at: datetime
    footprint: object
    history: list[_Sample]
    velocity_xy_m_s: tuple[float, float] | None
    reason_codes: tuple[str, ...] = ()
    pair_diagnostics: list[PairDiagnostics] = field(default_factory=list)
    fit_rms_residual_cells: float | None = 0.0


def _patch(direction: str, index: int) -> PatchDiagnostics:
    return PatchDiagnostics(
        direction=direction,
        center_column=20 + 4 * index,
        center_row=20 + 4 * index,
        status="available",
        reason_codes=[],
        support_fraction=1.0,
        ncc=0.9,
        competing_peak_margin=0.2,
        dx_cells=0.0,
        dy_cells=0.0,
        refinement="integer",
    )


def _pair(left: AnalysisFrame, right: AnalysisFrame, *, final: bool) -> PairDiagnostics:
    return PairDiagnostics(
        from_frame_id=left.frame_id,
        to_frame_id=right.frame_id,
        elapsed_seconds=(right.reference_at - left.reference_at).total_seconds(),
        status="available",
        reason_codes=[],
        patches=[_patch("forward", 0), _patch("forward", 1), _patch("reverse", 0), _patch("reverse", 1)],
        forward_dx_cells=0.0,
        forward_dy_cells=0.0,
        patch_disagreement_cells=0.0,
        reverse_residual_cells=0.0,
        next_observation_residual_cells=None if final else 0.0,
        common_support_iou=1.0,
        area_ratio=1.0,
        plausible_parent_count=1,
        plausible_child_count=1,
        lineage_complete=True,
    )


def _tracking(history: HistoryResult):
    from weatherbrief.observed.motion.tracking import TrackingCount, TrackingResult

    tracks = []
    for source_id, frames in history.frames_by_source.items():
        samples = [_Sample(frame.frame_id, frame.reference_at, box(20_000, 20_000, 36_000, 36_000)) for frame in frames]
        diagnostics = [_pair(frames[0], frames[1], final=False), _pair(frames[1], frames[2], final=True)]
        tracks.append(_Track(
            f"{source_id}-feature-1",
            source_id,
            frames[-1].reference_at,
            samples[-1].footprint,
            samples,
            (0.0, 0.0),
            pair_diagnostics=diagnostics,
        ))
    counts = tuple(
        TrackingCount(source_id, frames[-1].frame_id, 1, 0, 1, 1, 1, 0, True, True)
        for source_id, frames in history.frames_by_source.items()
    )
    return TrackingResult(tracks, (), counts)


def _tracking_many(history: HistoryResult):
    from weatherbrief.observed.motion.tracking import TrackingCount, TrackingResult

    tracks = []
    for source_id, frames in history.frames_by_source.items():
        for index in range(30):
            offset = index * 3_000
            samples = [
                _Sample(frame.frame_id, frame.reference_at, box(20_000 + offset, 20_000, 22_000 + offset, 22_000))
                for frame in frames
            ]
            tracks.append(_Track(
                f"{source_id}-feature-{index:02d}",
                source_id,
                frames[-1].reference_at,
                samples[-1].footprint,
                samples,
                (0.0, 0.0),
                pair_diagnostics=[_pair(frames[0], frames[1], final=False), _pair(frames[1], frames[2], final=True)],
            ))
    counts = tuple(
        TrackingCount(source_id, frames[-1].frame_id, 30, 0, 30, 30, 30, 0, True, True)
        for source_id, frames in history.frames_by_source.items()
    )
    return TrackingResult(tracks, (), counts)


def _tracking_uneven(history: HistoryResult):
    from weatherbrief.observed.motion.tracking import TrackingCount, TrackingResult

    tracks = []
    counts = []
    for source_id, frames in history.frames_by_source.items():
        total = 30 if source_id == "opera_dbzh" else 1
        for index in range(total):
            offset = index * 3_000
            samples = [
                _Sample(frame.frame_id, frame.reference_at, box(20_000 + offset, 20_000, 22_000 + offset, 22_000))
                for frame in frames
            ]
            tracks.append(_Track(
                f"{source_id}-feature-{index:02d}",
                source_id,
                frames[-1].reference_at,
                samples[-1].footprint,
                samples,
                (0.0, 0.0),
                pair_diagnostics=[_pair(frames[0], frames[1], final=False), _pair(frames[1], frames[2], final=True)],
            ))
        counts.append(TrackingCount(source_id, frames[-1].frame_id, total, 0, total, total, total, 0, True, True))
    return TrackingResult(tracks, (), tuple(counts))


def test_motion_enabled_requires_both_observed_and_motion_gates(monkeypatch) -> None:
    from weatherbrief.observed.motion.payload import motion_enabled

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "true")
    monkeypatch.delenv("WB_OBSERVED_MOTION_ENABLED", raising=False)
    assert motion_enabled() is False
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "yes")
    assert motion_enabled() is True


def test_disabled_gate_returns_explicit_empty_envelope_without_source_reads(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "true")
    monkeypatch.delenv("WB_OBSERVED_MOTION_ENABLED", raising=False)
    monkeypatch.setattr(payload, "load_history", lambda *args, **kwargs: pytest.fail("disabled motion read sources"))

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=7)

    assert motion.status == "disabled"
    assert motion.reason_codes == ["feature_disabled"]
    assert motion.sources == []
    assert {record.status for record in motion.completeness} == {"not_evaluated"}


def test_payload_uses_real_source_scalars_route_helpers_and_ctth_gate(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "1")
    _install_payload_inputs(monkeypatch, _history(cloud_geo=_geo("unverified")))

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=8)
    raw = motion.model_dump(mode="json")
    reparsed = ObservedMotion.model_validate(raw)

    assert reparsed.status == "available"
    by_source = {feature.source_id: feature for feature in reparsed.features}
    radar = by_source["opera_dbzh"]
    cloud = by_source["eumetsat_ctth"]
    assert radar.motion.status == "accepted"
    assert radar.motion.velocity_method == "inverse_aeqd_geodesic_1s"
    assert {observation.kind: observation.value for observation in radar.observations} == {
        "reflectivity_max": 42.0,
        "rain_rate_max": 7.5,
    }
    assert cloud.motion.status == "unavailable"
    assert "geolocation_unverified" in cloud.motion.reason_codes
    top = next(observation for observation in cloud.observations if observation.kind == "cloud_top_max")
    assert top.value == 9000.0
    assert top.paired_temperature_k == 225.0
    assert len(reparsed.associations) == 1
    assert reparsed.associations[0].status == "unavailable"
    assert reparsed.associations[0].reason_codes == ["geolocation_unverified"]
    assert radar.route_rows
    assert radar.planned_overlap.method == "relative_segment_contour_intersection"
    legs = next(record for record in reparsed.completeness if record.category == "legs")
    assert legs.status == "complete"
    assert legs.considered_count == 1
    assert legs.emitted_count == 1


def test_positive_lightning_summary_is_retained_when_marker_details_are_capped(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload
    from weatherbrief.observed.motion.policy import DEFAULT_POLICY

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    _install_payload_inputs(monkeypatch, _history(lightning_count=DEFAULT_POLICY.max_lightning_records + 5))

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=9)

    assert len(motion.lightning) == DEFAULT_POLICY.max_lightning_records
    assert all(record.associated_feature_ids == ["opera_dbzh-feature-1"] for record in motion.lightning)
    radar = next(feature for feature in motion.features if feature.source_id == "opera_dbzh")
    assert radar.lightning_evidence.reported_detection_count == DEFAULT_POLICY.max_lightning_records + 5
    assert radar.lightning_evidence.emitted_marker_count == DEFAULT_POLICY.max_lightning_records
    assert "lightning_marker_limit" in radar.lightning_evidence.reason_codes
    lightning_completeness = next(record for record in motion.completeness if record.category == "lightning")
    assert lightning_completeness.status == "partial"


def test_feature_cap_omits_details_without_serializing_dropped_references(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload
    from weatherbrief.observed.motion.policy import DEFAULT_POLICY

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    history = _history(cloud_geo=_geo(), lightning_count=1)
    _install_payload_inputs(monkeypatch, history)
    monkeypatch.setattr(payload, "track_history", lambda *args, **kwargs: _tracking_many(history))

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=11)

    assert motion.status == "available"
    assert len(motion.features) == DEFAULT_POLICY.max_features
    retained_ids = {feature.feature_id for feature in motion.features}
    assert all(
        feature_id in retained_ids
        for record in motion.lightning
        for feature_id in (record.associated_feature_ids or [])
    )
    assert all(
        association.radar_feature_id in retained_ids and association.cloud_feature_id in retained_ids
        for association in motion.associations
    )
    features = next(record for record in motion.completeness if record.category == "features")
    assert features.status == "partial"
    assert features.considered_count == 60
    assert features.emitted_count == DEFAULT_POLICY.max_features
    assert features.omitted_count == 12


def test_geometry_cap_preserves_feature_card_and_trims_projection_details(monkeypatch) -> None:
    from dataclasses import replace
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    history = _history(cloud_geo=_geo())
    _install_payload_inputs(monkeypatch, history)
    monkeypatch.setattr(payload, "DEFAULT_POLICY", replace(payload.DEFAULT_POLICY, max_total_geometry_positions=20))

    ring = [
        (0.01 * math.cos(i * math.tau / 14), 50.0 + 0.01 * math.sin(i * math.tau / 14))
        for i in range(14)
    ]
    ring.append(ring[0])

    def large_geometry(*args, **kwargs):
        return GeometryRecord(
            status="available",
            reason_codes=[],
            geometry={"type": "MultiPolygon", "coordinates": [[ring]]},
            provenance="grid_contour",
            simplification_tolerance_m=0.0,
        )

    monkeypatch.setattr(payload, "display_geometry", large_geometry)

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=12)

    radar = next(feature for feature in motion.features if feature.source_id == "opera_dbzh")
    assert radar.display_geometry.status == "available"
    assert any(projection.display_geometry.reason_codes == ["geometry_limit"] for projection in radar.projections)
    geometry = next(record for record in motion.completeness if record.category == "geometry")
    assert geometry.status == "partial"


def test_stale_reference_withholds_motion_without_generic_replacement(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    history = _history_with_times([
        T0 - timedelta(minutes=31),
        T0 - timedelta(minutes=26),
        T0 - timedelta(minutes=21),
    ])
    _install_payload_inputs(monkeypatch, history)

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=13)

    assert motion.status == "unavailable"
    assert "stale_reference" in motion.reason_codes
    assert "runtime_error" not in motion.reason_codes
    assert motion.features
    assert motion.projection_times == []
    assert all(feature.motion.status == "unavailable" for feature in motion.features)


def test_advertised_projection_ticks_stop_at_max_accepted_feature_expiry(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    history = _history_with_times([
        T0 - timedelta(minutes=20),
        T0 - timedelta(minutes=15),
        T0 - timedelta(minutes=10),
    ])
    _install_payload_inputs(monkeypatch, history)

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=14)

    assert motion.status == "available"
    assert motion.expires_at == T0 + timedelta(minutes=5)
    assert motion.projection_times == [T0 + timedelta(minutes=5)]
    radar = next(feature for feature in motion.features if feature.source_id == "opera_dbzh")
    assert [projection.at for projection in radar.projections] == motion.projection_times


def test_projection_leaving_analysis_domain_is_unavailable(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload
    from weatherbrief.observed.motion.tracking import TrackingCount, TrackingResult

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    history = _history(cloud_geo=_geo("unverified"))
    _install_payload_inputs(monkeypatch, history)

    def edge_tracking(*args, **kwargs):
        frames = history.frames_by_source["opera_dbzh"]
        samples = [
            _Sample(frame.frame_id, frame.reference_at, box(150_000, 20_000, 156_000, 26_000))
            for frame in frames
        ]
        track = _Track(
            "opera_dbzh-edge",
            "opera_dbzh",
            frames[-1].reference_at,
            samples[-1].footprint,
            samples,
            (10.0, 0.0),
            pair_diagnostics=[_pair(frames[0], frames[1], final=False), _pair(frames[1], frames[2], final=True)],
        )
        count = TrackingCount("opera_dbzh", frames[-1].frame_id, 1, 0, 1, 1, 1, 0, True, True)
        return TrackingResult([track], (), (count,))

    monkeypatch.setattr(payload, "track_history", edge_tracking)

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=15)

    radar = motion.features[0]
    assert radar.projections[0].display_geometry.status == "unavailable"
    assert radar.projections[0].display_geometry.reason_codes == ["outside_analysis_domain"]


def test_rate_scalar_samples_radar_contour_at_rate_observation_time(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload
    from weatherbrief.observed.motion.tracking import TrackingCount, TrackingResult

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    rate = _analysis("opera_rate", T0 - timedelta(minutes=2), values=7.5)
    rate.detected[:] = False
    rate.values[:] = np.nan
    rate.detected[10:12, 17:19] = True
    rate.values[10:12, 17:19] = 7.5
    history = _history_with_times([T0 - timedelta(minutes=10), T0 - timedelta(minutes=5), T0], rate_frames=(rate,))
    _install_payload_inputs(monkeypatch, history)

    def moving_tracking(*args, **kwargs):
        frames = history.frames_by_source["opera_dbzh"]
        samples = [
            _Sample(frames[0].frame_id, frames[0].reference_at, box(10_000, 20_000, 14_000, 24_000)),
            _Sample(frames[1].frame_id, frames[1].reference_at, box(25_000, 20_000, 29_000, 24_000)),
            _Sample(frames[2].frame_id, frames[2].reference_at, box(40_000, 20_000, 44_000, 24_000)),
        ]
        track = _Track(
            "opera_dbzh-moving",
            "opera_dbzh",
            frames[-1].reference_at,
            samples[-1].footprint,
            samples,
            (50.0, 0.0),
            pair_diagnostics=[_pair(frames[0], frames[1], final=False), _pair(frames[1], frames[2], final=True)],
        )
        count = TrackingCount("opera_dbzh", frames[-1].frame_id, 1, 0, 1, 1, 1, 0, True, True)
        return TrackingResult([track], (), (count,))

    monkeypatch.setattr(payload, "track_history", moving_tracking)

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=16)

    radar = motion.features[0]
    rain = next(observation for observation in radar.observations if observation.kind == "rain_rate_max")
    assert rain.status == "available"
    assert rain.value == 7.5
    assert rain.observed_at == T0 - timedelta(minutes=2)
    assert rain.comparison_at == T0 - timedelta(minutes=2)
    assert rain.alignment_method == "in_history_translation"


def test_feature_cap_fills_unused_family_slots_from_other_family(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    history = _history(cloud_geo=_geo())
    _install_payload_inputs(monkeypatch, history)
    monkeypatch.setattr(payload, "track_history", lambda *args, **kwargs: _tracking_uneven(history))

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=17)

    assert len(motion.features) == 31
    assert sum(feature.family == "radar_echo" for feature in motion.features) == 30
    assert sum(feature.family == "high_cloud_top" for feature in motion.features) == 1


def test_concurrent_motion_build_returns_busy_without_second_analysis(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    history = _history(cloud_geo=_geo("unverified"))
    _install_payload_inputs(monkeypatch, history)
    entered = Event()
    release = Event()
    calls = 0

    def blocking_history(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(timeout=5)
        return history

    monkeypatch.setattr(payload, "load_history", blocking_history)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(payload.build_observed_motion, route(), departure_time=T0, cutoff_at=T0, revision=18)
        assert entered.wait(timeout=5)
        busy = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=19)
        release.set()
        first.result(timeout=5)

    assert busy.status == "unavailable"
    assert busy.reason_codes == ["busy"]
    assert calls == 1


def test_bounded_failure_codes_are_preserved(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    monkeypatch.setattr(payload, "load_history", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("compute_deadline")))

    deadline = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=20)

    assert deadline.status == "unavailable"
    assert deadline.reason_codes == ["compute_deadline"]

    history = _history(cloud_geo=_geo("unverified"))
    monkeypatch.setattr(payload, "load_history", lambda *args, **kwargs: history)
    monkeypatch.setattr(payload, "build_route_geometry", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid_route")))

    invalid_route = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=24)

    assert invalid_route.status == "unavailable"
    assert invalid_route.reason_codes == ["invalid_route"]

    monkeypatch.setattr(payload, "load_history", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    failed = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=21)

    assert failed.status == "unavailable"
    assert failed.reason_codes == ["compute_failed"]


def test_payload_limit_failure_is_reported_explicitly(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    history = _history(cloud_geo=_geo("unverified"))
    _install_payload_inputs(monkeypatch, history)

    def too_many_rows(*args, **kwargs):
        rows = [
            RouteRow(
                leg_id=f"leg-{index}",
                leg_index=0,
                from_label="A",
                to_label="B",
                at=T0,
                status="available",
                reason_codes=[],
                distance_nm=1.0,
                closure_kt=1.0,
                closure_interval=Interval(start_at=T0, end_at=T0 + timedelta(seconds=1)),
                relationship="approaching",
                planned_time_method="distance_proportional_planned",
                planned_time_status="unavailable",
                planned_time_reason_codes=["missing_departure_time"],
                planned_overlap_at_time=None,
            )
            for index in range(1_025)
        ]
        return rows, PlannedOverlapResult(
            status="available",
            reason_codes=[],
            method="relative_segment_contour_intersection",
            planned_time_method="distance_proportional_planned",
            evaluated_interval=Interval(start_at=T0, end_at=T0 + timedelta(minutes=15)),
            intervals=[],
            complete=True,
        )

    monkeypatch.setattr(payload, "route_relationships", too_many_rows)

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=22)

    assert motion.status == "unavailable"
    assert motion.reason_codes == ["payload_limit"]


def test_route_row_selection_limit_keeps_unknown_incomplete_completeness(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    history = _history(cloud_geo=_geo("unverified"))
    _install_payload_inputs(monkeypatch, history)
    monkeypatch.setattr(
        payload,
        "route_relationships",
        lambda *args, **kwargs: (
            [],
            PlannedOverlapResult(
                status="unavailable",
                reason_codes=["selection_limit"],
                method="relative_segment_contour_intersection",
                planned_time_method="distance_proportional_planned",
                evaluated_interval=None,
                intervals=[],
                complete=False,
            ),
        ),
    )

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=25)

    route_rows = next(record for record in motion.completeness if record.category == "route_rows")
    overlap_intervals = next(record for record in motion.completeness if record.category == "overlap_intervals")
    assert route_rows.status == "partial"
    assert route_rows.reason_codes == ["selection_limit"]
    assert route_rows.considered_count is None
    assert route_rows.emitted_count == 0
    assert route_rows.omitted_count is None
    assert overlap_intervals.status == "partial"
    assert overlap_intervals.reason_codes == ["selection_limit"]
    assert overlap_intervals.considered_count is None
    assert overlap_intervals.omitted_count is None


def test_overlap_interval_limit_keeps_route_rows_but_unknown_overlap_count(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    history = _history(cloud_geo=_geo("unverified"))
    _install_payload_inputs(monkeypatch, history)

    row = RouteRow(
        leg_id="leg-1",
        leg_index=0,
        from_label="A",
        to_label="B",
        at=T0,
        status="available",
        reason_codes=[],
        distance_nm=1.0,
        closure_kt=1.0,
        closure_interval=Interval(start_at=T0, end_at=T0 + timedelta(seconds=1)),
        relationship="approaching",
        planned_time_method="distance_proportional_planned",
        planned_time_status="unavailable",
        planned_time_reason_codes=["missing_departure_time"],
        planned_overlap_at_time=None,
    )
    monkeypatch.setattr(
        payload,
        "route_relationships",
        lambda *args, **kwargs: (
            [row],
            PlannedOverlapResult(
                status="unavailable",
                reason_codes=["overlap_interval_limit"],
                method="relative_segment_contour_intersection",
                planned_time_method="distance_proportional_planned",
                evaluated_interval=Interval(start_at=T0, end_at=T0 + timedelta(minutes=15)),
                intervals=[],
                complete=False,
            ),
        ),
    )

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=26)

    route_rows = next(record for record in motion.completeness if record.category == "route_rows")
    overlap_intervals = next(record for record in motion.completeness if record.category == "overlap_intervals")
    assert route_rows.status == "complete"
    assert route_rows.considered_count == 1
    assert route_rows.emitted_count == 1
    assert route_rows.omitted_count == 0
    assert overlap_intervals.status == "partial"
    assert overlap_intervals.reason_codes == ["overlap_interval_limit"]
    assert overlap_intervals.considered_count is None
    assert overlap_intervals.omitted_count is None


def test_lightning_completeness_keeps_unknown_count_without_precise_evaluation(monkeypatch) -> None:
    from weatherbrief.observed.motion import payload

    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_MOTION_ENABLED", "true")
    _install_payload_inputs(monkeypatch, _history(lightning_count=0))

    motion = payload.build_observed_motion(route(), departure_time=T0, cutoff_at=T0, revision=23)

    lightning = next(record for record in motion.completeness if record.category == "lightning")
    assert lightning.status == "partial"
    assert lightning.reason_codes == ["missing_source"]
    assert lightning.considered_count is None
    assert lightning.omitted_count is None
