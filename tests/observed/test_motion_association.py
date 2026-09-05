"""Cross-source links stay bounded, timed, and distinct from identities."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from shapely.geometry import box

from weatherbrief.models.observed_motion import (
    AssociationRecord,
    FrameRecord,
    GeolocationRecord,
    Interval,
    LightningRecord,
)
from weatherbrief.observed.frames import FlashFrame
from weatherbrief.observed.motion.geometry import AnalysisGrid
from weatherbrief.observed.motion.history import AnalysisFrame, LightningInput


T0 = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
GRID = AnalysisGrid(
    "+proj=aeqd +lat_0=50 +lon_0=0 +datum=WGS84 +units=m",
    (0.0, 50.0),
    0.0,
    0.0,
    40,
    40,
    2_000.0,
)


@dataclass
class Track:
    feature_id: str
    source_id: str
    reference_at: datetime
    footprint: object
    history: list
    velocity_xy_m_s: tuple[float, float] | None = (0.0, 0.0)
    reason_codes: tuple[str, ...] = ()
    pair_diagnostics: list = field(default_factory=list)
    fit_rms_residual_cells: float | None = 0.0


def _geo(status: str = "validated") -> GeolocationRecord:
    if status == "validated":
        return GeolocationRecord(
            status="validated",
            reason_codes=[],
            evidence_id="evidence",
            method_version="test_registration_v1",
            applicability_id="domain",
        )
    return GeolocationRecord(
        status="unverified",
        reason_codes=["geolocation_unverified"],
        evidence_id=None,
        method_version=None,
        applicability_id=None,
    )


def _record(source: str, at: datetime) -> FrameRecord:
    return FrameRecord(
        frame_id=f"{source}-{at:%H%M}",
        content_id=f"content-{source}-{at:%H%M}",
        product_id=f"product-{source}",
        decoder_version="decoder-v1",
        grid_id="grid-v1",
        valid_at=at,
        received_at=at,
        reference_at=at,
        acquisition_window=Interval(start_at=at - timedelta(minutes=5), end_at=at),
    )


def _frame(source: str, at: datetime, *, geolocation: str = "validated") -> AnalysisFrame:
    values = np.full(GRID.shape, np.nan)
    known = np.ones(GRID.shape, dtype=bool)
    detected = np.zeros(GRID.shape, dtype=bool)
    return AnalysisFrame(
        source,
        f"{source}-{at:%H%M}",
        at,
        GRID,
        np.zeros(GRID.shape),
        known,
        detected,
        values,
        None,
        _record(source, at),
        _geo(geolocation),
    )


def _track(source: str, feature_id: str, *, start: datetime = T0 - timedelta(minutes=10), dx: float = 0.0) -> Track:
    times = [start, start + timedelta(minutes=5), start + timedelta(minutes=10)]
    history = [
        type("Sample", (), {"frame_id": f"{source}-{at:%H%M}", "reference_at": at, "footprint": box(10_000 + dx, 10_000, 18_000 + dx, 18_000)})
        for at in times
    ]
    return Track(feature_id, source, times[-1], history[-1].footprint, history, (dx / 600.0, 0.0))


def _custom_track(source: str, feature_id: str, times: list[datetime], shapes: list[object], velocity=(0.0, 0.0)) -> Track:
    history = [
        type("Sample", (), {"frame_id": f"{source}-{at:%H%M}", "reference_at": at, "footprint": shape})
        for at, shape in zip(times, shapes)
    ]
    return Track(feature_id, source, times[-1], shapes[-1], history, velocity)


def _lightning(count: int, *, precise: bool = True) -> LightningInput:
    start = T0 - timedelta(minutes=5)
    record = FrameRecord(
        frame_id="li-1200",
        content_id="li-content",
        product_id="li",
        decoder_version="li-v1",
        grid_id="point",
        valid_at=T0,
        received_at=T0,
        reference_at=T0,
        acquisition_window=Interval(start_at=start, end_at=T0),
    )
    lon, lat = GRID.inverse(np.full(count, 14_000.0), np.full(count, 14_000.0))
    times = tuple(T0 - timedelta(seconds=i % 60) for i in range(count)) if precise else tuple(None for _ in range(count))
    frame = FlashFrame(
        "eumetsat_li",
        T0,
        10.0,
        np.asarray(lat),
        np.asarray(lon),
        np.asarray([T0] * count, dtype=object),
    )
    frame.event_times = times
    frame.time_precision = tuple("individual_time" if precise else "window_only" for _ in range(count))
    frame.time_reason_codes = tuple(() if precise else ("window_only_time",) for _ in range(count))
    frame.sample_ids = tuple(range(count))
    frame.acquisition_start = start
    frame.acquisition_end = T0
    return LightningInput(record, frame)


def test_asynchronous_overlap_translates_inside_bracketed_histories_only() -> None:
    from weatherbrief.observed.motion.association import AssociationContext, associate_tracks

    radar = _track("opera_dbzh", "radar-1", start=T0 - timedelta(minutes=12), dx=1_000.0)
    cloud = _track("eumetsat_ctth", "cloud-1", start=T0 - timedelta(minutes=15))
    frames = {
        "opera_dbzh": tuple(_frame("opera_dbzh", s.reference_at) for s in radar.history),
        "eumetsat_ctth": tuple(_frame("eumetsat_ctth", s.reference_at) for s in cloud.history),
    }

    associations, lightning, evidence = associate_tracks(
        [radar, cloud],
        frames,
        GRID,
        AssociationContext(lightning_frames=()),
    )

    assert lightning == []
    assert evidence == {}
    assert len(associations) == 1
    link = AssociationRecord.model_validate(associations[0].model_dump(mode="python"))
    assert link.status == "available"
    assert link.alignment_method == "in_history_translation"
    assert link.comparison_at == T0 - timedelta(minutes=5)
    assert link.radar_frame_ids == ["opera_dbzh-1153", "opera_dbzh-1158"]
    assert link.cloud_frame_ids == ["eumetsat_ctth-1155"]
    assert link.relation == "overlap"
    assert link.intersection_area_km2 > 0.0


def test_registration_or_missing_common_history_withholds_quantitative_association() -> None:
    from weatherbrief.observed.motion.association import AssociationContext, associate_tracks

    radar = _track("opera_dbzh", "radar-1")
    cloud = _track("eumetsat_ctth", "cloud-1", start=T0 - timedelta(minutes=40))
    frames = {
        "opera_dbzh": tuple(_frame("opera_dbzh", s.reference_at) for s in radar.history),
        "eumetsat_ctth": tuple(_frame("eumetsat_ctth", s.reference_at, geolocation="unverified") for s in cloud.history),
    }

    associations, _, _ = associate_tracks([radar, cloud], frames, GRID, AssociationContext(lightning_frames=()))

    assert len(associations) == 1
    link = AssociationRecord.model_validate(associations[0].model_dump(mode="python"))
    assert link.status == "unavailable"
    assert set(link.reason_codes) == {"geolocation_unverified", "no_common_history"}
    assert link.comparison_at is None
    assert link.intersection_area_km2 is None


def test_lightning_feature_summary_survives_marker_cap() -> None:
    from weatherbrief.observed.motion.association import AssociationContext, associate_tracks
    from weatherbrief.observed.motion.policy import DEFAULT_POLICY

    radar = _track("opera_dbzh", "radar-1")
    frames = {"opera_dbzh": tuple(_frame("opera_dbzh", s.reference_at) for s in radar.history)}

    _, lightning, evidence = associate_tracks(
        [radar],
        frames,
        GRID,
        AssociationContext(lightning_frames=(_lightning(DEFAULT_POLICY.max_lightning_records + 9),)),
    )

    assert len(lightning) == DEFAULT_POLICY.max_lightning_records
    assert all(isinstance(record, LightningRecord) for record in lightning)
    assert all(record.associated_feature_ids == ["radar-1"] for record in lightning)
    summary = evidence["radar-1"]
    assert summary.status == "available"
    assert summary.reported_detection_count == DEFAULT_POLICY.max_lightning_records + 9
    assert summary.emitted_marker_count == DEFAULT_POLICY.max_lightning_records
    assert not summary.evaluation_complete
    assert summary.reason_codes == ["lightning_marker_limit"]


def test_window_only_lightning_remains_observed_context_without_feature_claim() -> None:
    from weatherbrief.observed.motion.association import AssociationContext, associate_tracks

    radar = _track("opera_dbzh", "radar-1")
    frames = {"opera_dbzh": tuple(_frame("opera_dbzh", s.reference_at) for s in radar.history)}

    _, lightning, evidence = associate_tracks(
        [radar],
        frames,
        GRID,
        AssociationContext(lightning_frames=(_lightning(1, precise=False),)),
    )

    assert len(lightning) == 1
    record = LightningRecord.model_validate(lightning[0].model_dump(mode="python"))
    assert record.time_precision == "window_only"
    assert record.association_status == "unavailable"
    assert record.associated_feature_ids is None
    assert evidence == {}


def test_association_translates_left_bracket_contour_and_reports_source_windows() -> None:
    from weatherbrief.observed.motion.association import AssociationContext, associate_tracks

    radar_times = [T0 - timedelta(minutes=10), T0 - timedelta(minutes=5), T0]
    cloud_times = [T0 - timedelta(minutes=13), T0 - timedelta(minutes=8), T0 - timedelta(minutes=3)]
    radar = _custom_track(
        "opera_dbzh",
        "radar-1",
        radar_times,
        [
            box(0, 10_000, 2_000, 12_000),
            box(0, 10_000, 2_000, 12_000),
            box(100_000, 10_000, 102_000, 12_000),
        ],
        velocity=(10.0, 0.0),
    )
    cloud = _custom_track(
        "eumetsat_ctth",
        "cloud-1",
        cloud_times,
        [
            box(1_200, 10_000, 3_200, 12_000),
            box(1_200, 10_000, 3_200, 12_000),
            box(1_200, 10_000, 3_200, 12_000),
        ],
    )
    frames = {
        "opera_dbzh": tuple(_frame("opera_dbzh", at) for at in radar_times),
        "eumetsat_ctth": tuple(_frame("eumetsat_ctth", at) for at in cloud_times),
    }

    associations, _, _ = associate_tracks([radar, cloud], frames, GRID, AssociationContext())

    link = associations[0]
    assert link.status == "available"
    assert link.comparison_at == T0 - timedelta(minutes=3)
    assert link.radar_frame_ids == ["opera_dbzh-1155", "opera_dbzh-1200"]
    assert link.radar_window == Interval(start_at=T0 - timedelta(minutes=10), end_at=T0)
    assert link.cloud_window == Interval(start_at=T0 - timedelta(minutes=8), end_at=T0 - timedelta(minutes=3))
    assert link.relation == "overlap"


def test_nearby_association_is_limited_to_one_analysis_cell_diagonal() -> None:
    from weatherbrief.observed.motion.association import AssociationContext, associate_tracks

    radar = _custom_track("opera_dbzh", "radar-1", [T0], [box(10_000, 10_000, 12_000, 12_000)])
    cloud = _custom_track("eumetsat_ctth", "cloud-1", [T0], [box(15_000, 10_000, 17_000, 12_000)])
    frames = {
        "opera_dbzh": (_frame("opera_dbzh", T0),),
        "eumetsat_ctth": (_frame("eumetsat_ctth", T0),),
    }

    associations, _, _ = associate_tracks([radar, cloud], frames, GRID, AssociationContext())

    assert associations[0].status == "unavailable"
    assert associations[0].reason_codes == ["no_spatial_association"]
