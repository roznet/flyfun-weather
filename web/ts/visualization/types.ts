/** Shared types for the cross-section and map visualizations. */

// --- Settings ---

export type VizLayout = 'cross-section' | 'map' | 'split' | 'compare';
export type CompareBandMode = 'overlay' | 'overlay-soft' | 'consensus' | 'consensus-outline';

export interface VizSettings {
  layout: VizLayout;
  enabledLayers: Record<string, boolean>;
  mapColorMetric: string;
  mapWidthMetric: string;
  mapAltitudeFt: number | null;  // altitude for level-dependent map metrics (null = cruise)
  routeGraphVisible: boolean;
  routeGraphLeftMetric: string;
  routeGraphRightMetric: string;  // 'none' to disable right axis
  compareLayer: string;
  compareModels: Record<string, boolean>;
  compareBandMode: CompareBandMode;
  vizTheme?: string;
}

// --- Coordinate Transform ---

export interface PlotArea {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface CoordTransform {
  distanceToX(distanceNm: number): number;
  altitudeToY(altitudeFt: number): number;
  xToDistance(x: number): number;
  yToAltitude(y: number): number;
  readonly plotArea: PlotArea;
}

// --- Cross-Section Layer ---

export interface CrossSectionLayer {
  readonly id: string;
  readonly name: string;
  readonly group: LayerGroup;
  readonly defaultEnabled: boolean;
  readonly metricId?: string;
  render(
    ctx: CanvasRenderingContext2D,
    transform: CoordTransform,
    data: VizRouteData,
  ): void;
}

export type LayerGroup =
  | 'terrain'
  | 'temperature'
  | 'clouds'
  | 'icing'
  | 'stability'
  | 'turbulence'
  | 'convection'
  | 'reference';

// --- Terrain ---

export interface TerrainPoint {
  distanceNm: number;
  elevationFt: number;
}

// --- Viz-ready data structures ---

export interface VizRouteData {
  points: VizPoint[];
  cruiseAltitudeFt: number;
  /** Actual flight ceiling from route config (e.g. 18000). */
  ceilingAltitudeFt: number;
  /** Y-axis maximum = max(ceiling, cruise) + 5000. */
  flightCeilingFt: number;
  totalDistanceNm: number;
  waypointMarkers: WaypointMarker[];
  departureTime: string;
  flightDurationHours: number;
  terrainProfile: TerrainPoint[] | null;
}

export interface WaypointMarker {
  distanceNm: number;
  icao: string;
  lat: number;
  lon: number;
}

export interface VizCloudDiagLayer {
  coverPct: number | null;
  baseFt: number | null;
  topFt: number | null;
}

export interface VizCloudDiag {
  low: VizCloudDiagLayer;
  mid: VizCloudDiagLayer;
  high: VizCloudDiagLayer;
  ceilingFt: number | null;
}

export interface VizPoint {
  distanceNm: number;
  lat: number;
  lon: number;
  time: string;
  altitudeLines: AltitudeLines;
  cloudLayers: VizCloudLayer[];
  icingZones: VizIcingZone[];
  icingOgimetNwpZones: VizIcingZone[];
  sfipZones: VizSfipZone[];
  iengIcingZones: VizIcingZone[];
  sldZones: VizSldZone[];
  catLayers: VizCATLayer[];
  eShearLayers: VizCATLayer[];
  inversions: VizInversionLayer[];
  convectiveRisk: string;
  convectiveBaseFt: number | null;
  convectiveTopFt: number | null;
  // NWP convective fields (null when GRIB2 unavailable)
  nwpConvectiveRisk: string;
  nwpConvectiveBaseFt: number | null;
  nwpConvectiveTopFt: number | null;
  nwpConvectiveCoverPct: number | null;
  /**
   * True when the model produced a convective_nwp assessment (regardless
   * of risk level). Distinguishes "computed, no convection" (true) from
   * "no NWP data" (false) — use for toggle availability gating.
   */
  hasNwpConvective: boolean;
  // Map-specific scalars
  cloudCoverTotalPct: number;
  cloudCoverLowPct: number;
  cloudCoverMidPct: number;
  headwindKt: number;
  crosswindKt: number;
  capeSurfaceJkg: number;
  worstModelAgreement: string;
  /**
   * Native NWP cloud layers (GRIB diagnostics or per-level cc).
   * `null` when the model has no native NWP cloud envelope at all —
   * use this to gate the cross-section "NWP Layers" toggle.
   * `[]` when a native source is available but produced no layers
   * (genuine clear-sky forecast).
   */
  nwpCloudLayers: VizCloudLayer[] | null;
  // GFS cloud diagnostics (null when not available)
  nwpCloudDiag: VizCloudDiag | null;
  // Ceiling values (ft MSL)
  soundingCeilingFt: number | null;
  /** Terrain elevation at this point (ft MSL), for AGL conversion. */
  terrainElevationFt: number;
  // Route graph scalars (extracted from model_divergence)
  temperatureC: number | null;
  precipitationMm: number | null;
}

export interface AltitudeLines {
  freezingLevelFt: number | null;
  minus10cLevelFt: number | null;
  minus20cLevelFt: number | null;
  lclAltitudeFt: number | null;
  lfcAltitudeFt: number | null;
  elAltitudeFt: number | null;
}

export interface VizCloudLayer {
  baseFt: number;
  topFt: number;
  coverage: string;
  /** Mean dewpoint depression in °C (0–3). Lower = denser cloud. */
  meanDewpointDepressionC?: number;
  /** How this layer was derived: "dd", "grib", "synthesized". */
  source?: string;
}

export interface VizIcingZone {
  baseFt: number;
  topFt: number;
  risk: string;
  type: string;
}

export interface VizSfipZone {
  baseFt: number;
  topFt: number;
  risk: string;
  type: string;
  meanSfip100: number | null;
  variant: string;  // "full" or "proxy"
}

export interface VizSldZone {
  baseFt: number;
  topFt: number;
  risk: string;
  mechanism: string;
}

export interface VizCATLayer {
  baseFt: number;
  topFt: number;
  risk: string;
}

export interface VizInversionLayer {
  baseFt: number;
  topFt: number;
  strengthC: number;
}
