import pytest
from pydantic import ValidationError

from weatherbrief.models import (
    AdvisoryAggregation,
    AdvisoryEvidenceRegion,
    AdvisoryStatus,
    Mitigation,
    MitigationKind,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    RouteAdvisoriesManifest,
)
from weatherbrief.tasks.advise import derive_assessment_from_advisories


def test_legacy_model_result_has_unknown_data_state_and_no_evidence():
    result = ModelAdvisoryResult.model_validate({
        "model": "gfs",
        "status": "amber",
        "detail": "legacy",
    })
    assert result.data_state is None
    assert result.primary_method_id is None
    assert result.evidence_regions == []


@pytest.mark.parametrize("payload", [
    {
        "start_point_index": 4,
        "end_point_index": 2,
        "severity": "amber",
        "reason_code": "bad_order",
    },
    {
        "start_point_index": 1,
        "end_point_index": 1,
        "lower_altitude_ft": 5000,
        "upper_altitude_ft": None,
        "severity": "amber",
        "reason_code": "half_bounds",
    },
    {
        "start_point_index": 1,
        "end_point_index": 1,
        "lower_altitude_ft": 9000,
        "upper_altitude_ft": 5000,
        "severity": "amber",
        "reason_code": "reversed_bounds",
    },
    {
        "start_point_index": 1,
        "end_point_index": 1,
        "severity": "unavailable",
        "reason_code": "bad_severity",
    },
    {
        "start_point_index": 1,
        "end_point_index": 1,
        "severity": "amber",
        "reason_code": "   ",
    },
])
def test_invalid_evidence_region_is_rejected(payload):
    with pytest.raises(ValidationError):
        AdvisoryEvidenceRegion.model_validate(payload)


def test_empty_and_all_unavailable_aggregate_unavailable():
    empty = RouteAdvisoryResult.from_per_model(
        "cloud_top", [], {}, AdvisoryAggregation.MAJORITY,
    )
    all_missing = RouteAdvisoryResult.from_per_model(
        "cloud_top",
        [
            ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.UNAVAILABLE),
            ModelAdvisoryResult(model="ecmwf", status=AdvisoryStatus.UNAVAILABLE),
        ],
        {},
        AdvisoryAggregation.WORST,
    )
    assert empty.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert empty.representative_model is None
    assert all_missing.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert all_missing.representative_model == "gfs"


def test_representative_model_matches_detail_and_mitigations_source():
    red_mitigation = Mitigation(
        kind=MitigationKind.ALTITUDE,
        addresses="cloud_top",
        detail="descend",
        mitigated_status=AdvisoryStatus.AMBER,
        altitude_ft=6000,
    )
    result = RouteAdvisoryResult.from_per_model(
        "cloud_top",
        [
            ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.GREEN, detail="g"),
            ModelAdvisoryResult(
                model="ecmwf",
                status=AdvisoryStatus.RED,
                detail="r",
                mitigations=[red_mitigation],
            ),
        ],
        {},
        AdvisoryAggregation.WORST,
    )
    assert result.aggregate_status == AdvisoryStatus.RED
    assert result.aggregate_detail == "r"
    assert result.representative_model == "ecmwf"
    assert result.aggregate_mitigations == [red_mitigation]


def test_majority_representative_model_matches_majority_detail():
    result = RouteAdvisoryResult.from_per_model(
        "cloud_top",
        [
            ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.AMBER, detail="g"),
            ModelAdvisoryResult(model="ecmwf", status=AdvisoryStatus.GREEN, detail="e"),
            ModelAdvisoryResult(model="icon", status=AdvisoryStatus.AMBER, detail="i"),
        ],
        {},
        AdvisoryAggregation.MAJORITY,
    )
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert result.aggregate_detail == "g"
    assert result.representative_model == "gfs"


def test_new_evidence_contract_round_trips():
    original = ModelAdvisoryResult(
        model="gfs",
        status=AdvisoryStatus.AMBER,
        detail="evidence",
        data_state="partial",
        primary_method_id="nwp",
        evidence_regions=[AdvisoryEvidenceRegion(
            start_point_index=2,
            end_point_index=4,
            lower_altitude_ft=5000,
            upper_altitude_ft=9000,
            severity=AdvisoryStatus.AMBER,
            reason_code="cruise_in_bkn_cloud",
            metric_id="cloud_coverage",
            method_id="nwp",
        )],
    )
    decoded = ModelAdvisoryResult.model_validate_json(original.model_dump_json())
    assert decoded == original


def test_derive_assessment_all_unavailable_is_unavailable():
    manifest = RouteAdvisoriesManifest(advisories=[
        RouteAdvisoryResult.from_per_model(
            "cloud_top",
            [ModelAdvisoryResult(
                model="gfs",
                status=AdvisoryStatus.UNAVAILABLE,
                data_state="unavailable",
            )],
            {},
        ),
    ])
    assert derive_assessment_from_advisories(manifest) == (
        "UNAVAILABLE",
        "No advisory data available",
    )
