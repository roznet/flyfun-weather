import { test, expect, Page } from '@playwright/test';

// #625 — "Import from Autorouter" when the account isn't linked offers to link
// it right there (and comes back), rather than sending the pilot to Settings.

async function mockFlightsPage(page: Page, opts: { linked: boolean }) {
  await page.route('**/auth/me', route => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/profiles', route => route.fulfill({ json: [] }));
  await page.route('**/api/user/aircraft', route => route.fulfill({ json: [] }));
  await page.route('**/api/refresh/active', route => route.fulfill({ json: [] }));
  await page.route(/\/api\/flights(\?.*)?$/, route => route.fulfill({ json: [] }));
  await page.route('**/api/user/preferences', route => {
    if (route.request().url().includes('/preferences/')) return route.fallthrough();
    return route.fulfill({ json: { has_autorouter_creds: opts.linked } });
  });
  await page.route('**/api/flights/autorouter-routes*', route => route.fulfill({
    json: {
      routes: [{
        routeid: 'r1', departure: 'EGTF', destination: 'LFAT',
        departure_name: 'Fairoaks', destination_name: 'Le Touquet',
        departure_time: null, route_distance_nm: 120,
        fplan: '(FPL-ZZABC-VG -SR22/L -S/C -EGTF0900 -N0170F090 DCT -LFAT0100)',
        aircraft_description: null, callsign: null,
      }],
    },
  }));
}

test('not linked: the import prompt links Autorouter directly and returns here', async ({ page }) => {
  await mockFlightsPage(page, { linked: false });
  await page.goto('http://localhost:8000/');

  await page.locator('#btn-import-autorouter').click();

  const connect = page.getByRole('link', { name: 'Connect Autorouter' });
  await expect(connect).toBeVisible();
  await expect(connect).toHaveAttribute('href', '/autorouter/link?next=%2F');
  // No detour through the Settings page any more.
  await expect(page.locator('a[href*="settings.html"]', { hasText: 'Open Settings' })).toHaveCount(0);
});

test('back from linking: the picker opens and the flag leaves the URL', async ({ page }) => {
  await mockFlightsPage(page, { linked: true });
  await page.goto('http://localhost:8000/?autorouter=linked');

  await expect(page.getByText('Pick a recent Autorouter route')).toBeVisible();
  await expect(page.getByText('EGTF')).toBeVisible();
  expect(new URL(page.url()).search).toBe('');
});
