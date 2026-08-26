/** Shared types for the cross-section and map visualizations. */

import type { FrontCrossing, FrontProximity, FrontChain } from '../types/fronts';
import type { AdvisoryHighlights } from '../types/advisories';

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
  /** Show the airport forecast overlay on the briefing route map (#424): the
   *  same per-airport forecast markers the full forecast map draws, for the
   *  snapshot time nearest the flight. Default on; only has effect when the
   *  flight is within the forecast horizon (D-0..D-6). */
  mapForecastOverlayVisible?: boolean;
  /** Which forecast metric the airport overlay colours by (a `FORECAST_METRICS`
   *  id — category, wind, ceiling, …). Independent of the route-segment
   *  `mapColorMetric`. Default `flight_category`. */
  mapForecastMetric?: string;
  /** Skew-T overlay band state most recently applied by a preset / deep-link
   *  (#308). Full clean-slate map (every overlay id → on/off). The SkewT
   *  renderer keeps its own localStorage for ad-hoc user toggles; this field is
   *  the preset-driven view the store owns so it survives reload and seeds the
   *  renderer when an advisory preset is active. Undefined when no preset has
   *  touched the Skew-T. */
  skewtOverlays?: Record<string, boolean>;
  /** Primary side-panel variable id selected by a preset / deep-link (#308). */
  skewtPrimaryVar?: string;
  /** Which advisory the cross-section highlight (scrim + verdict ribbon, #373)
   *  is tracking, or null/undefined for no highlight. Only the advisory id is
   *  stored — the regions/ribbon are derived reactively from the advisory ×
   *  selectedModel at render time, so model switches / recalcs / altitude
   *  changes update the highlight with no stale-copy bugs. No-ops gracefully
   *  when the advisory no longer exists in the manifest. */
  activeHighlightAdvisoryId?: string | null;
  /** Corridor width (NM) the observed layers and the observed route-graph
   *  metrics are resolved at (#574). Every sampled radius ships in the
   *  payload, so changing this is a client-side pick with no re-fetch —
   *  which is why it lives in view settings rather than triggering a
   *  request. Undefined → the widest sampled radius. */
  observedRadiusNm?: number | null;
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
  /** When explicitly `false`, the render loop does NOT clip this layer to the
   *  plot area, so it may draw into the canvas margins. The verdict ribbon
   *  (#373) uses this to render its strip in the bottom margin, below the plot.
   *  Defaults to clipped (undefined/true). */
  readonly clipToPlot?: boolean;
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
  | 'highlight'
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
   * Observed radar / lightning / satellite cloud tops along the corridor
   * (#574), from `snapshot.observed_conditions`. `null` on D-1+ and wherever
   * the observed collector is not enabled — the two observed layers then gray
   * out, exactly like the fronts overlay does without front data.
   */
  observed: VizObserved | null;
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
  /**
   * Advisory highlight geometry (scrim regions + verdict ribbon, #373) for the
   * currently-tracked advisory × the rendered model. Derived reactively in
   * briefing-main from `activeHighlightAdvisoryId` × `selectedModel` and attached
   * before `setData`; `null` when no advisory is highlighted, the advisory is
   * gone, or the model/pack carries no highlight data (old pack) → the highlight
   * layer no-ops.
   */
  advisoryHighlights: AdvisoryHighlights | null;
  /**
   * Display name of the currently-tracked advisory (#412), attached alongside
   * `advisoryHighlights` so the ribbon-hover tooltip can name the advisory it is
   * reporting a verdict for. `null`/absent whenever `advisoryHighlights` is null
   * (no advisory tracked / old pack). Optional so the non-briefing VizRouteData
   * builders (airport profile, snapshot adapters) need not set it.
   */
  advisoryHighlightName?: string | null;
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

// --- Observed conditions (#574) --------------------------------------------
//
// Sibling of `VizCurrentConditions`, deliberately separate: METAR columns and
// SIGMET zones are point reports and airspace notices, while these are
// remotely-sensed fields with their own coverage and their own clocks. The
// existing `current-conditions` layer is untouched by any of this.

/** One FL band's share of the cloud-top pixels in a disc. */
export interface VizObservedTopBin {
  label: string;
  loFt: number;
  hiFt: number;
  /** Share of the disc's DETECTED pixels, 0–1. */
  fraction: number;
  /** Pixels in this band, so a hover can say "12 of 201" rather than only a
   *  percentage — 4% of 201 and 4% of 3 are very different evidence. */
  count: number;
}

/** Every observed quantity at one route point, for the selected corridor. */
export interface VizObservedPoint {
  distanceNm: number;
  /** Peak reflectivity (dBZ), or null when nothing was detected. */
  dbz: number | null;
  /** True when the radar does not cover enough of this disc to say anything.
   *  Renderers MUST distinguish this from `dbz === null`, which means the
   *  radar looked and found no echo. */
  radarNoCoverage: boolean;
  rateMmH: number | null;
  rateNoCoverage: boolean;
  flashCount: number;
  /** Flashes per 1000 km² per minute — comparable between corridor widths. */
  flashRate: number | null;
  /** Highest observed cloud top (ft), or null when the disc was clear. */
  topsHighestFt: number | null;
  topsBins: VizObservedTopBin[];
  /** Share of cloudy pixels the retrieval flagged multi-layer-suspect (qm 9). */
  topsMultiLayerFraction: number;
  topsNoCoverage: boolean;
  /** Coldest top in the disc (°C). Deepest convection, not an average. */
  topsColdestC: number | null;
  /** Effective cloudiness at the highest top, 0-1. Separates a solid deck from
   *  wispy cirrus — height alone renders both identically. */
  topsHighestCloudiness: number | null;
  /** Median opacity across the disc's cloudy pixels. */
  topsMedianCloudiness: number | null;
  /** Pressure-based FL of the highest top, what an altimeter agrees with.
   *  Coarse (10 FL steps) and can diverge from the geometric height, so it is
   *  secondary to `topsHighestFt`, never a replacement. */
  topsHighestAviationFl: number | null;
}

/** Per-source identity and age. There is no combined timestamp on purpose. */
export interface VizObservedSource {
  source: string;
  label: string;
  validTime: string;
  ageMinutes: number;
  /** Width of the product's own accumulation / rolling-max window; 0 = instant. */
  windowMinutes: number;
  attribution: string;
}

export interface VizObserved {
  /** All sampled radii — switching between them is a client-side pick. */
  radiiNm: number[];
  /** The radius these `points` were resolved at. */
  radiusNm: number;
  points: VizObservedPoint[];
  reflectivity: VizObservedSource | null;
  rainRate: VizObservedSource | null;
  cloudTops: VizObservedSource | null;
  lightning: VizObservedSource | null;
  summaryLines: string[];
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
  /** The observed sample matched to this route point (#574), or null when no
   *  observed frame covered it. Carries every measured field so hover rows do
   *  not have to re-derive the distance match. */
  observed?: VizObservedPoint | null;
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
  /** Native convective precip rate (mm/h) — the firing evidence on the
   *  "nwp_precip" path, where base/top are unresolved. Null otherwise. */
  nwpConvectivePrecipMmH: number | null;
  /** Method tag: "nwp", "nwp_lcl_top", "nwp_hybrid", "nwp_precip", etc. */
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
  /**
   * Observed radar rain rate (mm/h) at this point, for the selected corridor
   * width (#574). This is a MEASUREMENT, not a forecast — the sibling of
   * `precipitationMm`, which comes from the model.
   *
   * `null` means one of two very different things, disambiguated by
   * `observedRadarNoCoverage`: either the radar looked and found nothing, or
   * it does not cover this disc at all. A renderer that treats them alike
   * would paint about half of Europe as dry.
   */
  observedRateMmH: number | null;
  /** Observed lightning density (flashes per 1000 km² per minute) in the
   *  selected corridor. Lightning has no coverage caveat — the imager sees
   *  the whole disc, so zero here is an observation. */
  observedFlashRate: number | null;
  /** True when the radar does not cover this point's disc. */
  observedRadarNoCoverage: boolean;
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
