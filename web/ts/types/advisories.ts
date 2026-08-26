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

/** Curation tier driving progressive disclosure on the settings page (#387).
 *  `pilot` params render inline on the advisory row; `advanced` params hide
 *  behind the collapsed "Advanced" expander. Absent (old server) → 'advanced'. */
export type AdvisoryAudience = 'pilot' | 'advanced';

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
  /** Progressive-disclosure tier (#387). Optional for old-server compat;
   *  treat a missing value as 'advanced'. */
  audience?: AdvisoryAudience;
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

/** One category in the server-defined display order (#387). Labels stay
 *  client-side (i18n); `diagnostics` marks the visually-distinct dev group. */
export interface AdvisoryCategory {
  key: string;
  diagnostics: boolean;
}

/** Declared engine grading-method defaults (#403). The settings page reads these
 *  instead of hardcoding fallbacks, so the UI default and the runtime default
 *  cannot drift, and a method equal to the default can be pruned on save. Typed
 *  with the exact keys (not `Record<string, string>`) so a typo like
 *  `engineDefaults.icing_methdo` fails the build. */
export interface EngineMethodDefaults {
  icing_method: string;
  cloud_source: string;
  convective_method: string;
}

/** Response of `/advisories/catalog` (#387): advisories in display order plus
 *  the ordered category list. `engine_method_defaults` added in #403 (optional
 *  for old-server compat). */
export interface AdvisoryCatalogResponse {
  advisories: AdvisoryCatalogEntry[];
  categories: AdvisoryCategory[];
  engine_method_defaults?: EngineMethodDefaults;
}

/** One answer to a setup-interview question (#387, slice 3). Declarative patch:
 *  which advisories it enables/disables and which param values it sets. */
export interface InterviewOption {
  id: string;
  label: string;
  description: string;
  enabled: Record<string, boolean>;
  params: Record<string, Record<string, number>>;
}

export interface InterviewQuestion {
  id: string;
  title: string;
  help: string;
  options: InterviewOption[];
}

/** The declarative setup-interview served by `/advisories/interview` (#387). */
export interface Interview {
  questions: InterviewQuestion[];
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
  /** Stable, non-localised provenance tokens (#393). Absent on old packs.
   *  `reason_code` — why this region fired; `metric_id` — the cross-section
   *  layer a chip jumps to; `method_id` — the analysis method behind it. */
  reason_code?: string | null;
  metric_id?: string | null;
  method_id?: string | null;
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
  /** The extent's own denominator in nm (#571). Equals `total_nm` for every
   *  advisory whose domain is the whole route; `mountain_wind`'s domain is the
   *  route's mountain points, so `affected_nm / total_nm` would understate it.
   *  Absent on old packs. */
  domain_nm?: number | null;
  /** Names a denominator that is NOT the whole route ("of high terrain").
   *  Null/absent means the route. Anything rendering `affected_pct` must
   *  qualify it with this, or it presents a domain fraction as route coverage
   *  — the #571 D3 defect. */
  affected_domain?: string | null;
  cross_check?: string | null;
  mitigations?: Mitigation[];
  /** Cross-section highlight geometry (#373). Absent/null on old packs and for
   *  models/evaluators that don't emit it → the derive selector returns null. */
  highlights?: AdvisoryHighlights | null;
  /** Stable id of the analysis method that controlled this model's grade (#393),
   *  e.g. `ogimet_nwp`, `sfip`, `nwp_with_dd_floor`. The source for a method
   *  badge on the chip — NOT the user's selected method. Absent on old packs and
   *  for evaluators with no selectable method. */
  primary_method_id?: string | null;
}

export interface RouteAdvisoryResult {
  advisory_id: string;
  aggregate_status: AdvisoryStatus;
  aggregate_detail: string;
  per_model: ModelAdvisoryResult[];
  parameters_used: Record<string, number>;
  aggregate_mitigations?: Mitigation[];
  /** The model whose per-model result sources the aggregate view (#393): the
   *  first `per_model` entry whose status equals `aggregate_status`. Emitted by
   *  the backend so the client no longer reimplements the rule. Absent on old
   *  packs → `representativeModel()` falls back to the first per-model entry. */
  representative_model?: string | null;
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
