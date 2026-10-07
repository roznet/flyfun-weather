import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// Observed nutshell + route ribbon (#690), the web half of the iOS Observed
// tab. Reuses the egtf_eglf fixture with its departure moved into the live
// window, and serves a synthetic `/live` layer: the archived LiveScenarios
// ticks all have a dark cells feed, so the rain/core band path and the cell
// sheet would otherwise never be exercised end to end. The fixtures' own
// (fallback) path is covered in tests/unit/observed-live-fixtures.test.ts.

const FIXTURES = join(__dirname, 'fixtures', 'egtf_eglf');
// Departure 30 min out: inside the live window (departure − 3 h … arrival + 1 h).
const DEPARTURE = new Date(Date.now() + 30 * 60_000);
const DEP_DATE = DEPARTURE.toISOString().slice(0, 10);
const FLIGHT_ID = `egtf_eglf-${DEP_DATE}-45ed`;
const TIMESTAMP = '2026-02-25T16:10:07.255073+00:00';

function fixture(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

/** Minimal observed payload: the map's Cells toggle is offered only for a
 *  briefing that actually carries observed conditions, and on flight day — the
 *  only time the ribbon is built — it does. */
function withObserved(snapshot: Record<string, any>) {
  const wps = snapshot.route.waypoints as Array<{ icao: string; latitude: number; longitude: number }>;
  const stations = wps.map((w, i) => ({
    id: `P00${i}`, name: w.icao, lat: w.latitude, lon: w.longitude,
    enroute_distance_nm: i * 30, distance_from_route_nm: 0,
  }));
  const annulus = (radius: number) => ({
    radius_nm: radius, valid_px: 100, nodata_px: 0, undetect_px: 80, detected_px: 20,
    max_value: 45, mean_value: 30, p90_value: 40, coverage_fraction: 1,
    detected_fraction: 0.2, insufficient_coverage: false,
  });
  snapshot.observed_conditions = {
    computed_at: NOW, corridor_nm: 20, radii_nm: [5, 10, 20], stations,
    reflectivity: {
      source: 'opera_dbzh', quantity: 'DBZH', units: 'dBZ', valid_time: NOW, age_minutes: 5,
      window_minutes: 10, attribution: { text: 'EUMETNET OPERA' },
      stations: stations.map((s) => ({ station_id: s.id, annuli: [5, 10, 20].map(annulus) })),
    },
    rain_rate: null, cloud_tops: null, lightning: null,
  };
  return snapshot;
}

const iso = (d: Date) => d.toISOString().replace('.000Z', '+00:00');
const plus = (min: number) => iso(new Date(DEPARTURE.getTime() + min * 60_000));
const NOW = iso(new Date());

const STORM_ID = 'core35-a';

/** A box around the route, as the server's `LiveFocus` carries it. */
const BBOX: [number, number, number, number] = [-0.9, 51.0, -0.3, 51.5];

function liveLayer() {
  const focus = (kind: string, id: string, layers: string[]) =>
    ({ kind, id, bbox: BBOX, layers, time: null });
  return {
    flight_id: FLIGHT_ID,
    pack_timestamp: TIMESTAMP,
    live_updated_at: NOW,
    route_observations: null,
    observations_updated_at: null,
    route_sigmets: null,
    sigmets_updated_at: null,
    observed_conditions: null,
    observed_updated_at: null,
    last_refresh_delta: null,
    changes: null,
    glance: {
      as_of: NOW,
      headline: 'Observed · as briefed, departure improving',
      comparison: 'as_briefed',
      lines: [
        {
          phase: 'departure', icao: 'EGTF', alert: false, passed: false,
          text: 'EGTF VFR · nearest cell 9 NM NE (46 dBZ), moving away 11 kt · no lightning ≤20 NM',
          unavailable: [], sources: ['metar:EGTF'],
          focus: focus('station', 'EGTF', ['route', 'radar', 'cells', 'metar']),
        },
        {
          phase: 'enroute', icao: null, alert: true, passed: false,
          text: 'Cells 25–40 NM east (inland), all moving away ENE',
          unavailable: ['lightning'], sources: [`storm:${STORM_ID}`],
          focus: focus('segment', 'seg:1', ['route', 'radar', 'cells']),
        },
        {
          phase: 'arrival', icao: 'EGLF', alert: false, passed: false,
          text: 'EGLF VFR · nothing within 30 NM now',
          unavailable: [], sources: ['metar:EGLF'], focus: null,
        },
      ],
    },
    ribbon: {
      route_nm: 100, flown_nm: 0, segment_nm: 50,
      radar_radius_nm: 10, radar_time: NOW,
      departure_at: iso(DEPARTURE), arrival_at: plus(60),
      weather_status: 'available', weather_corridor_nm: 30, weather_bin_nm: 5,
      waypoints: [
        { icao: 'EGTF', along_nm: 0, eta: iso(DEPARTURE) },
        { icao: 'EGLF', along_nm: 100, eta: plus(60) },
      ],
      segments: [
        { index: 0, from_nm: 0, to_nm: 50, radar_status: 'measured', radar_max_dbz: 24,
          lightning: false, sigmet_ids: [], storm_ids: [],
          focus: focus('segment', 'seg:0', ['route', 'radar']) },
        { index: 1, from_nm: 50, to_nm: 100, radar_status: 'measured', radar_max_dbz: 46,
          lightning: true, sigmet_ids: ['sigmet:LECM|6'], storm_ids: [STORM_ID],
          focus: focus('segment', 'seg:1', ['route', 'radar', 'cells']) },
      ],
      stations: [
        { icao: 'EGTF', role: 'departure', along_nm: 0, cross_nm: 0, metar_category: 'VFR',
          metar_time: NOW, convective: [], taf_category_at_eta: 'VFR',
          focus: focus('station', 'EGTF', ['route', 'metar']) },
        { icao: 'EGLF', role: 'destination', along_nm: 100, cross_nm: 0, metar_category: 'MVFR',
          metar_time: NOW, convective: ['CB'], taf_category_at_eta: 'IFR',
          taf_temporary_type: 'PROB30', taf_temporary_category: 'IFR',
          focus: focus('station', 'EGLF', ['route', 'metar']) },
        { icao: 'EGLK', role: 'route', along_nm: 60, cross_nm: -14, metar_category: 'VFR',
          metar_time: NOW, convective: ['TCU'],
          focus: focus('station', 'EGLK', ['route', 'metar']) },
      ],
      sigmets: [
        { id: 'sigmet:LECM|6', label: 'LECM 6: EMBD TS', hazard: 'TS', qualifier: 'EMBD',
          from_nm: 50, to_nm: 100, valid_from: NOW, valid_to: plus(120), pending: false,
          new: false, motion: 'away',
          focus: focus('sigmet', 'sigmet:LECM|6', ['route', 'sigmets']) },
      ],
      weather: [
        { id: 'rain-1', tier: 'rain', from_nm: 40, to_nm: 90, side: 'right',
          near_nm: 4, far_nm: 26, peak_dbz: 28, intensity: 'moderate',
          profile: [[50, 4, 24], [60, 6, 26], [70, 8, 22]],
          motion_rel_deg: 70, speed_kt: 14, storm_id: null },
        { id: 'core-1', tier: 'core', from_nm: 55, to_nm: 70, side: 'right',
          near_nm: 8, far_nm: 16, peak_dbz: 46, intensity: 'very heavy', flashes: 3,
          profile: [[60, 8, 16]], motion_rel_deg: 70, speed_kt: 14, storm_id: STORM_ID },
      ],
    },
    storms: {
      status: 'available', frame_time: NOW, corridor_nm: 30, route_nm: 100,
      lightning_pending: false, policy_version: 'cells-1+test',
      storms: [{
        id: STORM_ID, cell_ids: [STORM_ID], lat: 51.25, lon: -0.5,
        peak_dbz: 46, intensity: 'very heavy', flashes: 3, flashes_pending: false,
        top_fl: 310, truncated: false, trend: 'developing',
        d_peak_db: 3, area_ratio: 1.4, d_flashes: 2,
        motion_status: 'available', speed_kt: 14, toward_deg: 70,
        along_nm: 60, offtrack_nm: 12, cross_nm: 12, side: 'right',
        abeam_eta: plus(36), minutes_to_abeam: 36, ahead: true,
        relative_motion: 'moving_away', closing_kt: -11,
        history: [{ at: NOW, offtrack_nm: 8, cross_nm: 8 }],
        backing: ['EGLK TCU'],
        estimate: { cpa_nm: 11, cpa_time: plus(20), at_eta_offtrack_nm: 18, horizon_min: 35 },
        focus: focus('storm', STORM_ID, ['route', 'radar', 'cells', 'lightning']),
      }],
    },
  };
}

async function mockBriefingApi(page: Page) {
  const enc = encodeURIComponent;
  await page.route(`**/api/flights/${FLIGHT_ID}`, route => {
    if (route.request().url().includes('/packs')) return route.fallthrough();
    return route.fulfill({ json: {
      ...fixture('flight.json'), id: FLIGHT_ID,
      departure_time: iso(DEPARTURE), target_date: DEP_DATE,
    } });
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, route => {
    const after = route.request().url().split('/packs')[1];
    if (after && after !== '' && after !== '/') return route.fallthrough();
    return route.fulfill({ json: fixture('packs.json') });
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/${enc(TIMESTAMP)}`, route => {
    const after = route.request().url().split(enc(TIMESTAMP))[1];
    if (after && after !== '' && after !== '/') return route.fallthrough();
    return route.fulfill({ json: fixture('pack_meta.json') });
  });
  await page.route(`**/packs/${enc(TIMESTAMP)}/snapshot`, r =>
    r.fulfill({ json: withObserved(fixture('snapshot.json')) }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/route-analyses`, r => r.fulfill({ json: fixture('route_analyses.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories`, r => r.fulfill({ json: fixture('advisories.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/elevation`, r => r.fulfill({ json: fixture('elevation.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/digest/json`, r => r.fulfill({ json: fixture('digest.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/gramet**`, r => r.fulfill({ status: 404, json: { detail: 'none' } }));
  await page.route('**/api/observed/status', r => r.fulfill({ json: { sources: [] } }));
  await page.route('**/api/observed/frames/**', r => {
    const source = r.request().url().split('/').pop();
    return r.fulfill({ json: {
      source, label: 'OPERA radar reflectivity', frames: [], stale: false,
      window_minutes: 10, attribution: { text: '' },
      tile_url_template: `/api/observed/tiles/${source}/{stamp}/{z}/{x}/{y}.png`,
      min_zoom: 3, max_zoom: 9,
    } });
  });
  await page.route('**/api/observed/cells/frames', r => r.fulfill({ json: {
    enabled: true, frames: [], newest: null, stale: false,
    stale_after_minutes: 25, unavailable_since: null,
    url_template: '/api/observed/cells/{stamp}.json',
  } }));
  await page.route('**/api/observed/**', r => r.fulfill({ json: {} }));
  // The live layer: the only place glance / ribbon / storms are served.
  await page.route(`**/api/flights/${FLIGHT_ID}/live`, r => r.fulfill({ json: liveLayer() }));
}

test.describe('Observed nutshell + route ribbon', () => {
  test.beforeEach(async ({ page }) => { await mockBriefingApi(page); });

  test('shows the server\'s nutshell lines verbatim, in flight order', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    const card = page.locator('[data-testid="observed-nutshell"]');
    await expect(card).toBeVisible();
    await expect(page.locator('[data-testid="glance-headline"]'))
      .toContainText('as briefed, departure improving');

    // Every word is the server's — no re-wording, no re-ordering.
    const lines = page.locator('.glance-line');
    await expect(lines).toHaveCount(3);
    await expect(lines.nth(0)).toContainText('EGTF VFR');
    await expect(lines.nth(0)).toContainText('moving away 11 kt');
    await expect(lines.nth(1)).toContainText('Cells 25–40 NM east');
    await expect(lines.nth(2)).toContainText('EGLF VFR');

    await expect(page.locator('[data-testid="glance-line-departure"] .glance-phase')).toHaveText('Departure');
    await expect(page.locator('[data-testid="glance-line-enroute"] .glance-phase')).toHaveText('En route');

    // The alert tier is a styling hint on the phase the server flagged.
    await expect(lines.nth(1)).toHaveClass(/glance-line-alert/);
    await expect(lines.nth(0)).not.toHaveClass(/glance-line-alert/);
    // A source it could not read is named, never rendered as "clear".
    await expect(lines.nth(1)).toContainText('lightning unavailable');
    await page.locator('#observed-glance-wrapper').scrollIntoViewIfNeeded();
    await page.locator('#observed-glance-wrapper').screenshot({ path: 'test-results/observed-nutshell.png' });
  });

  test('draws the ribbon as a symbolic map of the route', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    const svg = page.locator('.ribbon-svg');
    await expect(svg).toBeVisible();
    // Both route ends on the axis, with their ETAs.
    await expect(svg).toContainText('EGTF');
    await expect(svg).toContainText('EGLF');
    // Three airports: two ends on the line plus the en-route one in its row.
    await expect(svg.locator('.ribbon-station')).toHaveCount(3);
    // The SIGMET band across the top, named by FIR and hazard.
    await expect(svg.locator('.ribbon-sigmet')).toHaveCount(1);
    await expect(svg).toContainText('LECM 6 EMBD TS');
    // Rain band + core band, each drawn from its profile bins.
    const rects = svg.locator('rect[fill="rgba(60, 190, 90, 0.28)"]');
    await expect(rects).toHaveCount(3);
    await expect(svg.locator('rect[fill="#f08c28"]')).toHaveCount(1);
    // One arrow per moving band, and a tap target for the cell.
    await expect(svg.locator('.ribbon-arrow')).toHaveCount(2);
    await expect(svg.locator(`[data-ribbon-storm="${STORM_ID}"]`)).toHaveCount(1);
    // The key describes the lane that was actually drawn.
    await expect(page.locator('.ribbon-key')).toContainText('cells 35/41/50 dBZ');
    await expect(page.locator('.ribbon-caption')).toContainText('±30 NM of course');
    await page.locator('#observed-ribbon-wrapper').scrollIntoViewIfNeeded();
    await page.locator('#observed-ribbon-wrapper').screenshot({ path: 'test-results/observed-ribbon.png' });
  });

  test('lists the new sections in the iOS reading order', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    await expect(page.locator('.ribbon-svg')).toBeVisible();
    await expect(page.locator('.rail-nav-item .rail-nav-label').filter({ hasText: 'At a glance' }))
      .toBeVisible();
    const labels = await page.locator('.rail-nav-item .rail-nav-label').allTextContents();
    const glance = labels.indexOf('At a glance');
    const ribbon = labels.indexOf('Along the route');
    expect(glance).toBeGreaterThanOrEqual(0);
    expect(ribbon).toBe(glance + 1);
    // What you need to know comes before the detail by source.
    const metar = labels.indexOf('METAR / TAF');
    if (metar >= 0) expect(ribbon).toBeLessThan(metar);

    // And the page itself, which is what the pilot scrolls.
    const onPage = await page.locator('.section[data-section]').evaluateAll(
      (els) => els
        .filter((e) => (e as HTMLElement).style.display !== 'none')
        .map((e) => e.getAttribute('data-section')),
    );
    expect(onPage.indexOf('observed-glance')).toBeGreaterThanOrEqual(0);
    expect(onPage.indexOf('observed-ribbon')).toBe(onPage.indexOf('observed-glance') + 1);
    expect(onPage.indexOf('observed-ribbon')).toBeLessThan(onPage.indexOf('observations'));
  });

  test('every stretch of route is reachable by keyboard, not just by click', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    const svg = page.locator('.ribbon-svg');
    await expect(svg).toBeVisible();
    // One focusable button per segment that carries a focus, over the zone.
    const segHits = svg.locator('.ribbon-seg-hit');
    await expect(segHits).toHaveCount(2);
    await expect(segHits.first()).toHaveAttribute('role', 'button');
    await expect(segHits.first()).toHaveAttribute('tabindex', '0');
    await expect(segHits.first()).toHaveAttribute('aria-label', /radar peak 24 dBZ/);
    // Enter activates it, exactly as a click would.
    await segHits.nth(1).focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.leaflet-container')).toBeVisible();
    await expect(page.locator('#map-observed-cells')).toBeChecked();
  });

  test('labels the drawing for a screen reader without shouting the decoration', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    const svg = page.locator('.ribbon-svg');
    await expect(svg).toBeVisible();
    // The weather picture is described as an image, not an unlabelled rect.
    const zone = svg.locator('.ribbon-zone-hit');
    await expect(zone).toHaveAttribute('role', 'img');
    await expect(zone).toHaveAttribute('aria-label', /1 rain areas, 1 cells along the route/);
    // The motion arrows restate what the summary already says.
    await expect(svg.locator('.ribbon-arrow').first()).toHaveAttribute('aria-hidden', 'true');
    // Phase labels stay title case in the markup; CSS does the shouting.
    await expect(page.locator('[data-testid="glance-line-enroute"] .glance-phase'))
      .toHaveText('En route');
  });

  test('a cell opens its detail, with the estimate kept in its own block', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    await page.locator(`[data-ribbon-storm="${STORM_ID}"]`).click();
    const detail = page.locator('.storm-detail');
    await expect(detail).toBeVisible();
    await expect(detail).toContainText('Very heavy cell');
    await expect(detail).toContainText('12 NM right of track at 60 NM');
    await expect(detail).toContainText('moving away 11 kt');
    await expect(detail).toContainText('FL310');
    await expect(detail).toContainText('EGLK TCU');
    // The projection is set apart and labelled — never in the nutshell.
    const estimate = page.locator('[data-testid="storm-estimate"]');
    await expect(estimate).toContainText('Estimate at current motion');
    await expect(estimate).toContainText('A projection, not an observation');
    await expect(page.locator('[data-testid="observed-nutshell"]'))
      .not.toContainText('Estimate at current motion');
    await detail.screenshot({ path: 'test-results/observed-storm-detail.png' });
  });

  test('tapping a nutshell line opens the map on it with its layers on', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    await expect(page.locator('.ribbon-svg')).toBeVisible();
    // The en-route line's focus names the cells layer, and the map is not on
    // screen in the default cross-section layout: the tap must bring it up.
    await expect(page.locator('.leaflet-container')).toHaveCount(0);
    await page.locator('[data-testid="glance-line-enroute"]').click();
    await expect(page.locator('.leaflet-container')).toBeVisible();
    await expect(page.locator('#map-observed-cells')).toBeChecked();
  });
});
