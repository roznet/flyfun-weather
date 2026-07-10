from dataclasses import replace
from datetime import datetime
from itertools import permutations

import pytest

from weatherbrief.analysis.advisories.evidence import (
    EvidenceSample,
    build_non_spatial_result,
    cloud_method_id,
    icing_method_is_available,
    summarize_evidence,
)
from weatherbrief.models import AdvisoryStatus, RoutePointAnalysis, SoundingAnalysis


@pytest.fixture
def route_points():
    def build(
        distances: list[float],
        *,
        point_indices: list[int] | None = None,
    ) -> list[RoutePointAnalysis]:
        indices = point_indices or list(range(len(distances)))
        return [
            RoutePointAnalysis(
                point_index=point_index,
                lat=50.0 + position,
                lon=-1.0 + position,
                distance_from_origin_nm=distance,
                interpolated_time=datetime(2026, 7, 10, 10, 0),
                forecast_hour=datetime(2026, 7, 10, 9, 0),
                track_deg=90.0,
            )
            for position, (point_index, distance) in enumerate(zip(indices, distances))
        ]

    return build


def test_uneven_route_midpoint_cells(route_points):
    summary = summarize_evidence(
        route_points=route_points([0, 10, 50, 100]),
        total_distance_nm=100,
        evaluated_point_indices={0, 1, 2, 3},
        complete_point_indices={0, 1, 2, 3},
        affected_point_indices={1, 2},
        evidence_samples=[],
    )
    assert summary.affected_nm == 70.0
    assert summary.affected_pct == 50.0


def test_isolated_endpoint_owns_a_nonzero_bounded_cell(route_points):
    summary = summarize_evidence(
        route_points=route_points([0, 10, 50, 100]),
        total_distance_nm=100,
        evaluated_point_indices={0, 1, 2, 3},
        complete_point_indices={0, 1, 2, 3},
        affected_point_indices={0},
        evidence_samples=[],
    )
    assert summary.affected_nm == 5.0


def test_regions_split_on_gap_reason_method_severity_and_altitude(route_points):
    points = route_points([0, 10, 20, 30, 40])
    samples = [
        EvidenceSample(0, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp", 4000, 8000),
        EvidenceSample(1, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp", 4000, 8000),
        EvidenceSample(3, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp", 4000, 8000),
        EvidenceSample(4, AdvisoryStatus.RED, "cloud", "cloud_coverage", "nwp", 4000, 8000),
    ]
    summary = summarize_evidence(
        route_points=points,
        total_distance_nm=40,
        evaluated_point_indices={0, 1, 2, 3, 4},
        complete_point_indices={0, 1, 2, 3, 4},
        affected_point_indices={0, 1, 3, 4},
        evidence_samples=samples,
    )
    assert [(r.start_point_index, r.end_point_index) for r in summary.evidence_regions] == [
        (0, 1),
        (3, 3),
        (4, 4),
    ]


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        pytest.param("reason_code", "reason_b", id="reason"),
        pytest.param("metric_id", "metric_b", id="metric"),
        pytest.param("method_id", "method_b", id="method"),
        pytest.param("lower_altitude_ft", 1500, id="lower-altitude"),
        pytest.param("upper_altitude_ft", 2500, id="upper-altitude"),
    ],
)
def test_adjacent_regions_split_when_grouping_dimension_changes(
    route_points,
    field,
    changed_value,
):
    first = EvidenceSample(
        0,
        AdvisoryStatus.AMBER,
        "reason_a",
        "metric_a",
        "method_a",
        1000,
        2000,
    )
    second = replace(first, point_index=1, **{field: changed_value})

    summary = summarize_evidence(
        route_points=route_points([0, 10]),
        total_distance_nm=10,
        evaluated_point_indices={0, 1},
        complete_point_indices={0, 1},
        affected_point_indices={0, 1},
        evidence_samples=[first, second],
    )

    assert [(r.start_point_index, r.end_point_index) for r in summary.evidence_regions] == [
        (0, 0),
        (1, 1),
    ]


def test_identical_adjacent_samples_coalesce_to_one_region(route_points):
    first = EvidenceSample(
        0,
        AdvisoryStatus.AMBER,
        "reason_a",
        "metric_a",
        "method_a",
        1000,
        2000,
    )
    second = replace(first, point_index=1)

    summary = summarize_evidence(
        route_points=route_points([0, 10]),
        total_distance_nm=10,
        evaluated_point_indices={0, 1},
        complete_point_indices={0, 1},
        affected_point_indices={0, 1},
        evidence_samples=[first, second],
    )

    assert [(r.start_point_index, r.end_point_index) for r in summary.evidence_regions] == [
        (0, 1),
    ]


def test_partial_green_is_guarded_to_unavailable(route_points):
    summary = summarize_evidence(
        route_points=route_points([0, 10, 20]),
        total_distance_nm=20,
        evaluated_point_indices={0, 1},
        complete_point_indices={0, 1},
        affected_point_indices=set(),
        evidence_samples=[],
    )
    result = summary.build_result(
        model="gfs",
        status=AdvisoryStatus.GREEN,
        detail="clear",
        unavailable_detail="partial data",
        primary_method_id="nwp",
    )
    assert result.status == AdvisoryStatus.UNAVAILABLE
    assert result.data_state == "partial"
    assert result.detail == "partial data"


def test_partial_red_preserves_supported_hazard(route_points):
    summary = summarize_evidence(
        route_points=route_points([0, 10, 20]),
        total_distance_nm=20,
        evaluated_point_indices={0, 1},
        complete_point_indices={0, 1},
        affected_point_indices={1},
        evidence_samples=[],
    )
    result = summary.build_result(
        model="gfs",
        status=AdvisoryStatus.RED,
        detail="severe evidence",
        unavailable_detail="partial data",
        primary_method_id="nwp",
    )
    assert result.status == AdvisoryStatus.RED
    assert result.data_state == "partial"


def test_complete_clear_stays_green(route_points):
    summary = summarize_evidence(
        route_points=route_points([0, 10, 20]),
        total_distance_nm=20,
        evaluated_point_indices={0, 1, 2},
        complete_point_indices={0, 1, 2},
        affected_point_indices=set(),
        evidence_samples=[],
    )
    result = summary.build_result(
        model="gfs",
        status=AdvisoryStatus.GREEN,
        detail="clear",
        unavailable_detail="missing",
        primary_method_id="nwp",
    )
    assert result.status == AdvisoryStatus.GREEN
    assert result.data_state == "complete"


def test_exact_duplicate_samples_create_one_region(route_points):
    sample = EvidenceSample(
        1,
        AdvisoryStatus.AMBER,
        "cloud",
        "cloud_coverage",
        "nwp",
        4000,
        8000,
    )
    summary = summarize_evidence(
        route_points=route_points([0, 10, 20]),
        total_distance_nm=20,
        evaluated_point_indices={0, 1, 2},
        complete_point_indices={0, 1, 2},
        affected_point_indices={1},
        evidence_samples=[sample, sample],
    )
    assert len(summary.evidence_regions) == 1
    assert summary.affected_nm == 10.0


def test_tied_regions_use_a_deterministic_total_order(route_points):
    class CollidingEvidenceSample(EvidenceSample):
        def __hash__(self) -> int:
            return 0

    samples = [
        CollidingEvidenceSample(
            0,
            AdvisoryStatus.AMBER,
            "shared",
            "z_metric",
            "nwp",
            0,
            1000,
        ),
        CollidingEvidenceSample(
            0,
            AdvisoryStatus.RED,
            "shared",
            "a_metric",
            "ogimet_dd",
            0,
            2000,
        ),
        CollidingEvidenceSample(
            0,
            AdvisoryStatus.AMBER,
            "shared",
            "a_metric",
            "nwp",
            0,
            1000,
        ),
    ]
    expected = [
        (AdvisoryStatus.AMBER, "a_metric", "nwp", 1000),
        (AdvisoryStatus.AMBER, "z_metric", "nwp", 1000),
        (AdvisoryStatus.RED, "a_metric", "ogimet_dd", 2000),
    ]

    for ordered_samples in permutations(samples):
        summary = summarize_evidence(
            route_points=route_points([0]),
            total_distance_nm=10,
            evaluated_point_indices={0},
            complete_point_indices={0},
            affected_point_indices={0},
            evidence_samples=ordered_samples,
        )
        assert [
            (region.severity, region.metric_id, region.method_id, region.upper_altitude_ft)
            for region in summary.evidence_regions
        ] == expected


def test_overlapping_reason_regions_do_not_double_count_distance(route_points):
    samples = [
        EvidenceSample(1, AdvisoryStatus.AMBER, "icing", "icing_risk", "ogimet_dd"),
        EvidenceSample(1, AdvisoryStatus.RED, "sld", "sld_risk", "ogimet_dd"),
    ]
    summary = summarize_evidence(
        route_points=route_points([0, 10, 20]),
        total_distance_nm=20,
        evaluated_point_indices={0, 1, 2},
        complete_point_indices={0, 1, 2},
        affected_point_indices={1},
        evidence_samples=samples,
    )
    assert len(summary.evidence_regions) == 2
    assert summary.affected_points == 1
    assert summary.affected_nm == 10.0


def test_regions_split_across_a_missing_stable_point_index(route_points):
    points = route_points([0, 10, 20], point_indices=[0, 2, 3])
    samples = [
        EvidenceSample(0, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp"),
        EvidenceSample(2, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp"),
    ]
    summary = summarize_evidence(
        route_points=points,
        total_distance_nm=20,
        evaluated_point_indices={0, 2, 3},
        complete_point_indices={0, 2, 3},
        affected_point_indices={0, 2},
        evidence_samples=samples,
    )
    assert [(r.start_point_index, r.end_point_index) for r in summary.evidence_regions] == [
        (0, 0),
        (2, 2),
    ]


def test_non_spatial_partial_hazard_keeps_supported_grade():
    result = build_non_spatial_result(
        model="gfs",
        status=AdvisoryStatus.AMBER,
        detail="departure affected",
        unavailable_detail="partial airport data",
        expected_entities={"departure", "arrival"},
        evaluated_entities={"departure"},
        complete_entities={"departure"},
        affected_entities={"departure"},
        primary_method_id="airport_conditions",
    )
    assert result.status == AdvisoryStatus.AMBER
    assert result.data_state == "partial"
    assert result.affected_points == 1


def test_method_provenance_does_not_guess_native_nwp():
    assert cloud_method_id("nwp_synthesized", "square_nwp") == "nwp_synthesized"
    assert cloud_method_id(None, "square_nwp") is None


def test_ogimet_nwp_distinguishes_missing_from_available_clear_geometry():
    missing = SoundingAnalysis(nwp_cloud_layers=None)
    available_clear = SoundingAnalysis(nwp_cloud_layers=[])
    assert not icing_method_is_available(missing, "ogimet_nwp")
    assert icing_method_is_available(available_clear, "ogimet_nwp")
