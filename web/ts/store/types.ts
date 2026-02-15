/** Shared TypeScript types matching the API response models. */

export interface FlightResponse {
  id: string;
  user_id: string;
  route_name: string;
  waypoints: string[];
  target_date: string;
  target_time_utc: number;
  cruise_altitude_ft: number;
  flight_ceiling_ft: number;
  flight_duration_hours: number;
  created_at: string;
}

export interface CreateFlightRequest {
  route_name?: string;
  waypoints: string[];
  target_date: string;
  target_time_utc?: number;
  cruise_altitude_ft?: number;
  flight_ceiling_ft?: number;
  flight_duration_hours?: number;
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
  assessment: string | null;
  assessment_reason: string | null;
  model_init_times?: Record<string, number>;
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
}

export interface EnhancedCloudLayer {
  base_ft: number;
  top_ft: number;
  thickness_ft: number | null;
  mean_temperature_c: number | null;
  coverage: CloudCoverage;
  mean_dewpoint_depression_c: number | null;
}

export interface IcingZone {
  base_ft: number;
  top_ft: number;
  risk: IcingRisk;
  icing_type: IcingType;
  sld_risk: boolean;
  mean_temperature_c: number | null;
  mean_wet_bulb_c: number | null;
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

export interface SoundingAnalysis {
  indices: ThermodynamicIndices | null;
  cloud_layers: EnhancedCloudLayer[];
  icing_zones: IcingZone[];
  inversion_layers: InversionLayer[];
  convective: ConvectiveAssessment | null;
  vertical_motion: VerticalMotionAssessment | null;
  cloud_cover_low_pct: number | null;
  cloud_cover_mid_pct: number | null;
  cloud_cover_high_pct: number | null;
}

export interface VerticalRegime {
  floor_ft: number;
  ceiling_ft: number;
  in_cloud: boolean;
  icing_risk: IcingRisk;
  icing_type: IcingType;
  cloud_cover_pct: number | null;
  cat_risk: string | null;
  strong_vertical_motion: boolean;
  label: string;
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
  winds: string;
  cloud_visibility: string;
  precipitation_convection: string;
  icing: string;
  specific_concerns: string;
  model_agreement: string;
  trend: string;
  watch_items: string;
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
