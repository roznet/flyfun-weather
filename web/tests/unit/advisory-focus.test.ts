import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RouteAnalysesManifest } from '../../ts/store/types';
import type { AirportProfileSnapshot } from '../../ts/adapters/airport-profile-adapter';
import { snapshotToVizData } from '../../ts/adapters/airport-profile-adapter';
import { extractVizData } from '../../ts/visualization/data-extract';
import {
  effectiveEmphasis,
  focusedMethodId,
  pointCellBounds,
  reconcileAdvisoryFocus,
  replaceAdvisoryFocus,
  resolveAdvisoryFocus,
  routeCellPath,
} from '../../ts/visualization/advisory-focus';
import {
  activeFocus,
  legacyManifestWithoutEvidenceMetadata,
  manifestWithDisjointGfsAndEcmwfRegions,
  manifestWithOneValidAndOneInvalidRegion,
  manifestWithoutFocusedModel,
  manifestWithTwoModels,
  refreshedManifest,
  routeData,
} from './fixtures/advisory-focus';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('resolveAdvisoryFocus', () => {
  it('selects only the representative model for an aggregate focus', () => {
    const resolved = resolveAdvisoryFocus(
      {
        advisoryId: 'cloud_top',
        model: 'ecmwf',
        highlightSurfaces: ['cross-section', 'route-graph', 'route-map'],
        emphasizeLayers: ['square-nwp-cloud-bands', 'terrain', 'cruise-altitude'],
      },
      manifestWithTwoModels(),
      routeData(),
    );
    expect(resolved?.modelResult.model).toBe('ecmwf');
    expect(resolved?.regions.every((region) => region.model === 'ecmwf')).toBe(true);
    expect(resolved?.regions).toHaveLength(1);
  });

  it('never unions geometry from another model', () => {
    const resolved = resolveAdvisoryFocus(
      activeFocus('gfs'),
      manifestWithDisjointGfsAndEcmwfRegions(),
      routeData(),
    );
    expect(resolved?.regions.map((region) => [region.startNm, region.endNm]))
      .toEqual([[0, 15]]);
  });

  it('filters one malformed region without disabling valid regions', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const resolved = resolveAdvisoryFocus(
      activeFocus('gfs'),
      manifestWithOneValidAndOneInvalidRegion(),
      routeData(),
    );
    expect(resolved?.regions).toHaveLength(1);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('skips a non-object region without disabling valid regions', () => {
    const manifest = manifestWithOneValidAndOneInvalidRegion();
    const model = manifest.advisories[0].per_model[0];
    model.evidence_regions = [model.evidence_regions![0], null as never];
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    const resolved = resolveAdvisoryFocus(activeFocus(), manifest, routeData());

    expect(resolved?.regions).toHaveLength(1);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('keeps legacy focus but reports location unavailable', () => {
    const resolved = resolveAdvisoryFocus(
      activeFocus('gfs'),
      legacyManifestWithoutEvidenceMetadata(),
      routeData(),
    );
    expect(resolved?.locationState).toBe('legacy');
    expect(resolved?.regions).toEqual([]);
  });

  it.each([
    ['complete', 'available'],
    ['partial', 'partial'],
    ['unavailable', 'unavailable'],
  ] as const)('maps %s model data to %s location state', (dataState, expected) => {
    const manifest = manifestWithTwoModels();
    manifest.advisories[0].per_model[0].data_state = dataState;
    expect(resolveAdvisoryFocus(activeFocus(), manifest, routeData())?.locationState)
      .toBe(expected);
  });

  it.each([
    ['unavailable', 'unavailable'],
    [null, 'legacy'],
  ] as const)('does not expose geometry for %s model data', (dataState, expectedState) => {
    const manifest = manifestWithTwoModels();
    manifest.advisories[0].per_model[0].data_state = dataState;

    const resolved = resolveAdvisoryFocus(activeFocus(), manifest, routeData());

    expect(resolved?.locationState).toBe(expectedState);
    expect(resolved?.regions).toEqual([]);
  });

  it('treats an unknown runtime data state as unavailable', () => {
    const manifest = manifestWithTwoModels();
    manifest.advisories[0].per_model[0].data_state = 'future-state' as never;
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    const resolved = resolveAdvisoryFocus(activeFocus(), manifest, routeData());

    expect(resolved?.locationState).toBe('unavailable');
    expect(resolved?.regions).toEqual([]);
    expect(warn).toHaveBeenCalledOnce();
  });

  it('warns and returns null for an unknown advisory or exact model', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    expect(resolveAdvisoryFocus(
      activeFocus('gfs', 'missing'),
      manifestWithTwoModels(),
      routeData(),
    )).toBeNull();
    expect(resolveAdvisoryFocus(
      activeFocus('icon'),
      manifestWithTwoModels(),
      routeData(),
    )).toBeNull();
    expect(warn).toHaveBeenCalledTimes(2);
  });

  it.each([
    ['reversed point span', { start_point_index: 2, end_point_index: 1 }],
    ['fractional point index', { start_point_index: 0.5 }],
    ['one missing altitude bound', { upper_altitude_ft: null }],
    ['reversed altitude bounds', { lower_altitude_ft: 10000, upper_altitude_ft: 9000 }],
    ['unavailable severity', { severity: 'unavailable' }],
    ['blank reason', { reason_code: '   ' }],
  ])('skips a malformed region with %s', (_label, regionPatch) => {
    const manifest = manifestWithOneValidAndOneInvalidRegion();
    const model = manifest.advisories[0].per_model[0];
    model.evidence_regions = [{
      ...model.evidence_regions![0],
      ...regionPatch,
    } as typeof model.evidence_regions[number]];
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    const resolved = resolveAdvisoryFocus(activeFocus(), manifest, routeData());

    expect(resolved?.regions).toEqual([]);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('requires every stable point index in the inclusive span', () => {
    const manifest = manifestWithOneValidAndOneInvalidRegion();
    const model = manifest.advisories[0].per_model[0];
    model.evidence_regions = [{
      ...model.evidence_regions![0],
      start_point_index: 0,
      end_point_index: 2,
    }];
    const data = routeData([0, 60]);
    data.points[1].pointIndex = 2;
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    expect(resolveAdvisoryFocus(activeFocus(), manifest, data)?.regions).toEqual([]);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('requires stable point indices to appear in route order', () => {
    const manifest = manifestWithOneValidAndOneInvalidRegion();
    const model = manifest.advisories[0].per_model[0];
    model.evidence_regions = [{
      ...model.evidence_regions![0],
      start_point_index: 0,
      end_point_index: 2,
    }];
    const data = routeData();
    data.points[1].pointIndex = 2;
    data.points[2].pointIndex = 1;
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    expect(resolveAdvisoryFocus(activeFocus(), manifest, data)?.regions).toEqual([]);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('requires stable point indices to occupy contiguous route positions', () => {
    const manifest = manifestWithOneValidAndOneInvalidRegion();
    const model = manifest.advisories[0].per_model[0];
    model.evidence_regions = [{
      ...model.evidence_regions![0],
      start_point_index: 0,
      end_point_index: 1,
    }];
    const data = routeData();
    data.points[1].pointIndex = 99;
    data.points[2].pointIndex = 1;
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    expect(resolveAdvisoryFocus(activeFocus(), manifest, data)?.regions).toEqual([]);
    expect(warn).toHaveBeenCalledOnce();
  });

  it('rejects non-monotonic route distances', () => {
    const manifest = manifestWithOneValidAndOneInvalidRegion();
    const model = manifest.advisories[0].per_model[0];
    model.evidence_regions = [{
      ...model.evidence_regions![0],
      start_point_index: 0,
      end_point_index: 2,
    }];
    const data = routeData([0, 30, 20]);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    expect(resolveAdvisoryFocus(activeFocus(), manifest, data)?.regions).toEqual([]);
    expect(pointCellBounds(data.points, 1)).toBeNull();
    expect(warn).toHaveBeenCalledOnce();
  });

  it('rejects non-finite map coordinates', () => {
    const manifest = manifestWithOneValidAndOneInvalidRegion();
    manifest.advisories[0].per_model[0].evidence_regions = [
      manifest.advisories[0].per_model[0].evidence_regions![0],
    ];
    const data = routeData();
    data.points[0].lat = Number.NaN;
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    expect(resolveAdvisoryFocus(activeFocus(), manifest, data)?.regions).toEqual([]);
    expect(routeCellPath(data.points, 0, 0)).toEqual([]);
    expect(warn).toHaveBeenCalledOnce();
  });
});

describe('route evidence geometry', () => {
  it('turns inclusive point indices into midpoint-owned distances', () => {
    const points = routeData([0, 10, 50, 100]).points;
    expect(pointCellBounds(points, 1)).toEqual({ startNm: 5, endNm: 30 });
    expect(pointCellBounds(points, 2)).toEqual({ startNm: 30, endNm: 75 });
  });

  it('returns null for an invalid point-cell position', () => {
    expect(pointCellBounds(routeData().points, -1)).toBeNull();
    expect(pointCellBounds(routeData().points, 99)).toBeNull();
  });

  it('uses interpolated midpoint coordinates for a one-cell map path', () => {
    expect(routeCellPath(routeData([0, 10, 50]).points, 1, 1)).toEqual([
      { lat: 50.5, lon: -0.5 },
      { lat: 51, lon: 0 },
      { lat: 51.5, lon: 0.5 },
    ]);
  });
});

describe('focus lifecycle helpers', () => {
  it('replaces focus when another advisory is selected', () => {
    const next = activeFocus('gfs', 'turbulence');
    expect(replaceAdvisoryFocus(activeFocus(), next)).toBe(next);
  });

  it('reconciles existing identifiers and clears missing models', () => {
    expect(reconcileAdvisoryFocus(activeFocus(), refreshedManifest()))
      .toEqual(activeFocus());
    expect(reconcileAdvisoryFocus(activeFocus(), manifestWithoutFocusedModel()))
      .toBeNull();
  });

  it('copies emphasis only while focus and a preset are active', () => {
    const focus = activeFocus();
    const emphasized = effectiveEmphasis(focus, 'clouds');
    expect(emphasized).toEqual(focus.emphasizeLayers);
    expect(emphasized).not.toBe(focus.emphasizeLayers);
    expect(effectiveEmphasis(focus, null)).toBeNull();
    expect(effectiveEmphasis(null, 'clouds')).toBeNull();
  });

  it('uses a shared effective region method, then the model primary method', () => {
    const resolved = resolveAdvisoryFocus(
      activeFocus('ecmwf'),
      manifestWithTwoModels(),
      routeData(),
    );
    expect(focusedMethodId(resolved)).toBe('nwp');
    resolved!.regions[0].methodId = null;
    expect(focusedMethodId(resolved)).toBe('nwp');

    const mixed = resolveAdvisoryFocus(
      activeFocus(),
      manifestWithDisjointGfsAndEcmwfRegions(),
      routeData(),
    )!;
    mixed.regions = [
      { ...mixed.regions[0], methodId: 'dewpoint_depression' },
      { ...mixed.regions[0], methodId: 'nwp' },
    ];
    mixed.modelResult.primary_method_id = 'nwp_synthesized';
    expect(focusedMethodId(mixed)).toBe('nwp_synthesized');
    expect(focusedMethodId(null)).toBeNull();
  });

  it.each([
    ['legacy', undefined, 'amber'],
    ['unknown', 'future-state', 'amber'],
    ['unavailable', 'unavailable', 'amber'],
    ['partial unavailable', 'partial', 'unavailable'],
  ] as const)('suppresses method attribution for %s model results', (_label, dataState, status) => {
    const manifest = manifestWithTwoModels();
    const model = manifest.advisories[0].per_model[0];
    model.data_state = dataState as typeof model.data_state;
    model.status = status;
    model.primary_method_id = 'nwp';
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    const resolved = resolveAdvisoryFocus(activeFocus(), manifest, routeData());

    expect(focusedMethodId(resolved)).toBeNull();
    warn.mockRestore();
  });

  it.each(['complete', 'partial'] as const)(
    'keeps method attribution for an assessed %s hazard',
    (dataState) => {
      const manifest = manifestWithTwoModels();
      manifest.advisories[0].per_model[0].data_state = dataState;
      expect(focusedMethodId(
        resolveAdvisoryFocus(activeFocus(), manifest, routeData()),
      )).toBe('nwp');
    },
  );
});

describe('VizPoint stable indices', () => {
  it('copies the route point index during normal extraction', () => {
    const manifest: RouteAnalysesManifest = {
      route_name: 'TEST',
      target_date: '2026-07-10',
      departure_time: '2026-07-10T10:00:00Z',
      flight_duration_hours: 1,
      total_distance_nm: 10,
      cruise_altitude_ft: 8000,
      models: ['gfs'],
      analyses: [{
        point_index: 42,
        lat: 50,
        lon: -1,
        distance_from_origin_nm: 10,
        waypoint_icao: null,
        waypoint_name: null,
        interpolated_time: '2026-07-10T10:30:00Z',
        forecast_hour: '2026-07-10T11:00:00Z',
        track_deg: 90,
        wind_components: {},
        sounding: {},
        altitude_advisories: null,
        model_divergence: [],
      }],
    };

    expect(extractVizData(manifest, 'gfs').points[0].pointIndex).toBe(42);
  });

  it('uses the synthetic profile position as the airport point index', () => {
    const snapshot: AirportProfileSnapshot = {
      meta: {
        icao: 'EGTF',
        lat: 51.5,
        lon: -0.5,
        elevation_ft: 100,
        model: 'gfs',
        start_hour: '2026-07-10T10:00:00Z',
        window_h: 1,
        hours: ['2026-07-10T10:00:00Z', '2026-07-10T11:00:00Z'],
      },
      surface: [],
      levels: [],
      enriched: null,
      derived: [],
    };

    expect(snapshotToVizData(snapshot)?.points.map((point) => point.pointIndex))
      .toEqual([0, 1]);
  });
});
