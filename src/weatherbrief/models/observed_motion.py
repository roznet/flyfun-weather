"""Validated version-1 wire records for experimental observed motion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
from typing import Annotated, Any, Literal, Self

from pyproj import CRS
from pyproj.exceptions import CRSError
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from shapely.geometry import shape

from weatherbrief.observed.motion.policy import DEFAULT_POLICY


MAX_SAFE_INTEGER = 9_007_199_254_740_991
METHOD_VERSION = "masked_contour_translation_v1"
COMPLETENESS_CATEGORIES = (
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
)
UTC_WIRE_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z"
)
WGS84_ELLIPSOID = CRS.from_epsg(4326).ellipsoid


def _parse_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTC datetime must be timezone-aware")
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        if UTC_WIRE_PATTERN.fullmatch(value) is None:
            raise ValueError("UTC wire datetime must use full YYYY-MM-DDTHH:MM:SS[.ffffff]Z form")
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError("invalid UTC datetime") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("UTC wire datetime must be timezone-aware")
        return parsed.astimezone(timezone.utc)
    raise ValueError("UTC datetime must be an aware datetime or Z string")


UTCDateTime = Annotated[datetime, BeforeValidator(_parse_utc)]
Identifier = Annotated[StrictStr, Field(min_length=1)]
FiniteNumber = StrictFloat
SafeInteger = Annotated[StrictInt, Field(ge=0, le=MAX_SAFE_INTEGER)]
PositiveSafeInteger = Annotated[StrictInt, Field(ge=1, le=MAX_SAFE_INTEGER)]
Fraction = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
Point = tuple[
    Annotated[StrictFloat, Field(ge=-180.0, le=180.0)],
    Annotated[StrictFloat, Field(ge=-90.0, le=90.0)],
]
Reasons = list[Identifier]
Availability = Literal["available", "unavailable"]


def _availability(status: str, reasons: Reasons) -> None:
    if status == "unavailable" and not reasons:
        raise ValueError("unavailable records require reason_codes")


def _strictly_sorted(values: list[datetime]) -> bool:
    return all(left < right for left, right in zip(values, values[1:]))


def _minute_aligned(value: datetime) -> bool:
    return value.second == 0 and value.microsecond == 0


class MotionModel(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)


class Interval(MotionModel):
    start_at: UTCDateTime
    end_at: UTCDateTime

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.start_at > self.end_at:
            raise ValueError("interval start_at must not follow end_at")
        return self


class MultiPolygon(MotionModel):
    type: Literal["MultiPolygon"]
    coordinates: list[list[list[Point]]]

    @model_validator(mode="after")
    def validate_geometry(self) -> Self:
        if not self.coordinates:
            raise ValueError("available geometry cannot be empty")
        if len(self.coordinates) > DEFAULT_POLICY.max_polygon_components_per_footprint:
            raise ValueError("geometry component limit exceeded")
        holes = sum(max(0, len(polygon) - 1) for polygon in self.coordinates)
        if holes > DEFAULT_POLICY.max_holes_per_footprint:
            raise ValueError("geometry hole limit exceeded")
        positions = 0
        longitudes: list[float] = []
        for polygon in self.coordinates:
            if not polygon:
                raise ValueError("polygon requires an exterior ring")
            for ring in polygon:
                positions += len(ring)
                if len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError("rings must be closed with at least four positions")
                longitudes.extend(point[0] for point in ring)
        if positions > DEFAULT_POLICY.max_positions_per_footprint:
            raise ValueError("geometry position limit exceeded")
        if longitudes and max(longitudes) - min(longitudes) > 180.0:
            raise ValueError("wrapping geometry is unsupported")
        geometry = shape({"type": "MultiPolygon", "coordinates": self.coordinates})
        if geometry.is_empty or geometry.geom_type != "MultiPolygon" or not geometry.is_valid:
            raise ValueError("invalid polygon topology")
        return self

    @property
    def position_count(self) -> int:
        return sum(len(ring) for polygon in self.coordinates for ring in polygon)


class AnalysisDomain(MotionModel):
    center: Point
    crs: Identifier
    cell_size_m: Annotated[StrictFloat, Field(ge=2000.0, le=2000.0)]
    width_cells: PositiveSafeInteger
    height_cells: PositiveSafeInteger
    origin_x_m: FiniteNumber
    origin_y_m: FiniteNumber
    bounds: tuple[
        Annotated[StrictFloat, Field(ge=-180.0, le=180.0)],
        Annotated[StrictFloat, Field(ge=-90.0, le=90.0)],
        Annotated[StrictFloat, Field(ge=-180.0, le=180.0)],
        Annotated[StrictFloat, Field(ge=-90.0, le=90.0)],
    ]
    reason_codes: Reasons

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        west, south, east, north = self.bounds
        if west >= east or south >= north:
            raise ValueError("analysis bounds must be ordered and non-wrapping")
        if self.width_cells > DEFAULT_POLICY.max_domain_dimension_cells:
            raise ValueError("analysis width limit exceeded")
        if self.height_cells > DEFAULT_POLICY.max_domain_dimension_cells:
            raise ValueError("analysis height limit exceeded")
        if self.width_cells * self.height_cells > DEFAULT_POLICY.max_domain_cells:
            raise ValueError("analysis cell limit exceeded")
        x_limits = (self.origin_x_m, self.origin_x_m + self.width_cells * self.cell_size_m)
        y_limits = (self.origin_y_m, self.origin_y_m + self.height_cells * self.cell_size_m)
        max_radius_m = DEFAULT_POLICY.max_distance_from_projection_center_km * 1000.0
        if any(math.hypot(x, y) > max_radius_m for x in x_limits for y in y_limits):
            raise ValueError("analysis domain exceeds the projection-centre radius")
        if self.crs.startswith("+"):
            allowed_proj_keys = {
                "proj",
                "lat_0",
                "lon_0",
                "x_0",
                "y_0",
                "datum",
                "ellps",
                "units",
                "no_defs",
                "type",
            }
            keys: list[str] = []
            for token in self.crs.split():
                if not token.startswith("+"):
                    raise ValueError("analysis CRS contains an invalid PROJ token")
                key = token[1:].split("=", 1)[0]
                if not key or key not in allowed_proj_keys:
                    raise ValueError("analysis CRS contains an unsupported PROJ token")
                keys.append(key)
            if len(keys) != len(set(keys)):
                raise ValueError("analysis CRS contains duplicate PROJ parameters")
        try:
            crs = CRS.from_user_input(self.crs)
        except CRSError as exc:
            raise ValueError("analysis CRS must be parseable") from exc
        operation = crs.coordinate_operation
        datum_name = crs.datum.name if crs.datum is not None else ""
        ellipsoid = crs.ellipsoid
        if (
            not crs.is_projected
            or operation is None
            or operation.method_name != "Azimuthal Equidistant"
            or not datum_name.startswith("World Geodetic System 1984")
            or ellipsoid is None
            or not math.isclose(
                ellipsoid.semi_major_metre,
                WGS84_ELLIPSOID.semi_major_metre,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
            or not math.isclose(
                ellipsoid.inverse_flattening,
                WGS84_ELLIPSOID.inverse_flattening,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or any(axis.unit_name.lower() not in {"metre", "meter"} for axis in crs.axis_info)
        ):
            raise ValueError("analysis CRS must be a WGS84 AEQD definition")
        parameters = {parameter.code: parameter.value for parameter in operation.params}
        longitude, latitude = self.center
        if (
            not math.isclose(parameters.get("8801", math.inf), latitude, abs_tol=1e-12)
            or not math.isclose(parameters.get("8802", math.inf), longitude, abs_tol=1e-12)
            or not math.isclose(parameters.get("8806", 0.0), 0.0, abs_tol=1e-12)
            or not math.isclose(parameters.get("8807", 0.0), 0.0, abs_tol=1e-12)
        ):
            raise ValueError("analysis CRS must be centred on the declared domain center")
        return self


class SupportRecord(MotionModel):
    status: Availability
    reason_codes: Reasons
    scope: Literal["analysis_domain", "feature_contour", "match_template", "point_detections"]
    known_cells: SafeInteger | None
    total_cells: SafeInteger | None
    known_fraction: Fraction | None

    @model_validator(mode="after")
    def validate_support(self) -> Self:
        _availability(self.status, self.reason_codes)
        if self.known_cells is not None and self.total_cells is not None:
            if self.known_cells > self.total_cells:
                raise ValueError("known_cells cannot exceed total_cells")
        if self.status == "unavailable" or self.scope == "point_detections" or self.total_cells == 0:
            if self.known_fraction is not None:
                raise ValueError("known_fraction must be null for unavailable, zero, or point support")
        elif self.known_cells is not None and self.total_cells is not None:
            expected = self.known_cells / self.total_cells
            if self.known_fraction is None or not math.isclose(self.known_fraction, expected, abs_tol=1e-12):
                raise ValueError("known_fraction must match the declared cell counts")
        return self


class GeolocationRecord(MotionModel):
    status: Literal["validated", "unverified", "failed"]
    reason_codes: Reasons
    evidence_id: Identifier | None
    method_version: Identifier | None
    applicability_id: Identifier | None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.status == "validated":
            if self.reason_codes:
                raise ValueError("validated geolocation cannot carry failure reasons")
            if None in (self.evidence_id, self.method_version, self.applicability_id):
                raise ValueError("validated geolocation requires complete evidence identity")
        elif not self.reason_codes:
            raise ValueError("unverified or failed geolocation requires reason_codes")
        return self


class FrameRecord(MotionModel):
    frame_id: Identifier
    content_id: Identifier
    product_id: Identifier
    decoder_version: Identifier
    grid_id: Identifier
    valid_at: UTCDateTime
    received_at: UTCDateTime
    acquisition_window: Interval
    reference_at: UTCDateTime

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.reference_at != self.valid_at:
            raise ValueError("reference_at must equal canonical valid_at")
        if self.acquisition_window.end_at > self.received_at:
            raise ValueError("acquisition must end no later than receipt")
        if self.valid_at > self.received_at:
            raise ValueError("valid_at must not follow receipt")
        return self


class FrameGap(MotionModel):
    from_frame_id: Identifier
    to_frame_id: Identifier
    elapsed_seconds: Annotated[StrictFloat, Field(gt=0.0)]
    missing_nominal_publications: SafeInteger
    reason_codes: Reasons


class SourceRecord(MotionModel):
    source_id: Identifier
    status: Availability
    reason_codes: Reasons
    frames: Annotated[list[FrameRecord], Field(max_length=DEFAULT_POLICY.max_primary_frames_per_source)]
    gaps: list[FrameGap]
    attribution: StrictStr
    coverage: SupportRecord
    geolocation: GeolocationRecord

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        _availability(self.status, self.reason_codes)
        frame_ids = [frame.frame_id for frame in self.frames]
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("frame_id must be unique within a source")
        times = [frame.valid_at for frame in self.frames]
        if times and not _strictly_sorted(times):
            raise ValueError("source frames must be ordered by distinct valid_at")
        frame_index = {frame.frame_id: index for index, frame in enumerate(self.frames)}
        previous_index = -1
        for gap in self.gaps:
            if gap.from_frame_id not in frame_index or gap.to_frame_id not in frame_index:
                raise ValueError("frame gap references must resolve within the source")
            start = frame_index[gap.from_frame_id]
            end = frame_index[gap.to_frame_id]
            if start >= end or start < previous_index:
                raise ValueError("frame gaps must follow chronological frame order")
            previous_index = start
        return self


class ContourDefinition(MotionModel):
    quantity: Literal["reflectivity", "geometric_cloud_top_height"]
    operator: Literal["gte"]
    threshold: FiniteNumber
    unit: Literal["dBZ", "m_msl"]

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        valid = (
            self.quantity == "reflectivity"
            and self.unit == "dBZ"
            and self.threshold == DEFAULT_POLICY.radar_threshold_dbz
        ) or (
            self.quantity == "geometric_cloud_top_height"
            and self.unit == "m_msl"
            and self.threshold == DEFAULT_POLICY.cloud_threshold_m_msl
        )
        if not valid:
            raise ValueError("unsupported contour definition")
        return self


class GeometryRecord(MotionModel):
    status: Availability
    reason_codes: Reasons
    geometry: MultiPolygon | None
    provenance: Literal["grid_contour"]
    simplification_tolerance_m: Annotated[
        StrictFloat, Field(ge=0.0, le=DEFAULT_POLICY.max_display_simplification_tolerance_m)
    ]

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        _availability(self.status, self.reason_codes)
        if self.status == "available" and self.geometry is None:
            raise ValueError("available geometry requires a polygon")
        if self.status == "unavailable" and self.geometry is not None:
            raise ValueError("unavailable geometry must not carry a polygon")
        return self


class TrailSample(MotionModel):
    frame_id: Identifier
    observed_at: UTCDateTime
    center: Point


class ScalarObservation(MotionModel):
    kind: Literal["reflectivity_max", "rain_rate_max", "cloud_top_max"]
    status: Availability
    reason_codes: Reasons
    value: FiniteNumber | None
    unit: Literal["dBZ", "mm_h", "m_msl"]
    source_id: Identifier | None
    frame_id: Identifier | None
    observed_at: UTCDateTime | None
    comparison_at: UTCDateTime | None
    acquisition_window: Interval | None
    alignment_method: Literal["observed", "in_history_translation"] | None
    sample_id: Identifier | None
    sample_position: Point | None
    paired_temperature_k: FiniteNumber | None
    coverage: SupportRecord

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        _availability(self.status, self.reason_codes)
        expected_units = {
            "reflectivity_max": "dBZ",
            "rain_rate_max": "mm_h",
            "cloud_top_max": "m_msl",
        }
        if self.unit != expected_units[self.kind]:
            raise ValueError("scalar observation unit does not match kind")
        required = (
            self.value,
            self.source_id,
            self.frame_id,
            self.observed_at,
            self.comparison_at,
            self.acquisition_window,
            self.alignment_method,
            self.sample_id,
        )
        if self.status == "available" and any(item is None for item in required):
            raise ValueError("available scalar observation requires measurement provenance")
        if self.status == "unavailable" and self.value is not None:
            raise ValueError("unavailable scalar observation must have null value")
        if self.kind == "rain_rate_max" and self.value is not None and self.value < 0:
            raise ValueError("rain rate cannot be negative")
        if self.kind != "cloud_top_max" and self.paired_temperature_k is not None:
            raise ValueError("paired temperature is only valid for cloud-top samples")
        return self


class PatchDiagnostics(MotionModel):
    direction: Literal["forward", "reverse"]
    center_column: SafeInteger
    center_row: SafeInteger
    status: Availability
    reason_codes: Reasons
    support_fraction: Fraction | None
    ncc: Annotated[StrictFloat, Field(ge=-1.0, le=1.0)] | None
    competing_peak_margin: FiniteNumber | None
    dx_cells: FiniteNumber | None
    dy_cells: FiniteNumber | None
    refinement: Literal["quadratic", "integer"] | None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        _availability(self.status, self.reason_codes)
        values = (self.support_fraction, self.ncc, self.competing_peak_margin, self.dx_cells, self.dy_cells, self.refinement)
        if self.status == "available" and any(value is None for value in values):
            raise ValueError("available patch requires complete diagnostics")
        if self.status == "available":
            if self.support_fraction < DEFAULT_POLICY.min_template_support_fraction:
                raise ValueError("available patch has insufficient template support")
            if self.ncc < DEFAULT_POLICY.min_ncc:
                raise ValueError("available patch has insufficient normalized cross-correlation")
            if self.competing_peak_margin < DEFAULT_POLICY.min_competing_peak_margin:
                raise ValueError("available patch has an ambiguous competing peak")
        return self


class PairDiagnostics(MotionModel):
    from_frame_id: Identifier
    to_frame_id: Identifier
    elapsed_seconds: Annotated[StrictFloat, Field(gt=0.0)]
    status: Availability
    reason_codes: Reasons
    patches: Annotated[list[PatchDiagnostics], Field(max_length=4)]
    forward_dx_cells: FiniteNumber | None
    forward_dy_cells: FiniteNumber | None
    patch_disagreement_cells: Annotated[StrictFloat, Field(ge=0.0)] | None
    reverse_residual_cells: Annotated[StrictFloat, Field(ge=0.0)] | None
    next_observation_residual_cells: Annotated[StrictFloat, Field(ge=0.0)] | None
    common_support_iou: Fraction | None
    area_ratio: Annotated[StrictFloat, Field(gt=0.0)] | None
    plausible_parent_count: SafeInteger | None
    plausible_child_count: SafeInteger | None
    lineage_complete: StrictBool

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        _availability(self.status, self.reason_codes)
        if self.status == "available":
            available_patches = [patch for patch in self.patches if patch.status == "available"]
            directions = [patch.direction for patch in available_patches]
            if directions.count("forward") != 2 or directions.count("reverse") != 2:
                raise ValueError("available pair requires two forward and two reverse patches")
            required = (
                self.forward_dx_cells,
                self.forward_dy_cells,
                self.patch_disagreement_cells,
                self.reverse_residual_cells,
                self.common_support_iou,
                self.area_ratio,
                self.plausible_parent_count,
                self.plausible_child_count,
            )
            if any(value is None for value in required):
                raise ValueError("available pair requires complete diagnostics")
            cell_diagonal = math.sqrt(2.0)
            if self.patch_disagreement_cells > cell_diagonal:
                raise ValueError("accepted patch translations disagree by more than one cell diagonal")
            if self.reverse_residual_cells > DEFAULT_POLICY.max_reverse_error_cell_diagonals * cell_diagonal:
                raise ValueError("accepted pair exceeds the reverse-error policy")
            if (
                self.next_observation_residual_cells is not None
                and self.next_observation_residual_cells
                > DEFAULT_POLICY.max_next_observation_residual_cell_diagonals * cell_diagonal
            ):
                raise ValueError("accepted pair exceeds the next-observation residual policy")
            if self.common_support_iou < DEFAULT_POLICY.min_common_support_iou:
                raise ValueError("accepted pair has insufficient common-support overlap")
            if not (
                DEFAULT_POLICY.min_common_support_area_ratio
                <= self.area_ratio
                <= DEFAULT_POLICY.max_common_support_area_ratio
            ):
                raise ValueError("accepted pair has an unsupported common-support area ratio")
            if (
                not self.lineage_complete
                or self.plausible_parent_count != 1
                or self.plausible_child_count != 1
            ):
                raise ValueError("accepted pair requires complete unambiguous lineage")
            for direction in ("forward", "reverse"):
                directional = [patch for patch in available_patches if patch.direction == direction]
                separation = math.hypot(
                    directional[0].center_column - directional[1].center_column,
                    directional[0].center_row - directional[1].center_row,
                )
                if separation < DEFAULT_POLICY.competing_peak_neighborhood_cells:
                    raise ValueError("accepted pair patch centres must be spatially separated")
            forward_patches = [patch for patch in available_patches if patch.direction == "forward"]
            mean_dx = sum(patch.dx_cells for patch in forward_patches) / len(forward_patches)
            mean_dy = sum(patch.dy_cells for patch in forward_patches) / len(forward_patches)
            if not math.isclose(self.forward_dx_cells, mean_dx, abs_tol=1e-12) or not math.isclose(
                self.forward_dy_cells, mean_dy, abs_tol=1e-12
            ):
                raise ValueError("forward translation must equal the mean accepted patch translation")
            max_displacement_cells = (
                DEFAULT_POLICY.max_search_speed_mps
                * self.elapsed_seconds
                / DEFAULT_POLICY.analysis_cell_size_m
            )
            if any(
                math.hypot(patch.dx_cells, patch.dy_cells) >= max_displacement_cells
                for patch in available_patches
            ):
                raise ValueError("accepted patch translation reaches the search-speed boundary")
        return self


class MotionRecord(MotionModel):
    status: Literal["accepted", "unavailable"]
    reason_codes: Reasons
    ground_speed_kt: Annotated[StrictFloat, Field(ge=0.0)] | None
    bearing_deg_true: Annotated[StrictFloat, Field(ge=0.0, lt=360.0)] | None
    velocity_reference_point: Point | None
    velocity_method: Literal["inverse_aeqd_geodesic_1s"] | None
    pair_diagnostics: Annotated[list[PairDiagnostics], Field(max_length=3)]
    fit_rms_residual_cells: Annotated[StrictFloat, Field(ge=0.0)] | None

    @model_validator(mode="after")
    def validate_motion(self) -> Self:
        if self.status == "unavailable":
            if not self.reason_codes:
                raise ValueError("unavailable motion requires reason_codes")
            if any(value is not None for value in (
                self.ground_speed_kt,
                self.bearing_deg_true,
                self.velocity_reference_point,
                self.velocity_method,
            )):
                raise ValueError("unavailable motion cannot publish a velocity")
            return self
        if self.ground_speed_kt is None or self.velocity_reference_point is None or self.velocity_method is None:
            raise ValueError("accepted motion requires a velocity and reference point")
        if self.ground_speed_kt == 0 and self.bearing_deg_true is not None:
            raise ValueError("zero movement has no bearing")
        if self.ground_speed_kt > 0 and self.bearing_deg_true is None:
            raise ValueError("nonzero movement requires a bearing")
        return self


class ProjectionRecord(MotionModel):
    at: UTCDateTime
    status: Availability
    reason_codes: Reasons
    display_geometry: GeometryRecord

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        _availability(self.status, self.reason_codes)
        if self.status != self.display_geometry.status:
            raise ValueError("projection and display geometry availability must agree")
        return self


class FeatureLightningEvidence(MotionModel):
    status: Availability
    reason_codes: Reasons
    source_id: Identifier | None
    frame_ids: list[Identifier]
    evaluated_window: Interval | None
    reported_detection_count: SafeInteger | None
    emitted_marker_count: SafeInteger
    evaluation_complete: StrictBool

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        _availability(self.status, self.reason_codes)
        if self.status == "available":
            if self.source_id is None or self.evaluated_window is None or self.reported_detection_count is None:
                raise ValueError("available lightning evidence requires evaluated provenance and count")
            if not self.frame_ids:
                raise ValueError("available lightning evidence requires inspected source frames")
            if self.reported_detection_count == 0 and not self.evaluation_complete:
                raise ValueError("zero lightning detections require complete evaluation")
            if self.reported_detection_count > 0 and not self.evaluation_complete and not self.reason_codes:
                raise ValueError("partial positive lightning evidence requires a lower-bound reason")
            if self.emitted_marker_count > self.reported_detection_count:
                raise ValueError("emitted lightning markers cannot exceed reported detections")
        else:
            if self.reported_detection_count is not None:
                raise ValueError("unavailable lightning evidence must not report a detection count")
            if self.emitted_marker_count != 0:
                raise ValueError("unavailable lightning evidence cannot emit markers")
        return self


class AssociationRecord(MotionModel):
    association_id: Identifier
    radar_feature_id: Identifier
    cloud_feature_id: Identifier
    status: Availability
    reason_codes: Reasons
    relation: Literal["overlap", "nearby"] | None
    comparison_at: UTCDateTime | None
    alignment_method: Literal["simultaneous_observed", "in_history_translation"] | None
    radar_frame_ids: list[Identifier]
    cloud_frame_ids: list[Identifier]
    radar_window: Interval | None
    cloud_window: Interval | None
    intersection_area_km2: Annotated[StrictFloat, Field(ge=0.0)] | None
    radar_overlap_fraction: Fraction | None
    cloud_overlap_fraction: Fraction | None
    edge_distance_nm: Annotated[StrictFloat, Field(ge=0.0)] | None
    measurement_basis: Literal["analysis_grid_contours"]

    @model_validator(mode="after")
    def validate_association(self) -> Self:
        _availability(self.status, self.reason_codes)
        measurements = (
            self.relation,
            self.comparison_at,
            self.alignment_method,
            self.radar_window,
            self.cloud_window,
            self.intersection_area_km2,
            self.radar_overlap_fraction,
            self.cloud_overlap_fraction,
            self.edge_distance_nm,
        )
        if self.status == "available" and any(value is None for value in measurements):
            raise ValueError("available association requires complete comparison measurements")
        if self.status == "available":
            assert self.comparison_at is not None
            assert self.radar_window is not None
            assert self.cloud_window is not None
            assert self.intersection_area_km2 is not None
            if not (
                self.radar_window.start_at <= self.comparison_at <= self.radar_window.end_at
                and self.cloud_window.start_at <= self.comparison_at <= self.cloud_window.end_at
            ):
                raise ValueError("association comparison_at must lie inside both declared windows")
            if self.relation == "overlap" and self.intersection_area_km2 <= 0:
                raise ValueError("overlap relation requires nonzero intersection area")
            if self.relation == "nearby" and self.intersection_area_km2 != 0:
                raise ValueError("nearby relation cannot carry an intersecting area")
        if self.status == "unavailable" and any(value is not None for value in (
            self.relation,
            self.comparison_at,
            self.alignment_method,
            self.intersection_area_km2,
            self.radar_overlap_fraction,
            self.cloud_overlap_fraction,
            self.edge_distance_nm,
        )):
            raise ValueError("unavailable association cannot carry comparison measurements")
        return self


class LightningRecord(MotionModel):
    detection_id: Identifier
    source_id: Identifier
    frame_id: Identifier
    position: Point
    time_precision: Literal["individual_time", "window_only"]
    event_at: UTCDateTime | None
    acquisition_window: Interval
    reason_codes: Reasons
    association_status: Availability
    association_reason_codes: Reasons
    associated_feature_ids: list[Identifier] | None

    @model_validator(mode="after")
    def validate_lightning(self) -> Self:
        if self.time_precision == "individual_time":
            if self.event_at is None or not (
                self.acquisition_window.start_at <= self.event_at <= self.acquisition_window.end_at
            ):
                raise ValueError("individual lightning time must lie inside its acquisition window")
        else:
            if self.event_at is not None or not self.reason_codes:
                raise ValueError("window-only lightning requires null event_at and a reason")
            if self.association_status != "unavailable" or self.associated_feature_ids is not None:
                raise ValueError("window-only lightning cannot claim feature association")
        _availability(self.association_status, self.association_reason_codes)
        if self.association_status == "available" and self.associated_feature_ids is None:
            raise ValueError("available association requires a feature-id array")
        if self.association_status == "unavailable" and self.associated_feature_ids is not None:
            raise ValueError("unavailable association requires null feature IDs")
        return self


class RouteRow(MotionModel):
    leg_id: Identifier
    leg_index: SafeInteger
    from_label: StrictStr
    to_label: StrictStr
    at: UTCDateTime
    status: Availability
    reason_codes: Reasons
    distance_nm: Annotated[StrictFloat, Field(ge=0.0)] | None
    closure_kt: FiniteNumber | None
    closure_interval: Interval | None
    relationship: Literal["approaching", "receding", "approximately_unchanged", "intersecting", "unavailable"]
    planned_time_method: Literal["distance_proportional_planned"]
    planned_time_status: Availability
    planned_time_reason_codes: Reasons
    planned_overlap_at_time: StrictBool | None

    @model_validator(mode="after")
    def validate_route_row(self) -> Self:
        _availability(self.status, self.reason_codes)
        _availability(self.planned_time_status, self.planned_time_reason_codes)
        if self.status == "unavailable":
            if (
                self.distance_nm is not None
                or self.closure_kt is not None
                or self.closure_interval is not None
                or self.relationship != "unavailable"
            ):
                raise ValueError("unavailable route row cannot carry a relationship measurement")
        else:
            if self.distance_nm is None or self.relationship == "unavailable":
                raise ValueError("available route row requires distance and relationship")
            if self.relationship == "intersecting":
                if self.distance_nm != 0 or self.closure_kt is not None or self.closure_interval is not None:
                    raise ValueError("intersection requires zero distance and null closure data")
            else:
                if self.distance_nm <= 0 or self.closure_kt is None or self.closure_interval is None:
                    raise ValueError("nonintersecting route row requires distance, closure, and interval")
                duration = (
                    self.closure_interval.end_at - self.closure_interval.start_at
                ).total_seconds()
                if (
                    duration <= 0
                    or duration > 60
                    or not (
                        self.closure_interval.start_at
                        <= self.at
                        <= self.closure_interval.end_at
                    )
                ):
                    raise ValueError("closure interval must contain at and span at most sixty seconds")
                if abs(self.closure_kt) < 1.0:
                    expected_relationship = "approximately_unchanged"
                elif self.closure_kt > 0:
                    expected_relationship = "approaching"
                else:
                    expected_relationship = "receding"
                if self.relationship != expected_relationship:
                    raise ValueError("route relationship must match closure sign and threshold")
        if self.planned_time_status == "available" and self.planned_overlap_at_time is None:
            raise ValueError("available planned-time evaluation requires a boolean result")
        if self.planned_time_status == "unavailable" and self.planned_overlap_at_time is not None:
            raise ValueError("unavailable planned-time evaluation requires null result")
        return self


class OverlapInterval(MotionModel):
    leg_id: Identifier
    leg_index: SafeInteger
    start_at: UTCDateTime
    end_at: UTCDateTime
    contact: Literal["interval", "tangent"]
    approximate: StrictBool

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.start_at > self.end_at:
            raise ValueError("overlap start_at must not follow end_at")
        if self.contact == "tangent" and self.start_at != self.end_at:
            raise ValueError("tangent overlap must be instantaneous")
        if self.contact == "interval" and self.start_at == self.end_at:
            raise ValueError("interval overlap must have nonzero duration")
        if not self.approximate:
            raise ValueError("version-1 overlap intervals are approximate")
        return self


class PlannedOverlapResult(MotionModel):
    status: Availability
    reason_codes: Reasons
    method: Literal["relative_segment_contour_intersection"]
    planned_time_method: Literal["distance_proportional_planned"]
    evaluated_interval: Interval | None
    intervals: list[OverlapInterval]
    complete: StrictBool

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        _availability(self.status, self.reason_codes)
        if self.status == "available":
            if not self.complete or self.evaluated_interval is None:
                raise ValueError("available overlap result must be complete over an interval")
            if any(
                interval.start_at < self.evaluated_interval.start_at
                or interval.end_at > self.evaluated_interval.end_at
                for interval in self.intervals
            ):
                raise ValueError("overlap interval lies outside evaluated_interval")
            boundaries = {
                self.evaluated_interval.start_at,
                self.evaluated_interval.end_at,
            }
            if any(
                endpoint not in boundaries and not _minute_aligned(endpoint)
                for interval in self.intervals
                for endpoint in (interval.start_at, interval.end_at)
            ):
                raise ValueError("overlap display times must be minute-rounded or boundary-clamped")
        elif self.complete or self.intervals:
            raise ValueError("unavailable overlap result must be incomplete with no intervals")
        return self


class FeatureRecord(MotionModel):
    feature_id: Identifier
    source_id: Identifier
    family: Literal["radar_echo", "high_cloud_top"]
    definition: ContourDefinition
    reference_at: UTCDateTime
    reference_frame_id: Identifier
    frame_ids: list[Identifier]
    display_geometry: GeometryRecord
    trail: Annotated[list[TrailSample], Field(max_length=DEFAULT_POLICY.max_trail_samples_per_feature)]
    observations: list[ScalarObservation]
    lightning_evidence: FeatureLightningEvidence
    coverage: SupportRecord
    geolocation: GeolocationRecord
    motion: MotionRecord
    projection_end_at: UTCDateTime | None
    projections: Annotated[list[ProjectionRecord], Field(max_length=DEFAULT_POLICY.max_projection_times)]
    route_rows: list[RouteRow]
    planned_overlap: PlannedOverlapResult
    reason_codes: Reasons

    @model_validator(mode="after")
    def validate_feature(self) -> Self:
        expected_quantity = "reflectivity" if self.family == "radar_echo" else "geometric_cloud_top_height"
        if self.definition.quantity != expected_quantity:
            raise ValueError("feature family and contour definition do not agree")
        if len(self.frame_ids) != len(set(self.frame_ids)):
            raise ValueError("feature frame_ids must be unique")
        if self.reference_frame_id not in self.frame_ids:
            raise ValueError("reference_frame_id must be part of frame_ids")
        if self.reference_frame_id != self.frame_ids[-1]:
            raise ValueError("reference_frame_id must be the final feature frame")
        trail_times = [sample.observed_at for sample in self.trail]
        if trail_times and not _strictly_sorted(trail_times):
            raise ValueError("trail samples must be in chronological order")
        if self.motion.status == "accepted":
            if self.geolocation.status != "validated":
                raise ValueError("accepted motion requires validated geolocation")
            expected_end = self.reference_at + timedelta(minutes=DEFAULT_POLICY.projection_horizon_minutes)
            if self.projection_end_at != expected_end:
                raise ValueError("accepted motion projection_end_at must be reference +15 minutes")
        elif self.projection_end_at is not None:
            raise ValueError("unavailable motion requires null projection_end_at")
        return self


class CompletenessRecord(MotionModel):
    category: StrictStr
    status: Literal["complete", "partial", "not_evaluated"]
    reason_codes: Reasons
    considered_count: SafeInteger | None
    emitted_count: SafeInteger
    omitted_count: SafeInteger | None

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.status in ("partial", "not_evaluated") and not self.reason_codes:
            raise ValueError("incomplete categories require reason_codes")
        if self.status == "not_evaluated":
            if self.considered_count is not None or self.omitted_count is not None:
                raise ValueError("not-evaluated counts must remain unknown")
            if self.emitted_count != 0:
                raise ValueError("not-evaluated work cannot claim emitted records")
        if self.considered_count is not None and self.omitted_count is not None:
            if self.considered_count != self.emitted_count + self.omitted_count:
                raise ValueError("considered_count must equal emitted_count plus omitted_count")
        return self


class ObservedMotion(MotionModel):
    schema_version: Annotated[StrictInt, Field(ge=1, le=1)]
    status: Literal["disabled", "unavailable", "available"]
    reason_codes: Reasons
    revision: Annotated[StrictInt, Field(ge=1, le=MAX_SAFE_INTEGER)]
    run_id: Identifier | None
    route_geometry_id: Identifier
    planned_timing_id: Identifier | None
    computed_at: UTCDateTime
    cutoff_at: UTCDateTime
    expires_at: UTCDateTime | None
    method_version: Literal["masked_contour_translation_v1"]
    policy_version: Literal["observed_motion_policy_v1"]
    analysis_domain: AnalysisDomain | None
    sources: list[SourceRecord]
    features: Annotated[list[FeatureRecord], Field(max_length=DEFAULT_POLICY.max_features)]
    associations: Annotated[list[AssociationRecord], Field(max_length=DEFAULT_POLICY.max_associations)]
    lightning: Annotated[list[LightningRecord], Field(max_length=DEFAULT_POLICY.max_lightning_records)]
    projection_times: Annotated[list[UTCDateTime], Field(max_length=DEFAULT_POLICY.max_projection_times)]
    completeness: list[CompletenessRecord]

    @model_validator(mode="after")
    def validate_envelope(self) -> Self:
        if self.status != "available" and not self.reason_codes:
            raise ValueError("disabled or unavailable envelopes require reason_codes")
        if self.computed_at < self.cutoff_at:
            raise ValueError("computed_at cannot precede cutoff_at")
        categories = [record.category for record in self.completeness]
        if len(categories) != len(COMPLETENESS_CATEGORIES) or set(categories) != set(COMPLETENESS_CATEGORIES):
            raise ValueError("completeness must contain every required category once")

        source_by_id = {source.source_id: source for source in self.sources}
        if len(source_by_id) != len(self.sources):
            raise ValueError("source_id values must be unique")
        feature_by_id = {feature.feature_id: feature for feature in self.features}
        if len(feature_by_id) != len(self.features):
            raise ValueError("feature_id values must be unique")
        if len({association.association_id for association in self.associations}) != len(self.associations):
            raise ValueError("association_id values must be unique")
        if len({record.detection_id for record in self.lightning}) != len(self.lightning):
            raise ValueError("detection_id values must be unique")

        all_frame_ids: dict[str, FrameRecord] = {}
        frame_source: dict[str, str] = {}
        for source in self.sources:
            for frame in source.frames:
                if frame.frame_id in all_frame_ids:
                    raise ValueError("frame_id values must be unique across the envelope")
                all_frame_ids[frame.frame_id] = frame
                frame_source[frame.frame_id] = source.source_id
                if frame.acquisition_window.end_at > frame.received_at:
                    raise ValueError("frame acquisition must end no later than receipt")
                if frame.valid_at > frame.received_at:
                    raise ValueError("frame valid_at must not follow receipt")
                if frame.received_at > self.cutoff_at:
                    raise ValueError("selected frames cannot bypass the cutoff")

        projection_times = self.projection_times
        if projection_times and not _strictly_sorted(projection_times):
            raise ValueError("projection_times must be sorted and unique")
        if any(at <= self.cutoff_at for at in projection_times):
            raise ValueError("projection_times must be strictly after cutoff")
        if any(
            not _minute_aligned(at)
            or at.minute % DEFAULT_POLICY.projection_tick_minutes != 0
            for at in projection_times
        ):
            raise ValueError("projection_times must be exact absolute five-minute UTC ticks")

        accepted_features: list[FeatureRecord] = []
        for feature in self.features:
            source = source_by_id.get(feature.source_id)
            if source is None:
                raise ValueError("feature source reference does not resolve")
            for frame_id in feature.frame_ids:
                if frame_source.get(frame_id) != feature.source_id:
                    raise ValueError("feature frame reference does not resolve to its source")
            if frame_source.get(feature.reference_frame_id) != feature.source_id:
                raise ValueError("feature reference frame does not resolve to its source")
            frame_times = [all_frame_ids[frame_id].valid_at for frame_id in feature.frame_ids]
            if frame_times and not _strictly_sorted(frame_times):
                raise ValueError("feature frame history must be chronological")
            if all_frame_ids[feature.reference_frame_id].valid_at != feature.reference_at:
                raise ValueError("feature reference time must match its reference frame")
            if feature.geolocation.model_dump(exclude_none=False) != source.geolocation.model_dump(exclude_none=False):
                raise ValueError("feature geolocation must agree with source evidence")
            for trail in feature.trail:
                if trail.frame_id not in feature.frame_ids:
                    raise ValueError("trail frame reference does not resolve to feature history")
                if all_frame_ids[trail.frame_id].valid_at != trail.observed_at:
                    raise ValueError("trail observed_at must match its referenced frame")
            pairs = feature.motion.pair_diagnostics
            for pair in pairs:
                if pair.from_frame_id not in feature.frame_ids or pair.to_frame_id not in feature.frame_ids:
                    raise ValueError("motion pair reference does not resolve to feature history")
            if feature.motion.status == "accepted":
                if len(feature.frame_ids) < DEFAULT_POLICY.min_primary_valid_times:
                    raise ValueError("accepted motion requires a clean three-frame history")
                source_frame_ids = [frame.frame_id for frame in source.frames]
                if source_frame_ids[-len(feature.frame_ids) :] != feature.frame_ids:
                    raise ValueError("accepted motion must use the newest selected source-frame suffix")
                if frame_times[-1] - frame_times[0] > timedelta(
                    minutes=DEFAULT_POLICY.max_history_span_minutes
                ):
                    raise ValueError("accepted motion history exceeds the maximum span")
                max_gap_minutes = (
                    DEFAULT_POLICY.max_dbzh_adjacent_gap_minutes
                    if feature.family == "radar_echo"
                    else DEFAULT_POLICY.max_ctth_adjacent_gap_minutes
                )
                if any(
                    right - left > timedelta(minutes=max_gap_minutes)
                    for left, right in zip(frame_times, frame_times[1:])
                ):
                    raise ValueError("accepted motion history contains an excessive adjacent gap")
                if self.cutoff_at - feature.reference_at > timedelta(
                    minutes=DEFAULT_POLICY.max_reference_age_minutes
                ):
                    raise ValueError("accepted motion reference is stale at the cutoff")
                if len(pairs) != len(feature.frame_ids) - 1:
                    raise ValueError("accepted motion requires diagnostics for every adjacent frame pair")
                for index, pair in enumerate(pairs):
                    if pair.status != "available":
                        raise ValueError("accepted motion requires an available adjacent-pair chain")
                    if (pair.from_frame_id, pair.to_frame_id) != (
                        feature.frame_ids[index],
                        feature.frame_ids[index + 1],
                    ):
                        raise ValueError("motion pair diagnostics must follow adjacent feature frames")
                    elapsed = (frame_times[index + 1] - frame_times[index]).total_seconds()
                    if not math.isclose(pair.elapsed_seconds, elapsed, abs_tol=1e-9):
                        raise ValueError("motion pair elapsed_seconds must match frame times")
                    if index < len(pairs) - 1 and pair.next_observation_residual_cells is None:
                        raise ValueError("nonfinal motion pairs require the next-observation residual")
                    if index == len(pairs) - 1 and pair.next_observation_residual_cells is not None:
                        raise ValueError("final motion pair has no next observation to test")
            if [projection.at for projection in feature.projections] != projection_times:
                raise ValueError("each feature requires one projection entry per advertised time")
            for projection in feature.projections:
                if projection.status == "available":
                    if feature.motion.status != "accepted" or feature.geolocation.status != "validated":
                        raise ValueError("available projection requires accepted registered motion")
                    if feature.projection_end_at is None or not (
                        self.cutoff_at < projection.at <= feature.projection_end_at
                    ):
                        raise ValueError("available projection time lies outside its valid lead")
                    if feature.coverage.status != "available" or feature.coverage.known_fraction != 1.0:
                        raise ValueError("available projection requires full support")
            if feature.motion.status == "accepted":
                accepted_features.append(feature)
            evidence = feature.lightning_evidence
            if evidence.source_id is not None:
                evidence_source = source_by_id.get(evidence.source_id)
                if evidence_source is None:
                    raise ValueError("lightning evidence source reference does not resolve")
                evidence_frames = {frame.frame_id for frame in evidence_source.frames}
                if any(frame_id not in evidence_frames for frame_id in evidence.frame_ids):
                    raise ValueError("lightning evidence frame reference does not resolve")
            for observation in feature.observations:
                if observation.source_id is not None:
                    observation_source = source_by_id.get(observation.source_id)
                    if observation_source is None:
                        raise ValueError("scalar observation source reference does not resolve")
                    if observation.frame_id is not None and observation.frame_id not in {
                        frame.frame_id for frame in observation_source.frames
                    }:
                        raise ValueError("scalar observation frame reference does not resolve")
            for row in feature.route_rows:
                if row.at != feature.reference_at and row.at not in projection_times:
                    raise ValueError("route row time must be reference or advertised projection time")

        for association in self.associations:
            radar = feature_by_id.get(association.radar_feature_id)
            cloud = feature_by_id.get(association.cloud_feature_id)
            if radar is None or cloud is None or radar.family != "radar_echo" or cloud.family != "high_cloud_top":
                raise ValueError("association feature references must resolve to radar and cloud features")
            if any(frame_id not in radar.frame_ids for frame_id in association.radar_frame_ids):
                raise ValueError("association radar frame reference does not resolve")
            if any(frame_id not in cloud.frame_ids for frame_id in association.cloud_frame_ids):
                raise ValueError("association cloud frame reference does not resolve")
            if association.status == "available":
                if radar.geolocation.status != "validated" or cloud.geolocation.status != "validated":
                    raise ValueError("available association requires registered feature histories")
                if radar.coverage.status != "available" or cloud.coverage.status != "available":
                    raise ValueError("available association requires usable feature support")
                radar_times = [all_frame_ids[frame_id].valid_at for frame_id in radar.frame_ids]
                cloud_times = [all_frame_ids[frame_id].valid_at for frame_id in cloud.frame_ids]
                common_start = max(radar_times[0], cloud_times[0])
                common_end = min(radar_times[-1], cloud_times[-1])
                if common_start > common_end or association.comparison_at != common_end:
                    raise ValueError("available association requires the latest compatible observed time")
                if not association.radar_frame_ids or not association.cloud_frame_ids:
                    raise ValueError("available association requires exact or bracketing frame references")
                radar_reference_times = [all_frame_ids[frame_id].valid_at for frame_id in association.radar_frame_ids]
                cloud_reference_times = [all_frame_ids[frame_id].valid_at for frame_id in association.cloud_frame_ids]
                if association.alignment_method == "simultaneous_observed":
                    if association.comparison_at not in radar_reference_times or association.comparison_at not in cloud_reference_times:
                        raise ValueError("simultaneous association requires observations at comparison_at")
                else:
                    if not (
                        min(radar_reference_times) <= association.comparison_at <= max(radar_reference_times)
                        and min(cloud_reference_times) <= association.comparison_at <= max(cloud_reference_times)
                    ):
                        raise ValueError("translated association must stay inside referenced history")

        if self.planned_timing_id is None and any(
            feature.planned_overlap.status == "available"
            or any(row.planned_time_status == "available" for row in feature.route_rows)
            for feature in self.features
        ):
            raise ValueError("available planned-time results require planned_timing_id")

        marker_counts = {feature_id: 0 for feature_id in feature_by_id}
        for record in self.lightning:
            if record.source_id not in source_by_id or frame_source.get(record.frame_id) != record.source_id:
                raise ValueError("lightning source/frame reference does not resolve")
            if record.associated_feature_ids is not None:
                for feature_id in record.associated_feature_ids:
                    if feature_id not in feature_by_id:
                        raise ValueError("lightning feature reference does not resolve")
                    marker_counts[feature_id] += 1
        for feature in self.features:
            if feature.lightning_evidence.emitted_marker_count != marker_counts[feature.feature_id]:
                raise ValueError("feature emitted_marker_count must match serialized associated markers")

        if self.status == "disabled":
            if any((self.run_id, self.analysis_domain, self.expires_at)):
                raise ValueError("disabled envelope requires null run/domain/expiry")
            if any((self.sources, self.features, self.associations, self.lightning, self.projection_times)):
                raise ValueError("disabled envelope cannot carry inspected contents")
            if any(record.status != "not_evaluated" for record in self.completeness):
                raise ValueError("disabled completeness must be not_evaluated")
        elif self.status == "unavailable":
            if accepted_features:
                raise ValueError("unavailable envelope cannot carry accepted motion")
            if self.expires_at is not None or self.projection_times:
                raise ValueError("unavailable envelope cannot advertise future motion")
            available_projection = any(
                projection.status == "available"
                for feature in self.features
                for projection in feature.projections
            )
            available_route_result = any(
                any(row.status == "available" for row in feature.route_rows)
                or feature.planned_overlap.status == "available"
                for feature in self.features
            )
            if available_projection or available_route_result:
                raise ValueError("unavailable envelope cannot carry available future/route results")
        else:
            if self.run_id is None or self.analysis_domain is None or not accepted_features:
                raise ValueError("available envelope requires run/domain and accepted motion")
            expected_expiry = max(feature.projection_end_at for feature in accepted_features)
            if self.expires_at != expected_expiry:
                raise ValueError("expires_at must equal the maximum accepted projection end")
            if projection_times and projection_times[-1] > expected_expiry:
                raise ValueError("advertised projection exceeds all accepted motion leads")

        if sum(len(feature.route_rows) for feature in self.features) > DEFAULT_POLICY.max_route_rows:
            raise ValueError("route-row payload limit exceeded")
        if sum(len(feature.planned_overlap.intervals) for feature in self.features) > DEFAULT_POLICY.max_overlap_intervals:
            raise ValueError("overlap-interval payload limit exceeded")
        geometry_positions = 0
        for feature in self.features:
            geometries = [feature.display_geometry, *(projection.display_geometry for projection in feature.projections)]
            geometry_positions += sum(
                record.geometry.position_count for record in geometries if record.geometry is not None
            )
        if geometry_positions > DEFAULT_POLICY.max_total_geometry_positions:
            raise ValueError("total geometry position limit exceeded")
        if len(self.model_dump_json().encode("utf-8")) > DEFAULT_POLICY.max_serialized_bytes:
            raise ValueError("serialized motion payload limit exceeded")
        return self


def empty_motion(
    *,
    route_geometry_id: str,
    planned_timing_id: str | None,
    cutoff_at: datetime,
    revision: int,
    status: str,
    reason_codes: list[str],
) -> ObservedMotion:
    """Build a validated disabled/unavailable envelope without fake evaluations."""

    completeness = [
        CompletenessRecord(
            category=category,
            status="not_evaluated",
            reason_codes=["not_evaluated"],
            considered_count=None,
            emitted_count=0,
            omitted_count=None,
        )
        for category in COMPLETENESS_CATEGORIES
    ]
    return ObservedMotion.model_validate(
        {
            "schema_version": 1,
            "status": status,
            "reason_codes": reason_codes,
            "revision": revision,
            "run_id": None,
            "route_geometry_id": route_geometry_id,
            "planned_timing_id": planned_timing_id,
            "computed_at": datetime.now(timezone.utc),
            "cutoff_at": cutoff_at,
            "expires_at": None,
            "method_version": METHOD_VERSION,
            "policy_version": DEFAULT_POLICY.policy_version,
            "analysis_domain": None,
            "sources": [],
            "features": [],
            "associations": [],
            "lightning": [],
            "projection_times": [],
            "completeness": completeness,
        }
    )


__all__ = [
    "AnalysisDomain",
    "AssociationRecord",
    "CompletenessRecord",
    "ContourDefinition",
    "FeatureLightningEvidence",
    "FeatureRecord",
    "FrameGap",
    "FrameRecord",
    "GeolocationRecord",
    "GeometryRecord",
    "Interval",
    "LightningRecord",
    "MotionRecord",
    "MultiPolygon",
    "ObservedMotion",
    "OverlapInterval",
    "PairDiagnostics",
    "PatchDiagnostics",
    "PlannedOverlapResult",
    "Point",
    "ProjectionRecord",
    "RouteRow",
    "ScalarObservation",
    "SourceRecord",
    "SupportRecord",
    "TrailSample",
    "empty_motion",
]
