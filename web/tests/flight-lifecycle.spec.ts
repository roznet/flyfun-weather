import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FIXTURES = join(__dirname, 'fixtures', 'egtf_eglf');
const FLIGHT_ID = 'egtf_eglf-2026-02-25-45ed';
const TIMESTAMP = '2026-02-25T16:10:07.255073+00:00';

function fixture(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

// ---------------------------------------------------------------------------
// Full flight lifecycle test
// ---------------------------------------------------------------------------

test('flight lifecycle: create → view → change setting → recalculate → delete', async ({ page }) => {
  const enc = encodeURIComponent;
  const flightData = fixture('flight.json');
  const packMetaData = fixture('pack_meta.json');
  const advisoriesData = fixture('advisories.json');
  const advisoriesWorstData = fixture('advisories_worst.json');

  // --- Mutable state flags ---
  let flightsCreated = false;
  let useWorstAdvisories = false;

  // --- Mock user auth ---
  await page.route('**/auth/me', route =>
    route.fulfill({
      json: {
        id: 'test-user-001',
        email: 'test@example.com',
        name: 'Test User',
        approved: true,
        is_admin: false,
        setup_completed: true,
      },
    }),
  );

  // --- Mock user profiles ---
  await page.route('**/api/user/profiles', route => {
    if (route.request().method() === 'GET') {
      // Only handle exact path, not sub-paths like /profiles/1
      const url = route.request().url();
      if (/\/profiles\/\d/.test(url)) return route.fallthrough();
      return route.fulfill({
        json: [{
          id: 1,
          name: 'Default',
          is_default: true,
          settings: {
            cruise_altitude_ft: 8000,
            flight_ceiling_ft: 18000,
            speed_kt: null,
            models: ['ecmwf', 'gfs', 'icon', 'ukmo', 'meteofrance'],
            advisory_models: ['ecmwf', 'gfs', 'icon', 'ukmo', 'meteofrance'],
            gramet_enabled: true,
            llm_digest_enabled: true,
            icing_severity_enhance: false,
            icing_method: 'ogimet_dd',
            cloud_method: 'dd',
            convective_method: 'thermo',
            flight_rules: 'vfr_ifr',
            advisories: { enabled: null, params: null, aggregation: 'majority' },
          },
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-01-01T00:00:00',
        }],
      });
    }
    return route.fallthrough();
  });

  // --- Mock PUT /api/user/profiles/1 (save settings) ---
  await page.route('**/api/user/profiles/1', route => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      return route.fulfill({
        json: {
          id: 1,
          name: 'Default',
          is_default: true,
          settings: body.settings ?? {},
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-03-01T00:00:00',
        },
      });
    }
    return route.fallthrough();
  });

  // --- Mock model catalog ---
  await page.route('**/api/models', route =>
    route.fulfill({
      json: [
        { key: 'ecmwf', name: 'ECMWF', default: true },
        { key: 'gfs', name: 'GFS', default: true },
        { key: 'icon', name: 'ICON', default: true },
        { key: 'ukmo', name: 'UKMO', default: true },
        { key: 'meteofrance', name: 'Météo-France', default: true },
      ],
    }),
  );

  // --- Mock user preferences ---
  await page.route('**/api/user/preferences', route => {
    const url = route.request().url();
    // Let sub-paths (like /preferences/advisories/catalog) fall through
    if (url.includes('/preferences/') || url.endsWith('/preferences/')) return route.fallthrough();
    return route.fulfill({
      json: {
        defaults: { cruise_altitude_ft: 8000, flight_ceiling_ft: 18000, models: null, advisory_models: null },
        digest_config: { config_name: null },
        advisories: { enabled: null, params: null, aggregation: 'majority' },
        has_autorouter_creds: false,
        gramet_enabled: true,
        llm_digest_enabled: true,
        icing_severity_enhance: false,
        icing_method: 'ogimet_dd',
        cloud_method: 'dd',
        convective_method: 'thermo',
      },
    });
  });

  // --- Mock advisory catalog ---
  await page.route('**/api/user/preferences/advisories/catalog', route =>
    route.fulfill({ json: advisoriesData.catalog }),
  );

  // --- Mock flights list ---
  await page.route('**/api/flights', route => {
    const url = route.request().url();
    const method = route.request().method();

    // Only match the flights list endpoint, not sub-paths
    if (url.includes(`/flights/${FLIGHT_ID}`) || url.includes('/flights/route-distance'))
      return route.fallthrough();

    if (method === 'POST') {
      flightsCreated = true;
      return route.fulfill({ json: flightData });
    }
    // GET — return empty or with flight depending on state
    return route.fulfill({ json: flightsCreated ? [flightData] : [] });
  });

  // --- Mock route distance ---
  await page.route('**/api/flights/route-distance', route =>
    route.fulfill({ json: { total_distance_nm: 9.2 } }),
  );

  // --- Mock active refreshes ---
  await page.route('**/api/refresh/active', route =>
    route.fulfill({ json: [] }),
  );

  // --- Mock flight detail ---
  await page.route(`**/api/flights/${FLIGHT_ID}`, route => {
    const url = route.request().url();
    const method = route.request().method();
    if (url.includes('/packs')) return route.fallthrough();
    if (method === 'DELETE') {
      flightsCreated = false;
      return route.fulfill({ status: 204, body: '' });
    }
    return route.fulfill({ json: flightData });
  });

  // --- Mock packs list ---
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, route => {
    const url = route.request().url();
    const afterPacks = url.split('/packs')[1];
    if (afterPacks && afterPacks !== '' && afterPacks !== '/')
      return route.fallthrough();
    return route.fulfill({ json: fixture('packs.json') });
  });

  // --- Mock pack metadata ---
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/${enc(TIMESTAMP)}`, route => {
    const url = route.request().url();
    const afterTs = url.split(enc(TIMESTAMP))[1];
    if (afterTs && afterTs !== '' && afterTs !== '/')
      return route.fallthrough();
    return route.fulfill({ json: packMetaData });
  });

  // --- Mock packs/latest ---
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/latest`, route =>
    route.fulfill({ json: packMetaData }),
  );

  // --- Mock snapshot ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/snapshot`, route =>
    route.fulfill({ json: fixture('snapshot.json') }),
  );

  // --- Mock route analyses ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/route-analyses`, route =>
    route.fulfill({ json: fixture('route_analyses.json') }),
  );

  // --- Mock advisories (dynamic) ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories`, route => {
    const url = route.request().url();
    if (url.includes('/recalculate')) return route.fallthrough();
    return route.fulfill({ json: useWorstAdvisories ? advisoriesWorstData : advisoriesData });
  });

  // --- Mock advisories recalculate ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories/recalculate`, route => {
    useWorstAdvisories = true;
    return route.fulfill({ json: advisoriesWorstData });
  });

  // --- Mock elevation ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/elevation`, route =>
    route.fulfill({ json: fixture('elevation.json') }),
  );

  // --- Mock digest ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/digest/json`, route =>
    route.fulfill({ json: fixture('digest.json') }),
  );

  // --- Mock gramet (404) ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/gramet**`, route =>
    route.fulfill({ status: 404, json: { detail: 'GRAMET not available' } }),
  );

  // --- Mock freshness ---
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/freshness`, route =>
    route.fulfill({ json: { is_fresh: true, reason: 'fixture data' } }),
  );

  // --- Mock refresh status ---
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/refresh/status`, route =>
    route.fulfill({ json: { active: false } }),
  );

  // --- Mock usage (non-critical, can 404) ---
  await page.route('**/api/user/usage', route =>
    route.fulfill({ status: 404, json: { detail: 'not available' } }),
  );

  // --- Mock credits (non-critical, can 404) ---
  await page.route('**/api/user/credits**', route =>
    route.fulfill({ status: 404, json: { detail: 'not available' } }),
  );

  // =========================================================================
  // Phase 1: Create a flight on the flights page
  // =========================================================================

  await page.goto('/');

  // Should show empty state
  await expect(page.locator('.empty-state')).toContainText('No flights yet');

  // Fill in the flight creation form
  await page.fill('#input-waypoints', 'EGTF EGLF');
  await page.fill('#input-date', '2026-02-25');
  await page.fill('#input-time', '17');
  await page.fill('#input-altitude', '2000');

  // Submit the form — triggers POST and navigates to briefing
  await page.click('#create-flight-form button[type="submit"]');

  // =========================================================================
  // Phase 2: Verify briefing page shows all-green advisories
  // =========================================================================

  await page.waitForURL(/briefing\.html/);

  // Verify route info is visible
  await expect(page.getByText('EGTF')).toBeVisible();

  // Wait for advisories to render
  await expect(page.locator('#advisories-wrapper')).toBeVisible();

  // Under majority aggregation, all advisories are GREEN
  await expect(page.locator('.advisory-summary .badge-green')).toBeVisible();
  // No red or amber badges should be present
  await expect(page.locator('.advisory-summary .badge-red')).not.toBeVisible();
  await expect(page.locator('.advisory-summary .badge-amber')).not.toBeVisible();

  // =========================================================================
  // Phase 3: Navigate to settings and change aggregation to "worst"
  // =========================================================================

  await page.click('a[href="/settings.html"]');
  await page.waitForURL(/settings\.html/);

  // Change aggregation mode
  await page.selectOption('#advisory-aggregation', 'worst');

  // Submit settings form
  await page.click('#settings-form button[type="submit"]');

  // Verify success message
  await expect(page.locator('#status-message')).toContainText('Settings saved');

  // =========================================================================
  // Phase 4: Return to briefing, recalculate, and verify red/amber appear
  // =========================================================================

  // Navigate back to flights list
  await page.click('a[href="/"]');
  await page.waitForURL(/\/$/);

  // Flight card should be visible now
  await expect(page.locator('.flight-card')).toBeVisible();

  // Click "View Briefing" on the flight card
  await page.click('.btn-view');
  await page.waitForURL(/briefing\.html/);

  // Wait for advisories to load
  await expect(page.locator('#advisories-wrapper')).toBeVisible();

  // Click recalculate
  await page.click('#recalc-advisories-btn');

  // After recalculation with worst mode, turbulence is RED and cloud_top is AMBER
  await expect(page.locator('.advisory-summary .badge-red')).toBeVisible();
  await expect(page.locator('.advisory-summary .badge-amber')).toBeVisible();

  // Verify specific advisory cards
  await expect(page.locator('.advisory-card[data-advisory="turbulence"].advisory-red')).toBeVisible();
  await expect(page.locator('.advisory-card[data-advisory="cloud_top"].advisory-amber')).toBeVisible();

  // =========================================================================
  // Phase 5: Delete the flight
  // =========================================================================

  // Navigate to flights list
  await page.click('a[href="/"]');
  await page.waitForURL(/\/$/);

  // Flight card should exist
  await expect(page.locator('.flight-card')).toBeVisible();

  // Accept the confirm dialog before clicking delete
  page.on('dialog', dialog => dialog.accept());

  // Click delete
  await page.click('.btn-delete');

  // Should return to empty state
  await expect(page.locator('.empty-state')).toContainText('No flights yet');
});
