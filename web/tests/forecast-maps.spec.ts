import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_USER = {
  id: 'test-user-001',
  email: 'test@example.com',
  name: 'Test Pilot',
  display_name: 'Test Pilot',
  is_admin: true,
  approved: true,
  setup_completed: true,
  synoptic_forecast_map_enabled: true,
};

const MOCK_FORECAST: Record<string, unknown> = {
  forecast_time: '2026-04-06T12:00:00+00:00',
  model_init_times: {
    gfs: '2026-04-06T00:00:00+00:00',
    icon: '2026-04-06T00:00:00+00:00',
    ecmwf: '2026-04-05T12:00:00+00:00',
  },
  airports: [
    {
      icao: 'LFPG', lat: 49.01, lon: 2.55,
      models: {
        gfs: { ceiling_ft: 5000, visibility_m: 9999, wind_speed_kt: 12, wind_dir_deg: 230, wind_gust_kt: 18, cloud_cover_pct: 30, cape_jkg: 50, convective_risk: 'none', temperature_c: 14, flight_category: 'VFR' },
        icon: { ceiling_ft: 4500, visibility_m: 9999, wind_speed_kt: 14, wind_dir_deg: 220, wind_gust_kt: 20, cloud_cover_pct: 40, cape_jkg: 60, convective_risk: 'none', temperature_c: 13, flight_category: 'VFR' },
        ecmwf: { ceiling_ft: 800, visibility_m: 5000, wind_speed_kt: 10, wind_dir_deg: 240, wind_gust_kt: 15, cloud_cover_pct: 80, cape_jkg: 30, convective_risk: 'none', temperature_c: 12, flight_category: 'IFR' },
      },
      consensus: { flight_category: 'IFR', agreement: { flight_category: 'mixed', wind_speed_kt: 'consistent', ceiling_ft: 'divergent', cape_jkg: 'consistent' }, wind_speed_kt: 12, wind_dir_deg: 230, ceiling_ft: 3433, cape_jkg: 47 },
    },
    {
      icao: 'EDDF', lat: 50.03, lon: 8.57,
      models: {
        gfs: { ceiling_ft: 6000, visibility_m: 9999, wind_speed_kt: 8, wind_dir_deg: 180, wind_gust_kt: null, cloud_cover_pct: 10, cape_jkg: 0, convective_risk: 'none', temperature_c: 16, flight_category: 'VFR' },
        icon: { ceiling_ft: 5500, visibility_m: 9999, wind_speed_kt: 9, wind_dir_deg: 190, wind_gust_kt: null, cloud_cover_pct: 15, cape_jkg: 0, convective_risk: 'none', temperature_c: 15, flight_category: 'VFR' },
        ecmwf: { ceiling_ft: 5800, visibility_m: 9999, wind_speed_kt: 7, wind_dir_deg: 185, wind_gust_kt: null, cloud_cover_pct: 12, cape_jkg: 10, convective_risk: 'none', temperature_c: 15, flight_category: 'VFR' },
      },
      consensus: { flight_category: 'VFR', agreement: { flight_category: 'consistent', wind_speed_kt: 'consistent', ceiling_ft: 'consistent', cape_jkg: 'consistent' }, wind_speed_kt: 8, wind_dir_deg: 185, ceiling_ft: 5767, cape_jkg: 3 },
    },
  ],
};

// The day/hour/model grid the page now renders its pickers from. Not
// rectangular: the far day carries fewer models and fewer hours, mirroring the
// real horizons (ICON's ceiling GRIB stops at 120h; ECMWF goes 6-hourly past
// 144h). See tasks/forecast_grid.py.
const MOCK_DAYS = {
  max_day: 6,
  days: [
    { day: 0, date: '2026-04-06', available: true,  hours: [6, 9, 12, 15, 18], models: ['ecmwf', 'gfs', 'icon'] },
    { day: 1, date: '2026-04-07', available: true,  hours: [6, 9, 12, 15, 18], models: ['ecmwf', 'gfs', 'icon'] },
    { day: 2, date: '2026-04-08', available: true,  hours: [6, 9, 12, 15, 18], models: ['ecmwf', 'gfs', 'icon'] },
    { day: 3, date: '2026-04-09', available: true,  hours: [6, 9, 12, 15, 18], models: ['ecmwf', 'gfs', 'icon'] },
    { day: 4, date: '2026-04-10', available: true,  hours: [6, 9, 12, 15, 18], models: ['ecmwf', 'gfs', 'icon'] },
    { day: 5, date: '2026-04-11', available: true,  hours: [6, 9, 12, 15, 18], models: ['ecmwf', 'gfs'] },
    { day: 6, date: '2026-04-12', available: true,  hours: [6, 12, 18],        models: ['ecmwf', 'gfs'] },
  ],
};

// ---------------------------------------------------------------------------
// Route mocking
// ---------------------------------------------------------------------------

async function mockApis(page: import('@playwright/test').Page) {
  await page.route('**/auth/me', route => route.fulfill({ json: MOCK_USER }));
  await page.route('**/api/maps/forecast/days*', route => route.fulfill({ json: MOCK_DAYS }));
  await page.route('**/api/maps/forecast*', route => {
    // The generic handler must not swallow /forecast/days — that response has a
    // different shape, and the page builds its day and hour pickers from it.
    if (route.request().url().includes('/days')) return route.fallthrough();
    return route.fulfill({ json: MOCK_FORECAST });
  });
  // Preferences (for nav rendering)
  await page.route('**/api/user/preferences', route => route.fulfill({ json: { pirep_enabled: false } }));
  // Messages badge
  await page.route('**/api/messages/status', route => route.fulfill({ json: { unseen_count: 0 } }));
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Forecast page', () => {
  test.beforeEach(async ({ page }) => {
    await mockApis(page);
  });

  test('loads with forecast tab active and shows airports', async ({ page }) => {
    await page.goto('/maps.html');
    // Page title
    await expect(page.locator('.page-header h2')).toHaveText('Forecast');
    // Forecast tab is active
    await expect(page.locator('.tab-btn[data-tab="forecast"]')).toHaveClass(/active/);
    // Forecast controls visible
    await expect(page.locator('#controls-forecast')).toBeVisible();
    // Leaflet map initialized (has the leaflet-container class)
    await expect(page.locator('#map-container.leaflet-container')).toBeVisible();
    // Info bar shows airport count
    await expect(page.locator('#map-info')).toContainText('2 airports');
  });

  test('shows forecast datetime label', async ({ page }) => {
    await page.goto('/maps.html');
    const dt = page.locator('#forecast-datetime');
    await expect(dt).toBeVisible();
    // Should contain a Z suffix (UTC hour)
    await expect(dt).toContainText(/\d{2}Z/);
  });

  test('experimental banner toggle shows detail', async ({ page }) => {
    await page.goto('/maps.html');
    // Banner is now scoped to the Synoptic Forecast tab only
    await page.click('.tab-btn[data-tab="synoptic"]');
    const detail = page.locator('#experimental-detail');
    await expect(detail).not.toBeVisible();
    await page.click('#experimental-toggle');
    await expect(detail).toBeVisible();
    await expect(detail).toContainText('experimental');
  });

  test('switching to Accuracy Stats tab shows iframe', async ({ page }) => {
    await page.goto('/maps.html');
    await page.click('.tab-btn[data-tab="stats"]');
    await expect(page.locator('.tab-btn[data-tab="stats"]')).toHaveClass(/active/);
    // Iframe should have src set
    const frame = page.locator('#stats-frame');
    await expect(frame).toBeVisible();
    await expect(frame).toHaveAttribute('src', /verification\.html\?embed/);
  });

  test('switching back to forecast tab preserves map', async ({ page }) => {
    await page.goto('/maps.html');
    // Go to stats tab then back
    await page.click('.tab-btn[data-tab="stats"]');
    await page.click('.tab-btn[data-tab="forecast"]');
    await expect(page.locator('#panel-forecast')).toBeVisible();
    await expect(page.locator('#map-container.leaflet-container')).toBeVisible();
    await expect(page.locator('#map-info')).toContainText('2 airports');
  });

  test('day picker reloads forecast data', async ({ page }) => {
    let requestCount = 0;
    await page.route('**/api/maps/forecast?*', route => {
      requestCount++;
      return route.fulfill({ json: MOCK_FORECAST });
    });

    await page.goto('/maps.html');
    await page.waitForTimeout(500); // let initial load complete
    const initial = requestCount;

    // Click D-1
    await page.click('#day-picker .btn-toggle[data-day="1"]');
    await page.waitForTimeout(500);
    expect(requestCount).toBeGreaterThan(initial);
    // D-1 button is active
    await expect(page.locator('#day-picker .btn-toggle[data-day="1"]')).toHaveClass(/active/);
  });

  test('day picker is rendered from the grid, out to the far day', async ({ page }) => {
    await page.goto('/maps.html');
    // Buttons come from /forecast/days, not from markup — D-0..D-6.
    await expect(page.locator('#day-picker .btn-toggle')).toHaveCount(7);
    await expect(page.locator('#day-picker .btn-toggle[data-day="6"]')).toBeVisible();
  });

  test('far day offers three hours, not five', async ({ page }) => {
    await page.goto('/maps.html');
    // Near day: the full five sample hours.
    await expect(page.locator('#hour-picker .btn-toggle')).toHaveCount(5);

    // D-6: ECMWF only delivers 6-hourly steps out there, so 09Z and 15Z are
    // not offered — they'd be slots no second model could fill.
    await page.click('#day-picker .btn-toggle[data-day="6"]');
    await expect(page.locator('#hour-picker .btn-toggle')).toHaveCount(3);
    await expect(page.locator('#hour-picker .btn-toggle[data-hour="9"]')).toHaveCount(0);
    await expect(page.locator('#hour-picker .btn-toggle[data-hour="15"]')).toHaveCount(0);
  });

  test('a model that cannot reach the selected day explains itself', async ({ page }) => {
    await page.goto('/maps.html');
    const icon = page.locator('#model-picker .btn-toggle[data-model="icon"]');
    await expect(icon).toHaveAttribute('aria-disabled', 'false');

    // ICON's ceiling GRIB stops at 120h, so it has nothing to say at D-6. It
    // must read as absent, never as agreement.
    await page.click('#day-picker .btn-toggle[data-day="6"]');
    await expect(icon).toHaveAttribute('aria-disabled', 'true');
    await expect(page.locator('#model-picker .btn-toggle[data-model="ecmwf"]'))
      .toHaveAttribute('aria-disabled', 'false');

    // Crucially NOT the native `disabled` attribute. Blink and WebKit suppress
    // hover and click on disabled elements, so the button could never say why
    // it is greyed out. (Playwright's own toBeEnabled() counts aria-disabled as
    // disabled, so assert the DOM property directly — that is what browsers
    // actually gate hover and click on.)
    expect(await icon.evaluate((el: HTMLButtonElement) => el.disabled)).toBe(false);

    // So it stays clickable, and clicking explains rather than selects.
    await icon.click({ force: true });
    await expect(page.locator('#map-info')).toContainText('ICON');
    await expect(icon).not.toHaveClass(/active/);
  });

  test('survives a 200 from /forecast/days carrying the wrong shape', async ({ page }) => {
    // Stale cache, a proxy, or version skew can return 200 with a body that has
    // no `days`. That must degrade to the offline fallback, not throw partway
    // through init and leave the page with no pickers and no map.
    await page.route('**/api/maps/forecast/days*', route =>
      route.fulfill({ json: { unexpected: 'shape' } }));

    await page.goto('/maps.html');
    await expect(page.locator('#day-picker .btn-toggle')).toHaveCount(4);  // fallback grid
    await expect(page.locator('#hour-picker .btn-toggle')).toHaveCount(5);
    // Crucially, init continued: the map still loaded.
    await expect(page.locator('#map-info')).toContainText('2 airports');
  });

  test('the airport card links out to Windy at the same place, time and model', async ({ page }) => {
    await page.goto('/maps.html');
    await page.click('#map-container .leaflet-interactive');

    const link = page.locator('.ap-card-link');
    await expect(link).toHaveText(/Open in Windy/);
    // Panel defaults to ECMWF, which has no dedicated Windy view — the URL
    // carries no model segment and Windy lands on its own default.
    let href = await link.getAttribute('href');
    expect(href).toContain('windy.com/49.010/2.550/meteogram');
    expect(href).toContain('2026-04-06-12');  // the selected day + hour, UTC

    // Switch the panel to a model Windy *does* have: the link follows.
    await page.selectOption('.ap-model-sel', 'gfs');
    href = await page.locator('.ap-card-link').getAttribute('href');
    expect(href).toContain('windy.com/49.010/2.550/gfs/meteogram?gfs,2026-04-06-12');
  });

  test('the loading status sits below the card, so it cannot push it around', async ({ page }) => {
    // The status row appears mid-stream (stage label → GRIB badge). Above the
    // body, every appearance shoved the card down under the user's cursor.
    await page.goto('/maps.html');
    await page.click('#map-container .leaflet-interactive');

    const order = await page.locator('.ap-panel').evaluate((panel) =>
      Array.from(panel.children).map((c) => c.className));
    expect(order.indexOf('ap-panel-status')).toBeGreaterThan(order.indexOf('ap-panel-body'));
  });

  test('the airport panel does not offer a model the day has no data for', async ({ page }) => {
    // The panel's surface trace comes from the snapshot table, and ICON's
    // ceiling GRIB stops at 120h — so from D+5 there are no ICON snapshots.
    // Offering ICON would draw a cross-section with an empty surface: absence
    // rendered as if it were a forecast.
    await page.goto('/maps.html');
    await page.click('#day-picker .btn-toggle[data-day="6"]');
    await page.click('#map-container .leaflet-interactive');

    const iconOpt = page.locator('.ap-model-sel option[value="icon"]');
    await expect(iconOpt).toBeDisabled();
    await expect(iconOpt).toContainText('not at this range');
    await expect(page.locator('.ap-model-sel option[value="ecmwf"]')).toBeEnabled();
    // ...and the selection landed on a model that does reach D-6.
    await expect(page.locator('.ap-model-sel')).not.toHaveValue('icon');
  });

  test('model picker switches between consensus and single model', async ({ page }) => {
    await page.goto('/maps.html');
    // Default is "worst"
    await expect(page.locator('#model-picker .btn-toggle[data-model="worst"]')).toHaveClass(/active/);
    // Switch to GFS
    await page.click('#model-picker .btn-toggle[data-model="gfs"]');
    await expect(page.locator('#model-picker .btn-toggle[data-model="gfs"]')).toHaveClass(/active/);
    await expect(page.locator('#model-picker .btn-toggle[data-model="worst"]')).not.toHaveClass(/active/);
  });

  test('nav shows Forecast as current page', async ({ page }) => {
    await page.goto('/maps.html');
    await expect(page.locator('.nav-current')).toHaveText('Forecast');
  });
});
