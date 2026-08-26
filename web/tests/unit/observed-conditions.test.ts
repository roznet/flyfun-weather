/** Client-side observed-conditions behaviour (#574).
 *
 * The invariant that matters most here is the one a renderer can quietly
 * violate: **a coverage hole is not a clear sky**. About half the OPERA grid
 * has no radar over it, so a client that folds `nodata` into "no echo" paints
 * half of Europe dry. Several of these tests exist only to pin that.
 */

import { describe, it, expect } from 'vitest';

import { extractVizData, getUnavailableLayers } from '../../ts/visualization/data-extract';
import {
  drawablePoints, significantBins, isNoCoverage, ageBadgeText, observedTopsLayer,
  highestTopFt, topsAboveScale, flLabel, bandRuns,
} from '../../ts/visualization/cross-section/layers/observed-tops';
import {
  echoColor, flashTickCount, observedSurfaceLayer,
} from '../../ts/visualization/cross-section/layers/observed-surface';
// Imported from the Leaflet-free geometry module: Leaflet touches `window` at
// module load, which the node test environment does not provide.
import {
  corridorBox, flashOpacity, formatBadge, overlayUrl,
} from '../../ts/visualization/route-map/observed-overlay-geometry';
import { getMetricById, sampleMetric } from '../../ts/visualization/route-graph/metrics';
import { LAYER_TOOLTIPS } from '../../ts/visualization/cross-section/tooltip-formatters';
import type {
  ElevationProfile, ObservedConditions, RouteAnalysesManifest,
} from '../../ts/store/types';
import type { VizPoint } from '../../ts/visualization/types';

function makeManifest(): RouteAnalysesManifest {
  return {
    route_name: 'TEST',
    target_date: '2026-08-25',
    departure_time: '2026-08-25T14:00:00Z',
    flight_duration_hours: 2,
    total_distance_nm: 100,
    cruise_altitude_ft: 8000,
    models: ['gfs'],
    analyses: [],
  } as RouteAnalysesManifest;
}

const elevation: ElevationProfile = {
  route_name: 'TEST',
  points: [
    { distance_nm: 0, elevation_ft: 0, lat: 50.0, lon: 1.0 },
    { distance_nm: 100, elevation_ft: 500, lat: 51.0, lon: 2.0 },
  ],
  max_elevation_ft: 500,
  total_distance_nm: 100,
};

function annulus(radius: number, over: Record<string, unknown> = {}) {
  return {
    radius_nm: radius,
    total_px: 100, valid_px: 100, nodata_px: 0, undetect_px: 60, detected_px: 40,
    max_value: 45, mean_value: 30, p90_value: 42,
    coverage_fraction: 1, detected_fraction: 0.4, insufficient_coverage: false,
    ...over,
  } as never;
}

function topsAnnulus(radius: number, over: Record<string, unknown> = {}) {
  return {
    ...(annulus(radius) as object),
    max_value: 10668,
    fl_bins: { 'FL000-050': 10, 'FL050-150': 0, 'FL150-250': 0, 'FL250-400': 30, 'FL400+': 0 },
    quality_method: { '0': 60, '1': 10, '6': 30 },
    highest_fl: 350,
    // -50C, thin and semi-transparent: the case where height alone would be
    // misleading, since FL350 sounds impenetrable and 35% opacity is not.
    coldest_top_k: 223.15,
    highest_cloudiness: 0.35,
    median_cloudiness: 0.6,
    highest_aviation_fl: 340,
    ...over,
  } as never;
}

function meta(source: string, over: Record<string, unknown> = {}) {
  return {
    source,
    quantity: source,
    units: 'dBZ',
    valid_time: '2026-08-25T14:05:00Z',
    age_minutes: 12,
    window_minutes: 10,
    attribution: { producer: 'Météo-France', license: 'OPERA policy', url: null, text: 'EUMETNET OPERA · Météo-France' },
    ...over,
  };
}

function makeObserved(over: Partial<ObservedConditions> = {}): ObservedConditions {
  return {
    computed_at: '2026-08-25T14:10:00Z',
    corridor_nm: 20,
    radii_nm: [5, 10, 20],
    stations: [
      { id: 'P000', name: 'LFAT', lat: 50.0, lon: 1.0, enroute_distance_nm: 0, distance_from_route_nm: 0 },
      { id: 'P001', name: null, lat: 50.5, lon: 1.5, enroute_distance_nm: 50, distance_from_route_nm: 0 },
    ],
    reflectivity: {
      ...meta('opera_dbzh'),
      stations: [
        { station_id: 'P000', annuli: [annulus(5), annulus(10), annulus(20, { max_value: 52 })] },
        // Second point sits in a coverage hole: it must NEVER read as clear.
        {
          station_id: 'P001',
          annuli: [5, 10, 20].map((r) => annulus(r, {
            valid_px: 0, nodata_px: 100, undetect_px: 0, detected_px: 0,
            max_value: null, mean_value: null, p90_value: null,
            coverage_fraction: 0, detected_fraction: null, insufficient_coverage: true,
          })),
        },
      ],
    } as never,
    rain_rate: {
      ...meta('opera_rate', { units: 'mm/h', window_minutes: 15 }),
      stations: [
        { station_id: 'P000', annuli: [5, 10, 20].map((r) => annulus(r, { max_value: 6.5 })) },
        {
          station_id: 'P001',
          annuli: [5, 10, 20].map((r) => annulus(r, {
            valid_px: 0, nodata_px: 100, detected_px: 0, undetect_px: 0,
            max_value: null, coverage_fraction: 0, detected_fraction: null,
            insufficient_coverage: true,
          })),
        },
      ],
    } as never,
    cloud_tops: {
      ...meta('eumetsat_ctth', { units: 'm', window_minutes: 0, valid_time: '2026-08-25T14:00:00Z', age_minutes: 17 }),
      stations: [
        { station_id: 'P000', annuli: [topsAnnulus(5), topsAnnulus(10), topsAnnulus(20)] },
        {
          station_id: 'P001',
          annuli: [5, 10, 20].map((r) => topsAnnulus(r, {
            insufficient_coverage: true, detected_px: 0, highest_fl: null,
            fl_bins: {}, quality_method: {},
          })),
        },
      ],
    } as never,
    lightning: {
      ...meta('eumetsat_li', { units: 'count' }),
      stations: [
        {
          station_id: 'P000',
          annuli: [5, 10, 20].map((r) => ({
            radius_nm: r, flash_count: r, area_km2: 1000, window_minutes: 10,
            nearest_flash_nm: 4, latest_flash_time: '2026-08-25T14:04:00Z',
            flashes_per_1000km2_per_min: r / 10,
          })),
        },
      ],
    } as never,
    summary: 'Radar: peak 52 dBZ within 20 NM of LFAT (observed 12 min ago).',
    summary_lines: ['Radar: peak 52 dBZ within 20 NM of LFAT (observed 12 min ago).'],
    sources: [
      { source: 'opera_dbzh', available: true, reason: null, latest_valid_time: '2026-08-25T14:05:00Z' },
    ],
    has_any_field: true,
    ...over,
  } as ObservedConditions;
}

function extract(observed: ObservedConditions | null, radiusNm: number | null = null) {
  return extractVizData(makeManifest(), 'gfs', 12000, elevation, {
    observedConditions: observed,
    observedRadiusNm: radiusNm,
  });
}

// --- Extraction ------------------------------------------------------------

describe('observed extraction', () => {
  it('resolves to the widest sampled radius by default', () => {
    const data = extract(makeObserved());
    expect(data.observed).not.toBeNull();
    expect(data.observed!.radiusNm).toBe(20);
    expect(data.observed!.radiiNm).toEqual([5, 10, 20]);
    expect(data.observed!.points[0].dbz).toBe(52);
  });

  it('honours a corridor pick without any re-fetch', () => {
    // Every radius already rides in the payload; picking one is a re-extract.
    const observed = makeObserved();
    expect(extract(observed, 5).observed!.points[0].dbz).toBe(45);
    expect(extract(observed, 20).observed!.points[0].dbz).toBe(52);
  });

  it('never interpolates between sampled radii', () => {
    // The discs are pixel counts over real areas; a value between two of them
    // was not measured. An unsampled radius snaps to the nearest one.
    const data = extract(makeObserved(), 13 as never);
    expect(data.observed!.radiusNm).toBe(20);
  });

  it('distinguishes a coverage hole from a clear sky', () => {
    const points = extract(makeObserved()).observed!.points;
    const covered = points.find((p) => p.distanceNm === 0)!;
    const hole = points.find((p) => p.distanceNm === 50)!;

    expect(covered.radarNoCoverage).toBe(false);
    expect(covered.dbz).toBe(52);

    expect(hole.radarNoCoverage).toBe(true);
    expect(hole.dbz).toBeNull();
    // And the flag is what tells them apart — the null alone does not.
    expect(hole.topsNoCoverage).toBe(true);
    expect(hole.topsHighestFt).toBeNull();
  });

  it('converts cloud tops from flight level to feet', () => {
    const point = extract(makeObserved()).observed!.points[0];
    expect(point.topsHighestFt).toBe(35000);
  });

  it('exposes the FL histogram as fractions of the detected pixels', () => {
    const point = extract(makeObserved()).observed!.points[0];
    const high = point.topsBins.find((b) => b.label === 'FL250-400')!;
    const low = point.topsBins.find((b) => b.label === 'FL000-050')!;
    // 30 of 40 cloudy pixels high, 10 low — a bimodal scene, which is exactly
    // the multi-layer structure one cloud-top number would have destroyed.
    expect(high.fraction).toBeCloseTo(0.75);
    expect(low.fraction).toBeCloseTo(0.25);
  });

  it('carries each source own valid time and age', () => {
    const observed = extract(makeObserved()).observed!;
    // The four streams are minutes apart; nothing here synthesises a shared
    // instant, and the client must not either.
    expect(observed.reflectivity!.validTime).toBe('2026-08-25T14:05:00Z');
    expect(observed.cloudTops!.validTime).toBe('2026-08-25T14:00:00Z');
    expect(observed.reflectivity!.ageMinutes).toBe(12);
    expect(observed.cloudTops!.ageMinutes).toBe(17);
  });

  it('keeps the rolling-window width alongside the age', () => {
    const observed = extract(makeObserved()).observed!;
    // A 10-minute rolling maximum is not an instantaneous observation.
    expect(observed.reflectivity!.windowMinutes).toBe(10);
    expect(observed.cloudTops!.windowMinutes).toBe(0);
  });

  it('carries the frame own attribution through', () => {
    const observed = extract(makeObserved()).observed!;
    expect(observed.reflectivity!.attribution).toContain('Météo-France');
  });

  it('returns null when nothing was collected', () => {
    expect(extract(null).observed).toBeNull();
  });
});

// --- Layer availability ----------------------------------------------------

describe('observed layer availability', () => {
  it('grays each layer out on its own source, not on the payload', () => {
    // Radar without EUMETSAT credentials is half the feature working.
    const radarOnly = makeObserved({ cloud_tops: null, lightning: null });
    const unavailable = getUnavailableLayers(extract(radarOnly));
    expect(unavailable.has('observed-tops')).toBe(true);
    expect(unavailable.has('observed-surface')).toBe(false);
  });

  it('grays both out with no observed data at all', () => {
    const unavailable = getUnavailableLayers(extract(null));
    expect(unavailable.has('observed-tops')).toBe(true);
    expect(unavailable.has('observed-surface')).toBe(true);
  });

  it('leaves the pre-existing current-conditions layer alone', () => {
    // These are siblings of that layer, not a replacement for it.
    const unavailable = getUnavailableLayers(extract(makeObserved()));
    expect(unavailable.has('current-conditions')).toBe(true); // no METAR in this fixture
  });
});

// --- Cross-section layers --------------------------------------------------

describe('observed-tops layer', () => {
  it('is on by default and sits with the other observed layers', () => {
    expect(observedTopsLayer.defaultEnabled).toBe(true);
    // Grouped by provenance, not by what it happens to overlap: everything
    // measured lives under "Observed conditions" so a pilot has one place to
    // look. Where it DRAWS is a separate concern — see the registry's
    // PANEL_ORDER note; it still paints before terrain fill.
    expect(observedTopsLayer.group).toBe('conditions');
  });

  it('draws a mark for a no-coverage point rather than skipping it', () => {
    // A skipped point leaves a gap, and a gap reads as "nothing up there".
    const observed = extract(makeObserved()).observed!;
    const hole = observed.points.find((p) => p.distanceNm === 50)!;
    expect(isNoCoverage(hole)).toBe(true);
    expect(drawablePoints(observed)).toContain(hole);
  });

  it('filters out negligible FL bands', () => {
    const point = extract(makeObserved()).observed!.points[0];
    const bins = significantBins(point);
    expect(bins.map((b) => b.label)).toEqual(['FL000-050', 'FL250-400']);
  });

  it('renders nothing in the time-axis airport view', () => {
    const calls: string[] = [];
    const ctx = new Proxy({}, { get: (_t, prop) => { calls.push(String(prop)); return () => {}; } });
    const data = { ...extract(makeObserved()), timeAxisMode: true };
    observedTopsLayer.render(ctx as never, {} as never, data as never);
    expect(calls).toEqual([]);
  });

  it('reports the highest top and whether it is above the chart', () => {
    const observed = extract(makeObserved()).observed!;
    const highest = highestTopFt(observed);
    expect(highest).not.toBeNull();

    // A GA chart scaled to 20,000 ft cannot reach FL350 cirrus: the cap line
    // has nowhere to draw, and clipping it silently would leave only the
    // minority FL bands visible — reading as "tops around FL200".
    const gaChart = {
      plotArea: { left: 0, top: 0, width: 100, height: 200 },
      altitudeToY: (ft: number) => 200 - (ft / 20000) * 200,
    };
    expect(topsAboveScale(observed, gaChart as never)).toBe(true);

    // A chart that does reach them draws the line normally.
    const tallChart = {
      plotArea: { left: 0, top: 0, width: 100, height: 200 },
      altitudeToY: (ft: number) => 200 - (ft / 60000) * 200,
    };
    expect(topsAboveScale(observed, tallChart as never)).toBe(false);
  });

  it('puts the off-scale top in the badge, since the line cannot be drawn', () => {
    const texts: string[] = [];
    const ctx = new Proxy({}, {
      get: (_t, prop) => {
        if (prop === 'fillText') return (text: string) => { texts.push(text); };
        if (prop === 'measureText') return () => ({ width: 10 });
        return () => {};
      },
      set: () => true,
    });
    const gaChart = {
      plotArea: { left: 0, top: 0, width: 100, height: 200 },
      distanceToX: (nm: number) => nm,
      altitudeToY: (ft: number) => 200 - (ft / 20000) * 200,
    };
    observedTopsLayer.render(ctx as never, gaChart as never, extract(makeObserved()) as never);
    const badge = texts.find((t) => t.includes('Satellite'));
    expect(badge).toBeDefined();
    expect(badge).toMatch(/tops to FL\d+/);
  });

  it('formats FL labels from feet', () => {
    expect(flLabel(38100)).toBe('FL381');
    expect(flLabel(5000)).toBe('FL50');
  });

  it('formats an age badge from the frame own valid time', () => {
    expect(ageBadgeText('2026-08-25T14:05:00Z', 12, 'Satellite')).toBe('Satellite 14:05Z · 12 min old');
    expect(ageBadgeText('2026-08-25T14:05:00Z', 0.4, 'Satellite')).toBe('Satellite 14:05Z · just now');
  });
});

describe('observed-surface layer', () => {
  it('is off by default and sits with the other surface overlays', () => {
    expect(observedSurfaceLayer.defaultEnabled).toBe(false);
    expect(observedSurfaceLayer.group).toBe('conditions');
  });

  it('ramps echo colour with intensity', () => {
    expect(echoColor(10)).not.toBe(echoColor(30));
    expect(echoColor(30)).not.toBe(echoColor(50));
    expect(echoColor(60)).toBe('#e13c3c');
  });

  it('names the source that actually supplied the badge timestamp', () => {
    // When OPERA is down but EUMETSAT lightning is up, the layer falls back to
    // the lightning frame for its age. The label must fall back with it — a
    // hardcoded 'Radar' stamps a radar name on a lightning frame's age, which
    // is the per-source age blending this feature forbids.
    const texts: string[] = [];
    const ctx = new Proxy({}, {
      get: (_t, prop) => {
        if (prop === 'fillText') return (text: string) => { texts.push(text); };
        if (prop === 'measureText') return () => ({ width: 10 });
        return () => {};
      },
      set: () => true,
    });
    const transform = {
      plotArea: { left: 0, top: 0, width: 100, height: 100 },
      distanceToX: (nm: number) => nm,
      altitudeToY: (ft: number) => 100 - ft / 1000,
    };

    const lightningOnly = extract(makeObserved({ reflectivity: null, rain_rate: null } as never));
    observedSurfaceLayer.render(ctx as never, transform as never, lightningOnly as never);
    const badge = texts.find((t) => t.includes('Z ·'));
    expect(badge).toBeDefined();
    expect(badge).not.toMatch(/Radar/);
    expect(badge).toMatch(/Lightning/);
  });

  it('scales flash ticks sub-linearly and draws none for zero', () => {
    expect(flashTickCount(0)).toBe(0);
    expect(flashTickCount(1)).toBe(1);
    expect(flashTickCount(200)).toBeLessThanOrEqual(4);
    expect(flashTickCount(8)).toBeGreaterThan(flashTickCount(2));
  });
});

// --- Route graph -----------------------------------------------------------

describe('observed route-graph metrics', () => {
  const point = (over: Partial<VizPoint>) => ({
    observedRateMmH: null, observedFlashRate: null, observedRadarNoCoverage: false,
    ...over,
  }) as VizPoint;

  it('registers both observed metrics', () => {
    expect(getMetricById('observed-rain-rate')).toBeDefined();
    expect(getMetricById('observed-flash-rate')).toBeDefined();
  });

  it('reports a coverage hole as its own state, not as unavailable or zero', () => {
    // A gap in the graph reads as "no rain"; this is "we cannot see".
    const metric = getMetricById('observed-rain-rate')!;
    const sample = sampleMetric(metric, point({ observedRadarNoCoverage: true, observedRateMmH: null }));
    expect(sample.kind).toBe('no-coverage');
  });

  it('reports a covered dry point as a real absence', () => {
    const metric = getMetricById('observed-rain-rate')!;
    const sample = sampleMetric(metric, point({ observedRateMmH: null }));
    expect(sample.kind).toBe('unavailable');
  });

  it('plots a measured rate', () => {
    const metric = getMetricById('observed-rain-rate')!;
    const sample = sampleMetric(metric, point({ observedRateMmH: 6.5 }));
    expect(sample).toEqual({ kind: 'value', value: 6.5 });
  });

  it('gives lightning no coverage state — the imager sees the whole disc', () => {
    const metric = getMetricById('observed-flash-rate')!;
    expect(metric.isNoCoverage).toBeUndefined();
    const sample = sampleMetric(metric, point({ observedFlashRate: 0, observedRadarNoCoverage: true }));
    expect(sample).toEqual({ kind: 'value', value: 0 });
  });

  it('folds observed values onto the route points', () => {
    const data = extract(makeObserved());
    // The fixture manifest has no analyses, so there are no points to fold
    // onto — the merge must not throw on that.
    expect(data.points).toEqual([]);
  });
});

// --- Map overlay -----------------------------------------------------------

describe('observed map overlay', () => {
  const routePoints = [
    { lat: 50.0, lon: 1.0 },
    { lat: 51.0, lon: 2.0 },
  ];

  it('pads the route bbox by the corridor width', () => {
    const box = corridorBox(routePoints, 20)!;
    expect(box.south).toBeLessThan(50.0);
    expect(box.north).toBeGreaterThan(51.0);
    // 20 NM ≈ 37 km ≈ 0.33° of latitude.
    expect(box.north - 51.0).toBeCloseTo(0.333, 2);
  });

  it('pads longitude more than latitude away from the equator', () => {
    const box = corridorBox(routePoints, 20)!;
    expect(box.east - 2.0).toBeGreaterThan(box.north - 51.0);
  });

  it('returns nothing for an empty route', () => {
    expect(corridorBox([], 20)).toBeNull();
  });

  it('builds an overlay URL from the box', () => {
    const url = overlayUrl('opera_dbzh', corridorBox(routePoints, 20)!);
    expect(url).toContain('/api/observed/overlay/opera_dbzh.png');
    expect(url).toContain('south=');
    expect(url).toContain('east=');
  });

  it('fades lightning by age and drops the tail', () => {
    expect(flashOpacity(0)).toBeGreaterThan(flashOpacity(30));
    expect(flashOpacity(30)).toBeGreaterThan(0);
    expect(flashOpacity(90)).toBe(0);
  });

  it('badges the frame with its own age and rolling window', () => {
    const observed = extract(makeObserved()).observed!;
    const text = formatBadge(observed.reflectivity);
    expect(text).toContain('14:05Z');
    expect(text).toContain('12 min old');
    // A rolling maximum is not a snapshot and the badge says so.
    expect(text).toContain('10 min rolling max');
    expect(text).toContain('Météo-France');
  });

  it('badges the cloud-top frame with ITS age, not the radar one', () => {
    const observed = extract(makeObserved()).observed!;
    const text = formatBadge(observed.cloudTops);
    expect(text).toContain('17 min old');
    // An instantaneous retrieval carries no rolling-window note.
    expect(text).not.toContain('rolling max');
  });

  it('says nothing when there is no frame to label', () => {
    expect(formatBadge(null)).toBe('');
  });
});

// --- Canvas rendering ------------------------------------------------------
//
// The tests above pin the DATA classification (hole vs clear vs value). These
// pin that the classification actually reaches the canvas: a regression that
// dropped the no-coverage hatch — collapsing it back to a blank gap, the exact
// bug the whole three-state design exists to prevent — would have passed every
// test above untouched.

interface DrawCall { op: string; args: number[] }

function recordingCtx() {
  const calls: DrawCall[] = [];
  const record = (op: string) => (...args: number[]) => { calls.push({ op, args }); };
  const ctx = {
    calls,
    save: record('save'), restore: record('restore'),
    beginPath: record('beginPath'), stroke: record('stroke'), fill: record('fill'),
    moveTo: record('moveTo'), lineTo: record('lineTo'),
    fillRect: record('fillRect'), strokeRect: record('strokeRect'),
    fillText: record('fillText'), strokeText: record('strokeText'),
    arc: record('arc'), closePath: record('closePath'), setLineDash: record('setLineDash'),
    measureText: () => ({ width: 60 }),
    globalAlpha: 1, fillStyle: '', strokeStyle: '', lineWidth: 1,
    font: '', textBaseline: '', textAlign: '',
  };
  return ctx as unknown as CanvasRenderingContext2D & { calls: DrawCall[] };
}

const transform = {
  plotArea: { left: 0, top: 0, width: 800, height: 400 },
  distanceToX: (d: number) => d * 4,
  altitudeToY: (ft: number) => 400 - ft / 100,
} as never;

describe('observed layers actually draw', () => {
  it('draws a hatch for a radar coverage hole rather than leaving a gap', () => {
    const data = extract(makeObserved());
    const ctx = recordingCtx();
    observedSurfaceLayer.render(ctx, transform, data);
    // The hole point is drawn with stroked hatch segments; a blank gap would
    // produce no stroke at all for it.
    const strokes = ctx.calls.filter((c) => c.op === 'stroke').length;
    expect(strokes).toBeGreaterThan(0);
  });

  it('stops drawing the hatch if the point is no longer flagged', () => {
    // Same scene with coverage restored: strictly fewer strokes, proving the
    // strokes above really came from the no-coverage branch.
    const withHole = extract(makeObserved());
    const ctxHole = recordingCtx();
    observedSurfaceLayer.render(ctxHole, transform, withHole);

    const patched = extract(makeObserved());
    for (const p of patched.observed!.points) { p.radarNoCoverage = false; p.dbz = 30; }
    const ctxClear = recordingCtx();
    observedSurfaceLayer.render(ctxClear, transform, patched);

    const holeStrokes = ctxHole.calls.filter((c) => c.op === 'stroke').length;
    const clearStrokes = ctxClear.calls.filter((c) => c.op === 'stroke').length;
    expect(holeStrokes).toBeGreaterThan(clearStrokes);
  });

  it('draws a hatch for a cloud-top no-coverage point', () => {
    const data = extract(makeObserved());
    const ctx = recordingCtx();
    observedTopsLayer.render(ctx, transform, data);
    expect(ctx.calls.some((c) => c.op === 'stroke')).toBe(true);
    // And the FL-band ticks are filled rects.
    expect(ctx.calls.some((c) => c.op === 'fillRect')).toBe(true);
  });

  it('gives each observed source its own badge row', () => {
    // Both layers on: the radar badge must not paint over the satellite one,
    // which is the "one age for two sources" outcome the design rules out.
    const data = extract(makeObserved());
    const tops = recordingCtx();
    observedTopsLayer.render(tops, transform, data);
    const surface = recordingCtx();
    observedSurfaceLayer.render(surface, transform, data);

    const badgeY = (ctx: typeof tops) =>
      ctx.calls.filter((c) => c.op === 'fillText').map((c) => c.args[2]);
    const topsY = badgeY(tops);
    const surfaceY = badgeY(surface);
    expect(topsY.length).toBeGreaterThan(0);
    expect(surfaceY.length).toBeGreaterThan(0);
    expect(surfaceY[surfaceY.length - 1]).not.toBe(topsY[topsY.length - 1]);
  });

  it('draws nothing at all when there is no observed data', () => {
    const ctx = recordingCtx();
    observedTopsLayer.render(ctx, transform, extract(null));
    observedSurfaceLayer.render(ctx, transform, extract(null));
    expect(ctx.calls).toEqual([]);
  });
});

describe('echo palette', () => {
  it('covers every server stop, including 65 dBZ', () => {
    // The server's _DBZ_STOPS has six stops; the client had five, so the most
    // intense echo on the map rendered as ordinary red on the cross-section.
    const stops = [5, 20, 35, 45, 55, 65];
    const colours = stops.map((dbz) => echoColor(dbz));
    expect(new Set(colours).size).toBe(stops.length);
    expect(echoColor(70)).toBe(echoColor(65));
    expect(echoColor(65)).not.toBe(echoColor(55));
  });

  it('matches the server palette values', () => {
    // Mirrors observed/imagery.py::_DBZ_STOPS exactly.
    expect(echoColor(5)).toBe('#5aa0dc');
    expect(echoColor(20)).toBe('#3cbe5a');
    expect(echoColor(35)).toBe('#f0d23c');
    expect(echoColor(45)).toBe('#f08c28');
    expect(echoColor(55)).toBe('#e13c3c');
    expect(echoColor(65)).toBe('#be3cbe');
  });
});

// --- Hover rows ------------------------------------------------------------

describe('observed hover rows', () => {
  /** A route point carrying the observed sample, which is all these defs read.
   *  (The distance-match that populates `.observed` in real extraction is
   *  `mergeObserved`; this manifest has no analyses, so there are no route
   *  points to merge onto.) */
  function pointWithObserved(index = 0) {
    const observed = extract(makeObserved()).observed!;
    const sample = observed.points[index];
    expect(sample, 'fixture produced no observed points').toBeDefined();
    return { distanceNm: sample.distanceNm, observed: sample } as never;
  }

  function rowFor(layerId: string, altFt: number): string | null {
    const def = LAYER_TOOLTIPS.find((d) => d.id === layerId)!;
    expect(def, `no tooltip registered for ${layerId}`).toBeDefined();
    const zones = def.getZones(pointWithObserved());
    if (zones.length === 0) return null;
    const match = zones.find((z) => altFt >= z.baseFt && altFt <= z.topFt) ?? zones[0];
    return def.formatLine(match, altFt);
  }

  it('reports radar, rain rate and lightning at any altitude', () => {
    // The composite is a column MAXIMUM with no height, so the row must show
    // wherever the cursor is — pinning it to an altitude would invent one.
    for (const alt of [1000, 8000, 20000]) {
      const line = rowFor('observed-surface', alt);
      expect(line, `nothing at ${alt} ft`).toBeTruthy();
      expect(line).toMatch(/dBZ|no echo|no radar coverage/);
    }
  });

  it('says "no radar coverage" rather than reporting a clear sky', () => {
    const def = LAYER_TOOLTIPS.find((d) => d.id === 'observed-surface')!;
    const observed = extract(makeObserved()).observed!;
    const holeIndex = observed.points.findIndex((p) => p.radarNoCoverage);
    expect(holeIndex, 'fixture must contain a coverage hole').toBeGreaterThanOrEqual(0);
    const zones = def.getZones(pointWithObserved(holeIndex));
    expect(def.formatLine(zones[0], 8000)).toContain('no radar coverage');
  });

  it('reports the top with its temperature and opacity', () => {
    const line = rowFor('observed-tops', 35000);
    expect(line).toBeTruthy();
    expect(line).toMatch(/FL\d+/);
    // Opacity is what separates "cannot get on top" from "wispy cirrus";
    // height alone draws both identically.
    expect(line).toMatch(/opaque \((solid|broken|thin)\)/);
    expect(line).toMatch(/-?\d+°C/);
  });
});

// --- Deck structure --------------------------------------------------------

describe('cloud-top deck grouping', () => {
  const band = (fl: number, count = 5) => ({
    label: `FL${fl}`, loFt: fl * 100, hiFt: (fl + 10) * 100, fraction: 0.1, count,
  });

  it('groups contiguous bands and keeps the gaps between decks', () => {
    // The structure coarse buckets destroyed. One real station measured decks
    // at FL7-31, FL60-92 and FL302-370 with nothing in between; the old
    // rendering painted slabs implying continuous cloud from the surface to
    // FL150.
    const runs = bandRuns([band(0), band(10), band(20), band(60), band(70), band(300)]);
    expect(runs.map((r) => r.length)).toEqual([3, 2, 1]);
    expect(runs[0][0].loFt).toBe(0);
    expect(runs[1][0].loFt).toBe(6000);
    expect(runs[2][0].loFt).toBe(30000);
  });

  it('does not care what order the bands arrive in', () => {
    const runs = bandRuns([band(70), band(0), band(60), band(10)]);
    expect(runs.map((r) => r.map((b) => b.loFt))).toEqual([[0, 1000], [6000, 7000]]);
  });

  it('keeps a single isolated band as its own deck', () => {
    // Often the coldest top on the chart, and the one worth seeing.
    expect(bandRuns([band(350, 1)]).length).toBe(1);
  });

  it('draws every populated band, however small its share', () => {
    // The old 5% floor was sized for 10,000-ft buckets. Against 1,000-ft
    // bands it would delete the tail — including that single coldest pixel.
    const tiny = { label: 'FL350', loFt: 35000, hiFt: 36000, fraction: 0.004, count: 1 };
    expect(significantBins({ topsBins: [tiny] } as never)).toHaveLength(1);
  });
});
