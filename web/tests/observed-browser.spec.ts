import { expect, test, type Page, type Route } from '@playwright/test';
import { fulfillFlashes, fulfillImage, openObservedPage } from './fixtures/observed-browser';

async function refreshObservations(page: Page) {
  const section = page.locator('[data-section="observations"]');
  if (await section.evaluate(el => el.classList.contains('collapsed'))) await section.locator('h3').first().click();
  await page.locator('.obs-refresh-btn').click();
}

test('full page preserves focused observed controls while ages advance without image polling', async ({ page }) => {
  const state = await openObservedPage(page);
  const image = page.locator('#map-container .leaflet-image-layer');
  await expect(image).toBeVisible();
  expect(await image.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth === 16)).toBe(true);
  await expect(page.locator('.map-observed-badge')).toContainText('16:55Z · 5 min old');
  await expect(page.locator('.map-observed-badge')).toContainText('Synthetic image producer');
  const info = page.locator('#observed-section .observed-info-btn');
  await info.focus();
  const node = await info.elementHandle();
  const before = [...state.images];
  const imageNode = await image.elementHandle();
  await page.clock.runFor(60_000);
  await expect(info).toBeFocused();
  expect(await node!.evaluate(el => el.isConnected)).toBe(true);
  expect(await imageNode!.evaluate(el => el.isConnected)).toBe(true);
  await expect(page.locator('.map-observed-badge')).toContainText('6 min old');
  await expect(page.locator('#observed-section')).toContainText('21 min old');
  expect(state.images).toEqual(before);
  state.assertHealthy();
});

test('unavailable saved source displays the same fallback in the selector and map', async ({ page }) => {
  const state = await openObservedPage(page, { source: 'eumetsat_ctth', includeTops: false });
  await expect(page.locator('.map-observed-badge')).toContainText('Radar reflectivity');
  await expect(page.locator('#map-observed-overlay')).toHaveValue('opera_dbzh');
  expect(state.images.at(-1)).toBe('opera_dbzh');
  await page.locator('#map-observed-overlay').selectOption('');
  await expect(page.locator('#map-container .leaflet-image-layer')).toHaveCount(0);
  await expect(page.locator('.map-observed-badge')).toHaveCount(0);
  await expect(page.locator('.map-observed-legend')).toHaveCount(0);
  state.assertHealthy();
});

test('same-frame raster failures retry once only after the actual observations refresh', async ({ page }) => {
  const state = await openObservedPage(page, { image: (route, _source, attempt) => attempt === 1
    ? route.fulfill({ status: 503 }) : fulfillImage(route, 'Recovered image') });
  await expect(page.locator('.map-observed-badge')).toContainText('unavailable');
  await page.locator('#map-color-metric').selectOption('temp-at-level');
  await page.locator('#map-color-metric').selectOption('headwind');
  await page.clock.runFor(60_000);
  expect(state.images).toEqual(['opera_dbzh']);
  await refreshObservations(page);
  await expect(page.locator('.map-observed-badge')).toContainText('Recovered image');
  expect(state.images).toEqual(['opera_dbzh', 'opera_dbzh']);
  expect(state.refreshes).toBe(1);
  // A second click after the panel re-renders still sends only one request.
  await refreshObservations(page);
  await expect.poll(() => state.refreshes).toBe(2);
  await page.clock.runFor(1000);
  expect(state.refreshes).toBe(2);
  expect(state.images).toEqual(['opera_dbzh', 'opera_dbzh']);
  state.assertHealthy();
});

test('same-frame lightning failures are bounded and recover through observations refresh', async ({ page }) => {
  const state = await openObservedPage(page, { source: 'eumetsat_li', flashes: (route, attempt) => attempt === 1
    ? route.fulfill({ status: 503 }) : fulfillFlashes(route, 'Recovered flashes') });
  await expect.poll(() => state.flashes).toBe(1);
  await page.locator('#map-color-metric').selectOption('temp-at-level');
  await page.clock.runFor(60_000);
  expect(state.flashes).toBe(1);
  await refreshObservations(page);
  await expect(page.locator('.map-observed-badge')).toContainText('Recovered flashes');
  expect(state.flashes).toBe(2);
  expect(state.images).toEqual([]);
  expect(state.refreshes).toBe(1);
  state.assertHealthy();
});

test('real Leaflet flashes fade and expire without refetching weather', async ({ page }) => {
  const state = await openObservedPage(page, { source: 'eumetsat_li' });
  const flashes = page.locator('#map-container path[fill="#7c3aed"]');
  await expect(flashes).toHaveCount(1);
  expect(Number(await flashes.getAttribute('fill-opacity'))).toBeCloseTo(0.0225);
  await page.clock.runFor(60_000);
  expect(Number(await flashes.getAttribute('fill-opacity'))).toBeCloseTo(0.0075);
  await page.clock.runFor(60_000);
  await expect(flashes).toHaveCount(0);
  expect(state.flashes).toBe(1);
  expect(state.images).toEqual([]);
  state.assertHealthy();
});

test('pending flash responses cannot resurrect a destroyed map or overwrite its replacement', async ({ page }) => {
  const pending: Route[] = [];
  const state = await openObservedPage(page, { source: 'eumetsat_li', flashes: async route => { pending.push(route); } });
  await expect.poll(() => pending.length).toBe(1);
  await page.locator('[data-layout="cross-section"]').click();
  await expect(page.locator('.map-observed-badge')).toHaveCount(0);
  await page.locator('[data-layout="map"]').click();
  await expect.poll(() => pending.length).toBe(2);
  await fulfillFlashes(pending[1], 'New map flashes');
  await expect(page.locator('.map-observed-badge')).toContainText('New map flashes');
  await fulfillFlashes(pending[0], 'Obsolete map flashes');
  await page.clock.runFor(1000);
  await expect(page.locator('.map-observed-badge')).toContainText('New map flashes');
  await expect(page.locator('#map-container path[fill="#7c3aed"]')).toHaveCount(1);
  expect(state.flashes).toBe(2);
  state.assertHealthy();
});

test('A to B to A raster responses retain newest provenance and release their object URLs', async ({ page }) => {
  await page.addInitScript(() => {
    const created: string[] = [], revoked: string[] = [];
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = blob => { const url = create(blob); created.push(url); return url; };
    URL.revokeObjectURL = url => { revoked.push(url); revoke(url); };
    Object.assign(window, { observedTestUrls: { created, revoked } });
  });
  const pending: Route[] = [];
  const state = await openObservedPage(page, { image: async route => { pending.push(route); } });
  await expect.poll(() => pending.length).toBe(1);
  await page.locator('#map-observed-overlay').selectOption('opera_rate');
  await expect.poll(() => pending.length).toBe(2);
  await page.locator('#map-observed-overlay').selectOption('opera_dbzh');
  await expect.poll(() => pending.length).toBe(3);
  await fulfillImage(pending[2], 'Newest A image');
  await expect(page.locator('.map-observed-badge')).toContainText('Newest A image');
  const image = page.locator('#map-container .leaflet-image-layer');
  const activeUrl = await image.getAttribute('src');
  await fulfillImage(pending[0], 'Obsolete A image');
  await fulfillImage(pending[1], 'Obsolete B image');
  await page.clock.runFor(1000);
  await expect(image).toHaveAttribute('src', activeUrl!);
  await expect(page.locator('.map-observed-badge')).toContainText('Newest A image');
  await page.locator('[data-layout="cross-section"]').click();
  await expect(page.locator('.map-observed-legend')).toHaveCount(0);
  const urls = await page.evaluate(() => (window as unknown as { observedTestUrls: { created: string[]; revoked: string[] } }).observedTestUrls);
  expect(urls.created).toEqual([activeUrl]);
  expect(urls.revoked).toEqual([activeUrl]);
  expect(state.images).toEqual(['opera_dbzh', 'opera_rate', 'opera_dbzh']);
  state.assertHealthy();
});

for (const width of [1280, 390, 320]) {
  test(`observed map labels and controls stay readable in the ${width}px layout`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await openObservedPage(page, {
      source: 'eumetsat_ctth_temp',
      image: route => fulfillImage(route,
        'Synthetic image producer with a longer attribution for the regional composite',
        '2026-02-24T16:55:00Z'),
    });
    const badge = page.locator('.map-observed-badge');
    await expect(badge).toContainText('Cloud-top temperature');
    await expect(badge).toContainText('2026-02-24');
    await page.clock.runFor(1000);
    for (const selector of ['.map-observed-badge', '.map-observed-legend', '#map-observed-overlay',
      '#map-observed-opacity', '#map-observed-opacity-value']) {
      const bounds = await page.locator(selector).boundingBox();
      expect(bounds).not.toBeNull();
      expect.soft(bounds!.x, selector).toBeGreaterThanOrEqual(0);
      expect.soft(bounds!.x + bounds!.width, selector).toBeLessThanOrEqual(width);
    }
    const legendBounds = (await page.locator('.map-observed-legend').boundingBox())!;
    const badgeBounds = (await badge.boundingBox())!;
    const attributionBounds = (await page.locator('#map-container .leaflet-control-attribution').boundingBox())!;
    expect.soft(legendBounds.y + legendBounds.height).toBeLessThanOrEqual(badgeBounds.y);
    expect.soft(badgeBounds.y + badgeBounds.height).toBeLessThanOrEqual(attributionBounds.y);
    await testInfo.attach('synthetic-observed-map', {
      body: await page.locator('#viz-section').screenshot(), contentType: 'image/png',
    });
    await page.locator('[data-layout="cross-section"]').click();
    await expect(page.locator('.map-observed-labels')).toHaveCount(0);
    await expect(page.locator('.map-observed-badge')).toHaveCount(0);
    await expect(page.locator('.map-observed-legend')).toHaveCount(0);
    state.assertHealthy();
  });
}
