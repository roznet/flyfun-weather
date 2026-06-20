/** Shared types for the cross-section and map visualizations. */

import type { FrontCrossing, FrontProximity, FrontChain } from '../types/fronts';

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
  /** Which preset the cross-section currently reflects, or `null` for the
   *  "Custom" (dirty) state. Set when a preset is applied (GRAMET or an
   *  advisory preset); cleared to `null` by any user-initiated layer toggle
   *  or cloud-style change. Programmatic batch updates (compact-mode
   *  enforcement) deliberately leave it untouched. Persisted with the rest
   *  of vizSettings so the dropdown sticks across reloads. */
  activePreset?: string | null;
  /** Last cloud style picked from the compound cloud control. Persisted
   *  so re-checking a cloud source after unchecking all keeps the user's
   *  choice instead of snapping back to the default. */
  cloudStyle?: 'natural' | 'soft' | 'square';
  /** Show the experimental Hewson front overlay on the route map (#196).
   *  Default off; only has effect when front data is present. */
  mapFrontsVisible?: boolean;
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
  | 'obscuration'
  | 'conditions'
  | 'fronts'
  | 'sun'
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
  /**
   * When true, the cross-section's X-axis renders time labels (HH:MMZ)
   * instead of distance ticks. Used by the airport-profile panel where
   * the spatial extent collapses to one point and time becomes the X-axis.
   * Distance values in `points[i].distanceNm` are repurposed as hour
   * offsets from the start time, and `totalDistanceNm` is the total span.
   */
  timeAxisMode?: boolean;
  /**
   * D-0 current conditions overlay (METAR airport columns + route SIGMET
   * zones). Sourced from `snapshot.route_observations` / `route_sigmets`,
   * which are model-independent and `null` on D-1+ — so this is `null`
   * whenever the snapshot carries no observations or SIGMETs.
   */
  currentConditions: VizCurrentConditions | null;
  /**
   * Experimental Hewson front overlay (#196), already resolved to the rendered
   * model at its primary (nearest-cruise) level. `null` whenever the "Auto
   * Front Detection" pref was off or the rendered model carries no front data
   * (only ecmwf/gfs/icon do) — so the fronts layer/overlay grays out.
   */
  fronts: VizFronts | null;
  /**
   * Twilight/night bands along the route for the night-shading layer (#227),
   * extracted from `manifest.sun.night_intervals`. Empty on old packs / daytime
   * flights (the layer no-ops).
   */
  nightIntervals: VizNightInterval[];
  /**
   * Sun-side summary for the seating note (#227), from `manifest.sun.sun_side`.
   * `null` when no sun analysis is present.
   */
  sunSide: VizSunSide | null;
}

/** A twilight or night stretch along the route (distance-based for shading). */
export interface VizNightInterval {
  startNm: number;
  endNm: number;
  /** Boundary times (ISO-Z strings) — drive the sunset/sunrise marker labels. */
  startTime: string;
  endTime: string;
  phase: 'twilight' | 'night';
}

/** Sun geometry at one route point, for the cross-section hover readout. */
export interface VizSunAtPoint {
  elevationDeg: number;
  azimuthDeg: number;
  /** Signed sun azimuth − track, ±180; positive = right of track. */
  relativeBearingDeg: number;
}

/** Which side the sun favours over the route (informational). */
export interface VizSunSide {
  dominantSide: 'left' | 'right' | 'none';
  dominantSidePct: number;
}

/** Front crossings + nearest off-track front for the rendered model/level. */
export interface VizFronts {
  crossings: FrontCrossing[];
  nearest: FrontProximity | null;
  /** Pressure level (hPa) the crossings were detected at — for tooltip provenance. */
  primaryLevelHpa: number;
  /** Vertically-linked front chains (the same boundary across 925/850/700) for
   *  the rendered model — drives the slanted front lines. Empty on pre-linking
   *  packs (the renderer falls back to vertical markers from `crossings`). */
  chains: FrontChain[];
}

/** METAR-reporting airport projected onto the cross-section X axis. */
export interface VizMetarColumn {
  icao: string;
  /** Along-route distance (nm) → column center on the X axis. */
  enrouteDistanceNm: number;
  /** Perpendicular offset from the route (nm) → draw order (closest on top). */
  distanceFromRouteNm: number;
  /** "VFR" | "MVFR" | "IFR" | "LIFR" — drives the column fill color. */
  flightCategory: string;
  /** Column base (ft MSL): terrain elevation under the column's X position. */
  baseFt: number;
  // Hover-detail fields (METAR ground truth).
  metarRaw: string | null;
  ceilingFt: number | null;
  visibilityM: number | null;
  windDir: number | null;
  windSpeedKt: number | null;
  windGustKt: number | null;
}

/** Route SIGMET projected onto the cross-section (enroute span × vertical band). */
export interface VizSigmetZone {
  /** Enroute span start/end (nm) on the X axis. */
  enrouteFromNm: number;
  enrouteToNm: number;
  /** Vertical band (ft MSL); `null` → span the full plot height. */
  baseFt: number | null;
  topFt: number | null;
  /** Hazard word (TURB/ICE/TS/MTW/VA...). */
  hazard: string;
  /** SEV/EMBD/... — drives severity coloring. */
  qualifier: string | null;
  rawText: string;
}

export interface VizCurrentConditions {
  airports: VizMetarColumn[];
  sigmets: VizSigmetZone[];
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
  /** Surface-based CIN (J/kg) — companion to capeSurfaceJkg. */
  cinSurfaceJkg: number;
  // NWP convective fields (null when GRIB2 unavailable)
  nwpConvectiveRisk: string;
  nwpConvectiveBaseFt: number | null;
  nwpConvectiveTopFt: number | null;
  nwpConvectiveCoverPct: number | null;
  /** Method tag: "nwp", "nwp_lcl_top", "nwp_hybrid", etc. */
  nwpConvectiveMethod: string | null;
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
   * use this to gate the cross-section NWP clouds toggle.
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
  /** Temperature (°C) at the elected cruise level for the selected model.
   *  `isaDevC` is derived from this; kept as a separate field so a future
   *  raw "cruise temperature" metric (or richer tooltip) needs no re-plumbing. */
  temperatureCruiseC: number | null;
  /** ISA deviation (°C) at the elected cruise level: actual − ISA standard.
   *  Positive = warmer than standard (higher density altitude / degraded
   *  performance); negative = colder. `null` when cruise temp is unavailable. */
  isaDevC: number | null;
  precipitationMm: number | null;
  /** Mean sea-level pressure (hPa), used as the QNH proxy. Canonical hPa;
   *  display-unit conversion (hPa/inHg) happens at the route-graph edge. */
  qnhHpa: number | null;
  /**
   * Surface obscuration band (fog / low stratus). Populated when the
   * surface forecast indicates reduced visibility or near-saturated low
   * cloud — `null` otherwise. Pure visualization data; no advisory or
   * analysis logic depends on it.
   */
  surfaceObscuration: VizSurfaceObscuration | null;
  /**
   * Sun geometry at this point (#227) for the hover readout — azimuth and
   * angle relative to track. `null` on old packs (no `manifest.sun.points`).
   */
  sun: VizSunAtPoint | null;
}

export interface VizSurfaceObscuration {
  baseFt: number;
  topFt: number;
  /** Aviation flight category — drives both the tooltip label and the
   *  rendered color (LIFR=purple, IFR=red, MVFR=amber). */
  severity: 'lifr' | 'ifr' | 'mvfr';
  visM: number | null;
  surfaceTC: number | null;
  surfaceTdC: number | null;
  surfaceRhPct: number | null;
  /** Which trigger fired — primary (visibility) or secondary (low cloud + small DD). */
  reason: 'visibility' | 'low_cloud_dd';
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
  /** Peak/band cloud cover fraction (%) — populated for nwp_3d and grib sources. */
  meanCloudCoverPct?: number;
  /** Layer-mean temperature (°C). */
  meanTemperatureC?: number;
  /** How this layer was derived: "dd", "grib", "synthesized", "nwp_3d". */
  source?: string;
}

export interface VizIcingZone {
  baseFt: number;
  topFt: number;
  risk: string;
  type: string;
  /** Numeric icing index (0–100) — populated for Ogimet-DD/NWP and IENG. */
  meanIcingIndex?: number;
  /** Layer-mean temperature (°C). */
  meanTemperatureC?: number;
  /** Super-cooled large drop risk flag. */
  sldRisk?: boolean;
}

export interface VizSfipZone {
  baseFt: number;
  topFt: number;
  risk: string;
  type: string;
  meanSfip100: number | null;
  variant: string;  // "full", "proxy", "interp_no_vv", etc.
  /** Layer-mean temperature (°C). */
  meanTemperatureC?: number;
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
  /** Richardson number (Ri < 0.25 → turbulent). */
  richardsonNumber?: number;
}

export interface VizInversionLayer {
  baseFt: number;
  topFt: number;
  strengthC: number;
  surfaceBased?: boolean;
}
