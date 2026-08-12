/** Sticky page header (#543 / PR #546) — browser behaviour.
 *
 *  PR #546 shipped with no tests at all, and it is the case where that hurts
 *  most: it is pure layout with no logic to unit-test. The web unit suite runs
 *  `environment: 'node'`, and jsdom would be worse than useless here because it
 *  performs no layout — `offsetHeight` is 0, so `trackHeaderHeight()` would
 *  "pass" while publishing garbage. A browser is the only honest check.
 *
 *  Covers the fix and the three consequences the PR body calls out as handled:
 *  `--header-h`, `scroll-padding-top`, and Leaflet z-index.
 */

import { test, expect, type Page } from '@playwright/test';

const FUTURE = new Date(Date.now() + 5 * 86400_000).toISOString().slice(0, 10);

/** Enough flights to make the page scroll. */
const flights = Array.from({ length: 12 }, (_, i) => ({
  id: `f${i}-${FUTURE}-aaa${i}`, user_id: 'u1', profile_id: null, aircraft_id: null,
  aircraft: null, route_name: 'egtk_lsgs', waypoints: ['EGTK', 'LSGS'],
  departure_time: `${FUTURE}T09:00:00+00:00`, alt_departure_time: null,
  target_date: FUTURE, target_time_utc: 9,
  cruise_altitude_ft: 8000, flight_ceiling_ft: 18000, flight_duration_hours: 4.5,
  private: false, auto_refresh: false, auto_refresh_hour: null,
  section: 'future', role: 'owner', latest_briefing: null,
  created_at: '2026-04-20T10:00:00+00:00',
}));

async function stubFlights(page: Page): Promise<void> {
  await page.route('**/auth/me', route => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/profiles', route => route.fulfill({ json: [] }));
  await page.route('**/api/user/aircraft', route => route.fulfill({ json: [] }));
  await page.route('**/api/user/preferences', route => route.fulfill({ json: {} }));
  await page.route('**/api/refresh/active', route => route.fulfill({ json: [] }));
  await page.route('**/api/debriefs/stats', route => route.fulfill({ json: null }));
  await page.route('**/api/flights/*/packs/latest', route => route.fulfill({ status: 404, body: '' }));
  await page.route(/\/api\/flights(\?.*)?$/, route => route.fulfill({ json: flights }));
}

const headerVar = (page: Page) => page.evaluate(() =>
  getComputedStyle(document.documentElement).getPropertyValue('--header-h').trim());

test('the header stays put when the page scrolls', async ({ page }) => {
  await stubFlights(page);
  await page.goto('/');
  await expect(page.locator('.flight-card').first()).toBeVisible();

  const header = page.locator('.page-header');

  // Wheel scrolling is asynchronous — poll rather than reading straight after.
  await page.mouse.wheel(0, 2000);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(100);

  const after = await header.boundingBox();
  expect(after).not.toBeNull();
  // A sticky header starts at its natural offset (the container's top margin)
  // and pins at 0 once scrolled past — so the invariant is "clamped to the top
  // of the viewport", not "unchanged from before the scroll". Unpinned, this
  // would be roughly -`scrolled`.
  expect(after!.y).toBeGreaterThanOrEqual(0);
  expect(after!.y).toBeLessThanOrEqual(1);
  await expect(header).toBeInViewport();
});

test('--header-h matches the rendered header height', async ({ page }) => {
  await stubFlights(page);
  await page.goto('/');
  await expect(page.locator('.flight-card').first()).toBeVisible();

  const measured = await page.locator('.page-header').evaluate(el => (el as HTMLElement).offsetHeight);
  const published = await headerVar(page);

  expect(published).not.toBe('');
  expect(parseFloat(published)).toBeCloseTo(measured, 0);
});

test('--header-h follows the header when the viewport reflows', async ({ page }) => {
  // The ResizeObserver contract: the bar changes height when the nav wraps or
  // the hamburger takes over, and anything offsetting from it must follow.
  await stubFlights(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/');
  await expect(page.locator('.flight-card').first()).toBeVisible();
  const wide = parseFloat(await headerVar(page));

  await page.setViewportSize({ width: 480, height: 800 });
  await expect.poll(async () => {
    const measured = await page.locator('.page-header').evaluate(el => (el as HTMLElement).offsetHeight);
    return Math.abs(parseFloat(await headerVar(page)) - measured) < 1.5;
  }).toBe(true);

  const narrow = parseFloat(await headerVar(page));
  expect(narrow).toBeGreaterThan(0);
  expect(wide).toBeGreaterThan(0);
});

test('scroll-padding-top offsets anchors by the header height', async ({ page }) => {
  // Without this, scrollIntoView({block:'start'}) parks each heading
  // underneath the pinned bar — which would break the rail's section nav,
  // i.e. the very navigation #543 was about.
  await stubFlights(page);
  await page.goto('/');
  await expect(page.locator('.flight-card').first()).toBeVisible();

  const padding = await page.evaluate(() =>
    getComputedStyle(document.documentElement).scrollPaddingTop);
  const measured = await page.locator('.page-header').evaluate(el => (el as HTMLElement).offsetHeight);

  expect(padding).not.toBe('auto');
  expect(parseFloat(padding)).toBeGreaterThan(0);
  // It must actually clear the bar, not just be non-zero.
  expect(parseFloat(padding)).toBeGreaterThanOrEqual(measured - 1);
});

test('a Leaflet map does not paint over the header', async ({ page }) => {
  // Leaflet panes and controls run z-index 400–1000 in the root stacking
  // context. `.map-container` already had `z-index: 0`; `maps.html`'s
  // `.map-wrapper` did not. Easy to reintroduce by adding a new map container
  // without its own stacking context, and invisible to every non-browser test.
  await page.route('**/auth/me', route => route.fulfill({
    json: { id: 'u1', email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true },
  }));
  await page.route('**/api/user/preferences', route => route.fulfill({ json: {} }));
  await page.route('**/api/maps/**', route => route.fulfill({ json: { airports: [], generated_at: null } }));

  await page.goto('/maps.html');
  const header = page.locator('.page-header');
  await expect(header).toBeVisible();

  const box = await header.boundingBox();
  expect(box).not.toBeNull();

  // Whatever sits at the header's centre must be the header (or inside it) —
  // never a map pane that has painted over the top of it.
  const owned = await page.evaluate(({ x, y }) => {
    const el = document.elementFromPoint(x, y);
    return !!el?.closest('.page-header');
  }, { x: box!.x + box!.width / 2, y: box!.y + box!.height / 2 });

  expect(owned).toBe(true);
});
