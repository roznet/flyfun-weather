import type {
  AdvisoryEvidenceRegion,
  ModelAdvisoryResult,
  RouteAdvisoriesManifest,
  RouteAdvisoryResult,
} from '../types/advisories';
import type { CoordTransform, PlotArea, VizPoint, VizRouteData } from './types';
import { isAssessedAdvisoryResult } from './advisory-methods';

export type AdvisoryHighlightSurface =
  | 'cross-section'
  | 'route-graph'
  | 'route-map';

export interface ActiveAdvisoryFocus {
  advisoryId: string;
  model: string;
  highlightSurfaces: AdvisoryHighlightSurface[];
  emphasizeLayers: string[];
  /** False only for an aggregate action on a legacy manifest that omitted its
   * representative model. The selected model still drives the displayed preset,
   * but must not be presented as evidence attribution in the focus banner. */
  modelAttributionKnown?: boolean;
}

export interface ResolvedFocusRegion {
  model: string;
  startPointIndex: number;
  endPointIndex: number;
  startNm: number;
  endNm: number;
  lowerAltitudeFt: number | null;
  upperAltitudeFt: number | null;
  severity: 'green' | 'amber' | 'red';
  reasonCode: string;
  metricId: string | null;
  methodId: string | null;
  mapPath: Array<{ lat: number; lon: number }>;
}

export interface ResolvedAdvisoryFocus {
  active: ActiveAdvisoryFocus;
  advisory: RouteAdvisoryResult;
  modelResult: ModelAdvisoryResult;
  regions: ResolvedFocusRegion[];
  locationState: 'available' | 'partial' | 'unavailable' | 'legacy';
}

export type CrossSectionFocusPrimitive =
  | {
    kind: 'band';
    startNm: number;
    endNm: number;
    lowerAltitudeFt: number;
    upperAltitudeFt: number;
    severity: 'green' | 'amber' | 'red';
  }
  | {
    kind: 'route-rail';
    startNm: number;
    endNm: number;
    severity: 'green' | 'amber' | 'red';
  };

interface FocusStyle {
  stroke: string;
  fill: string;
  hatch: string;
}

interface FocusRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export const ROUTE_EVIDENCE_HALO_WEIGHT = 32;
export const ROUTE_EVIDENCE_PARTIAL_DASH = { dash: 24, gap: 44 } as const;
export const COMPARE_FOCUS_LABEL_TEXT_ALIGN: CanvasTextAlign = 'left';

export type RoutePointBounds = [
  [lat: number, lon: number],
  [lat: number, lon: number],
];

export interface RouteMapFitCoordinate {
  lat: number;
  lon: number;
}

export interface RouteMapFitFrontAxis {
  coordinates: ReadonlyArray<ReadonlyArray<number>>;
}

export interface RouteMapFitNearestFront extends RouteMapFitCoordinate {
  on_track: boolean;
  trend: string;
}

export interface RouteMapFitSources {
  routePoints: readonly RouteMapFitCoordinate[];
  focus: ResolvedAdvisoryFocus | null;
  showFronts: boolean;
  frontAxes: readonly RouteMapFitFrontAxis[];
  frontCrossings: readonly RouteMapFitCoordinate[];
  nearestFront: RouteMapFitNearestFront | null;
  frontAxisNearRoute: (
    coordinates: ReadonlyArray<ReadonlyArray<number>>,
  ) => boolean;
}

const FOCUS_STYLES: Record<ResolvedFocusRegion['severity'], FocusStyle> = {
  green: {
    stroke: '#15803d',
    fill: 'rgba(34, 197, 94, 0.16)',
    hatch: 'rgba(21, 128, 61, 0.55)',
  },
  amber: {
    stroke: '#b45309',
    fill: 'rgba(245, 158, 11, 0.18)',
    hatch: 'rgba(180, 83, 9, 0.58)',
  },
  red: {
    stroke: '#b91c1c',
    fill: 'rgba(239, 68, 68, 0.17)',
    hatch: 'rgba(185, 28, 28, 0.58)',
  },
};

const FOCUS_SEVERITY_RANK: Record<ResolvedFocusRegion['severity'], number> = {
  green: 0,
  amber: 1,
  red: 2,
};

export function routeEvidenceDashArray(
  locationState: ResolvedAdvisoryFocus['locationState'],
): string | undefined {
  return locationState === 'partial'
    ? `${ROUTE_EVIDENCE_PARTIAL_DASH.dash}, ${ROUTE_EVIDENCE_PARTIAL_DASH.gap}`
    : undefined;
}

export function focusRegionsInPaintOrder(
  regions: readonly ResolvedFocusRegion[],
): ResolvedFocusRegion[] {
  return regions
    .map((region, index) => ({ region, index }))
    .sort((a, b) => (
      FOCUS_SEVERITY_RANK[a.region.severity] - FOCUS_SEVERITY_RANK[b.region.severity]
      || a.index - b.index
    ))
    .map(({ region }) => region);
}

export type CrossSectionPaintStep<T> =
  | { kind: 'layer'; layer: T }
  | { kind: 'focus'; primitiveKind: CrossSectionFocusPrimitive['kind'] };

function layerPaintSteps<T>(layers: readonly T[]): CrossSectionPaintStep<T>[] {
  return layers.map((layer) => ({ kind: 'layer', layer }));
}

export function crossSectionPaintPlan<T extends { group: string }>(
  layers: readonly T[],
): CrossSectionPaintStep<T>[] {
  const firstTerrain = layers.findIndex((layer) => layer.group === 'terrain');
  if (firstTerrain < 0) {
    const firstReference = layers.findIndex((layer) => layer.group === 'reference');
    const insertion = firstReference < 0 ? layers.length : firstReference;
    return [
      ...layerPaintSteps(layers.slice(0, insertion)),
      { kind: 'focus', primitiveKind: 'band' },
      { kind: 'focus', primitiveKind: 'route-rail' },
      ...layerPaintSteps(layers.slice(insertion)),
    ];
  }
  let lastTerrain = firstTerrain;
  for (let index = firstTerrain + 1; index < layers.length; index += 1) {
    if (layers[index].group === 'terrain') lastTerrain = index;
  }
  return [
    ...layerPaintSteps(layers.slice(0, firstTerrain)),
    { kind: 'focus', primitiveKind: 'band' },
    ...layerPaintSteps(layers.slice(firstTerrain, lastTerrain + 1)),
    { kind: 'focus', primitiveKind: 'route-rail' },
    ...layerPaintSteps(layers.slice(lastTerrain + 1)),
  ];
}

function validPlotArea(plotArea: PlotArea): boolean {
  return Number.isFinite(plotArea.left)
    && Number.isFinite(plotArea.top)
    && Number.isFinite(plotArea.width)
    && Number.isFinite(plotArea.height)
    && plotArea.width > 0
    && plotArea.height > 0;
}

function clippedFocusRect(
  plotArea: PlotArea,
  left: number,
  right: number,
  top: number,
  bottom: number,
): FocusRect | null {
  if (![left, right, top, bottom].every(Number.isFinite)) return null;
  const plotRight = plotArea.left + plotArea.width;
  const plotBottom = plotArea.top + plotArea.height;
  const clippedLeft = Math.max(plotArea.left, Math.min(left, right));
  const clippedRight = Math.min(plotRight, Math.max(left, right));
  const clippedTop = Math.max(plotArea.top, Math.min(top, bottom));
  const clippedBottom = Math.min(plotBottom, Math.max(top, bottom));
  if (clippedRight <= clippedLeft || clippedBottom <= clippedTop) return null;
  return {
    left: clippedLeft,
    top: clippedTop,
    width: clippedRight - clippedLeft,
    height: clippedBottom - clippedTop,
  };
}

function drawHatchedFocusRect(
  ctx: CanvasRenderingContext2D,
  rect: FocusRect,
  severity: ResolvedFocusRegion['severity'],
  dashed: boolean,
): void {
  const style = FOCUS_STYLES[severity];
  ctx.save();
  try {
    ctx.fillStyle = style.fill;
    ctx.fillRect(rect.left, rect.top, rect.width, rect.height);

    ctx.save();
    try {
      ctx.beginPath();
      ctx.rect(rect.left, rect.top, rect.width, rect.height);
      ctx.clip();
      ctx.strokeStyle = style.hatch;
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.beginPath();
      for (let offset = -rect.height; offset < rect.width; offset += 8) {
        ctx.moveTo(rect.left + offset, rect.top + rect.height);
        ctx.lineTo(rect.left + offset + rect.height, rect.top);
      }
      ctx.stroke();
    } finally {
      ctx.restore();
    }

    ctx.strokeStyle = style.stroke;
    ctx.lineWidth = 2;
    ctx.setLineDash(dashed ? [6, 4] : []);
    ctx.strokeRect(rect.left, rect.top, rect.width, rect.height);
  } finally {
    ctx.restore();
  }
}

function validRouteInterval(startNm: number, endNm: number): boolean {
  return Number.isFinite(startNm)
    && Number.isFinite(endNm)
    && endNm > startNm;
}

export function routePointBounds(
  points: readonly Pick<VizPoint, 'lat' | 'lon'>[],
): RoutePointBounds | null {
  let minLat = Infinity;
  let maxLat = -Infinity;
  let minLon = Infinity;
  let maxLon = -Infinity;
  for (const point of points) {
    if (
      !Number.isFinite(point.lat)
      || !Number.isFinite(point.lon)
      || point.lat < -90
      || point.lat > 90
      || point.lon < -180
      || point.lon > 180
    ) {
      continue;
    }
    minLat = Math.min(minLat, point.lat);
    maxLat = Math.max(maxLat, point.lat);
    minLon = Math.min(minLon, point.lon);
    maxLon = Math.max(maxLon, point.lon);
  }
  return Number.isFinite(minLat)
    ? [[minLat, minLon], [maxLat, maxLon]]
    : null;
}

export function collectRouteMapFitCoordinates(
  sources: RouteMapFitSources,
): RouteMapFitCoordinate[] {
  const coordinates = sources.routePoints.map((point) => ({
    lat: point.lat,
    lon: point.lon,
  }));

  if (sources.focus?.active.highlightSurfaces.includes('route-map')) {
    for (const region of sources.focus.regions) {
      for (const point of region.mapPath) {
        coordinates.push({ lat: point.lat, lon: point.lon });
      }
    }
  }

  if (!sources.showFronts) return coordinates;
  for (const axis of sources.frontAxes) {
    if (axis.coordinates.length < 2 || !sources.frontAxisNearRoute(axis.coordinates)) {
      continue;
    }
    for (const coordinate of axis.coordinates) {
      const [lon, lat] = coordinate;
      coordinates.push({ lat, lon });
    }
  }
  for (const crossing of sources.frontCrossings) {
    coordinates.push({ lat: crossing.lat, lon: crossing.lon });
  }
  const nearest = sources.nearestFront;
  if (nearest && !nearest.on_track && nearest.trend === 'closing') {
    coordinates.push({ lat: nearest.lat, lon: nearest.lon });
  }
  return coordinates;
}

export function crossSectionPrimitive(
  region: ResolvedFocusRegion,
): CrossSectionFocusPrimitive {
  if (region.lowerAltitudeFt !== null && region.upperAltitudeFt !== null) {
    return {
      kind: 'band',
      startNm: region.startNm,
      endNm: region.endNm,
      lowerAltitudeFt: region.lowerAltitudeFt,
      upperAltitudeFt: region.upperAltitudeFt,
      severity: region.severity,
    };
  }
  return {
    kind: 'route-rail',
    startNm: region.startNm,
    endNm: region.endNm,
    severity: region.severity,
  };
}

export function focusRegionsForPrimitiveKind(
  regions: readonly ResolvedFocusRegion[],
  kind: CrossSectionFocusPrimitive['kind'],
): ResolvedFocusRegion[] {
  return focusRegionsInPaintOrder(regions).filter(
    (region) => crossSectionPrimitive(region).kind === kind,
  );
}

export function renderCrossSectionFocus(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  focus: ResolvedAdvisoryFocus,
): void {
  const { plotArea } = transform;
  if (!validPlotArea(plotArea)) return;

  ctx.save();
  try {
    ctx.beginPath();
    ctx.rect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
    ctx.clip();

    for (const region of focusRegionsInPaintOrder(focus.regions)) {
      const primitive = crossSectionPrimitive(region);
      if (!validRouteInterval(primitive.startNm, primitive.endNm)) continue;
      const startX = transform.distanceToX(primitive.startNm);
      const endX = transform.distanceToX(primitive.endNm);

      if (primitive.kind === 'route-rail') {
        const railHeight = Math.min(10, plotArea.height);
        const rect = clippedFocusRect(
          plotArea,
          startX,
          endX,
          plotArea.top + plotArea.height - railHeight,
          plotArea.top + plotArea.height,
        );
        if (rect) {
          drawHatchedFocusRect(
            ctx,
            rect,
            primitive.severity,
            focus.locationState === 'partial',
          );
        }
        continue;
      }

      if (
        !Number.isFinite(primitive.lowerAltitudeFt)
        || !Number.isFinite(primitive.upperAltitudeFt)
        || primitive.upperAltitudeFt < primitive.lowerAltitudeFt
      ) {
        continue;
      }
      const lowerY = transform.altitudeToY(primitive.lowerAltitudeFt);
      const upperY = transform.altitudeToY(primitive.upperAltitudeFt);
      if (!Number.isFinite(lowerY) || !Number.isFinite(upperY)) continue;
      let top = Math.min(lowerY, upperY);
      let bottom = Math.max(lowerY, upperY);
      if (bottom - top < 4) {
        const center = (top + bottom) / 2;
        top = center - 2;
        bottom = center + 2;
      }
      const rect = clippedFocusRect(plotArea, startX, endX, top, bottom);
      if (rect) {
        drawHatchedFocusRect(
          ctx,
          rect,
          primitive.severity,
          focus.locationState === 'partial',
        );
      }
    }
  } finally {
    ctx.restore();
  }
}

export function renderRouteGraphFocus(
  ctx: CanvasRenderingContext2D,
  plotArea: PlotArea,
  distanceToX: (distanceNm: number) => number,
  focus: ResolvedAdvisoryFocus,
): void {
  if (!validPlotArea(plotArea)) return;

  ctx.save();
  try {
    ctx.beginPath();
    ctx.rect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
    ctx.clip();
    for (const region of focusRegionsInPaintOrder(focus.regions)) {
      if (!validRouteInterval(region.startNm, region.endNm)) continue;
      const rect = clippedFocusRect(
        plotArea,
        distanceToX(region.startNm),
        distanceToX(region.endNm),
        plotArea.top,
        plotArea.top + plotArea.height,
      );
      if (rect) {
        drawHatchedFocusRect(
          ctx,
          rect,
          region.severity,
          focus.locationState === 'partial',
        );
      }
    }
  } finally {
    ctx.restore();
  }
}

interface PointSpan {
  startPosition: number;
  endPosition: number;
}

function midpoint(a: number, b: number): number {
  return a + (b - a) / 2;
}

function midpointCoordinate(
  a: VizPoint,
  b: VizPoint,
): { lat: number; lon: number } {
  return {
    lat: midpoint(a.lat, b.lat),
    lon: midpoint(a.lon, b.lon),
  };
}

function hasFiniteCoordinate(point: VizPoint): boolean {
  return Number.isFinite(point.lat) && Number.isFinite(point.lon);
}

function hasValidRouteGeometry(
  points: readonly VizPoint[],
  startPosition: number,
  endPosition: number,
): boolean {
  const validationStart = Math.max(0, startPosition - 1);
  const validationEnd = Math.min(points.length - 1, endPosition + 1);
  let previousDistanceNm: number | null = null;

  for (let position = validationStart; position <= validationEnd; position += 1) {
    const point = points[position];
    if (!Number.isFinite(point.distanceNm) || !hasFiniteCoordinate(point)) return false;
    if (previousDistanceNm !== null && point.distanceNm < previousDistanceNm) return false;
    previousDistanceNm = point.distanceNm;
  }
  return true;
}

export function pointCellBounds(
  points: readonly VizPoint[],
  position: number,
): { startNm: number; endNm: number } | null {
  if (!Number.isSafeInteger(position) || position < 0 || position >= points.length) {
    return null;
  }

  const point = points[position];
  if (!Number.isFinite(point.distanceNm)) return null;

  const previous = position > 0 ? points[position - 1] : null;
  const next = position + 1 < points.length ? points[position + 1] : null;
  if (previous && !Number.isFinite(previous.distanceNm)) return null;
  if (next && !Number.isFinite(next.distanceNm)) return null;
  if (previous && previous.distanceNm > point.distanceNm) return null;
  if (next && point.distanceNm > next.distanceNm) return null;

  return {
    startNm: previous
      ? midpoint(previous.distanceNm, point.distanceNm)
      : point.distanceNm,
    endNm: next
      ? midpoint(point.distanceNm, next.distanceNm)
      : point.distanceNm,
  };
}

export function routeCellPath(
  points: readonly VizPoint[],
  startPosition: number,
  endPosition: number,
): Array<{ lat: number; lon: number }> {
  if (
    !Number.isSafeInteger(startPosition)
    || !Number.isSafeInteger(endPosition)
    || startPosition < 0
    || endPosition < startPosition
    || endPosition >= points.length
  ) {
    return [];
  }
  if (!hasValidRouteGeometry(points, startPosition, endPosition)) return [];

  const startPoint = points[startPosition];
  const endPoint = points[endPosition];
  const startBoundary = startPosition > 0
    ? midpointCoordinate(points[startPosition - 1], startPoint)
    : { lat: startPoint.lat, lon: startPoint.lon };
  const endBoundary = endPosition + 1 < points.length
    ? midpointCoordinate(endPoint, points[endPosition + 1])
    : { lat: endPoint.lat, lon: endPoint.lon };

  return [
    startBoundary,
    ...points.slice(startPosition, endPosition + 1).map((point) => ({
      lat: point.lat,
      lon: point.lon,
    })),
    endBoundary,
  ];
}

function pointSpan(
  points: readonly VizPoint[],
  startPointIndex: number,
  endPointIndex: number,
): PointSpan | null {
  const spanLength = endPointIndex - startPointIndex + 1;
  if (spanLength > points.length) return null;

  const positionsByIndex = new Map<number, number[]>();
  points.forEach((point, position) => {
    if (!Number.isSafeInteger(point.pointIndex)) return;
    const positions = positionsByIndex.get(point.pointIndex) ?? [];
    positions.push(position);
    positionsByIndex.set(point.pointIndex, positions);
  });

  let previousPosition = -1;
  let startPosition = -1;
  let endPosition = -1;
  for (let pointIndex = startPointIndex; pointIndex <= endPointIndex; pointIndex += 1) {
    const positions = positionsByIndex.get(pointIndex);
    if (!positions || positions.length !== 1) return null;
    const position = positions[0];
    if (previousPosition >= 0 && position !== previousPosition + 1) return null;
    if (startPosition < 0) startPosition = position;
    endPosition = position;
    previousPosition = position;
  }

  return startPosition >= 0 && endPosition >= startPosition
    ? { startPosition, endPosition }
    : null;
}

function validationError(region: unknown): string | null {
  if (!region || typeof region !== 'object' || Array.isArray(region)) {
    return 'region must be an object';
  }
  const candidate = region as Partial<AdvisoryEvidenceRegion>;
  if (
    !Number.isSafeInteger(candidate.start_point_index)
    || !Number.isSafeInteger(candidate.end_point_index)
  ) {
    return 'point indices must be integers';
  }
  if (candidate.start_point_index! > candidate.end_point_index!) {
    return 'start_point_index must not exceed end_point_index';
  }

  const lowerAltitudeFt = candidate.lower_altitude_ft;
  const upperAltitudeFt = candidate.upper_altitude_ft;
  const hasLower = lowerAltitudeFt !== null && lowerAltitudeFt !== undefined;
  const hasUpper = upperAltitudeFt !== null && upperAltitudeFt !== undefined;
  if (hasLower !== hasUpper) {
    return 'altitude bounds must both be present or both absent';
  }
  if (lowerAltitudeFt !== null && lowerAltitudeFt !== undefined
    && upperAltitudeFt !== null && upperAltitudeFt !== undefined) {
    if (
      !Number.isSafeInteger(lowerAltitudeFt)
      || !Number.isSafeInteger(upperAltitudeFt)
    ) {
      return 'altitude bounds must be integers';
    }
    if (lowerAltitudeFt > upperAltitudeFt) {
      return 'lower_altitude_ft must not exceed upper_altitude_ft';
    }
  }

  if (!['green', 'amber', 'red'].includes(candidate.severity as string)) {
    return 'evidence severity cannot be unavailable';
  }
  if (typeof candidate.reason_code !== 'string' || !candidate.reason_code.trim()) {
    return 'reason_code must be non-empty';
  }
  if (
    candidate.metric_id !== null
    && candidate.metric_id !== undefined
    && typeof candidate.metric_id !== 'string'
  ) {
    return 'metric_id must be a string or null';
  }
  if (
    candidate.method_id !== null
    && candidate.method_id !== undefined
    && typeof candidate.method_id !== 'string'
  ) {
    return 'method_id must be a string or null';
  }
  return null;
}

function warnMalformedRegion(
  active: ActiveAdvisoryFocus,
  regionPosition: number,
  reason: string,
): void {
  console.warn(
    `Skipping malformed advisory evidence region ${active.advisoryId}/${active.model}`
      + ` at position ${regionPosition}: ${reason}`,
  );
}

function resolveRegion(
  region: unknown,
  regionPosition: number,
  active: ActiveAdvisoryFocus,
  points: readonly VizPoint[],
): ResolvedFocusRegion | null {
  const error = validationError(region);
  if (error) {
    warnMalformedRegion(active, regionPosition, error);
    return null;
  }
  const validatedRegion = region as AdvisoryEvidenceRegion;

  const span = pointSpan(
    points,
    validatedRegion.start_point_index,
    validatedRegion.end_point_index,
  );
  if (!span) {
    warnMalformedRegion(
      active,
      regionPosition,
      'stable point indices are missing, duplicated, or out of route order',
    );
    return null;
  }

  const startBounds = pointCellBounds(points, span.startPosition);
  const endBounds = pointCellBounds(points, span.endPosition);
  if (
    !hasValidRouteGeometry(points, span.startPosition, span.endPosition)
    || !startBounds
    || !endBounds
    || startBounds.startNm > endBounds.endNm
  ) {
    warnMalformedRegion(active, regionPosition, 'route geometry is invalid');
    return null;
  }

  return {
    model: active.model,
    startPointIndex: validatedRegion.start_point_index,
    endPointIndex: validatedRegion.end_point_index,
    startNm: startBounds.startNm,
    endNm: endBounds.endNm,
    lowerAltitudeFt: validatedRegion.lower_altitude_ft ?? null,
    upperAltitudeFt: validatedRegion.upper_altitude_ft ?? null,
    severity: validatedRegion.severity,
    reasonCode: validatedRegion.reason_code,
    metricId: validatedRegion.metric_id ?? null,
    methodId: validatedRegion.method_id ?? null,
    mapPath: routeCellPath(points, span.startPosition, span.endPosition),
  };
}

function locationState(
  modelResult: ModelAdvisoryResult,
): ResolvedAdvisoryFocus['locationState'] {
  const state: unknown = modelResult.data_state;
  switch (state) {
    case null:
    case undefined:
      return 'legacy';
    case 'complete':
      return 'available';
    case 'partial':
      return 'partial';
    case 'unavailable':
      return 'unavailable';
    default:
      console.warn(`Unknown advisory data_state: ${String(state)}`);
      return 'unavailable';
  }
}

function findAdvisoryAndModel(
  active: ActiveAdvisoryFocus,
  manifest: RouteAdvisoriesManifest,
): { advisory: RouteAdvisoryResult; modelResult: ModelAdvisoryResult } | null {
  const advisory = manifest.advisories.find(
    (candidate) => candidate.advisory_id === active.advisoryId,
  );
  if (!advisory) {
    console.warn(`Unknown advisory focus advisory_id: ${active.advisoryId}`);
    return null;
  }

  const modelResult = advisory.per_model.find(
    (candidate) => candidate.model === active.model,
  );
  if (!modelResult) {
    console.warn(
      `Unknown advisory focus model ${active.model} for advisory ${active.advisoryId}`,
    );
    return null;
  }
  return { advisory, modelResult };
}

export function resolveAdvisoryFocus(
  active: ActiveAdvisoryFocus | null,
  manifest: RouteAdvisoriesManifest | null,
  data: VizRouteData,
): ResolvedAdvisoryFocus | null {
  if (!active || !manifest) return null;
  const match = findAdvisoryAndModel(active, manifest);
  if (!match) return null;

  const rawRegions = match.modelResult.evidence_regions;
  let regions: ResolvedFocusRegion[] = [];
  if (rawRegions === undefined) {
    regions = [];
  } else if (!Array.isArray(rawRegions)) {
    console.warn(
      `Malformed evidence_regions for advisory ${active.advisoryId}/${active.model}`,
    );
  } else {
    regions = rawRegions.flatMap((region, regionPosition) => {
      const resolved = resolveRegion(region, regionPosition, active, data.points);
      return resolved ? [resolved] : [];
    });
  }

  const resolvedLocationState: ResolvedAdvisoryFocus['locationState'] =
    active.modelAttributionKnown === false
      ? 'legacy'
      : locationState(match.modelResult);
  if (resolvedLocationState === 'legacy' || resolvedLocationState === 'unavailable') {
    regions = [];
  }

  return {
    active,
    advisory: match.advisory,
    modelResult: match.modelResult,
    regions,
    locationState: resolvedLocationState,
  };
}

export function reconcileAdvisoryFocus(
  active: ActiveAdvisoryFocus | null,
  manifest: RouteAdvisoriesManifest | null,
): ActiveAdvisoryFocus | null {
  if (!active || !manifest) return null;
  return findAdvisoryAndModel(active, manifest) ? active : null;
}

export function replaceAdvisoryFocus(
  current: ActiveAdvisoryFocus | null,
  next: ActiveAdvisoryFocus,
): ActiveAdvisoryFocus {
  void current;
  return next;
}

export function effectiveEmphasis(
  active: ActiveAdvisoryFocus | null,
  activePreset: string | null,
): string[] | null {
  return active && activePreset !== null
    ? [...active.emphasizeLayers]
    : null;
}

export function focusedMethodId(
  focus: ResolvedAdvisoryFocus | null,
): string | null {
  if (!focus || !isAssessedAdvisoryResult(focus.modelResult)) return null;
  const primaryMethod = focus.modelResult.primary_method_id ?? null;
  if (focus.regions.length > 0) {
    const effectiveMethods = focus.regions.map(
      (region) => region.methodId ?? primaryMethod,
    );
    const first = effectiveMethods[0];
    if (first !== null && effectiveMethods.every((method) => method === first)) {
      return first;
    }
  }
  return primaryMethod;
}
