import { describe, expect, it } from 'vitest';

import {
  COMPARE_FOCUS_LABEL_TEXT_ALIGN,
  ROUTE_EVIDENCE_HALO_WEIGHT,
  ROUTE_EVIDENCE_PARTIAL_DASH,
  collectRouteMapFitCoordinates,
  crossSectionPrimitive,
  crossSectionPaintPlan,
  focusRegionsForPrimitiveKind,
  focusRegionsInPaintOrder,
  renderCrossSectionFocus,
  routeEvidenceDashArray,
  routePointBounds,
  routeCellPath,
  type ResolvedAdvisoryFocus,
  type ResolvedFocusRegion,
} from '../../ts/visualization/advisory-focus';
import type { CoordTransform } from '../../ts/visualization/types';
import { focusRegion, routeData } from './fixtures/advisory-focus';

function resolvedFocus(
  regions: ResolvedFocusRegion[],
  locationState: ResolvedAdvisoryFocus['locationState'] = 'available',
  highlightSurfaces: ResolvedAdvisoryFocus['active']['highlightSurfaces'] = [
    'cross-section',
    'route-graph',
    'route-map',
  ],
): ResolvedAdvisoryFocus {
  const modelResult: ResolvedAdvisoryFocus['modelResult'] = {
    model: 'gfs',
    status: 'amber',
    detail: 'GFS detail',
    affected_points: regions.length,
    total_points: regions.length,
    affected_pct: regions.length > 0 ? 100 : 0,
    affected_nm: 20,
    total_nm: 20,
  };
  return {
    active: {
      advisoryId: 'cloud_top',
      model: 'gfs',
      highlightSurfaces,
      emphasizeLayers: [],
    },
    advisory: {
      advisory_id: 'cloud_top',
      aggregate_status: 'amber',
      aggregate_detail: 'aggregate detail',
      per_model: [modelResult],
      parameters_used: {},
    },
    modelResult,
    regions,
    locationState,
  };
}

interface FakeCanvasCapture {
  ctx: CanvasRenderingContext2D;
  fillRects: Array<{ left: number; top: number; width: number; height: number }>;
  lineDashes: number[][];
  readonly depth: number;
  readonly saves: number;
  readonly restores: number;
}

function fakeCanvasCapture(): FakeCanvasCapture {
  const fillRects: FakeCanvasCapture['fillRects'] = [];
  const lineDashes: number[][] = [];
  let depth = 0;
  let saves = 0;
  let restores = 0;
  const ctx = {
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 1,
    save: () => { depth += 1; saves += 1; },
    restore: () => { depth -= 1; restores += 1; },
    beginPath: () => undefined,
    rect: () => undefined,
    clip: () => undefined,
    fillRect: (left: number, top: number, width: number, height: number) => {
      fillRects.push({ left, top, width, height });
    },
    moveTo: () => undefined,
    lineTo: () => undefined,
    stroke: () => undefined,
    setLineDash: (dash: number[]) => { lineDashes.push([...dash]); },
    strokeRect: () => undefined,
  } as unknown as CanvasRenderingContext2D;
  return {
    ctx,
    fillRects,
    lineDashes,
    get depth() { return depth; },
    get saves() { return saves; },
    get restores() { return restores; },
  };
}

const testTransform: CoordTransform = {
  plotArea: { left: 0, top: 0, width: 100, height: 100 },
  distanceToX: (distanceNm) => distanceNm,
  altitudeToY: (altitudeFt) => 100 - altitudeFt / 100,
  xToDistance: (x) => x,
  yToAltitude: (y) => (100 - y) * 100,
};

describe('advisory focus rendering adapters', () => {
  it('turns altitude-bounded evidence into a cross-section band', () => {
    expect(crossSectionPrimitive(focusRegion({
      startNm: 10,
      endNm: 30,
      lowerAltitudeFt: 5000,
      upperAltitudeFt: 9000,
    }))).toEqual({
      kind: 'band',
      startNm: 10,
      endNm: 30,
      lowerAltitudeFt: 5000,
      upperAltitudeFt: 9000,
      severity: 'amber',
    });
  });

  it('turns route-only evidence into a bottom rail instead of a full-depth fill', () => {
    expect(crossSectionPrimitive(focusRegion({
      lowerAltitudeFt: null,
      upperAltitudeFt: null,
    })).kind).toBe('route-rail');
  });

  it('uses midpoint-owned route cells for map evidence', () => {
    expect(routeCellPath(routeData([0, 10, 50]).points, 1, 1)).toEqual([
      { lat: 50.5, lon: -0.5 },
      { lat: 51, lon: 0 },
      { lat: 51.5, lon: 0.5 },
    ]);
  });

  it('keeps the evidence halo wider than the maximum hovered route segment', () => {
    expect(ROUTE_EVIDENCE_HALO_WEIGHT).toBeGreaterThan(28);
  });

  it('paints overlapping evidence least-to-worst without mutating resolved focus', () => {
    const redMountainWave = focusRegion({
      severity: 'red',
      reasonCode: 'mountain_wave_corroborated',
      startNm: 10,
      endNm: 30,
    });
    const firstAmber = focusRegion({ severity: 'amber', reasonCode: 'first_amber' });
    const green = focusRegion({ severity: 'green', reasonCode: 'green' });
    const secondAmber = focusRegion({ severity: 'amber', reasonCode: 'second_amber' });
    const regions = [redMountainWave, firstAmber, green, secondAmber];

    const ordered = focusRegionsInPaintOrder(regions);

    expect(ordered.map((region) => region.reasonCode)).toEqual([
      'green',
      'first_amber',
      'second_amber',
      'mountain_wave_corroborated',
    ]);
    expect(regions).toEqual([redMountainWave, firstAmber, green, secondAmber]);
    expect(ordered).not.toBe(regions);
  });

  it('plans bands below terrain and rails below later annotations', () => {
    const layers = [
      { id: 'clouds', group: 'clouds' },
      { id: 'convection', group: 'convection' },
      { id: 'terrain', group: 'terrain' },
      { id: 'current-conditions', group: 'conditions' },
      { id: 'fronts', group: 'fronts' },
      { id: 'freezing-level', group: 'temperature' },
      { id: 'ceiling', group: 'reference' },
    ];

    const plan = crossSectionPaintPlan(layers);

    expect(plan.map((step) => step.kind === 'layer'
      ? step.layer.id
      : `focus:${step.primitiveKind}`)).toEqual([
      'clouds',
      'convection',
      'focus:band',
      'terrain',
      'focus:route-rail',
      'current-conditions',
      'fronts',
      'freezing-level',
      'ceiling',
    ]);
    expect(plan.flatMap((step) => step.kind === 'layer' ? [step.layer.id] : []))
      .toEqual(layers.map((layer) => layer.id));
  });

  it('splits compare bands and route rails without double-painting regions', () => {
    const band = focusRegion({ reasonCode: 'band' });
    const rail = focusRegion({
      reasonCode: 'rail',
      lowerAltitudeFt: null,
      upperAltitudeFt: null,
    });

    expect(focusRegionsForPrimitiveKind([rail, band], 'band')).toEqual([band]);
    expect(focusRegionsForPrimitiveKind([rail, band], 'route-rail')).toEqual([rail]);
  });

  it('keeps compare focus labels explicitly left-aligned', () => {
    expect(COMPARE_FOCUS_LABEL_TEXT_ALIGN).toBe('left');
  });

  it('keeps round-capped partial map dashes visibly open', () => {
    expect(routeEvidenceDashArray('available')).toBeUndefined();
    expect(routeEvidenceDashArray('partial')).toBe('24, 44');
    expect(
      ROUTE_EVIDENCE_PARTIAL_DASH.gap - ROUTE_EVIDENCE_HALO_WEIGHT,
    ).toBeGreaterThan(0);
  });

  it('computes initial map bounds from valid route coordinates', () => {
    expect(routePointBounds(routeData().points)).toEqual([
      [50, -1],
      [52, 1],
    ]);
  });

  it('does not produce initial map bounds for invalid or empty coordinates', () => {
    expect(routePointBounds([])).toBeNull();
    expect(routePointBounds([
      { lat: Number.NaN, lon: -1 },
      { lat: 91, lon: Number.POSITIVE_INFINITY },
    ])).toBeNull();
  });

  it('collects only route coordinates when focus and fronts are hidden', () => {
    const focus = resolvedFocus([focusRegion({
      mapPath: [{ lat: 3, lon: 4 }, { lat: 5, lon: 6 }],
    })], 'available', ['cross-section']);

    expect(collectRouteMapFitCoordinates({
      routePoints: [{ lat: 1, lon: 2 }],
      focus,
      showFronts: false,
      frontAxes: [{ coordinates: [[7, 8], [9, 10]] }],
      frontCrossings: [{ lat: 11, lon: 12 }],
      nearestFront: { lat: 13, lon: 14, on_track: false, trend: 'closing' },
      frontAxisNearRoute: () => true,
    })).toEqual([{ lat: 1, lon: 2 }]);
  });

  it('collects rendered focus paths, nearby axes, crossings, and closing marker', () => {
    const focus = resolvedFocus([focusRegion({
      mapPath: [{ lat: 3, lon: 4 }, { lat: 5, lon: 6 }],
    })]);

    expect(collectRouteMapFitCoordinates({
      routePoints: [{ lat: 1, lon: 2 }],
      focus,
      showFronts: true,
      frontAxes: [
        { coordinates: [[7, 8], [9, 10]] },
        { coordinates: [[11, 12]] },
        { coordinates: [[99, 13], [100, 14]] },
      ],
      frontCrossings: [{ lat: 15, lon: 16 }],
      nearestFront: { lat: 17, lon: 18, on_track: false, trend: 'closing' },
      frontAxisNearRoute: (coordinates) => coordinates[0][0] !== 99,
    })).toEqual([
      { lat: 1, lon: 2 },
      { lat: 3, lon: 4 },
      { lat: 5, lon: 6 },
      { lat: 8, lon: 7 },
      { lat: 10, lon: 9 },
      { lat: 15, lon: 16 },
      { lat: 17, lon: 18 },
    ]);
  });

  it('excludes on-track or non-closing nearest-front markers from fit bounds', () => {
    const base = {
      routePoints: [{ lat: 1, lon: 2 }],
      focus: null,
      showFronts: true,
      frontAxes: [],
      frontCrossings: [],
      frontAxisNearRoute: () => true,
    };
    expect(collectRouteMapFitCoordinates({
      ...base,
      nearestFront: { lat: 3, lon: 4, on_track: true, trend: 'closing' },
    })).toEqual([{ lat: 1, lon: 2 }]);
    expect(collectRouteMapFitCoordinates({
      ...base,
      nearestFront: { lat: 3, lon: 4, on_track: false, trend: 'receding' },
    })).toEqual([{ lat: 1, lon: 2 }]);
  });

  it('expands zero-height altitude evidence to 4px and restores canvas state', () => {
    const capture = fakeCanvasCapture();

    renderCrossSectionFocus(capture.ctx, testTransform, resolvedFocus([
      focusRegion({
        startNm: 10,
        endNm: 30,
        lowerAltitudeFt: 5000,
        upperAltitudeFt: 5000,
      }),
    ]));

    expect(capture.fillRects[0]).toEqual({ left: 10, top: 48, width: 20, height: 4 });
    expect(capture.depth).toBe(0);
    expect(capture.saves).toBe(capture.restores);
  });

  it('uses a solid available rail border and a dashed partial rail border', () => {
    const rail = focusRegion({
      startNm: 10,
      endNm: 30,
      lowerAltitudeFt: null,
      upperAltitudeFt: null,
    });
    const available = fakeCanvasCapture();
    const partial = fakeCanvasCapture();

    renderCrossSectionFocus(
      available.ctx,
      testTransform,
      resolvedFocus([rail], 'available'),
    );
    renderCrossSectionFocus(
      partial.ctx,
      testTransform,
      resolvedFocus([rail], 'partial'),
    );

    expect(available.lineDashes[available.lineDashes.length - 1]).toEqual([]);
    expect(partial.lineDashes[partial.lineDashes.length - 1]).toEqual([6, 4]);
    expect(available.depth).toBe(0);
    expect(partial.depth).toBe(0);
  });
});
