from __future__ import annotations

from copy import deepcopy
from typing import Any


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


def unavailable_support(scope: str = "feature_contour") -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason_codes": ["unknown_support"],
        "scope": scope,
        "known_cells": None,
        "total_cells": None,
        "known_fraction": None,
    }


def available_support(scope: str = "feature_contour") -> dict[str, Any]:
    return {
        "status": "available",
        "reason_codes": [],
        "scope": scope,
        "known_cells": 100,
        "total_cells": 100,
        "known_fraction": 1.0,
    }


def validated_geolocation() -> dict[str, Any]:
    return {
        "status": "validated",
        "reason_codes": [],
        "evidence_id": "geo-evidence-1",
        "method_version": "ground_grid_registration_v1",
        "applicability_id": "opera-dbzh-grid-v1",
    }


def unverified_geolocation() -> dict[str, Any]:
    return {
        "status": "unverified",
        "reason_codes": ["geolocation_unverified"],
        "evidence_id": None,
        "method_version": None,
        "applicability_id": None,
    }


def valid_polygon() -> dict[str, Any]:
    return {
        "type": "MultiPolygon",
        "coordinates": [
            [
                [
                    [-1.0, 50.0],
                    [-0.9, 50.0],
                    [-0.9, 50.1],
                    [-1.0, 50.1],
                    [-1.0, 50.0],
                ]
            ]
        ],
    }


def available_geometry() -> dict[str, Any]:
    return {
        "status": "available",
        "reason_codes": [],
        "geometry": valid_polygon(),
        "provenance": "grid_contour",
        "simplification_tolerance_m": 200.0,
    }


def unavailable_geometry() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason_codes": ["invalid_geometry"],
        "geometry": None,
        "provenance": "grid_contour",
        "simplification_tolerance_m": 0.0,
    }


def frame_dict(frame_id: str, valid_at: str) -> dict[str, Any]:
    return {
        "frame_id": frame_id,
        "content_id": f"content-{frame_id}",
        "product_id": "opera-dbzh",
        "decoder_version": "odim-v1",
        "grid_id": "opera-grid-v1",
        "valid_at": valid_at,
        "received_at": valid_at,
        "acquisition_window": {"start_at": valid_at, "end_at": valid_at},
        "reference_at": valid_at,
    }


def source_dict() -> dict[str, Any]:
    return {
        "source_id": "opera-dbzh",
        "status": "available",
        "reason_codes": [],
        "frames": [
            frame_dict("frame-1", "2026-09-05T11:40:00Z"),
            frame_dict("frame-2", "2026-09-05T11:50:00Z"),
            frame_dict("frame-3", "2026-09-05T12:00:00Z"),
        ],
        "gaps": [],
        "attribution": "OPERA",
        "coverage": available_support("analysis_domain"),
        "geolocation": validated_geolocation(),
    }


def patch_dict(direction: str, index: int) -> dict[str, Any]:
    return {
        "direction": direction,
        "center_column": 10 + 3 * index,
        "center_row": 20 + 3 * index,
        "status": "available",
        "reason_codes": [],
        "support_fraction": 1.0,
        "ncc": 0.9,
        "competing_peak_margin": 0.2,
        "dx_cells": 1.0 if direction == "forward" else -1.0,
        "dy_cells": 0.0,
        "refinement": "quadratic",
    }


def pair_dict(from_frame_id: str, to_frame_id: str) -> dict[str, Any]:
    return {
        "from_frame_id": from_frame_id,
        "to_frame_id": to_frame_id,
        "elapsed_seconds": 600.0,
        "status": "available",
        "reason_codes": [],
        "patches": [
            patch_dict("forward", 0),
            patch_dict("forward", 1),
            patch_dict("reverse", 0),
            patch_dict("reverse", 1),
        ],
        "forward_dx_cells": 1.0,
        "forward_dy_cells": 0.0,
        "patch_disagreement_cells": 0.1,
        "reverse_residual_cells": 0.1,
        "next_observation_residual_cells": None,
        "common_support_iou": 0.8,
        "area_ratio": 1.0,
        "plausible_parent_count": 1,
        "plausible_child_count": 1,
        "lineage_complete": True,
    }


def accepted_radar_feature_dict() -> dict[str, Any]:
    first_pair = pair_dict("frame-1", "frame-2")
    first_pair["next_observation_residual_cells"] = 0.2
    return {
        "feature_id": "radar-feature-1",
        "source_id": "opera-dbzh",
        "family": "radar_echo",
        "definition": {
            "quantity": "reflectivity",
            "operator": "gte",
            "threshold": 5.0,
            "unit": "dBZ",
        },
        "reference_at": "2026-09-05T12:00:00Z",
        "reference_frame_id": "frame-3",
        "frame_ids": ["frame-1", "frame-2", "frame-3"],
        "display_geometry": available_geometry(),
        "trail": [
            {"frame_id": "frame-1", "observed_at": "2026-09-05T11:40:00Z", "center": [-0.98, 50.04]},
            {"frame_id": "frame-2", "observed_at": "2026-09-05T11:50:00Z", "center": [-0.96, 50.04]},
            {"frame_id": "frame-3", "observed_at": "2026-09-05T12:00:00Z", "center": [-0.94, 50.04]},
        ],
        "observations": [],
        "lightning_evidence": {
            "status": "unavailable",
            "reason_codes": ["missing_source"],
            "source_id": None,
            "frame_ids": [],
            "evaluated_window": None,
            "reported_detection_count": None,
            "emitted_marker_count": 0,
            "evaluation_complete": False,
        },
        "coverage": available_support(),
        "geolocation": validated_geolocation(),
        "motion": {
            "status": "accepted",
            "reason_codes": [],
            "ground_speed_kt": 20.0,
            "bearing_deg_true": 90.0,
            "velocity_reference_point": [-0.94, 50.04],
            "velocity_method": "inverse_aeqd_geodesic_1s",
            "pair_diagnostics": [first_pair, pair_dict("frame-2", "frame-3")],
            "fit_rms_residual_cells": 0.25,
        },
        "projection_end_at": "2026-09-05T12:15:00Z",
        "projections": [
            {
                "at": at,
                "status": "available",
                "reason_codes": [],
                "display_geometry": available_geometry(),
            }
            for at in [
                "2026-09-05T12:05:00Z",
                "2026-09-05T12:10:00Z",
                "2026-09-05T12:15:00Z",
            ]
        ],
        "route_rows": [],
        "planned_overlap": {
            "status": "unavailable",
            "reason_codes": ["invalid_planned_timing"],
            "method": "relative_segment_contour_intersection",
            "planned_time_method": "distance_proportional_planned",
            "evaluated_interval": None,
            "intervals": [],
            "complete": False,
        },
        "reason_codes": [],
    }


def analysis_domain_dict() -> dict[str, Any]:
    return {
        "center": [-0.95, 50.05],
        "crs": "+proj=aeqd +lat_0=50.05 +lon_0=-0.95 +datum=WGS84 +units=m +no_defs",
        "cell_size_m": 2000.0,
        "width_cells": 100,
        "height_cells": 100,
        "origin_x_m": -100000.0,
        "origin_y_m": -100000.0,
        "bounds": [-2.0, 49.0, 1.0, 51.0],
        "reason_codes": [],
    }


def completeness_dict(*, available: bool = False) -> list[dict[str, Any]]:
    counts = {
        "regions": 1,
        "input_frames": 3,
        "small_detections": 0,
        "candidates": 1,
        "features": 1,
        "geometry": 4,
        "associations": 0,
        "lightning": 0,
        "legs": 0,
        "route_rows": 0,
        "overlap_intervals": 0,
    }
    return [
        {
            "category": category,
            "status": "complete" if available else "not_evaluated",
            "reason_codes": [] if available else ["not_evaluated"],
            "considered_count": counts[category] if available else None,
            "emitted_count": counts[category] if available else 0,
            "omitted_count": 0 if available else None,
        }
        for category in COMPLETENESS_CATEGORIES
    ]


def unavailable_motion_dict(*, revision: int = 1) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "unavailable",
        "reason_codes": ["insufficient_history"],
        "revision": revision,
        "run_id": None,
        "route_geometry_id": "route-geometry-1",
        "planned_timing_id": None,
        "computed_at": "2026-09-05T12:00:01Z",
        "cutoff_at": "2026-09-05T12:00:00Z",
        "expires_at": None,
        "method_version": "masked_contour_translation_v1",
        "policy_version": "observed_motion_policy_v1",
        "analysis_domain": None,
        "sources": [],
        "features": [],
        "associations": [],
        "lightning": [],
        "projection_times": [],
        "completeness": completeness_dict(),
    }


def disabled_motion_dict() -> dict[str, Any]:
    result = unavailable_motion_dict()
    result["status"] = "disabled"
    result["reason_codes"] = ["observed_disabled"]
    return result


def available_motion_dict() -> dict[str, Any]:
    result = unavailable_motion_dict()
    result.update(
        {
            "status": "available",
            "reason_codes": [],
            "run_id": "run-1",
            "expires_at": "2026-09-05T12:15:00Z",
            "analysis_domain": analysis_domain_dict(),
            "sources": [source_dict()],
            "features": [accepted_radar_feature_dict()],
            "projection_times": [
                "2026-09-05T12:05:00Z",
                "2026-09-05T12:10:00Z",
                "2026-09-05T12:15:00Z",
            ],
            "completeness": completeness_dict(available=True),
        }
    )
    return deepcopy(result)


def motion_with_cloud_association(*, validated: bool = True) -> dict[str, Any]:
    result = available_motion_dict()
    cloud_source = deepcopy(result["sources"][0])
    cloud_source["source_id"] = "eumetsat-ctth"
    cloud_source["geolocation"] = validated_geolocation() if validated else unverified_geolocation()
    for index, frame in enumerate(cloud_source["frames"], start=1):
        frame["frame_id"] = f"cloud-frame-{index}"
        frame["content_id"] = f"cloud-content-{index}"
        frame["product_id"] = "mtg-fci-ctth"

    cloud = deepcopy(result["features"][0])
    cloud.update(
        {
            "feature_id": "cloud-feature-1",
            "source_id": "eumetsat-ctth",
            "family": "high_cloud_top",
            "definition": {
                "quantity": "geometric_cloud_top_height",
                "operator": "gte",
                "threshold": 4572.0,
                "unit": "m_msl",
            },
            "reference_frame_id": "cloud-frame-3",
            "frame_ids": ["cloud-frame-1", "cloud-frame-2", "cloud-frame-3"],
            "geolocation": deepcopy(cloud_source["geolocation"]),
            "motion": {
                "status": "unavailable",
                "reason_codes": ["geolocation_unverified"] if not validated else ["insufficient_patches"],
                "ground_speed_kt": None,
                "bearing_deg_true": None,
                "velocity_reference_point": None,
                "velocity_method": None,
                "pair_diagnostics": [],
                "fit_rms_residual_cells": None,
            },
            "projection_end_at": None,
        }
    )
    for index, trail in enumerate(cloud["trail"], start=1):
        trail["frame_id"] = f"cloud-frame-{index}"
    for projection in cloud["projections"]:
        projection["status"] = "unavailable"
        projection["reason_codes"] = ["insufficient_patches"]
        projection["display_geometry"] = unavailable_geometry()

    result["sources"].append(cloud_source)
    result["features"].append(cloud)
    result["associations"] = [
        {
            "association_id": "association-1",
            "radar_feature_id": "radar-feature-1",
            "cloud_feature_id": "cloud-feature-1",
            "status": "available",
            "reason_codes": [],
            "relation": "overlap",
            "comparison_at": "2026-09-05T12:00:00Z",
            "alignment_method": "simultaneous_observed",
            "radar_frame_ids": ["frame-3"],
            "cloud_frame_ids": ["cloud-frame-3"],
            "radar_window": {
                "start_at": "2026-09-05T12:00:00Z",
                "end_at": "2026-09-05T12:00:00Z",
            },
            "cloud_window": {
                "start_at": "2026-09-05T12:00:00Z",
                "end_at": "2026-09-05T12:00:00Z",
            },
            "intersection_area_km2": 10.0,
            "radar_overlap_fraction": 0.5,
            "cloud_overlap_fraction": 0.5,
            "edge_distance_nm": 0.0,
            "measurement_basis": "analysis_grid_contours",
        }
    ]
    return result


def lightning_dict(detection_id: str = "flash-1") -> dict[str, Any]:
    return {
        "detection_id": detection_id,
        "source_id": "eumetsat-li",
        "frame_id": "li-frame-1",
        "position": [-0.95, 50.05],
        "time_precision": "window_only",
        "event_at": None,
        "acquisition_window": {
            "start_at": "2026-09-05T11:50:00Z",
            "end_at": "2026-09-05T12:00:00Z",
        },
        "reason_codes": ["window_only_time"],
        "association_status": "unavailable",
        "association_reason_codes": ["window_only_time"],
        "associated_feature_ids": None,
    }
