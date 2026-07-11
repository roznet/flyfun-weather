import type {
  AdvisoryEvidenceRegion,
  ModelAdvisoryResult,
  RouteAdvisoriesManifest,
  RouteAdvisoryResult,
} from '../types/advisories';
import type { VizPoint, VizRouteData } from './types';

export type AdvisoryHighlightSurface =
  | 'cross-section'
  | 'route-graph'
  | 'route-map';

export interface ActiveAdvisoryFocus {
  advisoryId: string;
  model: string;
  highlightSurfaces: AdvisoryHighlightSurface[];
  emphasizeLayers: string[];
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

  const resolvedLocationState = locationState(match.modelResult);
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
  if (!focus) return null;
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
