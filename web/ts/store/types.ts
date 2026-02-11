/** Shared TypeScript types matching the API response models. */

export interface RouteInfo {
  name: string;
  display_name: string;
  waypoints: string[];
  cruise_altitude_ft: number;
  flight_duration_hours: number;
}

export interface FlightResponse {
  id: string;
  route_name: string;
  waypoints: string[];
  target_date: string;
  target_time_utc: number;
  cruise_altitude_ft: number;
  flight_duration_hours: number;
  created_at: string;
}

export interface CreateFlightRequest {
  route_name?: string;
  waypoints: string[];
  target_date: string;
  target_time_utc?: number;
  cruise_altitude_ft?: number;
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

export interface WaypointAnalysis {
  waypoint: { icao: string; name: string };
  model_divergence: ModelDivergence[];
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
