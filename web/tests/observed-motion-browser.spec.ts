import { expect, test } from '@playwright/test';
import { observedMotionFixture, openObservedPage } from './fixtures/observed-browser';

async function enterMotionMode(page: import('@playwright/test').Page) {
  await page.locator('#observed-motion-toggle').click();
  await expect(page.locator('#observed-motion-panel')).toBeVisible();
}

test('BFCache pagehide/pageshow cycles restart exactly one expiry clock without timer requests', async ({ page }) => {
  const instrumentTimers = () => {
    const active = new Set<number>();
    (window as any).minuteIntervals = active;
    const set = window.setInterval.bind(window);
    const clear = window.clearInterval.bind(window);
    window.setInterval = ((handler: TimerHandler, delay?: number, ...args: any[]) => {
      const id = set(handler, delay, ...args);
      if (delay === 60_000) active.add(id);
      return id;
    }) as typeof window.setInterval;
    window.clearInterval = ((id?: number) => { active.delete(id!); clear(id); }) as typeof window.clearInterval;
  };
  const state = await openObservedPage(page, { beforeEntrypoint: `(${instrumentTimers.toString()})();` });
  await enterMotionMode(page);
  await expect.poll(() => state.snapshots).toBe(2);
  await page.locator('#observed-motion-time').selectOption('2026-02-25T17:05:00Z');
  const timers = await page.evaluate(() => (window as any).minuteIntervals.size);
  expect(timers).toBeGreaterThan(0);
  for (let cycle = 0; cycle < 3; cycle++) {
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
    expect(await page.evaluate(() => (window as any).minuteIntervals.size)).toBe(timers - 1);
    const reads = state.snapshots;
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    await expect.poll(() => state.snapshots).toBe(reads + 1);
    expect(await page.evaluate(() => (window as any).minuteIntervals.size)).toBe(timers);
  }
  const reads = state.snapshots;
  await page.clock.runFor(5 * 60_000 + 1_000);
  await expect(page.locator('#observed-motion-status')).toContainText('Expired selection');
  await expect(page.locator('.observed-motion-projection')).toHaveCount(0);
  await expect(page.locator('#observed-motion-time')).toHaveValue('2026-02-25T17:05:00Z');
  expect(state.snapshots).toBe(reads);
  state.assertHealthy();
});

test('known offline immediately stores selected motion and reconnect performs one authority read', async ({ page }) => {
  const state = await openObservedPage(page);
  await enterMotionMode(page);
  await expect.poll(() => state.snapshots).toBe(2);
  await page.locator('#observed-motion-time').selectOption('2026-02-25T17:05:00Z');
  await expect(page.locator('.observed-motion-projection:not(.observed-motion-stored)')).toHaveCount(1);
  const reads = state.snapshots;
  await page.evaluate(() => window.dispatchEvent(new Event('offline')));
  await expect(page.locator('#observed-motion-status')).toContainText('Stored analysis');
  await expect(page.locator('.observed-motion-projection.observed-motion-stored')).toHaveCount(1);
  await expect(page.locator('#observed-motion-time')).toHaveValue('2026-02-25T17:05:00Z');
  expect(state.snapshots).toBe(reads);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await expect.poll(() => state.snapshots).toBe(reads + 1);
  await expect(page.locator('.observed-motion-projection:not(.observed-motion-stored)')).toHaveCount(1);
  state.assertHealthy();
});

test('cards state absolute evaluated windows, numeric omissions and unknown totals for old analysis', async ({ page }) => {
  const motion = observedMotionFixture() as any;
  motion.features[0].planned_overlap.intervals = [];
  motion.completeness.push({ category: 'small_detections', status: 'partial', reason_codes: ['selection_limit'], considered_count: 9, emitted_count: 2, omitted_count: 7 });
  motion.completeness.push({ category: 'candidates', status: 'not_evaluated', reason_codes: ['not_evaluated'], considered_count: null, emitted_count: 0, omitted_count: null });
  const state = await openObservedPage(page, { motion });
  await enterMotionMode(page);
  await page.locator('[data-motion-feature-id="radar-feature-1"]').click();
  const detail = page.locator('#observed-motion-detail');
  await expect(detail).toContainText('7 reported detections; 1 map markers (partial/lower bound)');
  await expect(detail).toContainText('Evaluated lightning window: 2026-02-25 16:40Z–2026-02-25 16:50Z');
  await expect(detail).toContainText('Evaluated planned interval: 2026-02-25 17:00Z–2026-02-25 17:10Z');
  await expect(detail).toContainText('No overlap calculated for this tracked contour under this model');
  const scope = page.locator('.observed-motion-limitations');
  await expect(scope).toContainText('small detections (untracked): partial; considered 9, emitted 2, omitted 7');
  await expect(scope).toContainText('candidates: not evaluated; considered unknown, emitted 0, omitted unknown');
  await page.locator('[data-motion-feature-id="cloud-feature-1"]').click();
  await expect(detail).toContainText('Unavailable');
  await expect(detail).toContainText('invalid planned timing');
  await expect(detail).toContainText('Cloud top: 9000 m MSL');
  state.assertHealthy();
});

test('malformed positive overlap and unverified motion never produce negative overlap or ground claims on cards', async ({ page }) => {
  const motion = observedMotionFixture() as any;
  motion.features[0].planned_overlap.intervals[0].start_at = 'broken';
  motion.sources[2].geolocation = { status: 'unverified', reason_codes: ['geolocation_unverified'], evidence_id: null, method_version: null, applicability_id: null };
  motion.features[1].geolocation = motion.sources[2].geolocation;
  const state = await openObservedPage(page, { motion });
  await enterMotionMode(page);
  await page.locator('[data-motion-feature-id="radar-feature-1"]').click();
  await expect(page.locator('#observed-motion-overlap')).toContainText('invalid planned overlap');
  await expect(page.locator('#observed-motion-overlap')).not.toContainText('No overlap');
  await page.locator('[data-motion-feature-id="cloud-feature-1"]').click();
  await expect(page.locator('#observed-motion-detail')).toContainText('Motion unavailable');
  await expect(page.locator('#observed-motion-detail')).not.toContainText('Ground speed');
  await expect(page.locator('.observed-motion-footprint-cloud')).toHaveCount(1);
  state.assertHealthy();
});

test('real entrypoint enters motion mode with independent families, holes and accessible selection', async ({ page }) => {
  const state = await openObservedPage(page);
  expect(state.snapshots).toBe(1);
  await enterMotionMode(page);
  await expect.poll(() => state.snapshots).toBe(2);
  await expect(page.locator('#map-container .leaflet-image-layer')).toHaveCount(0);
  await expect(page.locator('[data-motion-family="radar_echo"]')).toBeChecked();
  await expect(page.locator('[data-motion-family="high_cloud_top"]')).toBeChecked();
  await expect(page.locator('.observed-motion-footprint-radar')).toHaveCount(1);
  await expect(page.locator('.observed-motion-footprint-cloud')).toHaveCount(1);
  await expect(page.locator('.observed-motion-trail-radar')).toHaveCount(1);
  await expect(page.locator('.observed-motion-trail-cloud')).toHaveCount(1);
  await expect(page.locator('.observed-motion-lightning')).toHaveCount(2);
  await expect(page.locator('.observed-motion-lightning-window')).toHaveCount(1);
  const radarPath = await page.locator('.observed-motion-footprint-radar').getAttribute('d');
  expect((radarPath?.match(/M/g) ?? []).length).toBeGreaterThanOrEqual(2);
  const automaticReads = state.snapshots;
  await page.locator('[data-motion-family="radar_echo"]').uncheck();
  await expect(page.locator('.observed-motion-footprint-radar')).toHaveCount(0);
  await expect(page.locator('.observed-motion-footprint-cloud')).toHaveCount(1);
  await page.locator('[data-motion-family="radar_echo"]').check();

  const radarCard = page.locator('[data-motion-feature-id="radar-feature-1"]');
  await radarCard.focus();
  await page.keyboard.press('Enter');
  await expect(radarCard).toBeFocused();
  await expect(radarCard).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#observed-motion-detail')).toContainText('Ground speed 22 kt');
  await expect(page.locator('#observed-motion-detail')).toContainText('Closure 8.4 kt');
  await expect(page.locator('#observed-motion-detail')).toContainText('7 reported detections');
  await expect(page.locator('#observed-motion-detail')).toContainText('selection limit');
  await expect(page.locator('#observed-motion-route-table')).toContainText('Planned overlap at this instant: No');
  await expect(page.locator('#observed-motion-overlap')).toContainText('2026-02-25 17:03Z–2026-02-25 17:05Z');

  await page.locator('[data-motion-association-id="association-1"]').click();
  await expect(page.locator('.observed-motion-footprint.observed-motion-selected')).toHaveCount(2);
  await expect(page.locator('#observed-motion-detail')).toContainText('Source-time association');
  expect(state.snapshots).toBe(automaticReads);
  expect(state.images).toEqual(['opera_dbzh']);
  state.assertHealthy();
});

test('server-authored UTC time selection makes no request and expires in place with the date shown', async ({ page }) => {
  const state = await openObservedPage(page);
  await enterMotionMode(page);
  await expect.poll(() => state.snapshots).toBe(2);
  const before = { snapshots: state.snapshots, images: [...state.images], flashes: state.flashes };
  const time = page.locator('#observed-motion-time');
  const flashPositions = await page.locator('.observed-motion-lightning').evaluateAll(elements => elements.map(element => element.getAttribute('d')));
  await time.focus();
  await time.selectOption('2026-02-25T17:05:00Z');
  await expect(time).toBeFocused();
  await expect(page.locator('#observed-motion-status')).toContainText('Experimental constant-motion projection');
  await expect(page.locator('#observed-motion-status')).toContainText('2026-02-25 17:05Z');
  await expect(page.locator('.observed-motion-projection')).toHaveCount(1);
  await expect(page.locator('.observed-motion-trail')).toHaveCount(2);
  expect(await page.locator('.observed-motion-lightning').evaluateAll(elements => elements.map(element => element.getAttribute('d')))).toEqual(flashPositions);
  await page.clock.runFor(5 * 60_000 + 1_000);
  await expect(time).toHaveValue('2026-02-25T17:05:00Z');
  await expect(time).toBeFocused();
  await expect(page.locator('#observed-motion-status')).toContainText('Expired selection');
  await expect(page.locator('.observed-motion-projection')).toHaveCount(0);
  expect({ snapshots: state.snapshots, images: state.images, flashes: state.flashes }).toEqual(before);
  state.assertHealthy();
});

test('capability revocation removes active vectors while retaining stored analysis and raster preference', async ({ page }) => {
  const state = await openObservedPage(page);
  await enterMotionMode(page);
  await page.locator('#observed-motion-time').selectOption('2026-02-25T17:05:00Z');
  await expect(page.locator('.observed-motion-projection')).toHaveCount(1);
  state.setCapability(false);
  const observations = page.locator('[data-section="observations"]');
  if (await observations.evaluate(el => el.classList.contains('collapsed'))) await observations.locator('h3').first().click();
  await page.locator('.obs-refresh-btn').click();
  await expect(page.locator('#observed-motion-status')).toContainText('Stored analysis');
  await expect(page.locator('#observed-motion-status')).toContainText('server capability is disabled');
  await expect(page.locator('.observed-motion-projection')).toHaveCount(0);
  await page.locator('#observed-motion-toggle').click();
  await expect(page.locator('#map-observed-overlay')).toHaveValue('opera_dbzh');
  await expect(page.locator('#map-container .leaflet-image-layer')).toHaveCount(1);
  state.assertHealthy();
});

test('enabled lifecycle authority without a motion block leaves a retained projection stored', async ({ page }) => {
  const state = await openObservedPage(page);
  await enterMotionMode(page);
  await page.locator('#observed-motion-time').selectOption('2026-02-25T17:05:00Z');
  await expect(page.locator('.observed-motion-projection')).toHaveCount(1);
  state.omitMotionFromSnapshots();
  const before = state.snapshots;
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow')));
  await expect.poll(() => state.snapshots).toBe(before + 1);

  await expect(page.locator('#observed-motion-status')).toContainText('Stored analysis');
  await expect(page.locator('.observed-motion-projection.observed-motion-stored')).toHaveCount(1);
  state.assertHealthy();
});

test('a failed observations refresh leaves an already selected projection stored', async ({ page }) => {
  const state = await openObservedPage(page);
  await enterMotionMode(page);
  await page.locator('#observed-motion-time').selectOption('2026-02-25T17:05:00Z');
  await expect(page.locator('.observed-motion-projection')).toHaveCount(1);
  state.failRefresh = true;
  const observations = page.locator('[data-section="observations"]');
  if (await observations.evaluate(el => el.classList.contains('collapsed'))) await observations.locator('h3').first().click();
  await page.locator('.obs-refresh-btn').click();

  await expect(page.locator('#observed-motion-status')).toContainText('Refresh failed');
  await expect(page.locator('.observed-motion-projection.observed-motion-stored')).toHaveCount(1);
  state.assertHealthy();
});

test('foreground return rechecks authority and expires the selected time without advancing it', async ({ page }) => {
  const state = await openObservedPage(page);
  await enterMotionMode(page);
  await page.locator('#observed-motion-time').selectOption('2026-02-25T17:05:00Z');
  await expect(page.locator('.observed-motion-projection')).toHaveCount(1);
  const before = state.snapshots;
  await page.clock.setFixedTime(new Date('2026-02-25T17:06:00Z'));
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow')));
  await expect.poll(() => state.snapshots).toBe(before + 1);
  await expect(page.locator('#observed-motion-time')).toHaveValue('2026-02-25T17:05:00Z');
  await expect(page.locator('#observed-motion-status')).toContainText('Expired selection');
  await expect(page.locator('.observed-motion-projection')).toHaveCount(0);
  state.assertHealthy();
});

test('failed lifecycle and refresh requests preserve only dated stored analysis', async ({ page }) => {
  const state = await openObservedPage(page);
  state.failSnapshots = true;
  await enterMotionMode(page);
  await expect(page.locator('#observed-motion-status')).toContainText('Stored analysis');
  await expect(page.locator('#observed-motion-status')).toContainText('capability unavailable');
  state.failRefresh = true;
  const observations = page.locator('[data-section="observations"]');
  if (await observations.evaluate(el => el.classList.contains('collapsed'))) await observations.locator('h3').first().click();
  await page.locator('.obs-refresh-btn').click();
  await expect(page.locator('#observed-motion-status')).toContainText('Refresh failed');
  await expect(page.locator('[data-motion-feature-id="radar-feature-1"]')).toBeVisible();
  state.assertHealthy();
});

test('motion panel stays usable in narrow layout and its independent layer is torn down', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  const state = await openObservedPage(page);
  await enterMotionMode(page);
  const panel = page.locator('#observed-motion-panel');
  const bounds = await panel.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320);
  await page.locator('[data-layout="cross-section"]').click();
  await expect(page.locator('.observed-motion-footprint-radar')).toHaveCount(0);
  await expect(page.locator('.observed-motion-footprint-cloud')).toHaveCount(0);
  await page.locator('[data-layout="map"]').click();
  await expect(page.locator('.observed-motion-footprint-radar')).toHaveCount(1);
  state.assertHealthy();
});
