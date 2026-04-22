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

async function mockBriefingApi(page: Page, flightPayload: Record<string, unknown>) {
  await page.route('**/auth/me', (route: Route) => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/profiles', (route: Route) => route.fulfill({ json: [] }));
  await page.route('**/api/user/aircraft', (route: Route) => route.fulfill({ json: [] }));
  await page.route('**/api/refresh/active', (route: Route) => route.fulfill({ json: [] }));
  await page.route('**/api/refresh/stats', (route: Route) => route.fulfill({ json: { avg_refresh_seconds: 60 } }));
  await page.route(`**/api/flights/${FLIGHT_ID}`, (route: Route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: flightPayload });
    return route.fallback();
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, (route: Route) => route.fulfill({ json: [] }));
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/latest`, (route: Route) => route.fulfill({ status: 404, body: '' }));
  await page.route(`**/api/flights/${FLIGHT_ID}/freshness**`, (route: Route) => route.fulfill({
    json: { fresh: true, stale_models: [], model_init_times: {}, next_expected_update: null, next_expected_model: null },
  }));
}

test('owner sees share icon enabled; subscribe control and shared-by line hidden', async ({ page }) => {
  await mockBriefingApi(page, flight('owner'));
  const errors: string[] = [];
  page.on('pageerror', err => errors.push(String(err)));

  await page.goto(`http://localhost:8000/briefing.html?flight=${encodeURIComponent(FLIGHT_ID)}`);

  await expect(page.locator('#share-btn')).toBeVisible();
  await expect(page.locator('#share-btn')).toBeEnabled();
  await expect(page.locator('#briefing-subscribe-control')).toBeHidden();
  await expect(page.locator('#briefing-shared-by')).toBeHidden();
  // Refresh stays available for owners.
  await expect(page.locator('#refresh-btn')).toBeVisible();
  expect(errors).toEqual([]);
});

test('owner sees share icon disabled on private flights', async ({ page }) => {
  await mockBriefingApi(page, flight('owner', { private: true }));
  const errors: string[] = [];
  page.on('pageerror', err => errors.push(String(err)));

  await page.goto(`http://localhost:8000/briefing.html?flight=${encodeURIComponent(FLIGHT_ID)}`);

  await expect(page.locator('#share-btn')).toBeVisible();
  await expect(page.locator('#share-btn')).toBeDisabled();
  expect(errors).toEqual([]);
});

test('non-owner sees Subscribe + Shared-by line; refresh and share hidden', async ({ page }) => {
  await mockBriefingApi(page, flight('subscriber', { is_subscribed: false }));
  const errors: string[] = [];
  page.on('pageerror', err => errors.push(String(err)));

  await page.goto(`http://localhost:8000/briefing.html?flight=${encodeURIComponent(FLIGHT_ID)}`);

  await expect(page.locator('#briefing-shared-by')).toBeVisible();
  await expect(page.locator('#briefing-shared-by')).toContainText('Flight Owner');
  await expect(page.locator('#briefing-subscribe-control .btn-subscribe-briefing')).toBeVisible();
  await expect(page.locator('#briefing-subscribe-control .btn-subscribe-briefing')).toHaveText(/Subscribe/i);
  await expect(page.locator('#refresh-btn')).toBeHidden();
  await expect(page.locator('#share-btn')).toBeHidden();
  expect(errors).toEqual([]);
});

test('subscribed non-owner can toggle Unsubscribe -> Subscribe -> Unsubscribe', async ({ page }) => {
  let current = flight('subscriber', { is_subscribed: true });
  await page.route('**/auth/me', (route: Route) => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/profiles', (route: Route) => route.fulfill({ json: [] }));
  await page.route('**/api/user/aircraft', (route: Route) => route.fulfill({ json: [] }));
  await page.route('**/api/refresh/active', (route: Route) => route.fulfill({ json: [] }));
  await page.route('**/api/refresh/stats', (route: Route) => route.fulfill({ json: { avg_refresh_seconds: 60 } }));
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
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/latest`, (route: Route) => route.fulfill({ status: 404, body: '' }));
  await page.route(`**/api/flights/${FLIGHT_ID}/freshness**`, (route: Route) => route.fulfill({
    json: { fresh: true, stale_models: [], model_init_times: {}, next_expected_update: null, next_expected_model: null },
  }));

  const errors: string[] = [];
  page.on('pageerror', err => errors.push(String(err)));

  await page.goto(`http://localhost:8000/briefing.html?flight=${encodeURIComponent(FLIGHT_ID)}`);

  const btn = page.locator('#briefing-subscribe-control button');
  await expect(btn).toHaveText(/Unsubscribe/i);

  page.on('dialog', dialog => dialog.accept());
  await btn.click();
  await expect(btn).toHaveText(/Subscribe/i);

  await btn.click();
  await expect(btn).toHaveText(/Unsubscribe/i);

  expect(errors).toEqual([]);
});
