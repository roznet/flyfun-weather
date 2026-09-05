/** Shared TypeScript types matching the API response models. */

export interface AircraftInfo {
  id: number;
  icao_type: string;
  type_name: string;
  tail_number: string | null;
  nickname: string | null;
}

/** One named advisory concern for the flights-list summary chips. */
export interface AdvisoryChip {
  status: 'RED' | 'AMBER';
  name: string;
}

/** Compact per-flight advisory breakdown denormalized onto the latest pack. */
export interface AdvisorySummary {
  red: number;
  amber: number;
  top: AdvisoryChip[];
}

/** Summary of a flight's latest briefing pack, inlined in /flights responses.
 *  Carries everything the flights-list card and the debrief form need, so the
 *  page renders without per-flight /packs/latest round-trips. */
export interface BriefingStatusInfo {
  assessment: string | null;
  assessment_reason: string | null;
  // Long-range early outlook (beyond the GRIB horizon): TRENDING_SETTLED /
  // MIXED_SIGNALS / TRENDING_UNSETTLED. Mutually exclusive with assessment —
  // the card shows a soft outlook badge instead of the traffic light.
  outlook?: string | null;
  outlook_reason?: string | null;
  has_digest: boolean;
  days_out: number | null;
  fetch_timestamp: string | null;
  has_advisories: boolean;
  // Compact RED/AMBER breakdown + top named categories for the card chips.
  // null for old packs (pre-#276 or not yet refreshed).
  advisory_summary?: AdvisorySummary | null;
  // True when this flight has a notify-qualifying briefing update the viewer
  // hasn't opened yet (same predicate as the app-icon badge). Drives the
  // flight-card red "unseen" dot. Absent on older servers → treated as false.
  unseen?: boolean;
}

/** Weather-coverage status for a flight saved beyond the forecast horizon.
 *  Present on {@link FlightResponse.coverage} only while no model reaches the
 *  flight date yet. */
export interface CoveragePending {
  available_date: string;            // ISO date — first (early-outlook) briefing appears
  full_briefing_date?: string | null; // ISO date — full GRIB briefing, if resolved
  days_until_available: number;      // whole days from today until available_date
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
  // Timing-scenario Flexibility mode (what the scenario job grades).
  // 'alternate' uses alt_departure_time; day modes scan a daylight window.
  flexibility: 'none' | 'alternate' | 'same_day' | 'prev_day' | 'next_day';
  target_date: string;        // backward compat (computed from departure_time)
  target_time_utc: number;    // backward compat (computed from departure_time)
  cruise_altitude_ft: number;
  flight_ceiling_ft: number;
  flight_duration_hours: number;
  private: boolean;
  auto_refresh: boolean;
  auto_refresh_hour: number | null;
  // Per-flight briefing-notification override: follow the account setting
  // ('default'), always notify ('notify'), or never ('mute').
  notify_override: 'default' | 'notify' | 'mute';
  created_at: string;
  latest_briefing?: BriefingStatusInfo | null;
  // Present only when the flight is saved beyond the forecast horizon (no
  // model data yet). Drives the pending "available dd/mm" list chip and the
  // pending-coverage summary card. Null/absent once the flight is in range.
  coverage?: CoveragePending | null;
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
  // Timing-scenario Flexibility. 'alternate' is set post-create via PATCH
  // (needs an alt time), so create only offers the scan modes.
  flexibility?: 'none' | 'same_day' | 'prev_day' | 'next_day';
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
  // Long-range outlook (beyond the GRIB horizon), shown instead of the
  // traffic-light assessment: TRENDING_SETTLED / MIXED_SIGNALS / TRENDING_UNSETTLED.
  // Mutually exclusive with assessment.
  outlook?: string | null;
  outlook_reason?: string | null;
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
  // Météo-France TEMSI. A (zone, validity) list rather than a run cycle plus
  // offsets: AEROWEB keys charts by absolute valid time, so the picker offers
  // pairs ("France 15Z", "EUROC 18Z"). Nearest-to-ETD first; ids are
  // `zone|run_cycle`. Empty is the normal case for a briefing built more than
  // a few hours out — TEMSI only runs ~3h ahead.
  meteofrance_charts_options?: Array<{ zone: string; run_cycle: string }>;
  meteofrance_charts_default_id?: string | null;
  meteofrance_charts_in_coverage?: boolean;
  meteofrance_charts_within_horizon?: boolean;
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
  convective_precip_mm_h: number | null;
  method: string;
}

export interface CATRiskLayer {
  base_ft: number;
  top_ft: number;
  base_pressure_hpa: number | null;
  top_pressure_hpa: number | null;
  richardson_number: number | null;
  risk: CATRiskLevel;
  /** Layer lies wholly inside the boundary layer (#533) — a SEVERE one is
   *  graded by route percentage rather than forcing RED on its own. */
  boundary_layer?: boolean;
}

export interface VerticalMotionAssessment {
  classification: VerticalMotionClass;
  max_omega_pa_s: number | null;
  max_w_fpm: number | null;
  max_w_level_ft: number | null;
  cat_risk_layers: CATRiskLayer[];
  e_shear_layers: CATRiskLayer[];
  convective_contamination: boolean;
  /** Top of the surface well-mixed layer, when detected (#533). CAT layers
   *  below it are suppressed as boundary-layer roughness, not KH shear. */
  mixed_layer_top_ft?: number | null;
  /** Which detector produced `mixed_layer_top_ft` (#540): "model" = the
   *  model's own diagnosed PBL height (ECMWF `blh`), "derived" = the θv
   *  parcel walk. The two disagree by thousands of feet on stable profiles,
   *  where the walk is blind. Absent on older packs. */
  mixed_layer_top_source?: string | null;
  /** The model's own ground in the column's height datum (#541), from its
   *  surface pressure. The AGL datum for `boundary_layer`, and the cut below
   *  which levels are sub-surface extrapolation. Absent on older packs. */
  model_surface_altitude_ft?: number | null;
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
  // Short-range (within GRIB horizon) fields. Optional because a long-range
  // digest carries `outlook`/`outlook_reason` instead (the two shapes are
  // distinguished by the presence of `outlook`).
  assessment?: 'GREEN' | 'AMBER' | 'RED';
  assessment_reason?: string;
  specific_concerns?: string;
  // Long-range (beyond GRIB horizon) outlook fields.
  outlook?: 'TRENDING_SETTLED' | 'TRENDING_UNSETTLED' | 'MIXED_SIGNALS';
  outlook_reason?: string;
  // Shared fields (present in both regimes).
  synoptic: string;
  trend: string;
  watch_items: string;
  model_agreement?: string;
  // Profile tracking: which profile was active when digest was generated
  profile_id?: number | null;
  profile_name?: string | null;
  // Legacy fields (may be present in older digests)
  winds?: string;
  cloud_visibility?: string;
  precipitation_convection?: string;
  icing?: string;
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

// --- Observed conditions (#574) --------------------------------------------
//
// What a pilot can SEE along the corridor right now — radar, lightning and
// satellite cloud tops — sampled in concentric discs around each route point.
// Phase 1 displays observations only: nothing here carries a verdict and no
// advisory reads it. The cross-check is visual, with the observed-tops layer
// drawn over the NWP cloud bands.
//
// Two invariants the shapes below exist to preserve, and that any consumer
// must respect:
//
//   1. Absence is three-state, per source. `nodata_px` (the sensor does not
//      look here — about half the OPERA grid) is never the same thing as
//      `undetect_px` (it looked and saw nothing). Rendering a `nodata` disc as
//      clear sky is the single worst bug this layer can have, which is what
//      `insufficient_coverage` is for.
//   2. There is no shared timestamp. Each field carries its own frame's
//      `valid_time` and `age_minutes`; radar scans span the preceding
//      10 minutes plus delivery lag, so an echo on screen can be ~15
//      minutes old — about 30 NM of own-ship at 120 kt.

export interface ObservedAttribution {
  producer: string | null;
  license: string | null;
  url: string | null;
  /** Ready-to-render provenance line — use this rather than recomposing. */
  text: string;
}

/** One station × one radius × one field. Cumulative disc, not a ring. */
export interface ObservedAnnulus {
  radius_nm: number;
  total_px: number;
  valid_px: number;
  /** Pixels the sensor does not cover. NOT "nothing there". */
  nodata_px: number;
  /** Pixels the sensor covered and found empty — a real observation. */
  undetect_px: number;
  detected_px: number;
  max_value: number | null;
  mean_value: number | null;
  p90_value: number | null;
  coverage_fraction: number;
  /** `null` when nothing was looked at — deliberately not 0, which reads as clear. */
  detected_fraction: number | null;
  /** Limited coverage: retain detected values with a qualifier, never assert clear. */
  insufficient_coverage: boolean;
}

/** Cloud-top disc: adds the histograms a single top-per-pixel value destroys. */
export interface ObservedTopsAnnulus extends ObservedAnnulus {
  /** Legacy wire keys for geometric-height bins in hundreds of ft MSL, not pressure FL. */
  fl_bins: Record<string, number>;
  /** Sparse fine histogram: non-empty 1000-ft bands, keyed by the band's
   *  lower edge in hundreds of ft MSL ("60" == 6000–7000 ft). A renderer should
   *  draw: a bar spanning a coarse bucket claims cloud through air where none
   *  was measured, and erases the gaps between decks. */
  fl_fine: Record<string, number>;
  /** Retrieval method counts, not confidence. QM0 = not processed;
   *  QM9 = Opaque + RTM + inversion (EUMETSAT Table 10), not multilayer. */
  quality_method: Record<string, number>;
  highest_fl: number | null;
  /** Coldest top in the disc (K) — the deepest convection, not an average. */
  coldest_top_k: number | null;
  /** IR cloud amount × emissivity at the highest top and disc median, not visible opacity. */
  highest_cloudiness: number | null;
  median_cloudiness: number | null;
  /** Pressure-based FL of the highest top. Coarse (10 FL steps) and can
   *  diverge from the geometric height; secondary, never a replacement. */
  highest_aviation_fl: number | null;
}

/** Lightning disc. No coverage split: the imager sees the whole disc, so an
 *  absence of flashes is an observation rather than a gap. */
export interface ObservedFlashAnnulus {
  radius_nm: number;
  flash_count: number;
  area_km2: number;
  window_minutes: number;
  nearest_flash_nm: number | null;
  latest_flash_time: string | null;
  flashes_per_1000km2_per_min: number | null;
}

export interface ObservedStationRef {
  id: string;
  name: string | null;
  lat: number;
  lon: number;
  enroute_distance_nm: number | null;
  distance_from_route_nm: number | null;
}

export interface ObservedStationSamples<A> {
  station_id: string;
  annuli: A[];
}

interface ObservedFieldMeta {
  source: string;
  quantity: string;
  units: string;
  valid_time: string;
  age_minutes: number;
  /** Source accumulation/acquisition window; zero means no window supplied. */
  window_minutes: number;
  attribution: ObservedAttribution;
}

export interface ObservedField extends ObservedFieldMeta {
  stations: Array<ObservedStationSamples<ObservedAnnulus>>;
}

export interface ObservedTopsField extends ObservedFieldMeta {
  stations: Array<ObservedStationSamples<ObservedTopsAnnulus>>;
}

export interface ObservedFlashField extends ObservedFieldMeta {
  stations: Array<ObservedStationSamples<ObservedFlashAnnulus>>;
}

export interface ObservedSourceStatus {
  source: string;
  available: boolean;
  reason: string | null;
  latest_valid_time: string | null;
}

export interface ObservedConditions {
  computed_at: string;
  corridor_nm: number;
  /** All radii ship together, so the corridor selector is a client-side pick
   *  with no re-fetch. */
  radii_nm: number[];
  stations: ObservedStationRef[];
  reflectivity: ObservedField | null;
  rain_rate: ObservedField | null;
  cloud_tops: ObservedTopsField | null;
  lightning: ObservedFlashField | null;
  /** Deterministic "Observed now" readout — no LLM. */
  summary: string;
  /** Structured form of the readout: one entry per clause, tagged with the
   *  source it came from and the metric-catalog card that explains it. The
   *  clauses are not uniformly shaped ("Radar: peak 38 dBZ…" vs "Rain rate to
   *  1.8 mm/h…"), so never recover the source by parsing the prose. */
  summary_entries: ObservedSummaryEntry[];
  summary_lines: string[];
  sources: ObservedSourceStatus[];
  has_any_field: boolean;
}

export interface ObservedSummaryEntry {
  /** lightning | reflectivity | rain_rate | cloud_tops | coverage | unavailable */
  kind: string;
  text: string;
  /** Metric-catalog id for the (i) popup; empty when no card explains it. */
  metric_id: string;
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
  /** Re-sampled from locally-held frames, which is why the refresh button
   *  updates the observed panel without any provider fetch. */
  observed?: ObservedConditions | null;
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
  /** Forecast provenance: 'taf' when a TAF covered the candidate's ETA window
   * (D-0), else 'nwp' for the NWP-consensus model estimate. */
  source: 'taf' | 'nwp';
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

export interface OperationalFlag {
  code: string;
  label: string;
  detail: string;
  severity: 'amber' | 'red';
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
  iso_country: string | null;
  is_major: boolean;
  operational_flags: OperationalFlag[];
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
  /** Observed radar / lightning / cloud tops along the corridor (#574).
   *  D-0 only, and only where the observed collector is enabled. */
  observed_conditions?: ObservedConditions | null;
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
