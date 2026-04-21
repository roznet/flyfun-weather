import { test, expect } from '@playwright/test';

const FUTURE = new Date(Date.now() + 5 * 86400_000).toISOString().slice(0, 10);

const baseFlight = (id: string, overrides: Record<string, any> = {}) => ({
  id, user_id: 'u1', profile_id: null, aircraft_id: null, aircraft: null,
  route_name: 'egtk_lsgs', waypoints: ['EGTK', 'LSGS'],
  departure_time: `${FUTURE}T09:00:00+00:00`, alt_departure_time: null,
  target_date: FUTURE, target_time_utc: 9,
  cruise_altitude_ft: 8000, flight_ceiling_ft: 18000, flight_duration_hours: 4.5,
  private: false, auto_refresh: false, auto_refresh_hour: null,
  created_at: '2026-04-20T10:00:00+00:00',
  ...overrides,
});

test('multi-select + bulk-delete flow', async ({ page }) => {
  const flights = [
    baseFlight(`f1-${FUTURE}-aaaa`),
    baseFlight(`f2-${FUTURE}-bbbb`, { route_name: 'egtf_eglf', waypoints: ['EGTF', 'EGLF'] }),
    baseFlight(`f3-${FUTURE}-cccc`, { route_name: 'lfpb_lfpg', waypoints: ['LFPB', 'LFPG'] }),
  ];

  let bulkDeleteBody: any = null;

  await page.route('**/auth/me', route => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/profiles', route => route.fulfill({ json: [] }));
  await page.route('**/api/user/aircraft', route => route.fulfill({ json: [] }));
  await page.route('**/api/refresh/active', route => route.fulfill({ json: [] }));
  await page.route('**/api/flights/*/packs/latest', route => route.fulfill({ status: 404, body: '' }));

  let listCall = 0;
  await page.route('**/api/flights', async route => {
    if (route.request().method() === 'GET') {
      listCall++;
      // After the bulk delete runs, return the remaining flight only
      if (listCall > 1 && bulkDeleteBody) {
        route.fulfill({ json: [flights[2]] });
      } else {
        route.fulfill({ json: flights });
      }
    } else {
      route.fallback();
    }
  });
  await page.route('**/api/flights/bulk-delete', async route => {
    bulkDeleteBody = route.request().postDataJSON();
    route.fulfill({ json: { deleted: bulkDeleteBody.ids, not_found: [] } });
  });

  page.on('dialog', dialog => dialog.accept());

  const errors: string[] = [];
  page.on('pageerror', err => errors.push(String(err)));

  await page.goto('http://localhost:8000/');
  await expect(page.locator('.flight-card')).toHaveCount(3);

  // No bar until something is selected
  await expect(page.locator('#selection-bar')).toHaveCount(0);

  // Tick two flights
  await page.locator('.flight-select-checkbox').nth(0).check();
  await page.locator('.flight-select-checkbox').nth(1).check();

  // Bar shows "2 selected"
  await expect(page.locator('#selection-bar')).toBeVisible();
  await expect(page.locator('.selection-count')).toContainText('2');

  // Cards marked selected
  await expect(page.locator('.flight-card.selected')).toHaveCount(2);

  // Click Delete selected; confirm auto-accepts
  await page.locator('.btn-bulk-delete').click();

  // bulk-delete was called with the two IDs
  await expect.poll(() => bulkDeleteBody?.ids?.length ?? 0).toBe(2);
  expect(bulkDeleteBody.ids).toEqual(expect.arrayContaining([flights[0].id, flights[1].id]));

  // List re-rendered with remaining flight, bar gone
  await expect(page.locator('.flight-card')).toHaveCount(1);
  await expect(page.locator('#selection-bar')).toHaveCount(0);

  expect(errors).toEqual([]);
});

test('clear-selection hides bar without deleting', async ({ page }) => {
  const flights = [baseFlight(`f1-${FUTURE}-aaaa`)];
  await page.route('**/auth/me', route => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/profiles', route => route.fulfill({ json: [] }));
  await page.route('**/api/user/aircraft', route => route.fulfill({ json: [] }));
  await page.route('**/api/refresh/active', route => route.fulfill({ json: [] }));
  await page.route('**/api/flights/*/packs/latest', route => route.fulfill({ status: 404, body: '' }));
  await page.route('**/api/flights', route => route.fulfill({ json: flights }));

  await page.goto('http://localhost:8000/');
  await page.locator('.flight-select-checkbox').first().check();
  await expect(page.locator('#selection-bar')).toBeVisible();
  await page.locator('.btn-clear-selection').click();
  await expect(page.locator('#selection-bar')).toHaveCount(0);
  await expect(page.locator('.flight-card.selected')).toHaveCount(0);
});
