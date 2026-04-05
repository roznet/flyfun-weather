import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FIXTURES = join(__dirname, 'fixtures', 'egtf_eglf');
const FUTURE_DATE = new Date(Date.now() + 3 * 86400_000).toISOString().slice(0, 10);
const FLIGHT_ID = `egtf_eglf-${FUTURE_DATE}-45ed`;
const TIMESTAMP = '2026-02-25T16:10:07.255073+00:00';

function fixture(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

/**
 * Intercept all API calls the briefing page makes and return fixture data.
 * This avoids hitting the real backend / external services.
 */
async function mockBriefingApi(page: import('@playwright/test').Page) {
  const enc = encodeURIComponent;

  // GET /api/flights/{id}
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
    route.fulfill({ json: fixture('snapshot.json') })
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
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/freshness`, route =>
    route.fulfill({ json: { is_fresh: true, reason: 'fixture data' } })
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Briefing page', () => {

  test.beforeEach(async ({ page }) => {
    await mockBriefingApi(page);
  });

  test('loads and displays the briefing', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    // Assessment badge should show GREEN
    await expect(page.getByText('GREEN', { exact: true })).toBeVisible();

    // Route summary should appear
    await expect(page.getByText('EGTF → EGLF')).toBeVisible();
  });

  test('displays digest sections', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    // The digest contains a synoptic overview and other sections
    await expect(page.getByText('Synopsis', { exact: false })).toBeVisible();
  });

  test('shows advisories', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    // Advisories section heading should render
    await expect(page.getByRole('heading', { name: 'Route Advisories' })).toBeVisible();
  });

  test('handles missing GRAMET gracefully', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    // Should show an autorouter prompt or "not available" message
    // rather than a broken image
    await expect(page.locator('img[src*="gramet"]')).toHaveCount(0);
  });
});
