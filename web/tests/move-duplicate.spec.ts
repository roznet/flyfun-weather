import { test, expect } from '@playwright/test';

const FUTURE_DATE = new Date(Date.now() + 5 * 86400_000).toISOString().slice(0, 10);
const NEW_DATE = new Date(Date.now() + 10 * 86400_000).toISOString().slice(0, 10);
const FLIGHT_ID = `egtk_lsgs-${FUTURE_DATE}-aaaa`;
const NEW_FLIGHT_ID = `egtk_lsgs-${NEW_DATE}-bbbb`;

const baseFlight = (id: string, date: string) => ({
  id, user_id: 'u1', profile_id: null, aircraft_id: null, aircraft: null,
  route_name: 'egtk_lsgs', waypoints: ['EGTK', 'LFPB', 'LSGS'],
  departure_time: `${date}T09:00:00+00:00`, alt_departure_time: null,
  target_date: date, target_time_utc: 9,
  cruise_altitude_ft: 8000, flight_ceiling_ft: 18000, flight_duration_hours: 4.5,
  private: false, auto_refresh: false, auto_refresh_hour: null,
  created_at: '2026-04-20T10:00:00+00:00',
});

async function mockBaseAuth(page: any) {
  await page.route('**/auth/me', (route: any) => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/profiles', (route: any) => route.fulfill({ json: [] }));
  await page.route('**/api/user/aircraft', (route: any) => route.fulfill({ json: [] }));
  await page.route('**/api/refresh/active', (route: any) => route.fulfill({ json: [] }));
  await page.route('**/api/flights/route-distance', (route: any) => route.fulfill({
    json: { total_distance_nm: 500, waypoints: [
      { icao: 'EGTK', name: 'Oxford', lat: 51.8, lon: -1.3, timezone: 'Europe/London' },
      { icao: 'LFPB', name: 'Paris', lat: 48.9, lon: 2.4, timezone: 'Europe/Paris' },
      { icao: 'LSGS', name: 'Sion', lat: 46.2, lon: 7.3, timezone: 'Europe/Zurich' },
    ] },
  }));
}

test('save vs move/duplicate button swap responds to date change', async ({ page }) => {
  const flight = baseFlight(FLIGHT_ID, FUTURE_DATE);
  const packs = [
    { flight_id: FLIGHT_ID, fetch_timestamp: '2026-04-20T12:00:00+00:00', days_out: 5, has_gramet: true, has_skewt: true, has_digest: true, assessment: 'GREEN', assessment_reason: 'OK' },
    { flight_id: FLIGHT_ID, fetch_timestamp: '2026-04-19T12:00:00+00:00', days_out: 6, has_gramet: true, has_skewt: true, has_digest: true, assessment: 'GREEN', assessment_reason: 'OK' },
  ];

  await mockBaseAuth(page);
  await page.route(`**/api/flights/${FLIGHT_ID}`, (route: any) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: flight });
    return route.fallback();
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, (route: any) => route.fulfill({ json: packs }));

  const errors: string[] = [];
  page.on('pageerror', err => errors.push(String(err)));

  await page.goto(`http://localhost:8000/flight.html?id=${encodeURIComponent(FLIGHT_ID)}`);
  await page.locator('#btn-edit-flight').click();

  // Initially Save is visible, Move/Duplicate hidden
  await expect(page.locator('#edit-save')).toBeVisible();
  await expect(page.locator('#edit-move')).toBeHidden();
  await expect(page.locator('#edit-duplicate')).toBeHidden();

  // Change the date → Save hides, Move + Duplicate appear, note shows pack count
  await page.locator('#edit-date').fill(NEW_DATE);
  await page.locator('#edit-date').dispatchEvent('change');

  await expect(page.locator('#edit-save')).toBeHidden();
  await expect(page.locator('#edit-move')).toBeVisible();
  await expect(page.locator('#edit-duplicate')).toBeVisible();
  await expect(page.locator('#edit-date-note')).toBeVisible();
  await expect(page.locator('#edit-date-note')).toContainText('2');  // pack count

  // Revert the date — buttons should swap back
  await page.locator('#edit-date').fill(FUTURE_DATE);
  await page.locator('#edit-date').dispatchEvent('change');
  await expect(page.locator('#edit-save')).toBeVisible();
  await expect(page.locator('#edit-move')).toBeHidden();

  expect(errors).toEqual([]);
});

test('Move calls /move endpoint and navigates to new flight', async ({ page }) => {
  const flight = baseFlight(FLIGHT_ID, FUTURE_DATE);
  const newFlight = baseFlight(NEW_FLIGHT_ID, NEW_DATE);
  let moveBody: any = null;

  await mockBaseAuth(page);
  await page.route(`**/api/flights/${FLIGHT_ID}`, (route: any) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: flight });
    return route.fallback();
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, (route: any) => route.fulfill({ json: [
    { flight_id: FLIGHT_ID, fetch_timestamp: '2026-04-20T12:00:00+00:00', days_out: 5, has_gramet: true, has_skewt: true, has_digest: true, assessment: 'GREEN', assessment_reason: 'OK' },
  ] }));
  await page.route(`**/api/flights/${FLIGHT_ID}/move`, (route: any) => {
    moveBody = route.request().postDataJSON();
    route.fulfill({ json: newFlight });
  });
  // After navigation, mock the new flight URL too
  await page.route(`**/api/flights/${NEW_FLIGHT_ID}`, (route: any) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: newFlight });
    return route.fallback();
  });
  await page.route(`**/api/flights/${NEW_FLIGHT_ID}/packs`, (route: any) => route.fulfill({ json: [] }));

  page.on('dialog', dialog => dialog.accept());

  await page.goto(`http://localhost:8000/flight.html?id=${encodeURIComponent(FLIGHT_ID)}`);
  await page.locator('#btn-edit-flight').click();
  await page.locator('#edit-date').fill(NEW_DATE);
  await page.locator('#edit-date').dispatchEvent('change');

  await page.locator('#edit-move').click();

  await expect.poll(() => moveBody?.departure_time).toContain(NEW_DATE);
  await page.waitForURL(new RegExp(`flight.html\\?id=${NEW_FLIGHT_ID}`));
});

test('Duplicate calls POST /flights and navigates, leaving original intact', async ({ page }) => {
  const flight = baseFlight(FLIGHT_ID, FUTURE_DATE);
  const newFlight = baseFlight(NEW_FLIGHT_ID, NEW_DATE);
  let createBody: any = null;
  let moveCalled = false;

  await mockBaseAuth(page);
  await page.route(`**/api/flights/${FLIGHT_ID}`, (route: any) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: flight });
    return route.fallback();
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, (route: any) => route.fulfill({ json: [] }));
  await page.route(`**/api/flights/${FLIGHT_ID}/move`, (route: any) => {
    moveCalled = true;
    route.fulfill({ status: 500, body: 'should not be called' });
  });
  await page.route('**/api/flights', (route: any) => {
    if (route.request().method() === 'POST') {
      createBody = route.request().postDataJSON();
      return route.fulfill({ status: 201, json: newFlight });
    }
    return route.fallback();
  });
  await page.route(`**/api/flights/${NEW_FLIGHT_ID}`, (route: any) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: newFlight });
    return route.fallback();
  });
  await page.route(`**/api/flights/${NEW_FLIGHT_ID}/packs`, (route: any) => route.fulfill({ json: [] }));

  await page.goto(`http://localhost:8000/flight.html?id=${encodeURIComponent(FLIGHT_ID)}`);
  await page.locator('#btn-edit-flight').click();
  await page.locator('#edit-date').fill(NEW_DATE);
  await page.locator('#edit-date').dispatchEvent('change');

  await page.locator('#edit-duplicate').click();

  await expect.poll(() => createBody?.departure_time).toContain(NEW_DATE);
  expect(moveCalled).toBe(false);
  await page.waitForURL(new RegExp(`flight.html\\?id=${NEW_FLIGHT_ID}`));
});
