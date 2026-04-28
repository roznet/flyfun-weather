/**
 * Types for the dynamic Skew-T renderer.
 * Maps to SoundingProfileResponse from the backend API.
 */

export interface SoundingProfileLevel {
  pressure_hpa: number;
  altitude_ft: number | null;
  temperature_c: number;
  dewpoint_c: number | null;
  wind_speed_kt: number | null;
  wind_direction_deg: number | null;
  relative_humidity_pct: number | null;
  dewpoint_depression_c: number | null;
  wet_bulb_c: number | null;
  theta_e_k: number | null;
  lapse_rate_c_per_km: number | null;
  icing_index: number | null;
  icing_index_nwp: number | null;
  sfip_100: number | null;
  cloud_liquid_water_g_m3: number | null;
  ice_mixing_ratio_g_kg: number | null;
  cloud_area_fraction_pct: number | null;
  richardson_number: number | null;
  omega_pa_s: number | null;
  w_fpm: number | null;
}

export interface ParcelPathPoint {
  pressure_hpa: number;
  temperature_c: number;
}

export interface CloudLayer {
  base_ft: number;
  top_ft: number;
  base_pressure_hpa?: number;
  top_pressure_hpa?: number;
  coverage: string;
}

export interface IcingZone {
  base_ft: number;
  top_ft: number;
  base_pressure_hpa?: number;
  top_pressure_hpa?: number;
  risk: string;
  icing_type?: string;
}

export interface InversionLayer {
  base_ft: number;
  top_ft: number;
  base_pressure_hpa?: number;
  top_pressure_hpa?: number;
  strength_c?: number;
}

export interface SoundingProfileData {
  point_index: number;
  lat: number;
  lon: number;
  distance_from_origin_nm: number;
  waypoint_icao: string | null;
  model: string;
  time: string;
  levels: SoundingProfileLevel[];
  cruise_altitude_ft: number | null;
  track_deg: number | null;
  label: string | null;
  indices: Record<string, number | string | boolean | null> | null;
  parcel_path: ParcelPathPoint[];
  cloud_layers: CloudLayer[];
  nwp_cloud_layers: CloudLayer[];
  icing_zones: IcingZone[];
  icing_ogimet_nwp_zones: IcingZone[];
  sfip_zones: Record<string, unknown>[];
  inversion_layers: InversionLayer[];
  convective: Record<string, unknown> | null;
}

/** Plot area in pixel coordinates. */
export interface PlotArea {
  left: number;
  top: number;
  width: number;
  height: number;
  right: number;
  bottom: number;
}

/** Skew-T configuration parameters. */
export interface SkewTConfig {
  pBottom: number;   // Bottom pressure (hPa), default 1050
  pTop: number;      // Top pressure (hPa), default 250
  tMin: number;      // Min temperature (°C), default -60
  tMax: number;      // Max temperature (°C), default 40
  skewAngle: number; // Isotherm skew angle (degrees), default 45
}

export const DEFAULT_CONFIG: SkewTConfig = {
  pBottom: 1050,
  pTop: 250,
  tMin: -60,
  tMax: 40,
  skewAngle: 45,
};
