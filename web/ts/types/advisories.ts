/** TypeScript types for the route advisory system, matching Python Pydantic models. */

export type AdvisoryStatus = 'green' | 'amber' | 'red' | 'unavailable';

/** Axis along which an advisory's flagged sub-issue could be mitigated. */
export type MitigationKind = 'altitude' | 'route_position' | 'timing';

/**
 * A decision that could improve a flagged sub-issue — advice only.
 * A mitigation NEVER changes the advisory's grade (same contract as
 * `cross_check`). `mitigated_status` is the status of the addressed sub-issue
 * if applied, NOT the advisory overall. `addresses` is a stable English machine
 * tag (never displayed raw); `detail` is already localized server-side.
 */
export interface Mitigation {
  kind: MitigationKind;
  addresses: string;
  detail: string;
  mitigated_status: AdvisoryStatus;
  altitude_ft?: number | null;
  distance_nm?: number | null;
  reference?: string | null;
}

export interface AdvisoryParameterDef {
  key: string;
  label: string;
  description: string;
  type: 'number' | 'percent' | 'altitude' | 'speed' | 'boolean';
  unit: string;
  default: number;
  min: number | null;
  max: number | null;
  step: number | null;
}

export interface AdvisoryCatalogEntry {
  id: string;
  name: string;
  short_description: string;
  description: string;
  category: string;
  default_enabled: boolean;
  altitude_dependent: boolean;
  parameters: AdvisoryParameterDef[];
}

/** Severity of a highlight element (scrim cutout / ribbon segment, #373). */
export type HighlightSeverity = 'green' | 'amber' | 'red' | 'unavailable';

/** One run of the 1-D route verdict. Segments tile `[0, total_nm]` exactly. */
export interface RibbonSegment {
  dist_from_nm: number;
  dist_to_nm: number;
  severity: HighlightSeverity;
}

/** One 2-D scrim cutout — where the hazard physically is (flagged areas only).
 *  `base_ft`/`top_ft` both null = full column (terrain-to-top). */
export interface HighlightRegion {
  dist_from_nm: number;
  dist_to_nm: number;
  base_ft?: number | null;
  top_ft?: number | null;
  kind: string;
  severity: HighlightSeverity;
}

/** Cross-section highlight geometry for one advisory × one model (#373).
 *  Backend owns the geometry; the client only renders it. */
export interface AdvisoryHighlights {
  ribbon: RibbonSegment[];
  regions: HighlightRegion[];
  peak_dist_nm?: number | null;
}

export interface ModelAdvisoryResult {
  model: string;
  status: AdvisoryStatus;
  detail: string;
  affected_points: number;
  total_points: number;
  affected_pct: number;
  affected_nm: number;
  total_nm: number;
  cross_check?: string | null;
  mitigations?: Mitigation[];
  /** Cross-section highlight geometry (#373). Absent/null on old packs and for
   *  models/evaluators that don't emit it → the derive selector returns null. */
  highlights?: AdvisoryHighlights | null;
}

export interface RouteAdvisoryResult {
  advisory_id: string;
  aggregate_status: AdvisoryStatus;
  aggregate_detail: string;
  per_model: ModelAdvisoryResult[];
  parameters_used: Record<string, number>;
  aggregate_mitigations?: Mitigation[];
}

export type FlightCategory = 'VFR' | 'MVFR' | 'IFR' | 'LIFR';

export interface RunwayEnd {
  id: string;
  heading_deg: number;
}

export interface RunwayWind {
  runway_id: string;
  heading_deg: number;
  crosswind_kt: number;
  headwind_kt: number;
}

export interface AirportModelCondition {
  model: string;
  flight_category: FlightCategory;
  ceiling_ft: number | null;
  visibility_m?: number | null;
  visibility_sm: number | null;
  wind_speed_kt: number | null;
  wind_direction_deg: number | null;
  wind_gust_kt: number | null;
  best_runway: RunwayWind | null;
  all_runways: RunwayWind[];
}

export interface AirportConditionsSummary {
  icao: string;
  name: string;
  runway_ends: RunwayEnd[];
  conditions: AirportModelCondition[];
}

export interface AirportConditions {
  departure: AirportConditionsSummary;
  arrival: AirportConditionsSummary;
}

export interface RouteAdvisoriesManifest {
  advisories: RouteAdvisoryResult[];
  catalog: AdvisoryCatalogEntry[];
  route_name: string;
  cruise_altitude_ft: number;
  flight_ceiling_ft: number;
  total_distance_nm: number;
  models: string[];
  aggregation?: 'worst' | 'majority';
  airport_conditions: AirportConditions | null;
}

export interface AltitudeAdvisoryRow {
  altitude_ft: number;
  statuses: Record<string, AdvisoryStatus>;
  red_count: number;
  amber_count: number;
  green_count: number;
}

export interface AltitudeTableResult {
  rows: AltitudeAdvisoryRow[];
  advisory_ids: string[];
  advisory_names: Record<string, string>;
  cruise_altitude_ft: number;
  flight_ceiling_ft: number;
  step_ft: number;
  best_below_cruise: number | null;
  best_above_cruise: number | null;
}
