"""Shared observed-motion fixture parity across authored client boundaries."""

from __future__ import annotations

import json
from pathlib import Path

from weatherbrief.models.observed_motion import ObservedMotion

ROOT = Path(__file__).resolve().parents[2]
WEB_FIXTURE = ROOT / "web" / "tests" / "fixtures" / "observed-motion-v1.json"
NATIVE_UI_FIXTURE = (
    ROOT
    / "app"
    / "flyfun-weather"
    / "flyfun-weather"
    / "Services"
    / "FixtureBriefingData.swift"
)
NATIVE_TEST_FIXTURE = (
    ROOT
    / "app"
    / "flyfun-weather"
    / "flyfun-weatherTests"
    / "ObservedMotionTests.swift"
)
COMPLETENESS_CATEGORIES = [
    "regions",
    "input_frames",
    "small_detections",
    "candidates",
    "features",
    "geometry",
    "associations",
    "lightning",
    "legs",
    "route_rows",
    "overlap_intervals",
]


def _native_multiline_json(path: Path, start_marker: str, end_marker: str) -> str:
    source = path.read_text()
    start = source.index(start_marker)
    start = source.index("\n", start) + 1
    end = source.index(end_marker, start)
    return source[start:end]


def _native_raw_segment(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker) + len(start_marker)
    return source[start : source.index(end_marker, start)]


def _native_test_default_fixture() -> dict:
    source = NATIVE_TEST_FIXTURE.read_text()
    features = json.loads(
        _native_raw_segment(
            source,
            'let features = available ? #"""\n',
            '\n    """# : "[]"',
        )
    )
    projections = json.loads(
        _native_raw_segment(source, 'let projections = available ? #"', '"# : "[]"')
    )
    sources = json.loads(
        _native_raw_segment(source, 'let sources = available ? #"', '"# : "[]"')
    )
    domain = json.loads(
        _native_raw_segment(source, 'let domain = available ? #"', '"# : "null"')
    )
    return {
        "schema_version": 1,
        "status": "available",
        "reason_codes": [],
        "revision": 12,
        "run_id": "run-1",
        "route_geometry_id": "route-1",
        "planned_timing_id": "timing-1",
        "computed_at": "2026-09-05T10:04:00Z",
        "cutoff_at": "2026-09-05T10:00:00Z",
        "expires_at": "2026-09-05T10:15:00Z",
        "method_version": "masked_contour_translation_v1",
        "policy_version": "observed_motion_policy_v1",
        "analysis_domain": domain,
        "sources": sources,
        "features": features,
        "associations": [],
        "lightning": [],
        "projection_times": projections,
        "completeness": [
            {
                "category": category,
                "status": "complete",
                "reason_codes": [],
                "considered_count": 0,
                "emitted_count": 0,
                "omitted_count": 0,
            }
            for category in COMPLETENESS_CATEGORIES
        ],
    }


def _assert_radar_fixture_shape(motion: ObservedMotion) -> None:
    assert motion.schema_version == 1
    assert motion.status == "available"
    assert motion.reason_codes == []
    assert len(motion.features) == 1
    feature = motion.features[0]
    assert feature.family == "radar_echo"
    assert feature.definition.operator == "gte"
    assert feature.motion.status == "accepted"
    assert feature.motion.bearing_deg_true == 90
    assert len(feature.frame_ids) == 3
    assert len(feature.motion.pair_diagnostics) == 2
    assert all(len(pair.patches) == 4 for pair in feature.motion.pair_diagnostics)
    assert not any(source.source_id == "eumetsat_ctth" for source in motion.sources)


def test_web_version_one_fixture_is_strict_python_producer_compatible() -> None:
    """The TypeScript unit suite imports this exact file at its parser boundary."""
    motion = ObservedMotion.model_validate_json(WEB_FIXTURE.read_text())

    _assert_radar_fixture_shape(motion)
    assert motion.revision == 1
    assert motion.projection_times == [
        motion.features[0].projections[0].at,
        motion.features[0].projections[1].at,
        motion.features[0].projections[2].at,
    ]


def test_authored_native_fixture_literals_remain_producer_compatible() -> None:
    """Static fixture validation only; this is not Swift/Xcode execution."""
    ui_motion = ObservedMotion.model_validate_json(
        _native_multiline_json(
            NATIVE_UI_FIXTURE,
            'static let observedMotion = RawObservedMotion(rawJSON: Data(#"""',
            '\n    """#.utf8))',
        )
    )
    native_test_motion = ObservedMotion.model_validate(_native_test_default_fixture())

    _assert_radar_fixture_shape(ui_motion)
    _assert_radar_fixture_shape(native_test_motion)
    assert ui_motion.cutoff_at < ui_motion.projection_times[0]
    assert native_test_motion.cutoff_at < native_test_motion.projection_times[0]
