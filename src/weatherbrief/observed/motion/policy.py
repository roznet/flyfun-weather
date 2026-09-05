"""Versioned engineering bounds for the observed-motion experiment."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MotionPolicy:
    policy_version: str = "observed_motion_policy_v1"
    max_primary_frames_per_source: int = 4
    min_primary_valid_times: int = 3
    max_history_span_minutes: int = 45
    max_dbzh_adjacent_gap_minutes: int = 10
    max_ctth_adjacent_gap_minutes: int = 20
    max_reference_age_minutes: int = 20
    analysis_cell_size_m: int = 2_000
    supported_radar_spacing_m: int = 2_000
    max_domain_cells: int = 262_144
    max_domain_dimension_cells: int = 1_024
    max_distance_from_projection_center_km: int = 1_000
    max_ctth_decode_rows: int = 46
    max_ctth_decode_cells: int = 262_144
    max_radar_window_cells: int = 1_048_576
    max_radar_window_dimension_cells: int = 2_048
    radar_threshold_dbz: float = 5.0
    cloud_threshold_m_msl: float = 4_572.0
    min_track_cells: int = 9
    max_candidates_per_source: int = 32
    max_forward_patches_per_source_frame_pair: int = 64
    max_reverse_patches_per_source_frame_pair: int = 64
    template_size_cells: int = 31
    min_template_support_fraction: float = 0.80
    min_template_support_samples: int = 64
    required_usable_patches_per_feature_pair: int = 2
    max_search_speed_mps: float = 60.0
    min_ncc: float = 0.80
    competing_peak_neighborhood_cells: int = 2
    min_competing_peak_margin: float = 0.10
    min_common_support_iou: float = 0.50
    lineage_ambiguity_overlap_fraction: float = 0.20
    max_reverse_error_cell_diagonals: float = 1.0
    max_next_observation_residual_cell_diagonals: float = 2.0
    min_common_support_area_ratio: float = 2.0 / 3.0
    max_common_support_area_ratio: float = 3.0 / 2.0
    projection_horizon_minutes: int = 15
    max_projection_times: int = 3
    projection_tick_minutes: int = 5
    route_capture_corridor_nm: float = 20.0
    max_route_segment_nm: float = 1.0
    max_route_segments: int = 2_048
    max_concurrent_analyses: int = 1
    compute_budget_seconds: float = 15.0
    max_display_simplification_tolerance_m: float = 1_000.0
    max_features: int = 48
    initial_features_per_family: int = 24
    max_trail_samples_per_feature: int = 4
    max_positions_per_footprint: int = 128
    max_polygon_components_per_footprint: int = 8
    max_holes_per_footprint: int = 8
    max_total_geometry_positions: int = 12_000
    max_associations: int = 128
    max_lightning_records: int = 256
    max_route_rows: int = 1_024
    max_overlap_intervals: int = 256
    max_serialized_bytes: int = 1_048_576


DEFAULT_POLICY = MotionPolicy()


__all__ = ["DEFAULT_POLICY", "MotionPolicy"]
