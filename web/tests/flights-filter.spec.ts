/** Flights-list ICAO filter (#542) — browser behaviour.
 *
 *  The unit suite covers the matcher, the `past_q` endpoint and the store's
 *  race/prune logic. What it structurally cannot cover is everything below:
 *  the suite runs `environment: 'node'`, and jsdom would not help because it
 *  performs no layout, so computed visibility and `offsetHeight` are
 *  meaningless there.
 *
 *  Every case here is one that shipped a bug or fixed one during review:
 *  the >5 threshold (appeared on a 5-flight list because an author
 *  `display: flex` beat the UA `[hidden]` rule), select-all reaching hidden
 *  rows, individually-selected rows surviving being filtered away, and the
 *  in-progress filter draft being wiped by an unrelated re-render.
 */

import { test, expect, type Page } from '@playwright/test';

const FUTURE = new Date(Date.now() + 5 * 86400_000).toISOString().slice(0, 10);
const PAST = new Date(Date.now() - 90 * 86400_000).toISOString().slice(0, 10);

type Section = 'future' | 'recent' | 'past';

const flight = (
  id: string,
  waypoints: string[],
  section: Section = 'future',
  overrides: Record<string, unknown> = {},
) => {
  const date = section === 'future' ? FUTURE : PAST;
  return {
    id, user_id: 'u1', profile_id: null, aircraft_id: null, aircraft: null,
    route_name: waypoints.join('_').toLowerCase(), waypoints,
    departure_time: `${date}T09:00:00+00:00`, alt_departure_time: null,
    target_date: date, target_time_utc: 9,
    cruise_altitude_ft: 8000, flight_ceiling_ft: 18000, flight_duration_hours: 4.5,
    private: false, auto_refresh: false, auto_refresh_hour: null,
    section, role: 'owner', latest_briefing: null,
    created_at: '2026-04-20T10:00:00+00:00',
    ...overrides,
  };
};

/** Stub every request the flights page makes except the list itself. */
async function stubPage(page: Page): Promise<void> {
  await page.route('**/auth/me', route => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/profiles', route => route.fulfill({ json: [] }));
  await page.route('**/api/user/aircraft', route => route.fulfill({ json: [] }));
  await page.route('**/api/user/preferences', route => route.fulfill({ json: {} }));
  await page.route('**/api/refresh/active', route => route.fulfill({ json: [] }));
  await page.route('**/api/debriefs/stats', route => route.fulfill({ json: null }));
  await page.route('**/api/flights/*/packs/latest', route => route.fulfill({ status: 404, body: '' }));
}

/** Serve the flights list, applying `past_q` server-side the way the API does
 *  (prefix match on any waypoint, ANDed across tokens) and reporting the match
 *  count in X-Past-Total, so pagination and counts behave like production. */
async function stubList(page: Page, flights: ReturnType<typeof flight>[]): Promise<() => string[]> {
  const seenQueries: string[] = [];
  await page.route(/\/api\/flights(\?.*)?$/, route => {
    const req = route.request();
    if (req.method() !== 'GET') return route.fallback();
    const url = new URL(req.url());
    const q = url.searchParams.get('past_q') ?? '';
    seenQueries.push(q);
    const tokens = q.split(/\s+/).filter(Boolean).map(s => s.toUpperCase());
    const matches = (f: ReturnType<typeof flight>) =>
      tokens.every(tok => f.waypoints.some(w => w.toUpperCase().startsWith(tok)));
    const kept = flights.filter(f => f.section !== 'past' || matches(f));
    const pastCount = kept.filter(f => f.section === 'past').length;
    route.fulfill({ json: kept, headers: { 'X-Past-Total': String(pastCount) } });
  });
  return () => seenQueries;
}

/** Filler flights, deliberately all-UK: no waypoint here may start with "LF",
 *  or the `LF`-means-France assertions below would match the filler too. */
const upcoming = (n: number) =>
  Array.from({ length: n }, (_, i) => flight(`u${i}-${FUTURE}-aaa${i}`, [`EG${String.fromCharCode(65 + i)}${i}`, 'EGKB']));

test.describe('upcoming filter', () => {
  test('stays hidden at or below the threshold, appears above it', async ({ page }) => {
    // The regression that reached review: the JS correctly set `hidden`, but
    // `.list-filter-row { display: flex }` (an author rule) beat the UA's
    // `[hidden] { display: none }`, so the box rendered anyway. A CSS-text pin
    // guards that exact rule; this guards the *behaviour*, whatever breaks it.
    await stubPage(page);
    await stubList(page, upcoming(5));
    await page.goto('/');
    await expect(page.locator('.flight-card')).toHaveCount(5);
    await expect(page.locator('#upcoming-filter-row')).toBeHidden();

    await page.goto('about:blank');
    await stubList(page, upcoming(6));
    await page.goto('/');
    await expect(page.locator('.flight-card')).toHaveCount(6);
    await expect(page.locator('#upcoming-filter-row')).toBeVisible();
  });

  test('filters rows as you type and reports the match count', async ({ page }) => {
    await stubPage(page);
    await stubList(page, [
      ...upcoming(5),
      flight(`x-${FUTURE}-bbbb`, ['LFMD', 'EGTF']),
      flight(`y-${FUTURE}-cccc`, ['LFAT', 'LFPB']),
    ]);
    await page.goto('/');
    await expect(page.locator('.flight-card')).toHaveCount(7);

    await page.locator('#upcoming-filter-input').fill('LFAT');
    await expect(page.locator('.flight-card')).toHaveCount(1);
    await expect(page.locator('#upcoming-filter-count')).toContainText('1');
    await expect(page.locator('#upcoming-filter-count')).toContainText('7');

    // Prefix matching is what makes "LF" a country filter.
    await page.locator('#upcoming-filter-input').fill('LF');
    await expect(page.locator('.flight-card')).toHaveCount(2);

    // Clearing restores everything.
    await page.locator('#upcoming-filter-clear').click();
    await expect(page.locator('.flight-card')).toHaveCount(7);
  });

  test('a filter matching nothing does not show the global empty state', async ({ page }) => {
    // "No flights yet" while a query is active reads as data loss.
    await stubPage(page);
    await stubList(page, upcoming(6));
    await page.goto('/');

    await page.locator('#upcoming-filter-input').fill('KJFK');

    await expect(page.locator('.flight-card')).toHaveCount(0);
    await expect(page.locator('.list-filter-nomatch')).toBeVisible();
    await expect(page.locator('.empty-state')).toHaveCount(0);
  });
});

test.describe('filter and selection', () => {
  test('select-all never reaches a row the filter is hiding', async ({ page }) => {
    await stubPage(page);
    await stubList(page, [
      ...upcoming(5),
      flight(`x-${FUTURE}-bbbb`, ['LFMD', 'EGTF']),
    ]);
    await page.goto('/');

    await page.locator('#upcoming-filter-input').fill('LFMD');
    const visible = await page.locator('.flight-card').count();

    await page.locator('.flight-select-checkbox').first().check();
    await page.locator('.btn-select-all').click();

    // The bar counts only what you can see.
    await expect(page.locator('.selection-count')).toContainText(String(visible));
  });

  test('a row selected before filtering is dropped when the filter hides it', async ({ page }) => {
    // Scoping select-all closed only half the door: a row ticked beforehand
    // kept its id in selectedIds with no checkbox rendered, so it could not be
    // unticked and bulk delete would still have taken it.
    await stubPage(page);
    await stubList(page, [
      ...upcoming(5),
      flight(`target-${FUTURE}-bbbb`, ['LFAT', 'LFPB']),
    ]);
    await page.goto('/');

    // Tick the LFAT flight, then filter it away.
    await page.locator('.flight-card', { hasText: 'LFAT' }).locator('.flight-select-checkbox').check();
    await expect(page.locator('.selection-count')).toContainText('1');

    await page.locator('#upcoming-filter-input').fill('EG');

    await expect(page.locator('.flight-card', { hasText: 'LFAT' })).toHaveCount(0);
    await expect(page.locator('#selection-bar')).toHaveCount(0);
  });
});

test.describe('past filter', () => {
  const withPast = (n: number) => [
    flight(`fut-${FUTURE}-aaaa`, ['EGTF', 'LFMD']),
    ...Array.from({ length: n }, (_, i) =>
      flight(`p${i}-${PAST}-ddd${i}`, i === 0 ? ['LESB', 'LFMD'] : ['EGTF', 'LFAT'], 'past')),
  ];

  test('appears once the past section is worth filtering, and queries the server', async ({ page }) => {
    await stubPage(page);
    const queries = await stubList(page, withPast(6));
    await page.goto('/');

    // Past starts collapsed; expanding reveals the filter.
    await expect(page.locator('#past-filter-input')).toBeHidden();
    await page.locator('#past-flights-toggle').click();
    await expect(page.locator('#past-filter-input')).toBeVisible();

    await page.locator('#past-filter-input').fill('LESB');

    // Debounced, and it must reach the server — past is paginated, so the
    // match may not be loaded client-side.
    await expect.poll(() => queries().includes('LESB'), { timeout: 5_000 }).toBe(true);
    await expect(page.locator('.past-flights-list .flight-card')).toHaveCount(1);
  });

  test('keeps an in-progress draft when an unrelated re-render lands', async ({ page }) => {
    // The past input lives inside #flight-list, which is re-rendered wholesale.
    // Mid-debounce, the typed value is not yet in the store, so a re-render
    // rebuilt the box from the previous committed query and the user watched
    // their typing vanish. Ticking a checkbox is a synchronous re-render
    // trigger, which reproduces it without waiting on refresh polling.
    await stubPage(page);
    await stubList(page, withPast(6));
    await page.goto('/');
    await page.locator('#past-flights-toggle').click();

    const input = page.locator('#past-filter-input');
    await input.click();
    await input.type('LES', { delay: 10 });

    // Inside the 250ms debounce window, force a re-render.
    await page.locator('.flight-select-checkbox').first().check();

    await expect(input).toHaveValue('LES');
  });
});
