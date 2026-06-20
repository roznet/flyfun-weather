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

export type FrontTilt = 'coldward' | 'upright' | 'warmward';

/** One pressure level's node in a vertically-linked front chain. */
export interface FrontChainNode {
  level_hPa: number;        // 925 / 850 / 700
  distance_km: number;      // along-route position of the crossing at this level
  kind: FrontKind;
  intensity: FrontIntensity;
  gradient: number;
  delta_theta_e: number;
  co_location?: FrontCoLocation | null;  // weather at this node (drives the convective glyph)
}

/** A single front linked across pressure levels — the same air-mass boundary
 *  seen at 925/850/700, displaced toward the cold air with height. The cross-
 *  section connects the nodes into one slanted line. `n_levels` is the depth
 *  (1 = shallow/single-level). */
export interface FrontChain {
  nodes: FrontChainNode[];  // ordered bottom→top (925→850→700)
  kind: FrontKind;          // consensus kind
  n_levels: number;
  tilt: FrontTilt;
}

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
  per_model_primary_hPa?: Record<string, number>;  // per-model nearest-cruise level (#203)
  levels: number[];
  gate_config: Record<string, unknown>;
  models: string[];
  per_model: Record<string, RouteFrontAnalysis[]>;  // match by level_hPa
  front_chains?: Record<string, FrontChain[]>;      // model → vertically-linked chains
  models_without_snapshot: string[];
  snapshot_inits: Record<string, string>;
  notes: string[];
}
