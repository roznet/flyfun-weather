/** TypeScript types for the route advisory system, matching Python Pydantic models. */

export type AdvisoryStatus = 'green' | 'amber' | 'red' | 'unavailable';

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

export interface ModelAdvisoryResult {
  model: string;
  status: AdvisoryStatus;
  detail: string;
  affected_points: number;
  total_points: number;
  affected_pct: number;
  affected_nm: number;
  total_nm: number;
}

export interface RouteAdvisoryResult {
  advisory_id: string;
  aggregate_status: AdvisoryStatus;
  aggregate_detail: string;
  per_model: ModelAdvisoryResult[];
  parameters_used: Record<string, number>;
}

export type FlightCategory = 'vfr' | 'mvfr' | 'ifr' | 'lifr';

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
