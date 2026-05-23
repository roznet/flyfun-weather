/** Tests for the current-conditions cross-section overlay:
 *  - extract mapping of snapshot observations/SIGMETs into VizRouteData
 *  - availability gating in getUnavailableLayers
 *  - geometry helpers (±2 nm column span, 5 nm SIGMET minimum, draw order)
 *  - flight-category color helper
 */

import { describe, it, expect, vi } from 'vitest';

// `flightCategoryColor('LIFR')` reads the theme-driven `--lifr` CSS var via
// cssVar(), which calls getComputedStyle(document) — unavailable in the node
// test environment. Stub cssVar to return its fallback so the LIFR branch is
// exercisable here; the other categories use literals and are unaffected.
vi.mock('../../ts/visualization/interaction-utils', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../ts/visualization/interaction-utils')>();
  return { ...actual, cssVar: (_name: string, fallback: string) => fallback };
});

import { extractVizData, getUnavailableLayers } from '../../ts/visualization/data-extract';
import {
  columnSpanNm, sigmetSpanNm, sortColumnsForDraw, isSevereSigmet,
  currentConditionsLayer,
} from '../../ts/visualization/cross-section/layers/current-conditions';
import { flightCategoryColor } from '../../ts/visualization/scales';
import type {
  RouteAnalysesManifest, ElevationProfile, RouteObservations, RouteSigmets,
  AirportObservation, SigmetAlongRoute, VizMetarColumn,
} from '../../ts/store/types';
import type { CoordTransform, VizRouteData } from '../../ts/visualization/types';

function makeManifest(overrides: Partial<RouteAnalysesManifest> = {}): RouteAnalysesManifest {
  return {
    route_name: 'TEST',
    target_date: '2026-05-23',
    departure_time: '2026-05-23T08:00:00Z',
    flight_duration_hours: 2,
    total_distance_nm: 100,
    cruise_altitude_ft: 8000,
    models: ['gfs'],
    analyses: [],
    ...overrides,
  };
}

const elevation: ElevationProfile = {
  route_name: 'TEST',
  points: [
    { distance_nm: 0, elevation_ft: 0, lat: 0, lon: 0 },
    { distance_nm: 100, elevation_ft: 2000, lat: 1, lon: 1 },
  ],
  max_elevation_ft: 2000,
  total_distance_nm: 100,
};

function makeAirport(overrides: Partial<AirportObservation> = {}): AirportObservation {
  return {
    icao: 'EGKK', name: null,
    distance_from_route_nm: 1, enroute_distance_nm: 50,
    nearest_waypoint_icao: 'EGKK',
    metar_raw: 'EGKK 230820Z 24010KT 9999 BKN012 12/10 Q1012',
    metar_time: null, metar_flight_category: 'MVFR',
    metar_ceiling_ft: 1200, metar_visibility_m: 9999,
    metar_wind_dir: 240, metar_wind_speed_kt: 10, metar_wind_gust_kt: null,
    metar_weather: [], metar_temperature_c: 12, metar_dewpoint_c: 10, metar_qnh: 1012,
    taf_raw: null, taf_flight_category_at_eta: null, taf_trend_type: null,
    taf_wind_dir: null, taf_wind_speed_kt: null, taf_wind_gust_kt: null,
    taf_applicable_text: null, taf_applicable_lines: [],
    metar_wind_advisory: null, metar_best_runway_id: null,
    metar_crosswind_kt: null, metar_headwind_kt: null,
    taf_wind_advisory: null, taf_best_runway_id: null,
    taf_crosswind_kt: null, taf_headwind_kt: null,
    has_metar: true, has_taf: false, eta_hour_offset: 1,
    ...overrides,
  };
}

function makeObservations(airports: AirportObservation[]): RouteObservations {
  return {
    corridor_nm: 30, fetch_time: '2026-05-23T08:00:00Z',
    airports_found: airports.length, airports_with_metar: airports.length, airports_with_taf: 0,
    airports, comparisons: [],
    worst_metar_category: null, worst_taf_category: null,
    has_conflicts: false, phenomena_along_route: [],
  };
}

function makeSigmet(overrides: Partial<SigmetAlongRoute> = {}): SigmetAlongRoute {
  return {
    fir_id: 'EGTT', fir_name: 'London', hazard: 'TURB', qualifier: 'SEV',
    base_ft: 5000, top_ft: 15000,
    valid_from: null, valid_to: null, direction: null, speed_kt: null,
    raw_text: 'EGTT SIGMET 01 ... SEV TURB ...',
    matched_firs: ['EGTT'], min_distance_nm: 5,
    enroute_distance_from_nm: 20, enroute_distance_to_nm: 60,
    coords: [],
    ...overrides,
  };
}

function makeSigmets(sigmets: SigmetAlongRoute[]): RouteSigmets {
  return {
    corridor_nm: 50, fetch_time: '2026-05-23T08:00:00Z',
    altitude_low_ft: 0, altitude_high_ft: 13000,
    time_window_from: null, time_window_to: null,
    route_firs: ['EGTT'], sigmets, hazards: ['TURB'],
    has_severe: true, count: sigmets.length,
  };
}

describe('extractVizData → currentConditions', () => {
  it('maps reporting airports into columns with terrain-interpolated base', () => {
    const data = extractVizData(makeManifest(), 'gfs', 8000, elevation, {
      routeObservations: makeObservations([makeAirport()]),
    });
    expect(data.currentConditions).not.toBeNull();
    const a = data.currentConditions!.airports[0];
    expect(a.icao).toBe('EGKK');
    expect(a.flightCategory).toBe('MVFR');
    expect(a.enrouteDistanceNm).toBe(50);
    // terrain at nm 50 interpolates between 0 ft @ 0 nm and 2000 ft @ 100 nm.
    expect(a.baseFt).toBeCloseTo(1000);
    expect(a.ceilingFt).toBe(1200);
  });

  it('uppercases the flight category', () => {
    const data = extractVizData(makeManifest(), 'gfs', 8000, elevation, {
      routeObservations: makeObservations([makeAirport({ metar_flight_category: 'ifr' })]),
    });
    expect(data.currentConditions!.airports[0].flightCategory).toBe('IFR');
  });

  it('skips airports with no flight category or no along-route position', () => {
    const data = extractVizData(makeManifest(), 'gfs', 8000, elevation, {
      routeObservations: makeObservations([
        makeAirport({ icao: 'OK1' }),
        makeAirport({ icao: 'NOCAT', metar_flight_category: null }),
        makeAirport({ icao: 'NODIST', enroute_distance_nm: null }),
      ]),
    });
    const icaos = data.currentConditions!.airports.map((a) => a.icao);
    expect(icaos).toEqual(['OK1']);
  });

  it('maps SIGMETs with a usable enroute span and skips spanless ones', () => {
    const data = extractVizData(makeManifest(), 'gfs', 8000, elevation, {
      routeSigmets: makeSigmets([
        makeSigmet({ hazard: 'TS' }),
        makeSigmet({ hazard: 'ICE', enroute_distance_from_nm: null }),
      ]),
    });
    const zones = data.currentConditions!.sigmets;
    expect(zones).toHaveLength(1);
    expect(zones[0].hazard).toBe('TS');
    expect(zones[0].enrouteFromNm).toBe(20);
    expect(zones[0].enrouteToNm).toBe(60);
  });

  it('is null when the snapshot carries no observations or SIGMETs', () => {
    const data = extractVizData(makeManifest(), 'gfs', 8000, elevation, {});
    expect(data.currentConditions).toBeNull();
  });
});

describe('getUnavailableLayers — current-conditions gating', () => {
  it('marks the layer unavailable when there is no D-0 data', () => {
    const data = extractVizData(makeManifest(), 'gfs', 8000, elevation, {});
    expect(getUnavailableLayers(data).has('current-conditions')).toBe(true);
  });

  it('is available when at least one airport reports', () => {
    const data = extractVizData(makeManifest(), 'gfs', 8000, elevation, {
      routeObservations: makeObservations([makeAirport()]),
    });
    expect(getUnavailableLayers(data).has('current-conditions')).toBe(false);
  });

  it('is available when only a SIGMET is present (no METARs)', () => {
    const data = extractVizData(makeManifest(), 'gfs', 8000, elevation, {
      routeSigmets: makeSigmets([makeSigmet()]),
    });
    expect(getUnavailableLayers(data).has('current-conditions')).toBe(false);
  });
});

describe('columnSpanNm', () => {
  it('returns a ±2 nm span around the airport position', () => {
    expect(columnSpanNm(50)).toEqual([48, 52]);
  });
});

describe('sigmetSpanNm', () => {
  it('preserves spans wider than the 5 nm minimum', () => {
    expect(sigmetSpanNm(20, 60)).toEqual([20, 60]);
  });

  it('normalizes reversed bounds', () => {
    expect(sigmetSpanNm(60, 20)).toEqual([20, 60]);
  });

  it('widens a sub-5 nm span to 5 nm centered on the midpoint', () => {
    expect(sigmetSpanNm(30, 32)).toEqual([28.5, 33.5]);
  });

  it('widens a zero-width span to 5 nm centered on the point', () => {
    expect(sigmetSpanNm(40, 40)).toEqual([37.5, 42.5]);
  });
});

describe('sortColumnsForDraw', () => {
  it('orders farthest-from-route first so the closest draws last (on top)', () => {
    const cols = [
      { icao: 'NEAR', distanceFromRouteNm: 1 },
      { icao: 'FAR', distanceFromRouteNm: 20 },
      { icao: 'MID', distanceFromRouteNm: 8 },
    ] as VizMetarColumn[];
    expect(sortColumnsForDraw(cols).map((c) => c.icao)).toEqual(['FAR', 'MID', 'NEAR']);
  });

  it('does not mutate the input array', () => {
    const cols = [
      { icao: 'A', distanceFromRouteNm: 1 },
      { icao: 'B', distanceFromRouteNm: 2 },
    ] as VizMetarColumn[];
    sortColumnsForDraw(cols);
    expect(cols.map((c) => c.icao)).toEqual(['A', 'B']);
  });
});

describe('isSevereSigmet', () => {
  it('flags SEV and EMBD qualifiers', () => {
    expect(isSevereSigmet('SEV')).toBe(true);
    expect(isSevereSigmet('EMBD')).toBe(true);
    expect(isSevereSigmet('embd')).toBe(true);
  });

  it('treats other / missing qualifiers as non-severe', () => {
    expect(isSevereSigmet('MOD')).toBe(false);
    expect(isSevereSigmet(null)).toBe(false);
  });
});

describe('flightCategoryColor', () => {
  it('maps known categories to the badge colors (case-insensitive)', () => {
    expect(flightCategoryColor('VFR')).toBe('#2d8a4e');
    expect(flightCategoryColor('mvfr')).toBe('#b8860b');
    expect(flightCategoryColor('IFR')).toBe('#c0392b');
  });

  it('falls back to gray for an unknown category', () => {
    expect(flightCategoryColor('???')).toBe('#6b7280');
  });

  it('reads LIFR from the --lifr CSS var (cssVar fallback when unset)', () => {
    expect(flightCategoryColor('LIFR')).toBe('#8e24aa');
  });
});

// --- render() smoke + z-order ---

interface FillRectCall { fillStyle: string; alpha: number; }

/** Minimal CanvasRenderingContext2D stub recording fillRect/strokeText calls. */
function fakeCtx() {
  const fillRects: FillRectCall[] = [];
  const fillTexts: string[] = [];
  const stack: Array<{ fillStyle: string; globalAlpha: number }> = [];
  const ctx: any = {
    fillStyle: '#000', strokeStyle: '#000', lineWidth: 1, globalAlpha: 1,
    font: '', textBaseline: '', textAlign: '',
    save() { stack.push({ fillStyle: this.fillStyle, globalAlpha: this.globalAlpha }); },
    restore() { const s = stack.pop(); if (s) { this.fillStyle = s.fillStyle; this.globalAlpha = s.globalAlpha; } },
    beginPath() {}, rect() {}, clip() {}, moveTo() {}, lineTo() {}, stroke() {}, setLineDash() {},
    fillRect() { fillRects.push({ fillStyle: this.fillStyle, alpha: this.globalAlpha }); },
    strokeRect() {},
    fillText(t: string) { fillTexts.push(t); },
    strokeText() {},
    measureText(t: string) { return { width: t.length * 6 }; },
  };
  return { ctx: ctx as CanvasRenderingContext2D, fillRects, fillTexts };
}

const identityTransform: CoordTransform = {
  distanceToX: (d) => d,
  altitudeToY: (ft) => 1000 - ft / 100,
  xToDistance: (x) => x,
  yToAltitude: (y) => (1000 - y) * 100,
  plotArea: { left: 0, top: 0, width: 1000, height: 1000 },
};

function makeVizData(cc: VizRouteData['currentConditions'], timeAxisMode = false): VizRouteData {
  return {
    points: [], cruiseAltitudeFt: 8000, ceilingAltitudeFt: 8000, flightCeilingFt: 13000,
    totalDistanceNm: 100, waypointMarkers: [],
    departureTime: '2026-05-23T08:00:00Z', flightDurationHours: 2,
    terrainProfile: null, timeAxisMode, currentConditions: cc,
  };
}

describe('currentConditionsLayer.render', () => {
  it('draws nothing in time-axis mode (airport-profile drawer)', () => {
    const { ctx, fillRects } = fakeCtx();
    const cc = { airports: [makeColumn('A', 1)], sigmets: [] };
    currentConditionsLayer.render(ctx, identityTransform, makeVizData(cc, true));
    expect(fillRects).toHaveLength(0);
  });

  it('draws nothing when there are no current conditions', () => {
    const { ctx, fillRects } = fakeCtx();
    currentConditionsLayer.render(ctx, identityTransform, makeVizData(null));
    expect(fillRects).toHaveLength(0);
  });

  it('draws the closest-to-route column last (on top) and labels each airport', () => {
    const { ctx, fillRects, fillTexts } = fakeCtx();
    const cc = {
      airports: [
        makeColumn('NEAR', 1, 'VFR', 80),
        makeColumn('FAR', 20, 'IFR', 10),
      ],
      sigmets: [],
    };
    currentConditionsLayer.render(ctx, identityTransform, makeVizData(cc));
    // Two column fills, semi-transparent.
    expect(fillRects).toHaveLength(2);
    expect(fillRects.every((f) => f.alpha < 1)).toBe(true);
    // Closest airport (VFR green) draws last → on top.
    expect(fillRects[fillRects.length - 1].fillStyle).toBe(flightCategoryColor('VFR'));
    expect(fillTexts).toContain('NEAR VFR');
    expect(fillTexts).toContain('FAR IFR');
  });

  it('draws a SIGMET zone fill and hazard label', () => {
    const { ctx, fillRects, fillTexts } = fakeCtx();
    const cc = {
      airports: [],
      sigmets: [{
        enrouteFromNm: 20, enrouteToNm: 60, baseFt: 5000, topFt: 15000,
        hazard: 'TURB', qualifier: 'SEV', rawText: 'SIGMET ...',
      }],
    };
    currentConditionsLayer.render(ctx, identityTransform, makeVizData(cc));
    expect(fillRects.length).toBeGreaterThanOrEqual(1);
    expect(fillTexts).toContain('SEV TURB');
  });
});

function makeColumn(icao: string, distanceFromRouteNm: number, cat = 'VFR', enrouteDistanceNm = 50): VizMetarColumn {
  return {
    icao, enrouteDistanceNm, distanceFromRouteNm, flightCategory: cat, baseFt: 0,
    metarRaw: null, ceilingFt: null, visibilityM: null,
    windDir: null, windSpeedKt: null, windGustKt: null,
  };
}
