import { test, expect, type Browser, type Page } from '@playwright/test';

// Share view on the maps page (#796): a shared link reopens the same view on
// every tab, controls and map position included, in a fresh browser context
// (empty localStorage). Every API is stubbed. The map position is checked by
// zooming in once on the recipient's side: a zoom keeps the centre, so the URL
// then reads the sender's lat,lon at one zoom level more.

const MOCK_USER = {
  id: 'test-user-001', email: 'test@example.com', name: 'Test Pilot', display_name: 'Test Pilot',
  is_admin: false, approved: true, setup_completed: true, synoptic_forecast_map_enabled: true,
};

const MODELS = { ecmwf: {}, gfs: {}, icon: {} };
const AIRPORT = {
  icao: 'ZZAA', lat: 49.0, lon: 2.5, models: MODELS, consensus: null, consensus_majority: null,
};

const DAYS = {
  max_day: 2,
  days: [0, 1, 2].map((day) => ({
    day, date: '', available: true, hours: [6, 9, 12, 15, 18], models: ['ecmwf', 'gfs', 'icon'],
  })),
};

function snapshot(init: string, unix: number) {
  return {
    init_time: init, init_time_unix: unix, levels: [925, 850, 700], stride_hours: 3,
    valid_times: [], n_hours: 9, lat_min: 40, lat_max: 55, lon_min: -5, lon_max: 15, n_lat: 2, n_lon: 2,
  };
}
const SYN_OLD = '2026-10-16T00:00:00Z';
const SYN_NEW = '2026-10-16T12:00:00Z';
const HEWSON_MANIFEST = {
  models: {
    ecmwf: [snapshot(SYN_OLD, 1792108800), snapshot(SYN_NEW, 1792152000)],
    gfs: [snapshot(SYN_OLD, 1792108800), snapshot(SYN_NEW, 1792152000)],
  },
};
const GRID = { lat: [45, 50], lon: [0, 5] };
const values = [[1, 2], [3, 4]];

function monthsAgo(n: number): string {
  const now = new Date();
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - n, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

async function mockApis(page: Page): Promise<void> {
  // Stub the OS share sheet: the test reads exactly the URL the page shares.
  await page.addInitScript(() => {
    (navigator as unknown as { share: (d: { url: string }) => Promise<void> }).share =
      async (d) => { (window as unknown as { __shared: string }).__shared = d.url; };
  });
  await page.route(/tile\.openstreetmap\.org|stadiamaps\.com/, (r) => r.abort());
  await page.route('**/auth/me', (r) => r.fulfill({ json: MOCK_USER }));
  await page.route('**/api/user/preferences', (r) => r.fulfill({ json: { pirep_enabled: false } }));
  await page.route('**/api/messages/status', (r) => r.fulfill({ json: { unseen_count: 0 } }));
  await page.route('**/api/maps/forecast/days*', (r) => r.fulfill({ json: DAYS }));
  await page.route('**/api/maps/forecast?*', (r) => r.fulfill({ json: {
    forecast_time: '2026-10-17T12:00:00+00:00', model_init_times: {}, airports: [],
  } }));
  await page.route('**/api/maps/now', (r) => r.fulfill({ json: {
    at: new Date().toISOString(),
    sources: { metar: { available: true, count: 1, max_age_min: 90 } },
    airports: [{ ...AIRPORT, models: {}, observed: { metar: {
      flight_category: 'VFR', observation_time: new Date().toISOString(), age_min: 5, report_type: 'METAR',
      raw: 'METAR ZZAA 171200Z 27010KT 9999 FEW030 12/08 Q1015', weather: [],
    } } }],
  } }));
  await page.route('**/api/observed/**', (r) => r.fulfill({ status: 404, json: { detail: 'off' } }));
  await page.route('**/api/maps/historical/range', (r) => r.fulfill({ json: {
    latest: '2026-10-17T12:00:00Z', earliest_observation: '2026-10-01T00:00:00Z',
    earliest_model: '2026-10-01T00:00:00Z', step_minutes: 30, model_sample_hours: [6, 9, 12, 15, 18],
    metar_max_age_min: 90, model_max_age_min: 180, models: ['ecmwf', 'gfs', 'icon'],
    observed_sources: ['metar', 'taf'], leads: [],
  } }));
  await page.route('**/api/maps/historical?*', (r) => r.fulfill({ json: {
    at: '2026-10-17T12:00:00Z', lead_days: 0, run_cutoff: '2026-10-17T12:00:00Z',
    sources: { metar: { available: true }, taf: { available: true }, ecmwf: { available: true },
      gfs: { available: true }, icon: { available: true } },
    airports: [],
  } }));
  await page.route('**/api/hewson-map/manifest', (r) => r.fulfill({ json: HEWSON_MANIFEST }));
  await page.route('**/api/hewson-map/all-metrics?*', (r) => {
    const q = new URL(r.request().url()).searchParams;
    return r.fulfill({ json: {
      model: q.get('model'), init_time: q.get('init'), valid_time: q.get('init'), level: Number(q.get('level')),
      hour: Number(q.get('hour')), stride_hours: 3, ...GRID, missing_metrics: [],
      metrics: { theta_e: values, gradient: values, neg_laplacian: values, tfp: values, advection: values, tendency: values },
    } });
  });
  await page.route('**/api/hewson-map/fronts?*', (r) => r.fulfill({ json: { fronts: [] } }));
  await page.route('**/api/hewson-map?*', (r) => {
    const q = new URL(r.request().url()).searchParams;
    return r.fulfill({ json: {
      model: q.get('model'), init_time: q.get('init'), valid_time: q.get('init'), level: Number(q.get('level')),
      metric: q.get('metric'), hour: Number(q.get('hour')), stride_hours: 3, ...GRID, values,
    } });
  });
  await page.route('**/api/synoptic-charts/manifest', (r) => r.fulfill({ json: { sources: [] } }));
  await page.route('**/api/climatology/leaderboard?*', (r) => r.fulfill({ json: {
    month: monthsAgo(2), is_mtd: false, dataset: 'phenomena', sub: 'fog', unit: '%', label: 'Fog',
    min_n_obs: 10, rows: [],
  } }));
  await page.route('**/api/climatology/**', (r) => r.fulfill({ json: {
    month: monthsAgo(2), is_mtd: false, dataset: 'phenomena', metric: 'fog', unit: '%', airports: [],
  } }));
}

/** Click Share view and return the URL the page handed to the share sheet. */
async function share(page: Page): Promise<string> {
  await page.locator('#share-link-btn').click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __shared?: string }).__shared))
    .toBeTruthy();
  return page.evaluate(() => (window as unknown as { __shared: string }).__shared);
}

/** Open a URL in a fresh context (no localStorage, no cookies) with the mocks. */
async function openFresh(browser: Browser, url: string): Promise<Page> {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await mockApis(page);
  await page.goto(url);
  return page;
}

function param(url: string, key: string): string | null {
  return new URL(url).searchParams.get(key);
}

/** Drag the map a little, then zoom in: a view no tab opens on by default.
 *  Zoom first, so the view key can't settle on the drag alone. */
async function moveMap(page: Page, container: string): Promise<void> {
  await page.locator(`${container} .leaflet-control-zoom-in`).click();
  await page.waitForTimeout(400);
  const box = (await page.locator(container).boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 - 120, box.y + box.height / 2 + 60, { steps: 5 });
  // Come to rest before releasing, so Leaflet's inertia doesn't carry the
  // map on after the test has read the view.
  await page.waitForTimeout(300);
  await page.mouse.up();
}

/** The view key settles after Leaflet's zoom animation and the debounce. */
async function settledView(page: Page, key: string): Promise<string> {
  let last = '';
  await expect.poll(async () => {
    const v = param(page.url(), key) ?? '';
    const stable = v !== '' && v === last;
    last = v;
    return stable;
  }, { intervals: [400] }).toBe(true);
  return last;
}

/** On the recipient's side: one zoom-in keeps the centre and adds a level. */
async function expectSameView(page: Page, container: string, key: string, sent: string): Promise<void> {
  const [lat, lon, zoom] = sent.split(',');
  await page.locator(`${container} .leaflet-control-zoom-in`).click();
  await expect.poll(() => param(page.url(), key)).toBe(`${lat},${lon},${Number(zoom) + 1}`);
}

test.describe('Maps page share links', () => {
  test.beforeEach(async ({ page }) => {
    await mockApis(page);
  });

  // One test per tab so the 800 ms "nothing rewrote it" waits run in parallel.
  for (const path of ['/maps.html', '/maps.html?tab=now', '/maps.html?tab=synoptic',
    '/maps.html?tab=historical', '/maps.html?tab=climatology']) {
    test(`a bare URL stays bare until the user changes something: ${path}`, async ({ page }) => {
      await page.goto(path);
      await expect(page.locator('#tabs .tab-btn.active')).toBeVisible();
      await page.waitForTimeout(800);
      expect(page.url()).toBe(new URL(path, page.url()).href);
    });
  }

  test('Forecast: controls and map position', async ({ page, browser }) => {
    await page.goto('/maps.html');
    await page.locator('#day-picker button[data-day="1"]').click();
    await page.locator('#model-picker button[data-model="gfs"]').click();
    await moveMap(page, '#map-container');
    const view = await settledView(page, 'fc.view');
    const url = await share(page);
    expect(param(url, 'fc.view')).toBe(view);

    const other = await openFresh(browser, url);
    await expect(other.locator('#day-picker button[data-day="1"]')).toHaveClass(/active/);
    await expect(other.locator('#model-picker button[data-model="gfs"]')).toHaveClass(/active/);
    await expectSameView(other, '#map-container', 'fc.view', view);
  });

  test('Now: the sender\'s layers beat the viewer\'s saved ones, panel and position', async ({ page, browser }) => {
    await page.goto('/maps.html?tab=now');
    await page.locator('#controls-now input[data-layer="satellite"]').uncheck();
    await page.locator('#controls-now input[data-layer="rain"]').check();
    await page.locator('#map-container-now .leaflet-overlay-pane path').first().click({ force: true });
    await expect(page.locator('#now-panel-host')).toContainText('METAR ZZAA');
    await moveMap(page, '#map-container-now');
    const view = await settledView(page, 'now.view');
    const url = await share(page);
    expect(param(url, 'now.layers')).toBe('airports,radar,lightning,cells,rain');
    expect(param(url, 'now.apt')).toBe('ZZAA');

    // A viewer whose saved set differs (radar off, satellite on).
    const ctx = await browser.newContext();
    const other = await ctx.newPage();
    await mockApis(other);
    await other.addInitScript(() => {
      if (!sessionStorage.getItem('seeded')) {
        sessionStorage.setItem('seeded', '1');
        localStorage.setItem('wb.now.layers', JSON.stringify({ radar: false, satellite: true, lightning: false }));
      }
    });
    await other.goto(url);
    const layer = (k: string) => other.locator(`#controls-now input[data-layer="${k}"]`);
    await expect(layer('radar')).toBeChecked();
    await expect(layer('satellite')).not.toBeChecked();
    await expect(layer('lightning')).toBeChecked();
    await expect(layer('rain')).toBeChecked();
    await expect(other.locator('#now-panel-host')).toContainText('METAR ZZAA');
    // Their saved set is untouched until they toggle something.
    expect(await other.evaluate(() => localStorage.getItem('wb.now.layers')))
      .toBe(JSON.stringify({ radar: false, satellite: true, lightning: false }));
    await expectSameView(other, '#map-container-now', 'now.view', view);
  });

  test('Synoptic: every control, the pinned run and position', async ({ page, browser }) => {
    await page.goto('/maps.html?tab=synoptic');
    await expect(page.locator('#syn-model-picker button[data-model="gfs"]')).toBeVisible();
    await page.locator('#syn-model-picker button[data-model="gfs"]').click();
    await page.locator('#syn-init-picker').selectOption(SYN_OLD);
    await page.locator('#syn-level-picker button[data-level="700"]').click();
    await page.locator('#syn-metric-picker').selectOption('gradient');
    await page.locator('#syn-hour-slider').fill('6');
    await page.locator('#syn-opacity-slider').fill('80');
    await page.locator('#syn-scale-picker button[data-scale="storm"]').click();
    await page.locator('#syn-fronts-picker').selectOption('strict');
    await moveMap(page, '#map-container-synoptic');
    const view = await settledView(page, 'syn.view');
    const url = await share(page);
    expect(Object.fromEntries([...new URL(url).searchParams].filter(([k]) => k.startsWith('syn.')))).toEqual({
      'syn.model': 'gfs', 'syn.init': SYN_OLD, 'syn.level': '700', 'syn.metric': 'gradient',
      'syn.hour': '6', 'syn.opacity': '80', 'syn.scale': 'storm', 'syn.fronts': 'strict', 'syn.view': view,
    });

    const other = await openFresh(browser, url);
    await expect(other.locator('#syn-model-picker button[data-model="gfs"]')).toHaveClass(/active/);
    await expect(other.locator('#syn-init-picker')).toHaveValue(SYN_OLD);
    await expect(other.locator('#syn-level-picker button[data-level="700"]')).toHaveClass(/active/);
    await expect(other.locator('#syn-metric-picker')).toHaveValue('gradient');
    await expect(other.locator('#syn-hour-slider')).toHaveValue('6');
    await expect(other.locator('#syn-opacity-slider')).toHaveValue('80');
    await expect(other.locator('#syn-scale-picker button[data-scale="storm"]')).toHaveClass(/active/);
    await expect(other.locator('#syn-fronts-picker')).toHaveValue('strict');
    await expectSameView(other, '#map-container-synoptic', 'syn.view', view);
  });

  test('Synoptic: a run no longer in the manifest falls back to the newest, keeping the rest', async ({ page }) => {
    await page.goto('/maps.html?tab=synoptic&syn.init=2020-01-01T00:00:00Z&syn.level=700&syn.view=garbage');
    await expect(page.locator('#syn-init-picker')).toHaveValue(SYN_NEW);
    await expect(page.locator('#syn-level-picker button[data-level="700"]')).toHaveClass(/active/);
    await expect.poll(() => param(page.url(), 'syn.init')).toBeNull();
    expect(param(page.url(), 'syn.level')).toBe('700');
  });

  test('Historical: position joins the existing keys', async ({ page, browser }) => {
    await page.goto('/maps.html?tab=historical');
    await expect(page.locator('#hist-source button[data-source="ecmwf"]')).toBeVisible();
    await page.locator('#hist-source button[data-source="ecmwf"]').click();
    await page.locator('#hist-metric').selectOption('ceiling_ft');
    await moveMap(page, '#map-container-historical');
    const view = await settledView(page, 'hist.view');
    const url = await share(page);

    const other = await openFresh(browser, url);
    await expect(other.locator('#hist-source button[data-source="ecmwf"]')).toHaveClass(/active/);
    await expect(other.locator('#hist-metric')).toHaveValue('ceiling_ft');
    await expectSameView(other, '#map-container-historical', 'hist.view', view);
  });

  test('Climatology: sub-tab, month, dataset, sub-selection and position', async ({ page, browser }) => {
    await page.goto('/maps.html?tab=climatology');
    await expect(page.locator('#clim-month-picker option')).toHaveCount(13);
    await page.locator('#clim-month-picker').selectOption(monthsAgo(2));
    await page.locator('#clim-dataset-picker').selectOption('phenomena');
    await page.locator('#clim-metric-picker button').nth(1).click();
    const sub = await page.locator('#clim-metric-picker button.active').getAttribute('data-sub');
    await moveMap(page, '#clim-map-container');
    const view = await settledView(page, 'clim.view');
    await page.locator('#clim-subtabs button[data-subtab="top"]').click();
    const url = await share(page);
    expect(param(url, 'clim.sub')).toBe('top');
    expect(param(url, 'clim.cat')).toBe(sub);

    const other = await openFresh(browser, url);
    await expect(other.locator('#clim-subtabs button[data-subtab="top"]')).toHaveClass(/active/);
    await expect(other.locator('#clim-month-picker')).toHaveValue(monthsAgo(2));
    await expect(other.locator('#clim-dataset-picker')).toHaveValue('phenomena');
    await expect(other.locator(`#clim-metric-picker button[data-sub="${sub}"]`)).toHaveClass(/active/);
    await other.locator('#clim-subtabs button[data-subtab="map"]').click();
    await expectSameView(other, '#clim-map-container', 'clim.view', view);
  });

  test('Climatology: an out-of-range month falls back to the current one', async ({ page }) => {
    await page.goto('/maps.html?tab=climatology&clim.month=1999-01&clim.dataset=phenomena');
    await expect(page.locator('#clim-month-picker')).toHaveValue(monthsAgo(0));
    await expect(page.locator('#clim-dataset-picker')).toHaveValue('phenomena');
  });

  test('switching tabs keeps the other tabs\' params', async ({ page }) => {
    await page.goto('/maps.html?tab=now');
    await page.locator('#controls-now input[data-layer="satellite"]').uncheck();
    await moveMap(page, '#map-container-now');
    const view = await settledView(page, 'now.view');
    await page.locator('.tab-btn[data-tab="forecast"]').click();
    await page.locator('#model-picker button[data-model="icon"]').click();
    await page.locator('.tab-btn[data-tab="now"]').click();
    expect(param(page.url(), 'tab')).toBe('now');
    expect(param(page.url(), 'now.view')).toBe(view);
    expect(param(page.url(), 'now.layers')).toBe('airports,radar,lightning,cells');
    expect(param(page.url(), 'fc.model')).toBe('icon');
  });
});
