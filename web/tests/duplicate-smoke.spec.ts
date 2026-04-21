import { test, expect } from '@playwright/test';

const FUTURE_DATE = new Date(Date.now() + 5 * 86400_000).toISOString().slice(0, 10);
const FLIGHT_ID = `egtk_lsgs-${FUTURE_DATE}-aaaa`;

test('duplicate + route history UI smoke', async ({ page }) => {
  await page.route('**/auth/me', route => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/profiles', route => route.fulfill({ json: [] }));
  await page.route('**/api/user/aircraft', route => route.fulfill({ json: [] }));
  await page.route('**/api/flights', route => route.fulfill({
    json: [
      {
        id: FLIGHT_ID, user_id: 'u1', profile_id: null, aircraft_id: null, aircraft: null,
        route_name: 'egtk_lsgs', waypoints: ['EGTK', 'LFPB', 'LSGS'],
        departure_time: `${FUTURE_DATE}T09:00:00+00:00`, alt_departure_time: null,
        target_date: FUTURE_DATE, target_time_utc: 9,
        cruise_altitude_ft: 8000, flight_ceiling_ft: 18000, flight_duration_hours: 4.5,
        private: false, auto_refresh: false, auto_refresh_hour: null,
        created_at: '2026-04-20T10:00:00+00:00',
      },
      {
        id: 'egtf_eglf-2025-10-01-bbbb', user_id: 'u1', profile_id: null, aircraft_id: null, aircraft: null,
        route_name: 'egtf_eglf', waypoints: ['EGTF', 'EGLF'],
        departure_time: '2025-10-01T09:00:00+00:00', alt_departure_time: null,
        target_date: '2025-10-01', target_time_utc: 9,
        cruise_altitude_ft: 7000, flight_ceiling_ft: 15000, flight_duration_hours: 0.5,
        private: false, auto_refresh: false, auto_refresh_hour: null,
        created_at: '2025-10-01T10:00:00+00:00',
      },
    ],
  }));
  await page.route('**/api/flights/*/packs/latest', route => route.fulfill({ status: 404, body: '' }));
  await page.route('**/api/refresh/active', route => route.fulfill({ json: [] }));
  await page.route(`**/api/flights/${FLIGHT_ID}`, route => route.fulfill({
    json: {
      id: FLIGHT_ID, user_id: 'u1', profile_id: null, aircraft_id: null, aircraft: null,
      route_name: 'egtk_lsgs', waypoints: ['EGTK', 'LFPB', 'LSGS'],
      departure_time: `${FUTURE_DATE}T09:00:00+00:00`, alt_departure_time: null,
      target_date: FUTURE_DATE, target_time_utc: 9,
      cruise_altitude_ft: 8000, flight_ceiling_ft: 18000, flight_duration_hours: 4.5,
      private: false, auto_refresh: false, auto_refresh_hour: null,
      created_at: '2026-04-20T10:00:00+00:00',
    },
  }));
  await page.route('**/api/flights/route-distance', route => route.fulfill({
    json: { total_distance_nm: 500, waypoints: [
      { icao: 'EGTK', name: 'Oxford', lat: 51.8, lon: -1.3, timezone: 'Europe/London' },
      { icao: 'LFPB', name: 'Paris LB', lat: 48.9, lon: 2.4, timezone: 'Europe/Paris' },
      { icao: 'LSGS', name: 'Sion', lat: 46.2, lon: 7.3, timezone: 'Europe/Zurich' },
    ] },
  }));

  const errors: string[] = [];
  page.on('pageerror', err => errors.push(String(err)));

  // 1. Plain load: Duplicate button visible, Recent Routes hidden (only 2 distinct but need >=2 to show)
  await page.goto('http://localhost:8000/');
  await expect(page.locator('.btn-duplicate').first()).toBeVisible();
  // Two distinct routes, so dropdown should show
  await expect(page.locator('#input-route-history')).toBeVisible();
  const optCount = await page.locator('#input-route-history option').count();
  expect(optCount).toBe(3); // placeholder + 2 routes

  // 2. Click Duplicate → navigates with query param and prefills form
  await page.locator('.btn-duplicate').first().click();
  await page.waitForURL(/duplicate_from=/);
  await expect(page.locator('.duplicate-banner')).toBeVisible();
  await expect(page.locator('#input-waypoints')).toHaveValue('EGTK LFPB LSGS');
  await expect(page.locator('#input-date')).toHaveValue('');
  await expect(page.locator('#input-altitude')).toHaveValue('8000');
  await expect(page.locator('#input-duration')).toHaveValue('4.5');

  // 3. Use route history to fill input
  await page.goto('http://localhost:8000/');
  await page.locator('#input-route-history').selectOption({ index: 2 });
  await expect(page.locator('#input-waypoints')).toHaveValue('EGTF EGLF');

  expect(errors).toEqual([]);
});
