/** Tolerant browser boundary for the version-1 observed-motion wire record. */

export type MotionFamily = 'radar_echo' | 'high_cloud_top';
export type Availability = 'available' | 'unavailable';
export type MotionTime = 'observed' | string;
export type Point = [number, number];
export type MultiPolygon = { type: 'MultiPolygon'; coordinates: Point[][][] };

export interface Interval { start_at: string; end_at: string }
export interface GeometryRecord {
  status: Availability;
  reason_codes: string[];
  geometry: MultiPolygon | null;
  provenance: 'grid_contour';
  simplification_tolerance_m: number;
}

export interface SourceRecord {
  source_id: string;
  status: Availability;
  reason_codes: string[];
  frames: Array<Record<string, unknown> & { frame_id: string; valid_at: string; reference_at: string }>;
  gaps: Array<Record<string, unknown>>;
  attribution: string;
  coverage: Record<string, unknown>;
  geolocation: Record<string, unknown> & { status: 'validated' | 'unverified' | 'failed'; reason_codes: string[] };
}

export interface ScalarObservation extends Record<string, unknown> {
  kind: 'reflectivity_max' | 'rain_rate_max' | 'cloud_top_max';
  status: Availability;
  reason_codes: string[];
  value: number | null;
  unit: 'dBZ' | 'mm_h' | 'm_msl';
  observed_at: string | null;
  comparison_at: string | null;
  paired_temperature_k: number | null;
}

export interface LightningEvidence extends Record<string, unknown> {
  status: Availability;
  reason_codes: string[];
  reported_detection_count: number | null;
  emitted_marker_count: number;
  evaluation_complete: boolean;
  evaluated_window: Interval | null;
}

export interface MotionRecord extends Record<string, unknown> {
  status: 'accepted' | 'unavailable';
  reason_codes: string[];
  ground_speed_kt: number | null;
  bearing_deg_true: number | null;
}

export interface ProjectionRecord extends Record<string, unknown> {
  at: string;
  status: Availability;
  reason_codes: string[];
  display_geometry: GeometryRecord;
}

export interface RouteRow extends Record<string, unknown> {
  leg_id: string;
  leg_index: number;
  from_label: string;
  to_label: string;
  at: string;
  status: Availability;
  reason_codes: string[];
  distance_nm: number | null;
  closure_kt: number | null;
  closure_interval: Interval | null;
  relationship: 'approaching' | 'receding' | 'approximately_unchanged' | 'intersecting' | 'unavailable';
  planned_time_status: Availability;
  planned_time_reason_codes: string[];
  planned_overlap_at_time: boolean | null;
}

export interface PlannedOverlap extends Record<string, unknown> {
  status: Availability;
  reason_codes: string[];
  evaluated_interval: Interval | null;
  intervals: Array<Record<string, unknown> & {
    leg_id: string; leg_index: number; start_at: string; end_at: string;
    contact: 'interval' | 'tangent'; approximate: true;
  }>;
  complete: boolean;
}

export interface FeatureRecord {
  feature_id: string;
  source_id: string;
  family: MotionFamily;
  definition: {
    quantity: 'reflectivity' | 'geometric_cloud_top_height';
    operator: 'gte';
    threshold: number;
    unit: 'dBZ' | 'm_msl';
  };
  reference_at: string;
  reference_frame_id: string;
  frame_ids: string[];
  display_geometry: GeometryRecord;
  trail: Array<{ frame_id: string; observed_at: string; center: Point }>;
  observations: ScalarObservation[];
  lightning_evidence: LightningEvidence;
  coverage: Record<string, unknown>;
  geolocation: Record<string, unknown>;
  motion: MotionRecord;
  projection_end_at: string | null;
  projections: ProjectionRecord[];
  route_rows: RouteRow[];
  planned_overlap: PlannedOverlap;
  reason_codes: string[];
}

export interface AssociationRecord extends Record<string, unknown> {
  association_id: string;
  radar_feature_id: string;
  cloud_feature_id: string;
  status: Availability;
  reason_codes: string[];
  relation: 'overlap' | 'nearby' | null;
  comparison_at: string | null;
}

export interface LightningRecord extends Record<string, unknown> {
  detection_id: string;
  position: Point;
  time_precision: 'individual_time' | 'window_only';
  event_at: string | null;
  acquisition_window: Interval;
  associated_feature_ids: string[] | null;
}

export interface ObservedMotion {
  schema_version: 1;
  status: 'disabled' | 'unavailable' | 'available';
  reason_codes: string[];
  revision: number;
  run_id: string | null;
  route_geometry_id: string;
  planned_timing_id: string | null;
  computed_at: string;
  cutoff_at: string;
  expires_at: string | null;
  method_version: 'masked_contour_translation_v1';
  policy_version: 'observed_motion_policy_v1';
  analysis_domain: Record<string, unknown> | null;
  sources: SourceRecord[];
  features: FeatureRecord[];
  associations: AssociationRecord[];
  lightning: LightningRecord[];
  projection_times: string[];
  completeness: Array<Record<string, unknown>>;
}

export interface ParsedObservedMotion {
  /** Original object, retained verbatim for cache/refresh round trips. */
  raw: unknown;
  status: 'disabled' | 'unavailable' | 'available';
  revision: number | null;
  motion: ObservedMotion | null;
  unavailableReason: string | null;
  validationReasons: string[];
}

const UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const id = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
const utc = (value: unknown): value is string => typeof value === 'string' && UTC.test(value) && Number.isFinite(Date.parse(value));
const reasons = (value: unknown): value is string[] => Array.isArray(value) && value.every(id);
const availability = (value: unknown): value is Availability => value === 'available' || value === 'unavailable';

function point(value: unknown): value is Point {
  return Array.isArray(value) && value.length === 2 && finite(value[0]) && finite(value[1])
    && value[0] >= -180 && value[0] <= 180 && value[1] >= -90 && value[1] <= 90;
}

function interval(value: unknown): value is Interval {
  return record(value) && utc(value.start_at) && utc(value.end_at)
    && Date.parse(value.start_at) <= Date.parse(value.end_at);
}

function analysisDomain(value: unknown): Record<string, unknown> | null {
  if (!record(value) || !point(value.center) || typeof value.crs !== 'string' || value.crs.length === 0
      || !finite(value.cell_size_m) || value.cell_size_m <= 0 || !integer(value.width_cells) || value.width_cells < 1
      || !integer(value.height_cells) || value.height_cells < 1 || !finite(value.origin_x_m) || !finite(value.origin_y_m)
      || !Array.isArray(value.bounds) || value.bounds.length !== 4 || !value.bounds.every(finite)
      || value.bounds[0] >= value.bounds[2] || value.bounds[1] >= value.bounds[3] || !reasons(value.reason_codes)) return null;
  return value;
}

function multiPolygon(value: unknown): value is MultiPolygon {
  if (!record(value) || value.type !== 'MultiPolygon' || !Array.isArray(value.coordinates) || value.coordinates.length === 0) return false;
  return value.coordinates.every(polygon => Array.isArray(polygon) && polygon.length > 0 && polygon.every(ring => {
    if (!Array.isArray(ring) || ring.length < 4 || !ring.every(point)) return false;
    const first = ring[0], last = ring[ring.length - 1];
    return first[0] === last[0] && first[1] === last[1];
  }));
}

function unavailableGeometry(reason: string): GeometryRecord {
  return { status: 'unavailable', reason_codes: [reason], geometry: null, provenance: 'grid_contour', simplification_tolerance_m: 0 };
}

function geometry(value: unknown, validationReasons: string[]): GeometryRecord {
  if (!record(value) || !availability(value.status) || !reasons(value.reason_codes)
      || value.provenance !== 'grid_contour' || !finite(value.simplification_tolerance_m)
      || value.simplification_tolerance_m < 0 || value.simplification_tolerance_m > 1000) {
    validationReasons.push('invalid_geometry');
    return unavailableGeometry('invalid_geometry');
  }
  if (value.status === 'unavailable') {
    if (value.geometry !== null) validationReasons.push('invalid_geometry');
    return { status: 'unavailable', reason_codes: value.reason_codes.length ? value.reason_codes : ['invalid_geometry'], geometry: null,
      provenance: 'grid_contour', simplification_tolerance_m: value.simplification_tolerance_m };
  }
  if (!multiPolygon(value.geometry)) {
    validationReasons.push('invalid_geometry');
    return unavailableGeometry('invalid_geometry');
  }
  return value as unknown as GeometryRecord;
}

function parseSource(value: unknown): SourceRecord | null {
  if (!record(value) || !id(value.source_id) || !availability(value.status) || !reasons(value.reason_codes)
      || !Array.isArray(value.frames) || !Array.isArray(value.gaps) || typeof value.attribution !== 'string'
      || !record(value.coverage) || !record(value.geolocation)
      || !['validated', 'unverified', 'failed'].includes(String(value.geolocation.status))
      || !reasons(value.geolocation.reason_codes)) return null;
  const frames = value.frames.filter((frame): frame is Record<string, unknown> & { frame_id: string; valid_at: string; reference_at: string } =>
    record(frame) && id(frame.frame_id) && id(frame.content_id) && id(frame.product_id) && id(frame.decoder_version) && id(frame.grid_id)
      && utc(frame.valid_at) && utc(frame.received_at) && utc(frame.reference_at) && frame.reference_at === frame.valid_at
      && interval(frame.acquisition_window));
  if (frames.length !== value.frames.length) return null;
  return { ...value, frames } as unknown as SourceRecord;
}

function parseObservation(value: unknown): ScalarObservation | null {
  if (!record(value) || !['reflectivity_max', 'rain_rate_max', 'cloud_top_max'].includes(String(value.kind))
      || !availability(value.status) || !reasons(value.reason_codes)
      || !(value.value === null || finite(value.value)) || !['dBZ', 'mm_h', 'm_msl'].includes(String(value.unit))
      || !(value.observed_at === null || utc(value.observed_at)) || !(value.comparison_at === null || utc(value.comparison_at))
      || !(value.paired_temperature_k === null || finite(value.paired_temperature_k))) return null;
  return value as ScalarObservation;
}

function sourceOwnsFrame(sourcesById: Map<string, SourceRecord>, sourceId: unknown, frameId: unknown): boolean {
  if (!id(sourceId) || !id(frameId)) return false;
  return sourcesById.get(sourceId)?.frames.some(frame => frame.frame_id === frameId) === true;
}

function parseLightningEvidence(value: unknown, sourcesById: Map<string, SourceRecord>): LightningEvidence {
  const sourceId = record(value) ? value.source_id : null;
  const frameIds = record(value) ? value.frame_ids : null;
  const resolvedFrames = id(sourceId) && Array.isArray(frameIds)
    && frameIds.every(frameId => sourceOwnsFrame(sourcesById, sourceId, frameId));
  if (record(value) && availability(value.status) && reasons(value.reason_codes)
      && (value.reported_detection_count === null || integer(value.reported_detection_count))
      && integer(value.emitted_marker_count) && typeof value.evaluation_complete === 'boolean'
      && (value.evaluated_window === null || interval(value.evaluated_window))
      && Array.isArray(value.frame_ids)
      && ((value.source_id === null && value.frame_ids.length === 0) || resolvedFrames)
      && (value.reported_detection_count === null || value.emitted_marker_count <= value.reported_detection_count)
      && (value.status === 'unavailable'
        || (id(value.source_id) && value.frame_ids.length > 0 && value.reported_detection_count !== null && interval(value.evaluated_window)))) {
    return value as LightningEvidence;
  }
  return { status: 'unavailable', reason_codes: ['not_evaluated'], reported_detection_count: null,
    emitted_marker_count: 0, evaluation_complete: false, evaluated_window: null };
}

function patchDiagnostic(value: unknown): boolean {
  return record(value) && (value.direction === 'forward' || value.direction === 'reverse')
    && integer(value.center_column) && integer(value.center_row) && availability(value.status) && reasons(value.reason_codes)
    && (value.support_fraction === null || finite(value.support_fraction))
    && (value.ncc === null || finite(value.ncc)) && (value.competing_peak_margin === null || finite(value.competing_peak_margin))
    && (value.dx_cells === null || finite(value.dx_cells)) && (value.dy_cells === null || finite(value.dy_cells))
    && (value.refinement === null || value.refinement === 'quadratic' || value.refinement === 'integer');
}

function pairDiagnostic(value: unknown, featureFrameIds: Set<string>): boolean {
  return record(value) && id(value.from_frame_id) && id(value.to_frame_id) && finite(value.elapsed_seconds) && value.elapsed_seconds > 0
    && featureFrameIds.has(value.from_frame_id) && featureFrameIds.has(value.to_frame_id)
    && availability(value.status) && reasons(value.reason_codes) && Array.isArray(value.patches) && value.patches.every(patchDiagnostic)
    && (value.forward_dx_cells === null || finite(value.forward_dx_cells)) && (value.forward_dy_cells === null || finite(value.forward_dy_cells))
    && (value.patch_disagreement_cells === null || finite(value.patch_disagreement_cells))
    && (value.reverse_residual_cells === null || finite(value.reverse_residual_cells))
    && (value.next_observation_residual_cells === null || finite(value.next_observation_residual_cells))
    && (value.common_support_iou === null || finite(value.common_support_iou)) && (value.area_ratio === null || finite(value.area_ratio))
    && (value.plausible_parent_count === null || integer(value.plausible_parent_count))
    && (value.plausible_child_count === null || integer(value.plausible_child_count)) && typeof value.lineage_complete === 'boolean';
}

function parseMotion(value: unknown, featureFrameIds: Set<string>, validationReasons: string[]): MotionRecord {
  const common = record(value) && (value.status === 'accepted' || value.status === 'unavailable') && reasons(value.reason_codes)
    && Array.isArray(value.pair_diagnostics) && value.pair_diagnostics.every(item => pairDiagnostic(item, featureFrameIds))
    && (value.fit_rms_residual_cells === null || (finite(value.fit_rms_residual_cells) && value.fit_rms_residual_cells >= 0));
  const accepted = common && value.status === 'accepted' && finite(value.ground_speed_kt) && value.ground_speed_kt >= 0
    && (value.bearing_deg_true === null || (finite(value.bearing_deg_true) && value.bearing_deg_true >= 0 && value.bearing_deg_true < 360))
    && point(value.velocity_reference_point) && value.velocity_method === 'inverse_aeqd_geodesic_1s';
  const unavailable = common && value.status === 'unavailable' && value.ground_speed_kt === null && value.bearing_deg_true === null
    && value.velocity_reference_point === null && value.velocity_method === null;
  if (accepted || unavailable) return value as MotionRecord;
  validationReasons.push('invalid_motion');
  return { status: 'unavailable', reason_codes: ['not_evaluated'], ground_speed_kt: null, bearing_deg_true: null };
}

function parseProjection(value: unknown, validationReasons: string[]): ProjectionRecord | null {
  if (!record(value) || !utc(value.at) || !availability(value.status) || !reasons(value.reason_codes)) return null;
  const displayGeometry = geometry(value.display_geometry, validationReasons);
  return value.status === 'unavailable'
    ? { ...value, display_geometry: unavailableGeometry('projection_unavailable') } as ProjectionRecord
    : { ...value, display_geometry: displayGeometry } as ProjectionRecord;
}

function unavailableProjection(projection: ProjectionRecord, reason: string): ProjectionRecord {
  return {
    ...projection,
    status: 'unavailable',
    reason_codes: [...new Set([...projection.reason_codes, reason])],
    display_geometry: unavailableGeometry(reason),
  };
}

function validatedGeolocation(value: unknown): boolean {
  return record(value) && value.status === 'validated';
}

function parseRouteRow(value: unknown): RouteRow | null {
  if (!record(value) || !id(value.leg_id) || !integer(value.leg_index) || typeof value.from_label !== 'string'
      || typeof value.to_label !== 'string' || !utc(value.at) || !availability(value.status) || !reasons(value.reason_codes)
      || !(value.distance_nm === null || (finite(value.distance_nm) && value.distance_nm >= 0))
      || !(value.closure_kt === null || finite(value.closure_kt))
      || !(value.closure_interval === null || interval(value.closure_interval))
      || !['approaching', 'receding', 'approximately_unchanged', 'intersecting', 'unavailable'].includes(String(value.relationship))
      || !availability(value.planned_time_status) || !reasons(value.planned_time_reason_codes)
      || !(value.planned_overlap_at_time === null || typeof value.planned_overlap_at_time === 'boolean')) return null;
  return value as RouteRow;
}

function parsePlannedOverlap(value: unknown): PlannedOverlap {
  if (!record(value) || !availability(value.status) || !reasons(value.reason_codes)
      || !(value.evaluated_interval === null || interval(value.evaluated_interval))
      || !Array.isArray(value.intervals) || typeof value.complete !== 'boolean') {
    return { status: 'unavailable', reason_codes: ['not_evaluated'], evaluated_interval: null, intervals: [], complete: false };
  }
  const intervals = value.intervals.filter((item): item is PlannedOverlap['intervals'][number] =>
    record(item) && id(item.leg_id) && integer(item.leg_index) && utc(item.start_at) && utc(item.end_at)
      && Date.parse(item.start_at) <= Date.parse(item.end_at) && (item.contact === 'interval' || item.contact === 'tangent') && item.approximate === true);
  return { ...value, intervals } as PlannedOverlap;
}

function parseFeature(
  value: unknown,
  sourcesById: Map<string, SourceRecord>,
  projectionTimes: Set<string>,
  cutoffAt: string,
  validationReasons: string[],
): FeatureRecord | null {
  const sourceId = record(value) && id(value.source_id) ? value.source_id : null;
  const source = sourceId ? sourcesById.get(sourceId) : undefined;
  const sourceFrames = new Map(source?.frames.map(frame => [frame.frame_id, frame]) ?? []);
  const sourceFrameIds = new Set(source?.frames.map(frame => frame.frame_id) ?? []);
  if (!record(value) || !id(value.feature_id) || !id(value.source_id) || !source
      || (value.family !== 'radar_echo' && value.family !== 'high_cloud_top') || !record(value.definition)
      || value.definition.operator !== 'gte' || !finite(value.definition.threshold)
      || !utc(value.reference_at) || !id(value.reference_frame_id) || sourceFrames.get(value.reference_frame_id)?.reference_at !== value.reference_at
      || !Array.isArray(value.frame_ids) || value.frame_ids.length === 0
      || !value.frame_ids.every(frameId => id(frameId) && sourceFrameIds.has(frameId))
      || value.frame_ids[value.frame_ids.length - 1] !== value.reference_frame_id
      || !Array.isArray(value.trail) || !Array.isArray(value.observations) || !Array.isArray(value.projections)
      || !Array.isArray(value.route_rows) || !reasons(value.reason_codes)
      || !(value.projection_end_at === null || utc(value.projection_end_at))) return null;
  const definitionValid = value.family === 'radar_echo'
    ? value.definition.quantity === 'reflectivity' && value.definition.threshold === 5 && value.definition.unit === 'dBZ'
    : value.definition.quantity === 'geometric_cloud_top_height' && value.definition.threshold === 4572 && value.definition.unit === 'm_msl';
  if (!definitionValid) return null;
  const featureFrameIds = new Set(value.frame_ids);
  const trail = value.trail.filter((sample): sample is FeatureRecord['trail'][number] =>
    record(sample) && id(sample.frame_id) && featureFrameIds.has(sample.frame_id)
      && utc(sample.observed_at) && sourceFrames.get(sample.frame_id)?.reference_at === sample.observed_at && point(sample.center));
  if (trail.length !== value.trail.length) validationReasons.push('invalid_trail');
  const observations = value.observations.map(parseObservation).filter((item): item is ScalarObservation => {
    if (!item) return false;
    const sourceRef = item.source_id;
    const frameRef = item.frame_id;
    const resolved = sourceOwnsFrame(sourcesById, sourceRef, frameRef);
    const valid = item.status === 'available'
      ? resolved
      : (sourceRef === null && frameRef === null) || resolved;
    if (!valid) validationReasons.push('dangling_reference');
    return valid;
  });
  const motion = parseMotion(value.motion, featureFrameIds, validationReasons);
  const projectionAuthority = motion.status === 'accepted'
    && validatedGeolocation(value.geolocation) && validatedGeolocation(source.geolocation);
  const projectionEnd = value.projection_end_at === null ? null : Date.parse(value.projection_end_at);
  const cutoff = Date.parse(cutoffAt);
  const projections = value.projections.map(item => parseProjection(item, validationReasons)).filter((item): item is ProjectionRecord => {
    if (item === null) validationReasons.push('invalid_projection');
    return item !== null;
  }).map(projection => {
    const at = Date.parse(projection.at);
    const valid = projection.status === 'unavailable' || (projectionAuthority && projectionTimes.has(projection.at)
      && at > cutoff && projectionEnd !== null && at <= projectionEnd);
    if (valid) return projection;
    validationReasons.push('invalid_projection');
    return unavailableProjection(projection, 'invalid_projection');
  });
  const routeRows = value.route_rows.map(parseRouteRow).filter((item): item is RouteRow => item !== null);
  return {
    feature_id: value.feature_id, source_id: value.source_id, family: value.family,
    definition: value.definition as FeatureRecord['definition'], reference_at: value.reference_at,
    reference_frame_id: value.reference_frame_id, frame_ids: value.frame_ids,
    display_geometry: geometry(value.display_geometry, validationReasons), trail, observations,
    lightning_evidence: parseLightningEvidence(value.lightning_evidence, sourcesById), coverage: record(value.coverage) ? value.coverage : {},
    geolocation: record(value.geolocation) ? value.geolocation : {}, motion,
    projection_end_at: value.projection_end_at, projections, route_rows: routeRows,
    planned_overlap: parsePlannedOverlap(value.planned_overlap), reason_codes: value.reason_codes,
  };
}

function invalid(raw: unknown, reason: string, revision: number | null = null, validationReasons: string[] = []): ParsedObservedMotion {
  return { raw, status: 'unavailable', revision, motion: null, unavailableReason: reason, validationReasons };
}

/**
 * Parse enough known version-1 structure to render it safely. Unknown JSON is
 * retained on `raw`; malformed nested records are removed/degraded locally.
 */
export function parseObservedMotion(raw: unknown): ParsedObservedMotion {
  if (raw == null) return invalid(raw, 'missing_legacy_data');
  if (!record(raw)) return invalid(raw, 'invalid_motion');
  const revision = integer(raw.revision) && raw.revision >= 1 ? raw.revision : null;
  const orderingValid = revision !== null && id(raw.route_geometry_id) && (raw.run_id === null || id(raw.run_id))
    && (raw.planned_timing_id === null || id(raw.planned_timing_id)) && utc(raw.computed_at) && utc(raw.cutoff_at);
  if (raw.schema_version !== 1) return orderingValid ? invalid(raw, 'unsupported_schema', revision) : invalid(raw, 'invalid_motion');
  if (!['disabled', 'unavailable', 'available'].includes(String(raw.status))) {
    return orderingValid ? invalid(raw, 'unsupported_status', revision) : invalid(raw, 'invalid_motion');
  }
  if (revision === null || !id(raw.route_geometry_id) || !(raw.run_id === null || id(raw.run_id))
      || !(raw.planned_timing_id === null || id(raw.planned_timing_id)) || !utc(raw.computed_at) || !utc(raw.cutoff_at)
      || !(raw.expires_at === null || utc(raw.expires_at)) || !reasons(raw.reason_codes)
      || raw.method_version !== 'masked_contour_translation_v1' || raw.policy_version !== 'observed_motion_policy_v1'
      || !Array.isArray(raw.sources) || !Array.isArray(raw.features) || !Array.isArray(raw.associations)
      || !Array.isArray(raw.lightning) || !Array.isArray(raw.projection_times) || !raw.projection_times.every(utc)
      || !Array.isArray(raw.completeness)) return invalid(raw, 'invalid_motion', revision);

  const validationReasons: string[] = [];
  const domain = raw.analysis_domain === null ? null : analysisDomain(raw.analysis_domain);
  if (raw.status === 'available' && domain === null) validationReasons.push('invalid_analysis_domain');
  const sources = raw.sources.map(parseSource).filter((item): item is SourceRecord => item !== null);
  if (sources.length !== raw.sources.length) validationReasons.push('invalid_source');
  const sourcesById = new Map(sources.map(source => [source.source_id, source]));
  const projectionTimes = new Set(raw.projection_times);
  let features = raw.features.map(item => parseFeature(item, sourcesById, projectionTimes, raw.cutoff_at as string, validationReasons)).filter((item): item is FeatureRecord => item !== null);
  if (features.length !== raw.features.length) validationReasons.push('dangling_reference');
  if (raw.status === 'available' && domain === null) {
    features = features.map(feature => ({ ...feature,
      motion: { status: 'unavailable', reason_codes: ['invalid_analysis_domain'], ground_speed_kt: null, bearing_deg_true: null },
    }));
  }
  const featureIds = new Set(features.map(feature => feature.feature_id));
  const associations = raw.associations.filter((item): item is AssociationRecord => {
    const radar = record(item) && id(item.radar_feature_id) ? features.find(feature => feature.feature_id === item.radar_feature_id) : undefined;
    const cloud = record(item) && id(item.cloud_feature_id) ? features.find(feature => feature.feature_id === item.cloud_feature_id) : undefined;
    const valid = record(item) && id(item.association_id) && id(item.radar_feature_id) && id(item.cloud_feature_id)
      && featureIds.has(item.radar_feature_id) && featureIds.has(item.cloud_feature_id)
      && radar?.family === 'radar_echo' && cloud?.family === 'high_cloud_top'
      && availability(item.status) && reasons(item.reason_codes)
      && (item.comparison_at === null || utc(item.comparison_at));
    if (!valid) validationReasons.push('dangling_reference');
    return valid;
  });
  const lightning = raw.lightning.filter((item): item is LightningRecord => {
    const refs = record(item) && (item.associated_feature_ids === null
      || (Array.isArray(item.associated_feature_ids) && item.associated_feature_ids.every(ref => id(ref) && featureIds.has(ref))));
    const valid = refs && record(item) && id(item.detection_id) && point(item.position)
      && sourceOwnsFrame(sourcesById, item.source_id, item.frame_id)
      && (item.time_precision === 'individual_time' || item.time_precision === 'window_only')
      && (item.event_at === null || utc(item.event_at)) && interval(item.acquisition_window);
    if (!valid) validationReasons.push('dangling_reference');
    return valid;
  });
  const status = raw.status as ObservedMotion['status'];
  if (status !== 'available') {
    const retainedAcceptedMotion = features.some(feature => feature.motion.status === 'accepted');
    if (retainedAcceptedMotion) validationReasons.push('invalid_motion');
    features = features.map(feature => feature.motion.status === 'accepted' ? {
      ...feature,
      motion: { status: 'unavailable', reason_codes: ['not_evaluated'], ground_speed_kt: null, bearing_deg_true: null },
      projection_end_at: null,
      projections: [],
    } : feature);
  }
  const motion: ObservedMotion = {
    schema_version: 1, status, reason_codes: raw.reason_codes, revision, run_id: raw.run_id as string | null,
    route_geometry_id: raw.route_geometry_id, planned_timing_id: raw.planned_timing_id as string | null,
    computed_at: raw.computed_at, cutoff_at: raw.cutoff_at, expires_at: raw.expires_at as string | null,
    method_version: 'masked_contour_translation_v1', policy_version: 'observed_motion_policy_v1',
    analysis_domain: domain,
    sources, features, associations, lightning, projection_times: raw.projection_times, completeness: raw.completeness.filter(record),
  };
  if (status === 'available' && !features.some(feature => feature.motion.status === 'accepted')) {
    return {
      raw, status: 'unavailable', revision,
      motion: { ...motion, status: 'unavailable', reason_codes: [...new Set([...motion.reason_codes, 'invalid_motion'])] },
      unavailableReason: 'invalid_motion', validationReasons: [...new Set([...validationReasons, 'no_accepted_motion'])],
    };
  }
  return { raw, status, revision, motion, unavailableReason: status === 'available' ? null : status, validationReasons: [...new Set(validationReasons)] };
}
