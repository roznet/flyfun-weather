/** Shared TypeScript types matching the API response models. */

export interface RouteInfo {
  name: string;
  display_name: string;
  waypoints: string[];
  cruise_altitude_ft: number;
  flight_ceiling_ft: number;
  flight_duration_hours: number;
}

export interface FlightResponse {
  id: string;
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

export interface PackMeta {
  flight_id: string;
  fetch_timestamp: string;
  days_out: number;
  has_gramet: boolean;
  has_skewt: boolean;
  has_digest: boolean;
  assessment: string | null;
  assessment_reason: string | null;
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
export type ConvectiveRisk = 'none' | 'low' | 'moderate' | 'high' | 'extreme';

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

export interface SoundingAnalysis {
  indices: ThermodynamicIndices | null;
  cloud_layers: EnhancedCloudLayer[];
  icing_zones: IcingZone[];
  convective: ConvectiveAssessment | null;
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
