/** TypeScript types for the route advisory system, matching Python Pydantic models. */

export type AdvisoryStatus = 'green' | 'amber' | 'red' | 'unavailable';

export interface AdvisoryParameterDef {
  key: string;
  label: string;
  description: string;
  type: string; // "number" | "percent" | "altitude" | "speed" | "boolean"
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

export interface RouteAdvisoriesManifest {
  advisories: RouteAdvisoryResult[];
  catalog: AdvisoryCatalogEntry[];
  route_name: string;
  cruise_altitude_ft: number;
  flight_ceiling_ft: number;
  total_distance_nm: number;
  models: string[];
}
