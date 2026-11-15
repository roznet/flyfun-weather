import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// Briefing route map chrome (#998): one grouped metric picker in the bottom
// panel, the altitude stepper only for altitude metrics, each legend label
// under its own swatch, and Hide down to a pill. Reuses the egtf_eglf fixture.

const FIXTURES = join(__dirname, 'fixtures', 'egtf_eglf');
const FUTURE_DATE = new Date(Date.now() + 3 * 86400_000).toISOString().slice(0, 10);
const FLIGHT_ID = `egtf_eglf-${FUTURE_DATE}-45ed`;
const TIMESTAMP = '2026-02-25T16:10:07.255073+00:00';

function fixture(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

async function mockBriefingApi(page: Page) {
  const enc = encodeURIComponent;
  await page.route(`**/api/flights/${FLIGHT_ID}`, route => {
    if (route.request().url().includes('/packs')) return route.fallthrough();
    const flight = { ...fixture('flight.json'), id: FLIGHT_ID, departure_time: `${FUTURE_DATE}T17:00:00+00:00`, target_date: FUTURE_DATE };
    return route.fulfill({ json: flight });
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, route => {
    const afterPacks = route.request().url().split('/packs')[1];
    if (afterPacks && afterPacks !== '' && afterPacks !== '/') return route.fallthrough();
    return route.fulfill({ json: fixture('packs.json') });
  });
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/${enc(TIMESTAMP)}`, route => {
    const afterTs = route.request().url().split(enc(TIMESTAMP))[1];
    if (afterTs && afterTs !== '' && afterTs !== '/') return route.fallthrough();
    return route.fulfill({ json: fixture('pack_meta.json') });
  });
  await page.route(`**/packs/${enc(TIMESTAMP)}/snapshot`, r => r.fulfill({ json: fixture('snapshot.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/route-analyses`, r => r.fulfill({ json: fixture('route_analyses.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories`, r => r.fulfill({ json: fixture('advisories.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/elevation`, r => r.fulfill({ json: fixture('elevation.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/digest/json`, r => r.fulfill({ json: fixture('digest.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/gramet**`, r =>
    r.fulfill({ status: 404, json: { detail: 'GRAMET not available' } }));
}

/** Horizontal centres of a row of elements. */
async function centres(page: Page, selector: string): Promise<number[]> {
  return page.locator(selector).evaluateAll(els =>
    els.map(e => { const b = e.getBoundingClientRect(); return b.left + b.width / 2; }));
}

async function openMap(page: Page, metric = 'icing-risk-at-level') {
  await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
  await page.locator('button[data-layout="map"]').first().click();
  await page.locator('#rm-metric').selectOption(metric);
}

async function expectAligned(page: Page) {
  const s = await centres(page, '.rm-legend-route .map-legend-stop');
  const l = await centres(page, '.rm-legend-route .map-legend-labels span');
  expect(s.length).toBeGreaterThan(0);
  expect(l.length).toBe(s.length);
  s.forEach((x, i) => expect(Math.abs(x - l[i])).toBeLessThanOrEqual(1));
}

test.describe('Briefing route map — #996-style chrome (#998)', () => {
  test.beforeEach(async ({ page }) => { await mockBriefingApi(page); });

  test('one metric picker, grouped; stepper only at altitude; legend aligned', async ({ page }) => {
    await openMap(page);
    await expect(page.locator('#rm-metric optgroup')).toHaveCount(2);
    // No Width metric any more.
    await expect(page.locator('#map-width-metric')).toHaveCount(0);
    await expect(page.locator('#rm-alt-reset')).toBeVisible();
    const before = await page.locator('#rm-alt-reset').textContent();
    await page.locator('#rm-alt-up').click();
    await expect(page.locator('#rm-alt-reset')).not.toHaveText(before ?? '');
    await page.locator('#rm-alt-reset').click();
    await expect(page.locator('#rm-alt-reset')).toHaveText(before ?? '');
    await expectAligned(page);
    await page.locator('#viz-map-pane').screenshot({ path: 'test-results/route-map-chrome-desktop.png' });

    // A surface metric drops the stepper.
    await page.locator('#rm-metric').selectOption('cloud-cover-total');
    await expect(page.locator('#rm-alt-reset')).toHaveCount(0);
  });

  test('Hide leaves a pill and widens the map; Show restores', async ({ page }) => {
    await openMap(page);
    await page.locator('#rm-hide').click();
    await expect(page.locator('#rm-show')).toContainText('FL');
    await expect(page.locator('.rm-panel')).toHaveCount(0);
    await page.locator('#rm-show').click();
    await expect(page.locator('.rm-panel')).toBeVisible();
  });

  test('phone width: no horizontal overflow, labels still aligned', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await openMap(page, 'nwp-ceiling');  // longest stop labels
    await expectAligned(page);
    const overflow = await page.locator('.rm-panel').evaluate(e => e.scrollWidth - e.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.locator('#viz-map-pane').screenshot({ path: 'test-results/route-map-chrome-phone.png' });
  });
});
