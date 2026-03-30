/** Shared TypeScript types matching the API response models. */

export interface AircraftInfo {
  id: number;
  icao_type: string;
  type_name: string;
  tail_number: string | null;
  nickname: string | null;
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
}

export interface DataStatus {
  fresh: boolean;
  stale_models: string[];
  model_init_times: Record<string, number>;
  next_expected_update: string | null;
  next_expected_model: string | null;
}

export interface PackMeta {
  flight_id: string;
  fetch_timestamp: string;
  days_out: number;
  has_gramet: boolean;
  has_skewt: boolean;
  has_digest: boolean;
  has_advisories?: boolean;
  has_alt_advisories?: boolean;
  assessment: string | null;
  assessment_reason: string | null;
  alt_assessment?: string | null;
  alt_assessment_reason?: string | null;
  model_init_times?: Record<string, number>;
  grib_init_times?: Record<string, number>;
  models_skipped_region?: string[];
  diagnostics?: {level: string; message: string}[];
  data_status?: DataStatus | null;
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
