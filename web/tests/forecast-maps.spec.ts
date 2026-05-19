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

const MOCK_HOURS = { day: 0, date: '2026-04-06', hours: [6, 9, 12, 15, 18] };

// ---------------------------------------------------------------------------
// Route mocking
// ---------------------------------------------------------------------------

async function mockApis(page: import('@playwright/test').Page) {
  await page.route('**/auth/me', route => route.fulfill({ json: MOCK_USER }));
  await page.route('**/api/maps/forecast/hours*', route => route.fulfill({ json: MOCK_HOURS }));
  await page.route('**/api/maps/forecast*', route => {
    // Don't match /forecast/hours (already handled above)
    if (route.request().url().includes('/hours')) return route.fallthrough();
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
