import { test, expect, type Page } from '@playwright/test';

// "Now" tab (#656): METAR markers, radar/satellite tiles, lightning and the
// experimental cell overlay, each with its own time on the badge. Every API is
// stubbed; times are relative to the real clock so staleness is deterministic.

const MOCK_USER = {
  id: 'test-user-001', email: 'test@example.com', name: 'Test Pilot', display_name: 'Test Pilot',
  is_admin: false, approved: true, setup_completed: true,
};

function slot(minutesAgo: number): { stamp: string; iso: string } {
  const t = new Date(Date.now() - minutesAgo * 60_000);
  t.setUTCSeconds(0, 0);
  t.setUTCMinutes(t.getUTCMinutes() - (t.getUTCMinutes() % 5));
  const p = (n: number) => String(n).padStart(2, '0');
  const stamp = `${t.getUTCFullYear()}${p(t.getUTCMonth() + 1)}${p(t.getUTCDate())}T${p(t.getUTCHours())}${p(t.getUTCMinutes())}`;
  return { stamp, iso: t.toISOString().replace('.000Z', '+00:00') };
}

function hhmm(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}Z`;
}

const radar = slot(5);
const cellsFrame = slot(10);
const old = slot(60);
const TRANSPARENT_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');

function cell(id: string, tier: string, lat: number, lon: number, state: string, motion: string) {
  return {
    id, tier, lat, lon, area_km2: 120, peak_dbz: 54, rate_peak_mm_h: 40, flashes: 7, top_fl: null,
    truncated: false, age_min: 35, event: 'continued',
    trend: { state, window_min: 30, d_peak_db: 6, area_ratio: 1.7, d_flashes: 4 },
    motion: { status: motion, reason: motion === 'available' ? null : 'split on this frame', speed_kt: 18, toward_deg: 60 },
    arrow: motion === 'available' ? [lat + 0.1, lon + 0.15] : null,
  };
}

const DISPLAY = {
  schema: 'observed-cells-display/1', policy_version: 'cells-1+0123abcd', code_revision: 'abc',
  valid_time: cellsFrame.iso, window_minutes: 10,
  times: { radar: cellsFrame.iso, rate: cellsFrame.iso, lightning: cellsFrame.iso, cloud_top: null },
  unavailable: [], rain_min_area_km2: 2000, arrow_minutes: 30,
  outlines: {
    rain20: [[[48.0, 1.0], [48.6, 1.2], [48.3, 2.0], [48.0, 1.0]]],
    core35: [[[49.0, 2.0], [49.2, 2.1], [49.1, 2.3], [49.0, 2.0]]],
    core41: [[[49.05, 2.05], [49.15, 2.1], [49.1, 2.2], [49.05, 2.05]]],
  },
  cells: [
    cell('core41-a', 'core41', 49.1, 2.15, 'developing', 'available'),
    cell('core35-b', 'core35', 50.5, 6.0, 'decaying', 'withheld'),
  ],
};

async function mockApis(page: Page, cellFrames: unknown) {
  await page.route('**/auth/me', (r) => r.fulfill({ json: MOCK_USER }));
  await page.route('**/api/user/preferences', (r) => r.fulfill({ json: { pirep_enabled: false } }));
  await page.route('**/api/messages/status', (r) => r.fulfill({ json: { unseen_count: 0 } }));
  await page.route('**/api/maps/forecast/days*', (r) => r.fulfill({ json: { max_day: 0, days: [] } }));
  await page.route('**/api/maps/forecast*', (r) => r.fulfill({ json: { forecast_time: '', model_init_times: {}, airports: [] } }));
  await page.route('**/api/maps/now', (r) => r.fulfill({ json: {
    at: new Date().toISOString(),
    sources: { metar: { available: true, count: 1, max_age_min: 90 } },
    airports: [{
      icao: 'ZZAA', lat: 49.0, lon: 2.5, models: {}, consensus: null, consensus_majority: null,
      observed: { metar: {
        flight_category: 'MVFR', observation_time: radar.iso, age_min: 5, report_type: 'METAR',
        raw: 'METAR ZZAA 041200Z 27010KT 9999 BKN025 12/08 Q1015', weather: [], dewpoint_c: 8, qnh: 1015,
      } },
    }],
  } }));
  await page.route('**/api/observed/frames/**', (r) => {
    const source = r.request().url().split('/').pop();
    return r.fulfill({ json: {
      source, label: source === 'satellite_ir' ? 'Satellite IR' : 'OPERA radar reflectivity',
      frames: [{ stamp: radar.stamp, valid_time: radar.iso, age_minutes: 5 }], stale: false,
      window_minutes: source === 'satellite_ir' ? 0 : 10, attribution: { text: '' },
      tile_url_template: `/api/observed/tiles/${source}/{stamp}/{z}/{x}/{y}.png`, min_zoom: 3, max_zoom: 9,
    } });
  });
  await page.route('**/api/observed/tiles/**', (r) => r.fulfill({ body: TRANSPARENT_PNG, contentType: 'image/png' }));
  await page.route('**/api/observed/flashes*', (r) => r.fulfill({ json: {
    flashes: [{ lat: 49.1, lon: 2.2, time: new Date(Date.now() - 2 * 60_000).toISOString() }],
    count: 1, newest_valid_time: cellsFrame.iso, window_minutes: 10, retention_minutes: 180, attribution: {},
  } }));
  await page.route('**/api/observed/cells/frames', (r) => r.fulfill({ json: cellFrames }));
  await page.route('**/api/observed/cells/*.json*', (r) => r.fulfill({ json: DISPLAY }));
}

function framesInfo(frames: Array<{ stamp: string; iso: string }>, enabled = true) {
  return {
    enabled,
    frames: frames.map((f) => ({ stamp: f.stamp, valid_time: f.iso, received_at: f.iso, age_minutes: 0 })),
    newest: null, stale: false, stale_after_minutes: 25, unavailable_since: null,
    url_template: '/api/observed/cells/{stamp}.json',
  };
}

test.describe('Now tab', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => { try { localStorage.removeItem('wb.now.layers'); } catch { /* */ } });
  });

  test('draws every layer with its own time on the badge', async ({ page }) => {
    await mockApis(page, framesInfo([cellsFrame]));
    await page.goto('/maps.html?tab=now');
    await expect(page.locator('.tab-btn[data-tab="now"]')).toHaveClass(/active/);
    const badge = page.locator('#now-badge');
    await expect(badge).toContainText(`Cells ${hhmm(cellsFrame.iso)}`);
    await expect(badge).toContainText('experimental');
    await expect(badge).toContainText(`OPERA radar reflectivity ${hhmm(radar.iso)}`);
    await expect(badge).toContainText('Lightning');
    await expect(badge).toContainText('METAR');
    // The radar and the overlay are different frames: two different times.
    expect(hhmm(radar.iso)).not.toBe(hhmm(cellsFrame.iso));
    await expect(page.locator('#now-legend')).toContainText('position in 30 min');

    // Arrows only for available motion: one cell of two has one (Leaflet
    // names the custom pane `leaflet-cells-pane`).
    // A line and an end dot, for the one cell with available motion.
    await expect(page.locator('.leaflet-cells-pane path[stroke="#b000b5"]')).toHaveCount(2);

    // Marker popup describes the cell, not a verdict.
    await page.locator('.leaflet-cells-pane path[fill="#d7263d"]').first().click({ force: true });
    await expect(page.locator('.leaflet-popup-content')).toContainText('moving ENE at 18 kt');

    // Toggling cells off removes the overlay and its badge line.
    await page.locator('#controls-now input[data-layer="cells"]').uncheck();
    await expect(badge).not.toContainText('Cells');
    await expect(page.locator('.leaflet-cells-pane path')).toHaveCount(0);
  });

  test('a stale feed draws nothing and says since when', async ({ page }) => {
    await mockApis(page, framesInfo([old]));
    await page.goto('/maps.html?tab=now');
    await expect(page.locator('#now-badge')).toContainText(`Cell analysis unavailable since ${hhmm(old.iso)}`);
    await expect(page.locator('.leaflet-cells-pane path')).toHaveCount(0);
  });

  test('a server without ingest says so', async ({ page }) => {
    await mockApis(page, framesInfo([], false));
    await page.goto('/maps.html?tab=now');
    await expect(page.locator('#now-badge')).toContainText('Cell analysis: not available on this server');
  });

  test('clicking an airport shows the raw METAR and its time', async ({ page }) => {
    await mockApis(page, framesInfo([cellsFrame]));
    await page.goto('/maps.html?tab=now');
    await expect(page.locator('#now-badge')).toContainText('METAR');
    await page.locator('#map-container-now .leaflet-overlay-pane path').first().click({ force: true });
    await expect(page.locator('#now-panel-host')).toContainText('METAR ZZAA 041200Z');
    await expect(page.locator('#now-panel-host')).toContainText(hhmm(radar.iso));
  });
});
