/** Shared TypeScript types matching the API response models. */

export interface AircraftInfo {
  id: number;
  icao_type: string;
  type_name: string;
  tail_number: string | null;
  nickname: string | null;
}

/** Summary of a flight's latest briefing pack, inlined in /flights responses.
 *  Carries everything the flights-list card and the debrief form need, so the
 *  page renders without per-flight /packs/latest round-trips. */
export interface BriefingStatusInfo {
  assessment: string | null;
  assessment_reason: string | null;
  has_digest: boolean;
  days_out: number | null;
  fetch_timestamp: string | null;
  has_advisories: boolean;
}

export interface FlightResponse {
  id: string;
  user_id: string;
  profile_id: number | null;
  aircraft_id: number | null;
  aircraft: AircraftInfo | null;
  route_name: string;
  waypoints: string[];
  departure_time: string;
  alt_departure_time: string | null;
  target_date: string;        // backward compat (computed from departure_time)
  target_time_utc: number;    // backward compat (computed from departure_time)
  cruise_altitude_ft: number;
  flight_ceiling_ft: number;
  flight_duration_hours: number;
  private: boolean;
  auto_refresh: boolean;
  auto_refresh_hour: number | null;
  created_at: string;
  latest_briefing?: BriefingStatusInfo | null;
  role: 'owner' | 'subscriber';
  owner_display_name: string | null;
  is_subscribed: boolean;
  debrief?: DebriefResponse | null;
  section?: 'future' | 'recent' | 'past';
  // Original Field-15 input the pilot typed, when captured. Null for
  // iOS/MCP-created flights and for flights created before raw_route
  // existed. The detail view shows it under "Original" when present.
  raw_route?: string | null;
  parser_version?: string | null;
  // Short share token for /s/{code}. May be omitted on legacy rows that
  // didn't get backfilled — the share helpers then fall back to the
  // long ?id= URL.
  share_code?: string | null;
}

export type DebriefDecision = 'cancelled' | 'flown' | 'monitoring';
export type ConditionTagId = 'IMC' | 'ICE' | 'WIND' | 'TS' | 'TURB' | 'FRZ' | 'VIS' | 'OPS';
export type OutcomeValue = 'consistent' | 'better' | 'worse';

export interface DebriefResponse {
  flight_id: string;
  decision: DebriefDecision;
  reasons: ConditionTagId[];
  outcomes: Partial<Record<ConditionTagId, OutcomeValue>>;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface DebriefStatsCategory {
  queried_count: number;
  consistent: number;
  better: number;
  worse: number;
}

export interface DebriefStats {
  window_days: number;
  total_flights_in_window: number;
  flown_count: number;
  cancelled_count: number;
  monitoring_count: number;
  pending_debrief_count: number;
  cancellation_reasons: Partial<Record<ConditionTagId, number>>;
  category_accuracy: Partial<Record<ConditionTagId, DebriefStatsCategory>>;
}

export interface CreateFlightRequest {
  route_name?: string;
  waypoints: string[];
  departure_time: string;     // ISO 8601 datetime with timezone
  cruise_altitude_ft?: number;
  flight_ceiling_ft?: number;
  flight_duration_hours?: number;
  profile_id?: number;
  aircraft_id?: number;
  // Original Field-15 input. Present from web Save flows where the
  // pilot typed something like "EGTK DCT LFPB DCT LSGS" and the
  // interpret popup confirmed the resolved waypoints. Omitted from
  // iOS/MCP clients (which only know the resolved list).
  raw_route?: string;
}

export interface ModelStatus {
  source: string;
  pack_init: number | null;
  latest_available: number;
  next_expected: string;
  published_at?: string | null;
  state: "current" | "stale" | "awaiting" | "delayed";
  // True when this source's latest run reaches the flight horizon.
  covers_horizon?: boolean;
}

export interface ModelSourceDetail {
  model: string;
  source: string;
  provider: string;
  role: "primary" | "base";
  init: number;
  published_at?: string | null;
  next_expected: string;
  state: "current" | "stale" | "awaiting" | "delayed";
}

/** Outcome of the tiered refresh gate — what pressing refresh will do. */
export interface RefreshDecision {
  mode: "full" | "realtime" | "none";
  reason: string;
  needed: number;
  n_eligible: number;
  n_updated: number;
  days_out: number;
  eta_useful?: string | null;
  pending_models?: string[];
}

export interface DataStatus {
  fresh: boolean;
  stale_models: string[];
  model_init_times: Record<string, number>;
  next_expected_update: string | null;
  next_expected_model: string | null;
  marker_health?: "ok" | "suspect";
  models?: Record<string, ModelStatus>;
  sources?: ModelSourceDetail[];
  // What pressing the refresh button will actually do at the current lead
  // time. Present on GET /packs/freshness and the gated refresh complete;
  // absent from contexts without a flight.
  refresh_decision?: RefreshDecision | null;
}

/**
 * One structured event from the briefing pipeline — wire-safe shape
 * that mirrors the backend's `DiagnosticPublic`, NOT the full Python
 * `Diagnostic`.
 *
 * `level` and `message` are user-facing (rendered in the freshness
 * banner). The other fields are structured context. Pydantic serialises
 * absent optional fields as `null`, so consumers should treat
 * `value == null` as "not set" rather than checking `=== undefined`.
 * Legacy DB rows (pre-typed model) only carry `{level, message}`.
 *
 * `detail` (stack traces, file paths) and `request_id` (Anthropic
 * correlation id) are deliberately absent — `PackMetaResponse` strips
 * them at the API boundary via `Diagnostic.to_public()`. If you ever
 * need them client-side, that's a separate authenticated admin
 * endpoint, not this one.
 */
export type DiagnosticLevel = 'info' | 'warn' | 'error';

export interface Diagnostic {
  level: DiagnosticLevel;
  message: string;
  stage?: string | null;
  code?: string | null;
  error_id?: string | null;
  occurred_at?: string | null;
}

export interface PackMeta {
  flight_id: string;
  fetch_timestamp: string;
  days_out: number;
  has_gramet: boolean;
  has_skewt: boolean;
  has_digest: boolean;
  // False when the profile had the AI summary toggled off for this pack — the
  // UI shows "AI summary off" + a Generate button instead of a spinner.
  // Defaults true (legacy packs / older backends omit it).
  llm_digest_requested?: boolean;
  has_advisories?: boolean;
  has_alt_advisories?: boolean;
  assessment: string | null;
  assessment_reason: string | null;
  alt_assessment?: string | null;
  alt_assessment_reason?: string | null;
  model_init_times?: Record<string, number>;
  grib_init_times?: Record<string, number>;
  models_skipped_region?: string[];
  diagnostics?: Diagnostic[];
  data_status?: DataStatus | null;
  dwd_charts_run_cycle?: string | null;
  dwd_charts_default_id?: string | null;
  dwd_charts_in_coverage?: boolean;
  dwd_charts_within_horizon?: boolean;
  metoffice_charts_run_cycle?: string | null;
  metoffice_charts_default_id?: string | null;
  metoffice_charts_in_coverage?: boolean;
  metoffice_charts_within_horizon?: boolean;
  metoffice_charts_public?: boolean;
}

export interface ModelDivergence {
  variable: string;
  model_values: Record<string, number>;
  mean: number;
  spread: number;
  agreement: 'good' | 'moderate' | 'poor';
}

export type IcingRisk = 'none' | 'light' | 'moderate' | 'severe';
export type IcingType = 'none' | 'rime' | 'mixed' | 'clear';
export type CloudCoverage = 'sct' | 'bkn' | 'ovc';
export type ConvectiveRisk = 'none' | 'marginal' | 'low' | 'moderate' | 'high' | 'extreme';
export type VerticalMotionClass = 'quiescent' | 'synoptic_ascent' | 'synoptic_subsidence' | 'convective' | 'oscillating' | 'unavailable';
export type CATRiskLevel = 'none' | 'light' | 'moderate' | 'severe';

export interface ThermodynamicIndices {
  lcl_altitude_ft: number | null;
  lfc_altitude_ft: number | null;
  el_altitude_ft: number | null;
  cape_surface_jkg: number | null;
  cin_surface_jkg: number | null;
  lifted_index: number | null;
  k_index: number | null;
  total_totals: number | null;
  precipitable_water_mm: number | null;
  freezing_level_ft: number | null;
  minus10c_level_ft: number | null;
  minus20c_level_ft: number | null;
  bulk_shear_0_6km_kt: number | null;
  bulk_shear_0_1km_kt: number | null;
  sounding_ceiling_ft: number | null;
  nwp_ceiling_ft: number | null;
  nwp_cape_jkg: number | null;
  nwp_cape_type: string | null;
  nwp_cin_jkg: number | null;
  nwp_lifted_index: number | null;
  nwp_freezing_level_ft: number | null;
  cape_raw_vs_calc_divergent: boolean | null;
}

export interface EnhancedCloudLayer {
  base_ft: number;
  top_ft: number;
  thickness_ft: number | null;
  mean_temperature_c: number | null;
  coverage: CloudCoverage;
  mean_dewpoint_depression_c: number | null;
  mean_cloud_cover_pct: number | null;
  source: string;
}

export interface IcingZone {
  base_ft: number;
  top_ft: number;
  risk: IcingRisk;
  icing_type: IcingType;
  sld_risk: boolean;
  mean_temperature_c: number | null;
  mean_wet_bulb_c: number | null;
  mean_rh_pct: number | null;
  mean_icing_index: number | null;
}

export interface SfipZone {
  base_ft: number;
  top_ft: number;
  risk: IcingRisk;
  icing_type: IcingType;
  mean_sfip_100: number | null;
  mean_temperature_c: number | null;
  mean_rh_pct: number | null;
  variant: string;  // "full" or "proxy"
}

export interface SldZone {
  base_ft: number;
  top_ft: number;
  risk: IcingRisk;
  mechanism: string;  // "warm_nose" or "coalescence"
  mean_temperature_c: number | null;
}

export interface ConvectiveAssessment {
  risk_level: ConvectiveRisk;
  cape_jkg: number | null;
  cin_jkg: number | null;
  lcl_altitude_ft: number | null;
  lfc_altitude_ft: number | null;
  el_altitude_ft: number | null;
  bulk_shear_0_6km_kt: number | null;
  lifted_index: number | null;
  k_index: number | null;
  total_totals: number | null;
  severe_modifiers: string[];
  base_ft: number | null;
  top_ft: number | null;
  cover_pct: number | null;
  method: string;
}

export interface CATRiskLayer {
  base_ft: number;
  top_ft: number;
  base_pressure_hpa: number | null;
  top_pressure_hpa: number | null;
  richardson_number: number | null;
  risk: CATRiskLevel;
}

export interface VerticalMotionAssessment {
  classification: VerticalMotionClass;
  max_omega_pa_s: number | null;
  max_w_fpm: number | null;
  max_w_level_ft: number | null;
  cat_risk_layers: CATRiskLayer[];
  e_shear_layers: CATRiskLayer[];
  convective_contamination: boolean;
}

export interface InversionLayer {
  base_ft: number;
  top_ft: number;
  base_pressure_hpa: number | null;
  top_pressure_hpa: number | null;
  strength_c: number;
  base_temperature_c: number | null;
  top_temperature_c: number | null;
  surface_based: boolean;
}

export interface NWPCloudLayerDiag {
  cover_pct: number | null;
  base_ft: number | null;
  top_ft: number | null;
  top_temp_c: number | null;
}

export interface NWPCloudDiagnostics {
  low: NWPCloudLayerDiag;
  mid: NWPCloudLayerDiag;
  high: NWPCloudLayerDiag;
  convective_cover_pct: number | null;
  convective_base_ft: number | null;
  convective_top_ft: number | null;
  total_cover_pct: number | null;
  boundary_cover_pct: number | null;
  ceiling_ft: number | null;
}

export interface SoundingAnalysis {
  indices: ThermodynamicIndices | null;
  cloud_layers: EnhancedCloudLayer[];
  nwp_cloud_layers: EnhancedCloudLayer[] | null;
  icing_zones: IcingZone[];
  icing_ogimet_nwp_zones: IcingZone[];
  sfip_zones: SfipZone[];
  ieng_icing_zones?: IcingZone[];
  sld_zones?: SldZone[];
  inversion_layers: InversionLayer[];
  convective: ConvectiveAssessment | null;
  convective_nwp: ConvectiveAssessment | null;
  vertical_motion: VerticalMotionAssessment | null;
  cloud_cover_low_pct: number | null;
  cloud_cover_mid_pct: number | null;
  cloud_cover_high_pct: number | null;
  // Surface fields used for surface-obscuration cross-section layer.
  // Optional for backward-compat with snapshots taken before these
  // fields were added to SoundingAnalysis.
  visibility_m?: number | null;
  temperature_2m_c?: number | null;
  dewpoint_2m_c?: number | null;
  nwp_cloud_diagnostics: NWPCloudDiagnostics | null;
}

export interface VerticalRegime {
  floor_ft: number;
  ceiling_ft: number;
  in_cloud: boolean;
  icing_risk: IcingRisk;
  icing_type: IcingType;
  inversion: boolean;
  cloud_cover_pct: number | null;
  cat_risk: string | null;
  strong_vertical_motion: boolean;
  label: string;
  // Cloud diagnostics
  cloud_coverage: string | null;
  mean_temperature_c: number | null;
  mean_dewpoint_depression_c: number | null;
  // Icing diagnostics
  sld_risk: boolean;
  mean_wet_bulb_c: number | null;
  mean_rh_pct: number | null;
  mean_icing_index: number | null;
  // Inversion diagnostics
  inversion_strength_c: number | null;
  inversion_surface_based: boolean;
}

export interface AltitudeAdvisory {
  advisory_type: string;
  altitude_ft: number | null;
  feasible: boolean;
  reason: string;
  per_model_ft: Record<string, number | null>;
}

export interface AltitudeAdvisories {
  regimes: Record<string, VerticalRegime[]>;
  advisories: AltitudeAdvisory[];
  cruise_in_icing: boolean;
  cruise_icing_risk: IcingRisk;
}

export interface WaypointAnalysis {
  waypoint: { icao: string; name: string };
  sounding: Record<string, SoundingAnalysis>;
  altitude_advisories: AltitudeAdvisories | null;
  model_divergence: ModelDivergence[];
}

export interface WeatherDigest {
  assessment: 'GREEN' | 'AMBER' | 'RED';
  assessment_reason: string;
  synoptic: string;
  specific_concerns: string;
  trend: string;
  watch_items: string;
  // Profile tracking: which profile was active when digest was generated
  profile_id?: number | null;
  profile_name?: string | null;
  // Legacy fields (may be present in older digests)
  winds?: string;
  cloud_visibility?: string;
  precipitation_convection?: string;
  icing?: string;
  model_agreement?: string;
}

export interface AirportObservation {
  icao: string;
  name: string | null;
  distance_from_route_nm: number;
  enroute_distance_nm: number | null;
  nearest_waypoint_icao: string;
  metar_raw: string | null;
  metar_time: string | null;
  metar_flight_category: string | null;
  metar_ceiling_ft: number | null;
  metar_visibility_m: number | null;
  metar_wind_dir: number | null;
  metar_wind_speed_kt: number | null;
  metar_wind_gust_kt: number | null;
  metar_weather: string[];
  metar_temperature_c: number | null;
  metar_dewpoint_c: number | null;
  metar_qnh: number | null;
  taf_raw: string | null;
  taf_flight_category_at_eta: string | null;
  taf_trend_type: string | null;
  taf_wind_dir: number | null;
  taf_wind_speed_kt: number | null;
  taf_wind_gust_kt: number | null;
  taf_applicable_text: string | null;
  taf_applicable_lines: number[];
  metar_wind_advisory: string | null;
  metar_best_runway_id: string | null;
  metar_crosswind_kt: number | null;
  metar_headwind_kt: number | null;
  taf_wind_advisory: string | null;
  taf_best_runway_id: string | null;
  taf_crosswind_kt: number | null;
  taf_headwind_kt: number | null;
  has_metar: boolean;
  has_taf: boolean;
  eta_hour_offset: number | null;
}

export interface ObservationComparison {
  icao: string;
  obs_category: string | null;
  model_category: string | null;
  category_match: string;
  ceiling_delta_ft: number | null;
  visibility_delta_m: number | null;
  wind_speed_delta_kt: number | null;
  model_wind_dir: number | null;
  model_wind_speed_kt: number | null;
  model_wind_gust_kt: number | null;
  model_wind_advisory: string | null;
  model_best_runway_id: string | null;
  model_crosswind_kt: number | null;
  wind_advisory_match: string | null;
  detail: string;
}

export interface RouteObservations {
  corridor_nm: number;
  fetch_time: string;
  airports_found: number;
  airports_with_metar: number;
  airports_with_taf: number;
  airports: AirportObservation[];
  comparisons: ObservationComparison[];
  worst_metar_category: string | null;
  worst_taf_category: string | null;
  has_conflicts: boolean;
  phenomena_along_route: string[];
}

export interface SigmetAlongRoute {
  fir_id: string;
  fir_name: string | null;
  hazard: string | null;
  qualifier: string | null;
  base_ft: number | null;
  top_ft: number | null;
  valid_from: string | null;
  valid_to: string | null;
  direction: string | null;
  speed_kt: number | null;
  raw_text: string;
  matched_firs: string[];
  min_distance_nm: number | null;
  enroute_distance_from_nm: number | null;
  enroute_distance_to_nm: number | null;
  // Polygon outline as [lon, lat] vertices (for a future map/cross-section overlay).
  coords: Array<[number, number]>;
}

export interface RouteSigmets {
  corridor_nm: number;
  fetch_time: string;
  altitude_low_ft: number | null;
  altitude_high_ft: number | null;
  time_window_from: string | null;
  time_window_to: string | null;
  route_firs: string[];
  sigmets: SigmetAlongRoute[];
  hazards: string[];
  has_severe: boolean;
  count: number;
}

/** What got worse since the previous real-time refresh (deterministic, no LLM). */
export interface RefreshDelta {
  worsened: boolean;
  messages: string[];
  computed_at: string | null;
}

/** Combined output of the cheap D-0 real-time refresh: observations + SIGMETs. */
export interface RealtimeRefreshResult {
  observations: RouteObservations;
  sigmets: RouteSigmets | null;
  delta?: RefreshDelta | null;
}

/** One weather-based divert candidate (issue #210). */
/** One criterion (ceiling or visibility) vs its requirement band (#249). */
export interface CriterionAssessment {
  label: string;
  unit: string;
  forecast: number | null;
  required_min: number;
  required_max: number;
  verdict: 'likely' | 'marginal' | 'unlikely' | 'not_required' | 'required';
}

/** Destination "alternate required?" for one regulatory regime (#249). */
export interface RegAlternateTrigger {
  regime: 'faa' | 'easa';
  status: 'not_required' | 'marginal' | 'required';
  reason: string;
  source: 'taf' | 'nwp' | 'none';
  triggered_by_tempo: boolean;
  ceiling: CriterionAssessment;
  visibility: CriterionAssessment;
  /** Provenance of the ceiling requirement (approach class · est DH + margin). */
  ceiling_basis?: string | null;
}

/** Per-candidate alternate-minima qualification for one regime (#249). */
export interface AlternateQual {
  regime: 'faa' | 'easa';
  verdict: 'likely' | 'marginal' | 'unlikely';
  reason: string;
  ceiling: CriterionAssessment;
  visibility: CriterionAssessment;
  /** Provenance of the ceiling requirement (approach class · est DH + margin). */
  ceiling_basis?: string | null;
}

/** A TAF TEMPO/PROB group overlapping the ETA window (descriptive, #249). */
export interface ConditionalGroup {
  kind: string;
  probability: number | null;
  ceiling_ft: number | null;
  visibility_m: number | null;
  validity: string | null;
  counted: boolean;
}

/** Regulatory alternate-requirement assessment for the destination (#249). */
export interface AlternateRequirement {
  destination_icao: string;
  eta: string | null;
  faa: RegAlternateTrigger;
  easa: RegAlternateTrigger;
  caveats: string[];
  main_body_ceiling_ft: number | null;
  main_body_visibility_m: number | null;
  conditionals: ConditionalGroup[];
  computed_at: string | null;
}

export interface AlternateAirport {
  icao: string;
  name: string | null;
  lat: number;
  lon: number;
  distance_from_dest_nm: number;
  enroute_distance_nm: number | null;
  segment_distance_nm: number | null;
  position: 'before' | 'after';
  detour_early_nm: number | null;
  detour_late_nm: number | null;
  flight_category: string;
  wind_speed_kt: number | null;
  crosswind_kt: number | null;
  headwind_kt: number | null;
  best_runway_id: string | null;
  ceiling_ft: number | null;
  visibility_m: number | null;
  agreement: Record<string, string>;
  per_model: Record<string, Record<string, unknown>>;
  has_instrument_approach: boolean;
  best_approach_type: string | null;
  longest_runway_ft: number | null;
  has_hard_runway: boolean;
  point_of_entry: boolean;
  better_category: boolean;
  better_wind: boolean;
  better_crosswind: boolean;
  dominates_destination: boolean;
  faa: AlternateQual | null;
  easa: AlternateQual | null;
}

/** The nearest improving alternate for one deficient axis. */
export interface AlternateAxisPick {
  axis: 'category' | 'wind' | 'crosswind';
  icao: string | null;
  distance_from_dest_nm: number | null;
  position: 'before' | 'after' | null;
}

/** Weather-based alternates for a route's destination (D-2 inward). */
export interface RouteAlternates {
  destination_icao: string;
  destination_category: string;
  destination_crosswind_kt: number | null;
  destination_ceiling_ft: number | null;
  destination_visibility_m: number | null;
  eta: string | null;
  corridor_nm: number;
  radius_nm: number;
  require_approach: boolean;
  approach_filter_relaxed: boolean;
  candidates_evaluated: number;
  alternates: AlternateAirport[];
  nearest_improving: AlternateAxisPick[];
  alternate_requirement: AlternateRequirement | null;
  computed_at: string | null;
}

export interface ForecastSnapshot {
  route: {
    name: string;
    waypoints: Array<{ icao: string; name: string; lat: number; lon: number }>;
    cruise_altitude_ft: number;
  };
  target_date: string;
  fetch_date: string;
  days_out: number;
  analyses: WaypointAnalysis[];
  route_observations?: RouteObservations | null;
  route_sigmets?: RouteSigmets | null;
  alternates?: RouteAlternates | null;
  last_refresh_delta?: RefreshDelta | null;
}

export interface WindComponent {
  wind_speed_kt: number;
  wind_direction_deg: number;
  track_deg: number;
  headwind_kt: number;
  crosswind_kt: number;
}

export interface RoutePointAnalysis {
  point_index: number;
  lat: number;
  lon: number;
  distance_from_origin_nm: number;
  waypoint_icao: string | null;
  waypoint_name: string | null;
  interpolated_time: string;
  forecast_hour: string;
  track_deg: number;
  wind_components: Record<string, WindComponent>;
  sounding: Record<string, SoundingAnalysis>;
  altitude_advisories: AltitudeAdvisories | null;
  model_divergence: ModelDivergence[];
  /** model -> temperature (°C) at the elected cruise level. Absent on old packs. */
  cruise_temperature_c?: Record<string, number>;
}

export interface NightInterval {
  start_distance_nm: number;
  end_distance_nm: number;
  start_time: string;
  end_time: string;
  phase: 'twilight' | 'night';
}

export interface SunSideSegment {
  side: 'left' | 'right';
  start_distance_nm: number;
  end_distance_nm: number;
}

export interface SunSideSummary {
  dominant_side: 'left' | 'right' | 'none';
  dominant_side_pct: number;
  segments: SunSideSegment[];
}

export interface GlareAssessment {
  phase: 'takeoff' | 'landing';
  airport_icao: string;
  runway_ident: string | null;
  runway_heading_true: number | null;
  sun_azimuth_true: number | null;
  sun_elevation_deg: number | null;
  relative_bearing_deg: number | null;
  into_sun: boolean;
  is_dark: boolean;
}

export interface SunPoint {
  distance_nm: number;
  elevation_deg: number;
  azimuth_deg: number;
  /** Signed sun azimuth − track, ±180; positive = right of track. */
  relative_bearing_deg: number;
}

export interface RouteSunAnalysis {
  night_intervals: NightInterval[];
  sun_side: SunSideSummary;
  /** Per-route-point sun geometry for the hover readout. Absent on old packs. */
  points?: SunPoint[];
  takeoff: GlareAssessment | null;
  landing: GlareAssessment | null;
}

export interface RouteAnalysesManifest {
  route_name: string;
  target_date: string;
  departure_time: string;
  flight_duration_hours: number;
  total_distance_nm: number;
  cruise_altitude_ft: number;
  models: string[];
  analyses: RoutePointAnalysis[];
  /** Solar analysis (issue #227) — absent on old packs. */
  sun?: RouteSunAnalysis | null;
}

export interface ElevationPoint {
  distance_nm: number;
  elevation_ft: number;
  lat: number;
  lon: number;
}

export interface ElevationProfile {
  route_name: string;
  points: ElevationPoint[];
  max_elevation_ft: number;
  total_distance_nm: number;
}
