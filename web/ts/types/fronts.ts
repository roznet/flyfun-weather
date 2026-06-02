/** TypeScript types for the experimental Hewson front-detection artifact (#196),
 *  matching the Python Pydantic models in `weatherbrief/models/fronts.py`.
 *
 *  Present only when the "Experimental Auto Front Detection" preference was on
 *  at briefing generation time. Match each `RouteFrontAnalysis` by its
 *  `level_hPa` field, not by position. */

export type FrontKind = 'cold' | 'warm' | 'quasi-stationary';
export type FrontIntensity = 'significant' | 'classical' | 'sharp';
export type FrontTrend = 'closing' | 'receding' | 'steady' | 'unknown';

export interface FrontCrossing {
  lat: number;
  lon: number;
  distance_km: number;
  gradient: number;          // |∇θe|  K/100km
  neg_laplacian: number;     // −∇²θe  K/(100km)²
  advection: number;         // −V·∇θe  K/h
  tfp_before: number;
  tfp_after: number;
  delta_theta_e: number;     // θe jump across the window, K
  kind: FrontKind;
  intensity: FrontIntensity;
  // Relevance enrichment (present on current artifacts; optional for back-compat).
  co_location?: FrontCoLocation | null;
  weather_top_ft?: number | null;   // cloud/convective top at the crossing, ft
  persistence?: number | null;      // [0,1] fraction of ±window frames the gate holds
  vertical_levels?: number | null;  // # of this model's levels detecting the feature (1=shallow)
}

export type FrontCoLocation = 'dry' | 'partly' | 'wet' | 'convective';

export interface FrontProximity {
  distance_km: number;
  lat: number;
  lon: number;
  gradient: number;
  delta_theta_e: number;
  on_track: boolean;
  trend: FrontTrend;
  closing_km_per_h?: number | null;
}

export interface RouteFrontAnalysis {
  model: string;
  level_hPa: number;
  hour: number;
  crossings: FrontCrossing[];
  nearest?: FrontProximity | null;
  // `decisions` (rejection trace) is omitted — calibration-only, not used by UI.
}

export interface RouteFrontsManifest {
  schema_version: number;
  route_name: string;
  generated_at: string;
  primary_level_hPa: number;       // level nearest cruise altitude
  levels: number[];
  gate_config: Record<string, unknown>;
  models: string[];
  per_model: Record<string, RouteFrontAnalysis[]>;  // match by level_hPa
  models_without_snapshot: string[];
  snapshot_inits: Record<string, string>;
  notes: string[];
}
