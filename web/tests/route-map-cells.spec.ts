import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// Briefing route map: the Cells toggle on the observed layers (#656). Reuses
// the egtf_eglf fixture with a minimal observed payload injected, and stubs
// the observed + cells endpoints.

const FIXTURES = join(__dirname, 'fixtures', 'egtf_eglf');
const FUTURE_DATE = new Date(Date.now() + 3 * 86400_000).toISOString().slice(0, 10);
const FLIGHT_ID = `egtf_eglf-${FUTURE_DATE}-45ed`;
const TIMESTAMP = '2026-02-25T16:10:07.255073+00:00';

function fixture(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

function slot(minutesAgo: number): { stamp: string; iso: string } {
  const t = new Date(Date.now() - minutesAgo * 60_000);
  t.setUTCSeconds(0, 0);
  t.setUTCMinutes(t.getUTCMinutes() - (t.getUTCMinutes() % 5));
  const p = (n: number) => String(n).padStart(2, '0');
  const stamp = `${t.getUTCFullYear()}${p(t.getUTCMonth() + 1)}${p(t.getUTCDate())}T${p(t.getUTCHours())}${p(t.getUTCMinutes())}`;
  return { stamp, iso: t.toISOString().replace('.000Z', '+00:00') };
}
const hhmm = (iso: string) => {
  const d = new Date(iso);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}Z`;
};

const radar = slot(5);
const cellsFrame = slot(10);
const TRANSPARENT_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');

function annulus(radius: number) {
  return {
    radius_nm: radius, valid_px: 100, nodata_px: 0, undetect_px: 80, detected_px: 20,
    max_value: 45, mean_value: 30, p90_value: 40, coverage_fraction: 1, detected_fraction: 0.2,
    insufficient_coverage: false,
  };
}

function observedConditions(snapshot: Record<string, any>) {
  const wps = snapshot.route.waypoints as Array<{ icao: string; latitude: number; longitude: number }>;
  const stations = wps.map((w, i) => ({
    id: `P00${i}`, name: w.icao, lat: w.latitude, lon: w.longitude,
    enroute_distance_nm: i * 30, distance_from_route_nm: 0,
  }));
  return {
    computed_at: radar.iso, corridor_nm: 20, radii_nm: [5, 10, 20], stations,
    reflectivity: {
      source: 'opera_dbzh', quantity: 'DBZH', units: 'dBZ', valid_time: radar.iso, age_minutes: 5,
      window_minutes: 10, attribution: { text: 'EUMETNET OPERA' },
      stations: stations.map((s) => ({ station_id: s.id, annuli: [5, 10, 20].map(annulus) })),
    },
    rain_rate: null, cloud_tops: null, lightning: null,
  };
}

async function mockBriefingApi(page: Page) {
  const enc = encodeURIComponent;
  await page.route(`**/api/flights/${FLIGHT_ID}`, route => {
    if (route.request().url().includes('/packs'))
      return route.fallthrough();            // let more-specific routes handle /packs/*
    const flight = { ...fixture('flight.json'), id: FLIGHT_ID, departure_time: `${FUTURE_DATE}T17:00:00+00:00`, target_date: FUTURE_DATE };
    return route.fulfill({ json: flight });
  });

  // GET /api/flights/{id}/packs
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, route => {
    // Only match the packs list, not sub-paths like /packs/{ts}/snapshot
    const url = route.request().url();
    const afterPacks = url.split('/packs')[1];
    if (afterPacks && afterPacks !== '' && afterPacks !== '/')
      return route.fallthrough();
    return route.fulfill({ json: fixture('packs.json') });
  });

  // GET /api/flights/{id}/packs/{ts}  (pack metadata)
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/${enc(TIMESTAMP)}`, route => {
    const url = route.request().url();
    const afterTs = url.split(enc(TIMESTAMP))[1];
    if (afterTs && afterTs !== '' && afterTs !== '/')
      return route.fallthrough();
    return route.fulfill({ json: fixture('pack_meta.json') });
  });

  // GET /api/flights/{id}/packs/{ts}/snapshot
  await page.route(`**/packs/${enc(TIMESTAMP)}/snapshot`, route =>
    {
      const snapshot = fixture('snapshot.json');
      snapshot.observed_conditions = observedConditions(snapshot);
      return route.fulfill({ json: snapshot });
    }
  );

  // GET /api/flights/{id}/packs/{ts}/route-analyses
  await page.route(`**/packs/${enc(TIMESTAMP)}/route-analyses`, route =>
    route.fulfill({ json: fixture('route_analyses.json') })
  );

  // GET /api/flights/{id}/packs/{ts}/advisories
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories`, route =>
    route.fulfill({ json: fixture('advisories.json') })
  );

  // GET /api/flights/{id}/packs/{ts}/elevation
  await page.route(`**/packs/${enc(TIMESTAMP)}/elevation`, route =>
    route.fulfill({ json: fixture('elevation.json') })
  );

  // GET /api/flights/{id}/packs/{ts}/digest/json
  await page.route(`**/packs/${enc(TIMESTAMP)}/digest/json`, route =>
    route.fulfill({ json: fixture('digest.json') })
  );

  // Gramet — not configured, return 404
  await page.route(`**/packs/${enc(TIMESTAMP)}/gramet**`, route =>
    route.fulfill({ status: 404, json: { detail: 'GRAMET not available' } })
  );

  // Freshness check — return a simple response so the page doesn't trigger refresh

}

const cellRequests: string[] = [];

async function mockObserved(page: Page) {
  await page.route('**/api/observed/status', (r) => r.fulfill({ json: { sources: [] } }));
  await page.route('**/api/observed/frames/**', (r) => {
    const source = r.request().url().split('/').pop();
    return r.fulfill({ json: {
      source, label: source === 'satellite_ir' ? 'Satellite IR' : 'OPERA radar reflectivity',
      frames: [{ stamp: radar.stamp, valid_time: radar.iso, age_minutes: 5 }], stale: false,
      window_minutes: 10, attribution: { text: '' },
      tile_url_template: `/api/observed/tiles/${source}/{stamp}/{z}/{x}/{y}.png`, min_zoom: 3, max_zoom: 9,
    } });
  });
  await page.route('**/api/observed/tiles/**', (r) => r.fulfill({ body: TRANSPARENT_PNG, contentType: 'image/png' }));
  await page.route('**/api/observed/cells/frames', (r) => r.fulfill({ json: {
    enabled: true,
    frames: [{ stamp: cellsFrame.stamp, valid_time: cellsFrame.iso, received_at: cellsFrame.iso, age_minutes: 10 }],
    newest: null, stale: false, stale_after_minutes: 25, unavailable_since: null,
    url_template: '/api/observed/cells/{stamp}.json',
  } }));
  await page.route('**/api/observed/cells/*.json*', (r) => {
    cellRequests.push(r.request().url());
    return r.fulfill({ json: {
      schema: 'observed-cells-display/1', policy_version: 'cells-1+0123abcd', code_revision: null,
      valid_time: cellsFrame.iso, window_minutes: 10,
      times: { radar: cellsFrame.iso, rate: null, lightning: null, cloud_top: null },
      unavailable: [], rain_min_area_km2: 2000, arrow_minutes: 30,
      outlines: { core35: [[[51.2, -0.6], [51.3, -0.5], [51.25, -0.4], [51.2, -0.6]]] },
      cells: [{
        id: 'core35-a', tier: 'core35', lat: 51.25, lon: -0.5, area_km2: 30, peak_dbz: 44,
        rate_peak_mm_h: null, flashes: null, top_fl: null, truncated: false, age_min: 20, event: 'continued',
        trend: { state: 'steady', window_min: 30, d_peak_db: 1, area_ratio: 1.1, d_flashes: null },
        motion: { status: 'available', reason: null, speed_kt: 12, toward_deg: 90 },
        arrow: [51.25, -0.35],
      }],
    } });
  });
}

test.describe('Briefing route map — cell overlay', () => {
  test.beforeEach(async ({ page }) => {
    cellRequests.length = 0;
    await mockBriefingApi(page);
    await mockObserved(page);
  });

  test('Cells toggle draws the corridor\'s cells with their own time', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    await page.locator('button[data-layout="map"]').first().click();
    const toggle = page.locator('#map-observed-cells');
    await expect(toggle).toBeVisible();
    await expect(toggle).not.toBeChecked();  // experimental: off by default
    await toggle.check();

    const badge = page.locator('.map-observed-badge');
    await expect(badge).toContainText(`Cells ${hhmm(cellsFrame.iso)}`);
    await expect(badge).toContainText('experimental');
    await expect(page.locator('.leaflet-cells-pane path[fill="#7a7a7a"]')).toHaveCount(1);
    await expect(page.locator('.map-cells-legend')).toBeAttached();
    // The route's box, not all of Europe.
    expect(cellRequests.length).toBeGreaterThan(0);
    expect(cellRequests[0]).toContain('south=');

    await toggle.uncheck();
    await expect(page.locator('.leaflet-cells-pane path')).toHaveCount(0);
    await expect(badge).not.toContainText('Cells');
  });
});
