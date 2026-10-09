import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// Briefing route map legend (#731): the Color/Width pickers sit in the legend
// rows, each stop label sits under its own swatch, and the Width row draws
// thickness. Reuses the egtf_eglf fixture.

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

async function openMapWithLegend(page: Page) {
  await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
  await page.locator('button[data-layout="map"]').first().click();
  await page.locator('#map-color-metric').selectOption('icing-risk-at-level');
  await page.locator('#map-width-metric').selectOption('convective-risk');
}

async function expectAligned(page: Page) {
  for (const channel of ['color', 'width']) {
    const scope = `#map-legend .map-legend-scale-${channel}`;
    const swatch = channel === 'color' ? '.map-legend-stop' : '.map-legend-width-cell';
    const s = await centres(page, `${scope} ${swatch}`);
    const l = await centres(page, `${scope} .map-legend-labels span`);
    expect(s.length).toBeGreaterThan(0);
    expect(l.length).toBe(s.length);
    s.forEach((x, i) => expect(Math.abs(x - l[i])).toBeLessThanOrEqual(1));
  }
}

test.describe('Briefing route map — legend (#731)', () => {
  test.beforeEach(async ({ page }) => { await mockBriefingApi(page); });

  test('pickers live in the legend; labels align; width draws thickness', async ({ page }) => {
    await openMapWithLegend(page);

    // One picker per channel, inside the legend — not repeated in the controls row.
    await expect(page.locator('#map-legend #map-color-metric')).toHaveValue('icing-risk-at-level');
    await expect(page.locator('#map-legend #map-width-metric')).toHaveValue('convective-risk');
    await expect(page.locator('#map-controls select#map-color-metric')).toHaveCount(0);
    // The metric is named once, by its picker: the row's own text is the channel.
    await expect(page.locator('#map-legend .map-legend-channel')).toHaveText(['Color:', 'Width:']);

    await expectAligned(page);

    // Width bars thicken with convective risk.
    const heights = await page.locator('#map-legend .map-legend-width-bar').evaluateAll(els =>
      els.map(e => e.getBoundingClientRect().height));
    expect(heights).toHaveLength(5);
    for (let i = 1; i < heights.length; i++) expect(heights[i]).toBeGreaterThan(heights[i - 1]);
    await page.locator('#viz-map-pane').screenshot({ path: 'test-results/route-map-legend-desktop.png' });

    // Width "none": the picker stays, the strip goes.
    await page.locator('#map-width-metric').selectOption('none');
    await expect(page.locator('#map-legend .map-legend-width-bar')).toHaveCount(0);
    await expect(page.locator('#map-legend #map-width-metric')).toHaveValue('none');
  });

  test('phone width: no horizontal overflow, labels still aligned', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await openMapWithLegend(page);
    await page.locator('#map-color-metric').selectOption('nwp-ceiling');  // longest stop labels
    await expectAligned(page);
    const overflow = await page.locator('#map-legend').evaluate(e => e.scrollWidth - e.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.locator('#viz-map-pane').screenshot({ path: 'test-results/route-map-legend-phone.png' });
  });
});
