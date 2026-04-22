import { test, expect, type Page, type Route } from '@playwright/test';

const FUTURE_DATE = new Date(Date.now() + 5 * 86400_000).toISOString().slice(0, 10);
const FLIGHT_ID = `egtf_eglf-${FUTURE_DATE}-aaaa`;

/** Build a flight payload. `role` drives non-owner vs owner behavior. */
function flight(role: 'owner' | 'subscriber', overrides: Record<string, unknown> = {}) {
  return {
    id: FLIGHT_ID,
    user_id: role === 'owner' ? 'u1' : 'owner-user',
    profile_id: null,
    aircraft_id: null,
    aircraft: null,
    route_name: 'egtf_eglf',
    waypoints: ['EGTF', 'EGLF'],
    departure_time: `${FUTURE_DATE}T09:00:00+00:00`,
    alt_departure_time: null,
    target_date: FUTURE_DATE,
    target_time_utc: 9,
    cruise_altitude_ft: 7000,
    flight_ceiling_ft: 15000,
    flight_duration_hours: 1.0,
    private: false,
    auto_refresh: false,
    auto_refresh_hour: null,
    created_at: '2026-04-20T10:00:00+00:00',
    role,
    owner_display_name: role === 'subscriber' ? 'Flight Owner' : null,
    is_subscribed: role === 'subscriber',
    ...overrides,
  };
}

async function mockBaseAuth(page: Page) {
  await page.route('**/auth/me', (route: Route) => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/profiles', (route: Route) => route.fulfill({ json: [] }));
  await page.route('**/api/user/aircraft', (route: Route) => route.fulfill({ json: [] }));
  await page.route('**/api/refresh/active', (route: Route) => route.fulfill({ json: [] }));
  await page.route('**/api/flights/route-distance', (route: Route) => route.fulfill({
    json: { total_distance_nm: 20, waypoints: [
      { icao: 'EGTF', name: 'Fairoaks', lat: 51.3, lon: -0.55, timezone: 'Europe/London' },
      { icao: 'EGLF', name: 'Farnborough', lat: 51.28, lon: -0.77, timezone: 'Europe/London' },
    ] },
  }));
}

test('owner sees Edit + Copy share link, no Subscribe', async ({ page }) => {
  const payload = flight('owner');
  await mockBaseAuth(page);
  await page.route(`**/api/flights/${FLIGHT_ID}`, (route: Route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: payload });
    return route.fallback();
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, (route: Route) => route.fulfill({ json: [] }));

  const errors: string[] = [];
  page.on('pageerror', err => errors.push(String(err)));

  await page.goto(`http://localhost:8000/flight.html?id=${encodeURIComponent(FLIGHT_ID)}`);

  await expect(page.locator('#btn-edit-flight')).toBeVisible();
  await expect(page.locator('.btn-copy-share-link')).toBeVisible();
  await expect(page.locator('.btn-subscribe-flight')).toHaveCount(0);
  await expect(page.locator('.btn-unsubscribe-flight')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('non-owner sees Shared banner, Subscribe, no Edit', async ({ page }) => {
  const payload = flight('subscriber', { is_subscribed: false });
  await mockBaseAuth(page);
  await page.route(`**/api/flights/${FLIGHT_ID}`, (route: Route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: payload });
    return route.fallback();
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, (route: Route) => route.fulfill({ json: [] }));

  const errors: string[] = [];
  page.on('pageerror', err => errors.push(String(err)));

  await page.goto(`http://localhost:8000/flight.html?id=${encodeURIComponent(FLIGHT_ID)}`);

  await expect(page.locator('.sharing-owner-label')).toContainText('Flight Owner');
  await expect(page.locator('.btn-subscribe-flight')).toBeVisible();
  await expect(page.locator('#btn-edit-flight')).toHaveCount(0);
  await expect(page.locator('.btn-copy-share-link')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('subscriber sees Unsubscribe and can toggle to subscribed state', async ({ page }) => {
  let current = flight('subscriber', { is_subscribed: true });
  await mockBaseAuth(page);
  await page.route(`**/api/flights/${FLIGHT_ID}`, (route: Route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: current });
    return route.fallback();
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/subscribe`, (route: Route) => {
    const method = route.request().method();
    if (method === 'DELETE') {
      current = { ...current, is_subscribed: false };
      return route.fulfill({ status: 204, body: '' });
    }
    if (method === 'POST') {
      current = { ...current, is_subscribed: true };
      return route.fulfill({ json: { flight_id: FLIGHT_ID, user_id: 'u1', created: true } });
    }
    return route.fallback();
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, (route: Route) => route.fulfill({ json: [] }));

  const errors: string[] = [];
  page.on('pageerror', err => errors.push(String(err)));

  await page.goto(`http://localhost:8000/flight.html?id=${encodeURIComponent(FLIGHT_ID)}`);

  await expect(page.locator('.btn-unsubscribe-flight')).toBeVisible();

  // Accept the confirm dialog, then expect the button to swap to Subscribe.
  page.on('dialog', dialog => dialog.accept());
  await page.locator('.btn-unsubscribe-flight').click();
  await expect(page.locator('.btn-subscribe-flight')).toBeVisible();

  // Resubscribe
  await page.locator('.btn-subscribe-flight').click();
  await expect(page.locator('.btn-unsubscribe-flight')).toBeVisible();

  expect(errors).toEqual([]);
});
