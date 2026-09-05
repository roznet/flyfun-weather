from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from weatherbrief.models.observed_motion import (
    AnalysisDomain,
    AssociationRecord,
    FeatureLightningEvidence,
    FrameRecord,
    ObservedMotion,
    empty_motion,
)
from weatherbrief.observed.motion.policy import DEFAULT_POLICY

from .motion_fixtures import (
    COMPLETENESS_CATEGORIES,
    accepted_radar_feature_dict,
    available_motion_dict,
    disabled_motion_dict,
    lightning_dict,
    motion_with_cloud_association,
    unavailable_motion_dict,
)


def test_newer_failure_roundtrips_unknown_fields() -> None:
    raw = unavailable_motion_dict(revision=9)
    raw["future_extension"] = {"source_with_underscores": 42}

    result = ObservedMotion.model_validate(raw).model_dump(mode="json")

    assert result["revision"] == 9
    assert result["future_extension"] == {"source_with_underscores": 42}


def test_nested_unknown_fields_roundtrip_without_rewriting_identity() -> None:
    raw = available_motion_dict()
    raw["features"][0]["future_detail"] = {"opaque_id": "Keep_Me"}

    result = ObservedMotion.model_validate(raw).model_dump(mode="json")

    assert result["features"][0]["future_detail"] == {"opaque_id": "Keep_Me"}


@pytest.mark.parametrize("revision", [True, 1.0, "1", 0, 9007199254740992])
def test_revision_rejects_bool_coercions_and_unsafe_values(revision: object) -> None:
    raw = unavailable_motion_dict()
    raw["revision"] = revision

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_failure_envelope_cannot_carry_an_accepted_velocity() -> None:
    raw = unavailable_motion_dict()
    raw["features"] = [accepted_radar_feature_dict()]

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_disabled_envelope_cannot_carry_inspected_contents() -> None:
    raw = disabled_motion_dict()
    raw["sources"] = [available_motion_dict()["sources"][0]]

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


@pytest.mark.parametrize("field,value", [("schema_version", 2), ("status", "future_state")])
def test_producer_rejects_unsupported_root_schema_or_status(field: str, value: object) -> None:
    raw = unavailable_motion_dict()
    raw[field] = value

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_schema_version_is_a_strict_integer_literal(schema_version: object) -> None:
    raw = unavailable_motion_dict()
    raw["schema_version"] = schema_version

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_completeness_category_order_is_not_part_of_the_wire_contract() -> None:
    raw = unavailable_motion_dict()
    raw["completeness"] = list(reversed(raw["completeness"]))

    result = ObservedMotion.model_validate(raw)

    assert {row.category for row in result.completeness} == set(COMPLETENESS_CATEGORIES)


def test_datetime_requires_explicit_utc_and_normalizes_aware_instances() -> None:
    naive = unavailable_motion_dict()
    naive["cutoff_at"] = "2026-09-05T12:00:00"
    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(naive)

    raw = unavailable_motion_dict()
    raw["cutoff_at"] = datetime(2026, 9, 5, 13, 0, tzinfo=timezone(timedelta(hours=1)))
    result = ObservedMotion.model_validate(raw).model_dump(mode="json")
    assert result["cutoff_at"] == "2026-09-05T12:00:00Z"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_numbers_are_rejected_at_every_record_level(value: float) -> None:
    raw = available_motion_dict()
    raw["features"][0]["motion"]["ground_speed_kt"] = value

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_missing_required_nullable_key_is_rejected() -> None:
    raw = unavailable_motion_dict()
    del raw["planned_timing_id"]

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_nominal_valid_time_may_be_outside_the_documented_acquisition_window() -> None:
    record = FrameRecord.model_validate(
        {
            "frame_id": "frame-1",
            "content_id": "content-1",
            "product_id": "product-1",
            "decoder_version": "decoder-1",
            "grid_id": "grid-1",
            "valid_at": "2026-09-05T12:00:00Z",
            "received_at": "2026-09-05T12:01:00Z",
            "acquisition_window": {
                "start_at": "2026-09-05T11:45:00Z",
                "end_at": "2026-09-05T11:55:00Z",
            },
            "reference_at": "2026-09-05T12:00:00Z",
        }
    )

    assert record.valid_at.isoformat() == "2026-09-05T12:00:00+00:00"


def test_envelope_rejects_acquisition_ending_after_receipt() -> None:
    raw = available_motion_dict()
    raw["sources"][0]["frames"][2]["received_at"] = "2026-09-05T11:59:00Z"

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_frame_record_rejects_nominal_or_acquisition_times_after_receipt() -> None:
    raw = available_motion_dict()["sources"][0]["frames"][2]
    raw["received_at"] = "2026-09-05T11:59:00Z"

    with pytest.raises(ValidationError):
        FrameRecord.model_validate(raw)


def test_analysis_domain_rejects_ground_points_beyond_projection_radius() -> None:
    raw = available_motion_dict()["analysis_domain"]
    raw["origin_x_m"] = 900_000.0

    with pytest.raises(ValidationError):
        AnalysisDomain.model_validate(raw)


def test_dangling_feature_source_reference_is_rejected() -> None:
    raw = available_motion_dict()
    raw["features"][0]["source_id"] = "unknown-source"

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_dangling_feature_frame_reference_is_rejected() -> None:
    raw = available_motion_dict()
    raw["features"][0]["frame_ids"][1] = "unknown-frame"

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_motion_pair_references_must_follow_the_feature_history() -> None:
    raw = available_motion_dict()
    raw["features"][0]["motion"]["pair_diagnostics"][0]["from_frame_id"] = "unknown-frame"

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_trail_sample_references_must_resolve_to_the_feature_history() -> None:
    raw = available_motion_dict()
    raw["features"][0]["trail"][0]["frame_id"] = "unknown-frame"

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_accepted_cloud_motion_requires_validated_registration() -> None:
    raw = available_motion_dict()
    feature = raw["features"][0]
    feature["family"] = "high_cloud_top"
    feature["definition"] = {
        "quantity": "geometric_cloud_top_height",
        "operator": "gte",
        "threshold": 4572.0,
        "unit": "m_msl",
    }
    feature["geolocation"] = {
        "status": "unverified",
        "reason_codes": ["geolocation_unverified"],
        "evidence_id": None,
        "method_version": None,
        "applicability_id": None,
    }

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_available_association_requires_registered_histories() -> None:
    raw = motion_with_cloud_association(validated=False)

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_available_association_requires_compatible_observed_intervals() -> None:
    raw = motion_with_cloud_association(validated=True)
    cloud_source = raw["sources"][1]
    cloud_feature = raw["features"][1]
    old_times = [
        "2026-09-05T10:40:00Z",
        "2026-09-05T10:50:00Z",
        "2026-09-05T11:00:00Z",
    ]
    for frame, at in zip(cloud_source["frames"], old_times, strict=True):
        frame["valid_at"] = at
        frame["received_at"] = at
        frame["reference_at"] = at
        frame["acquisition_window"] = {"start_at": at, "end_at": at}
    cloud_feature["reference_at"] = old_times[-1]
    for trail, at in zip(cloud_feature["trail"], old_times, strict=True):
        trail["observed_at"] = at
    association = raw["associations"][0]
    association["comparison_at"] = old_times[-1]
    association["cloud_window"] = {"start_at": old_times[-1], "end_at": old_times[-1]}

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


@pytest.mark.parametrize(
    "relation,intersection_area_km2",
    [("overlap", 0.0), ("nearby", 1.0)],
)
def test_association_relation_agrees_with_measured_intersection(
    relation: str, intersection_area_km2: float
) -> None:
    raw = motion_with_cloud_association()["associations"][0]
    raw["relation"] = relation
    raw["intersection_area_km2"] = intersection_area_km2

    with pytest.raises(ValidationError):
        AssociationRecord.model_validate(raw)


@pytest.mark.parametrize(
    "reported,emitted,complete",
    [(None, 1, False), (0, 0, False), (1, 2, True)],
)
def test_lightning_evidence_counts_cannot_manufacture_or_overemit_detections(
    reported: int | None, emitted: int, complete: bool
) -> None:
    raw = available_motion_dict()
    evidence = raw["features"][0]["lightning_evidence"]
    evidence.update(
        {
            "status": "available",
            "reason_codes": [],
            "source_id": "eumetsat-li",
            "frame_ids": ["li-frame-1"],
            "evaluated_window": {
                "start_at": "2026-09-05T11:50:00Z",
                "end_at": "2026-09-05T12:00:00Z",
            },
            "reported_detection_count": reported,
            "emitted_marker_count": emitted,
            "evaluation_complete": complete,
        }
    )

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_partial_positive_lightning_evidence_carries_a_lower_bound_reason() -> None:
    evidence = {
        "status": "available",
        "reason_codes": [],
        "source_id": "eumetsat-li",
        "frame_ids": ["li-frame-1"],
        "evaluated_window": {
            "start_at": "2026-09-05T11:50:00Z",
            "end_at": "2026-09-05T12:00:00Z",
        },
        "reported_detection_count": 1,
        "emitted_marker_count": 0,
        "evaluation_complete": False,
    }

    with pytest.raises(ValidationError):
        FeatureLightningEvidence.model_validate(evidence)


def test_envelope_marker_count_must_match_emitted_lightning_records() -> None:
    raw = available_motion_dict()
    evidence = raw["features"][0]["lightning_evidence"]
    evidence.update(
        {
            "status": "available",
            "reason_codes": [],
            "source_id": "eumetsat-li",
            "frame_ids": ["li-frame-1"],
            "evaluated_window": {
                "start_at": "2026-09-05T11:50:00Z",
                "end_at": "2026-09-05T12:00:00Z",
            },
            "reported_detection_count": 1,
            "emitted_marker_count": 1,
            "evaluation_complete": True,
        }
    )
    raw["lightning"] = []

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_emitted_lightning_count_cannot_hide_a_serialized_associated_marker() -> None:
    raw = available_motion_dict()
    li_frame = deepcopy(raw["sources"][0]["frames"][2])
    li_frame.update(
        {
            "frame_id": "li-frame-1",
            "content_id": "li-content-1",
            "product_id": "mtg-li",
        }
    )
    raw["sources"].append(
        {
            "source_id": "eumetsat-li",
            "status": "available",
            "reason_codes": [],
            "frames": [li_frame],
            "gaps": [],
            "attribution": "EUMETSAT",
            "coverage": {
                "status": "unavailable",
                "reason_codes": ["point_coverage_unknown"],
                "scope": "point_detections",
                "known_cells": None,
                "total_cells": None,
                "known_fraction": None,
            },
            "geolocation": {
                "status": "unverified",
                "reason_codes": ["geolocation_unverified"],
                "evidence_id": None,
                "method_version": None,
                "applicability_id": None,
            },
        }
    )
    raw["features"][0]["lightning_evidence"].update(
        {
            "status": "available",
            "reason_codes": [],
            "source_id": "eumetsat-li",
            "frame_ids": ["li-frame-1"],
            "evaluated_window": {
                "start_at": "2026-09-05T12:00:00Z",
                "end_at": "2026-09-05T12:00:00Z",
            },
            "reported_detection_count": 1,
            "emitted_marker_count": 0,
            "evaluation_complete": True,
        }
    )
    raw["lightning"] = [
        {
            "detection_id": "flash-1",
            "source_id": "eumetsat-li",
            "frame_id": "li-frame-1",
            "position": [-0.95, 50.05],
            "time_precision": "individual_time",
            "event_at": "2026-09-05T12:00:00Z",
            "acquisition_window": {
                "start_at": "2026-09-05T12:00:00Z",
                "end_at": "2026-09-05T12:00:00Z",
            },
            "reason_codes": [],
            "association_status": "available",
            "association_reason_codes": [],
            "associated_feature_ids": ["radar-feature-1"],
        }
    ]

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_invalid_or_out_of_bounds_polygon_is_rejected() -> None:
    raw = available_motion_dict()
    raw["features"][0]["display_geometry"]["geometry"]["coordinates"] = [
        [[[0.0, 50.0], [1.0, 51.0], [0.0, 51.0], [1.0, 50.0], [0.0, 50.0]]]
    ]

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_positions_per_footprint_limit_is_enforced() -> None:
    raw = available_motion_dict()
    ring = [[-1.0 + (i / 1000), 50.0] for i in range(DEFAULT_POLICY.max_positions_per_footprint)]
    ring.append(ring[0])
    raw["features"][0]["display_geometry"]["geometry"]["coordinates"] = [[ring]]

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_projection_time_limit_is_enforced() -> None:
    raw = available_motion_dict()
    raw["projection_times"].append("2026-09-05T12:20:00Z")

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_lightning_payload_limit_is_enforced() -> None:
    raw = unavailable_motion_dict()
    raw["lightning"] = [lightning_dict(f"flash-{i}") for i in range(DEFAULT_POLICY.max_lightning_records + 1)]

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_serialized_payload_limit_includes_unknown_fields() -> None:
    raw = unavailable_motion_dict()
    raw["future_extension"] = "x" * DEFAULT_POLICY.max_serialized_bytes

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_empty_motion_marks_every_category_not_evaluated_without_fake_counts() -> None:
    result = empty_motion(
        route_geometry_id="route-geometry-1",
        planned_timing_id=None,
        cutoff_at=datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
        revision=7,
        status="unavailable",
        reason_codes=["insufficient_history"],
    ).model_dump(mode="json")

    assert [row["category"] for row in result["completeness"]] == COMPLETENESS_CATEGORIES
    assert all(row["status"] == "not_evaluated" for row in result["completeness"])
    assert all(row["considered_count"] is None for row in result["completeness"])
    assert all(row["emitted_count"] == 0 for row in result["completeness"])
    assert all(row["omitted_count"] is None for row in result["completeness"])
    assert result["features"] == []
    assert result["run_id"] is None


def test_not_evaluated_completeness_cannot_claim_emitted_work() -> None:
    raw = unavailable_motion_dict()
    raw["completeness"][0]["emitted_count"] = 1

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_zero_motion_is_accepted_without_manufacturing_a_bearing() -> None:
    raw = available_motion_dict()
    raw["features"][0]["motion"]["ground_speed_kt"] = 0.0
    raw["features"][0]["motion"]["bearing_deg_true"] = None

    result = ObservedMotion.model_validate(raw)

    assert result.features[0].motion.ground_speed_kt == 0.0
    assert result.features[0].motion.bearing_deg_true is None


def test_complete_planned_evaluation_can_have_no_overlap_intervals() -> None:
    raw = available_motion_dict()
    raw["planned_timing_id"] = "planned-timing-1"
    raw["features"][0]["planned_overlap"].update(
        {
            "status": "available",
            "reason_codes": [],
            "evaluated_interval": {
                "start_at": "2026-09-05T12:00:00Z",
                "end_at": "2026-09-05T12:15:00Z",
            },
            "intervals": [],
            "complete": True,
        }
    )

    result = ObservedMotion.model_validate(raw)

    assert result.features[0].planned_overlap.complete is True
    assert result.features[0].planned_overlap.intervals == []


def test_available_planned_evaluation_requires_planned_timing_identity() -> None:
    raw = available_motion_dict()
    raw["features"][0]["planned_overlap"].update(
        {
            "status": "available",
            "reason_codes": [],
            "evaluated_interval": {
                "start_at": "2026-09-05T12:00:00Z",
                "end_at": "2026-09-05T12:15:00Z",
            },
            "intervals": [],
            "complete": True,
        }
    )

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_available_fixture_roundtrips_as_a_valid_producer_record() -> None:
    raw = available_motion_dict()

    result = ObservedMotion.model_validate(deepcopy(raw)).model_dump(mode="json")

    assert result["status"] == "available"
    assert result["features"][0]["motion"]["ground_speed_kt"] == 20.0
