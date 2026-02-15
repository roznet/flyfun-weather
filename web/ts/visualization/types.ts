/** Shared types for the cross-section and map visualizations. */

// --- Settings ---

export type VizLayout = 'cross-section' | 'map' | 'split';
export type RenderMode = 'smooth' | 'columns';

export interface VizSettings {
  layout: VizLayout;
  renderMode: RenderMode;
  enabledLayers: Record<string, boolean>;
  mapColorMetric: string;
  mapWidthMetric: string;
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
    mode: RenderMode,
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
}

export interface VizPoint {
  distanceNm: number;
  time: string;
  altitudeLines: AltitudeLines;
  cloudLayers: VizCloudLayer[];
  icingZones: VizIcingZone[];
  catLayers: VizCATLayer[];
  inversions: VizInversionLayer[];
  convectiveRisk: string;
  // Map-specific scalars
  cloudCoverTotalPct: number;
  cloudCoverLowPct: number;
  cloudCoverMidPct: number;
  headwindKt: number;
  crosswindKt: number;
  capeSurfaceJkg: number;
  worstModelAgreement: string;
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
}

export interface VizIcingZone {
  baseFt: number;
  topFt: number;
  risk: string;
  type: string;
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
