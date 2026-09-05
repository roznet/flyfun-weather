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
    Interval,
    ObservedMotion,
    OverlapInterval,
    PlannedOverlapResult,
    RouteRow,
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


@pytest.mark.parametrize(
    "value",
    ["2026-09-05Z", "2026-09-05T12:00Z", "2026-09-05 12:00:00Z"],
)
def test_utc_wire_datetime_rejects_reduced_or_date_only_forms(value: str) -> None:
    with pytest.raises(ValidationError):
        Interval.model_validate({"start_at": value, "end_at": "2026-09-05T12:00:00Z"})


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


@pytest.mark.parametrize(
    "crs",
    [
        "+proj=aeqd definitely-not-a-valid-crs",
        "+proj=aeqd +lat_0=50.05 +lon_0=-0.95 +ellps=GRS80 +units=m +no_defs",
        "+proj=merc +lat_0=50.05 +lon_0=-0.95 +datum=WGS84 +units=m +no_defs",
        "+proj=aeqd +lat_0=49 +lon_0=-0.95 +datum=WGS84 +units=m +no_defs",
    ],
    ids=["garbage-token", "wrong-ellipsoid", "wrong-method", "wrong-centre"],
)
def test_analysis_domain_requires_parseable_wgs84_aeqd_at_its_declared_center(crs: str) -> None:
    raw = available_motion_dict()["analysis_domain"]
    raw["crs"] = crs

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


@pytest.mark.parametrize(
    "path,value",
    [
        (("patches", 0, "support_fraction"), 0.79),
        (("patches", 0, "ncc"), 0.79),
        (("patches", 0, "competing_peak_margin"), 0.09),
        (("patch_disagreement_cells",), 1.42),
        (("reverse_residual_cells",), 1.42),
        (("next_observation_residual_cells",), 2.83),
        (("common_support_iou",), 0.49),
        (("area_ratio",), 0.66),
        (("area_ratio",), 1.51),
        (("lineage_complete",), False),
        (("plausible_parent_count",), 2),
        (("plausible_child_count",), 0),
    ],
    ids=[
        "support",
        "ncc",
        "peak-margin",
        "patch-disagreement",
        "reverse-residual",
        "next-observation-residual",
        "iou",
        "area-low",
        "area-high",
        "lineage-incomplete",
        "multiple-parents",
        "missing-child",
    ],
)
def test_accepted_pair_diagnostics_must_satisfy_exported_policy(
    path: tuple[object, ...], value: object
) -> None:
    raw = available_motion_dict()
    target = raw["features"][0]["motion"]["pair_diagnostics"][0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_accepted_pair_patch_centres_must_be_separated_by_two_cells() -> None:
    raw = available_motion_dict()
    patches = raw["features"][0]["motion"]["pair_diagnostics"][0]["patches"]
    patches[1]["center_column"] = patches[0]["center_column"] + 1
    patches[1]["center_row"] = patches[0]["center_row"]

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_accepted_pair_displacement_cannot_exceed_search_speed() -> None:
    raw = available_motion_dict()
    pair = raw["features"][0]["motion"]["pair_diagnostics"][0]
    pair["forward_dx_cells"] = 20.0
    for patch in pair["patches"]:
        patch["dx_cells"] = 20.0 if patch["direction"] == "forward" else -20.0

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_accepted_radar_history_rejects_an_eleven_minute_adjacent_gap() -> None:
    raw = available_motion_dict()
    first_frame = raw["sources"][0]["frames"][0]
    first_frame.update(
        {
            "valid_at": "2026-09-05T11:39:00Z",
            "received_at": "2026-09-05T11:39:00Z",
            "reference_at": "2026-09-05T11:39:00Z",
            "acquisition_window": {
                "start_at": "2026-09-05T11:39:00Z",
                "end_at": "2026-09-05T11:39:00Z",
            },
        }
    )
    raw["features"][0]["trail"][0]["observed_at"] = "2026-09-05T11:39:00Z"
    raw["features"][0]["motion"]["pair_diagnostics"][0]["elapsed_seconds"] = 660.0

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_accepted_cloud_history_rejects_a_span_over_forty_five_minutes() -> None:
    raw = available_motion_dict()
    source = raw["sources"][0]
    feature = raw["features"][0]
    source["source_id"] = "eumetsat-ctth"
    feature["source_id"] = "eumetsat-ctth"
    feature["family"] = "high_cloud_top"
    feature["definition"] = {
        "quantity": "geometric_cloud_top_height",
        "operator": "gte",
        "threshold": 4572.0,
        "unit": "m_msl",
    }
    times = [
        "2026-09-05T11:12:00Z",
        "2026-09-05T11:28:00Z",
        "2026-09-05T11:44:00Z",
        "2026-09-05T12:00:00Z",
    ]
    newest = deepcopy(source["frames"][-1])
    newest["frame_id"] = "frame-4"
    newest["content_id"] = "content-frame-4"
    source["frames"].append(newest)
    feature["frame_ids"] = ["frame-1", "frame-2", "frame-3", "frame-4"]
    feature["reference_frame_id"] = "frame-4"
    newest_trail = deepcopy(feature["trail"][-1])
    newest_trail["frame_id"] = "frame-4"
    feature["trail"].append(newest_trail)
    for frame, trail, at in zip(source["frames"], feature["trail"], times, strict=True):
        frame["valid_at"] = at
        frame["received_at"] = at
        frame["reference_at"] = at
        frame["acquisition_window"] = {"start_at": at, "end_at": at}
        trail["observed_at"] = at
    pairs = feature["motion"]["pair_diagnostics"]
    pairs[0]["elapsed_seconds"] = 960.0
    pairs[1]["elapsed_seconds"] = 960.0
    pairs[1]["next_observation_residual_cells"] = 0.2
    last_pair = deepcopy(pairs[1])
    last_pair["from_frame_id"] = "frame-3"
    last_pair["to_frame_id"] = "frame-4"
    last_pair["next_observation_residual_cells"] = None
    pairs.append(last_pair)

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_accepted_feature_must_anchor_to_the_final_frame_in_its_chain() -> None:
    raw = available_motion_dict()
    feature = raw["features"][0]
    feature["reference_frame_id"] = "frame-2"
    feature["reference_at"] = "2026-09-05T11:50:00Z"
    feature["projection_end_at"] = "2026-09-05T12:05:00Z"
    feature["projections"] = []
    raw["projection_times"] = []
    raw["expires_at"] = "2026-09-05T12:05:00Z"

    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)


def test_accepted_feature_cannot_bypass_a_newer_selected_source_frame() -> None:
    raw = available_motion_dict()
    source = raw["sources"][0]
    feature = raw["features"][0]
    times = ["2026-09-05T11:30:00Z", "2026-09-05T11:40:00Z", "2026-09-05T11:50:00Z"]
    for frame, trail, at in zip(source["frames"], feature["trail"], times, strict=True):
        frame["valid_at"] = at
        frame["received_at"] = at
        frame["reference_at"] = at
        frame["acquisition_window"] = {"start_at": at, "end_at": at}
        trail["observed_at"] = at
    newest = deepcopy(source["frames"][-1])
    newest.update(
        {
            "frame_id": "frame-4",
            "content_id": "content-frame-4",
            "valid_at": "2026-09-05T12:00:00Z",
            "received_at": "2026-09-05T12:00:00Z",
            "reference_at": "2026-09-05T12:00:00Z",
            "acquisition_window": {
                "start_at": "2026-09-05T12:00:00Z",
                "end_at": "2026-09-05T12:00:00Z",
            },
        }
    )
    source["frames"].append(newest)
    feature["reference_at"] = times[-1]
    feature["projection_end_at"] = "2026-09-05T12:05:00Z"
    feature["projections"] = []
    raw["projection_times"] = []
    raw["expires_at"] = "2026-09-05T12:05:00Z"

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


def test_projection_times_must_be_absolute_five_minute_utc_ticks() -> None:
    raw = available_motion_dict()
    raw["projection_times"][0] = "2026-09-05T12:06:00Z"
    raw["features"][0]["projections"][0]["at"] = "2026-09-05T12:06:00Z"

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


def _available_route_row() -> dict[str, object]:
    return {
        "leg_id": "route-1:leg-0",
        "leg_index": 0,
        "from_label": "A",
        "to_label": "B",
        "at": "2026-09-05T12:00:00Z",
        "status": "available",
        "reason_codes": [],
        "distance_nm": 5.0,
        "closure_kt": 2.0,
        "closure_interval": {
            "start_at": "2026-09-05T11:59:30Z",
            "end_at": "2026-09-05T12:00:30Z",
        },
        "relationship": "approaching",
        "planned_time_method": "distance_proportional_planned",
        "planned_time_status": "unavailable",
        "planned_time_reason_codes": ["invalid_planned_timing"],
        "planned_overlap_at_time": None,
    }


def test_nonintersecting_available_route_row_requires_closure_and_interval() -> None:
    raw = _available_route_row()
    raw["closure_kt"] = None
    raw["closure_interval"] = None

    with pytest.raises(ValidationError):
        RouteRow.model_validate(raw)


@pytest.mark.parametrize(
    "relationship,closure_kt",
    [
        ("approaching", -2.0),
        ("approaching", 0.5),
        ("receding", 2.0),
        ("receding", -0.5),
        ("approximately_unchanged", 1.0),
        ("approximately_unchanged", -1.0),
    ],
)
def test_route_relationship_matches_closure_sign_and_one_knot_threshold(
    relationship: str, closure_kt: float
) -> None:
    raw = _available_route_row()
    raw["relationship"] = relationship
    raw["closure_kt"] = closure_kt

    with pytest.raises(ValidationError):
        RouteRow.model_validate(raw)


@pytest.mark.parametrize(
    "start_at,end_at",
    [
        ("2026-09-05T11:58:30Z", "2026-09-05T12:00:30Z"),
        ("2026-09-05T12:00:01Z", "2026-09-05T12:00:30Z"),
    ],
    ids=["longer-than-sixty-seconds", "does-not-contain-evaluation-time"],
)
def test_route_closure_interval_is_bounded_around_the_evaluation_time(
    start_at: str, end_at: str
) -> None:
    raw = _available_route_row()
    raw["closure_interval"] = {"start_at": start_at, "end_at": end_at}

    with pytest.raises(ValidationError):
        RouteRow.model_validate(raw)


def _available_planned_overlap(interval: dict[str, object]) -> dict[str, object]:
    return {
        "status": "available",
        "reason_codes": [],
        "method": "relative_segment_contour_intersection",
        "planned_time_method": "distance_proportional_planned",
        "evaluated_interval": {
            "start_at": "2026-09-05T12:00:30Z",
            "end_at": "2026-09-05T12:10:30Z",
        },
        "intervals": [interval],
        "complete": True,
    }


def test_tangent_overlap_is_an_instant() -> None:
    raw = {
        "leg_id": "route-1:leg-0",
        "leg_index": 0,
        "start_at": "2026-09-05T12:05:00Z",
        "end_at": "2026-09-05T12:06:00Z",
        "contact": "tangent",
        "approximate": True,
    }

    with pytest.raises(ValidationError):
        OverlapInterval.model_validate(raw)


def test_interval_overlap_has_nonzero_duration() -> None:
    raw = {
        "leg_id": "route-1:leg-0",
        "leg_index": 0,
        "start_at": "2026-09-05T12:05:00Z",
        "end_at": "2026-09-05T12:05:00Z",
        "contact": "interval",
        "approximate": True,
    }

    with pytest.raises(ValidationError):
        OverlapInterval.model_validate(raw)


@pytest.mark.parametrize(
    "interval",
    [
        {
            "leg_id": "route-1:leg-0",
            "leg_index": 0,
            "start_at": "2026-09-05T12:01:30Z",
            "end_at": "2026-09-05T12:03:00Z",
            "contact": "interval",
            "approximate": True,
        },
        {
            "leg_id": "route-1:leg-0",
            "leg_index": 0,
            "start_at": "2026-09-05T12:05:30Z",
            "end_at": "2026-09-05T12:05:30Z",
            "contact": "tangent",
            "approximate": True,
        },
    ],
    ids=["unrounded-interval-endpoint", "unrounded-tangent"],
)
def test_overlap_display_times_are_minute_rounded_unless_clamped_to_a_boundary(
    interval: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        PlannedOverlapResult.model_validate(_available_planned_overlap(interval))


def test_overlap_endpoint_can_equal_a_nonminute_evaluated_boundary_after_clamping() -> None:
    interval = {
        "leg_id": "route-1:leg-0",
        "leg_index": 0,
        "start_at": "2026-09-05T12:00:30Z",
        "end_at": "2026-09-05T12:03:00Z",
        "contact": "interval",
        "approximate": True,
    }

    result = PlannedOverlapResult.model_validate(_available_planned_overlap(interval))

    assert result.intervals[0].start_at.isoformat() == "2026-09-05T12:00:30+00:00"


def test_available_fixture_roundtrips_as_a_valid_producer_record() -> None:
    raw = available_motion_dict()

    result = ObservedMotion.model_validate(deepcopy(raw)).model_dump(mode="json")

    assert result["status"] == "available"
    assert result["features"][0]["motion"]["ground_speed_kt"] == 20.0
