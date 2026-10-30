// Screenshots of one flight's briefing page, for docs and talks. Driven by
// scripts/capture/web.py, which mints the token (FLYFUN_CAPTURE_TOKEN, an env
// var so it stays out of `ps`) and checks the flight is in the dev DB; this
// file only drives the browser. See designs/flight-captures.md.
//
//   node scripts/capture-flight.mjs --base URL --flight ID --out DIR \
//        [--screens a,b] [--width 1440] [--height 900] [--scale 2] [--appearance light|dark]
//
// Writes DIR/<screen>.png for each screen it found; web.py numbers them.
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { parseArgs } from 'node:util';
import { chromium } from '@playwright/test';

const { values: opts } = parseArgs({
  options: {
    base: { type: 'string' },
    flight: { type: 'string' },
    out: { type: 'string' },
    screens: { type: 'string', default: '' },
    width: { type: 'string', default: '1440' },
    height: { type: 'string', default: '900' },
    scale: { type: 'string', default: '2' },
    appearance: { type: 'string', default: 'light' },
  },
});
const token = process.env.FLYFUN_CAPTURE_TOKEN;
if (!opts.base || !opts.flight || !opts.out || !token) {
  console.error('usage: --base URL --flight ID --out DIR, with FLYFUN_CAPTURE_TOKEN set');
  process.exit(2);
}
const wanted = new Set(opts.screens.split(',').filter(Boolean));
const want = (screen) => wanted.size === 0 || wanted.has(screen);

// Briefing-page sections per shared screen name (data-section on web/briefing.html).
// cross-section and map share #viz-section, switched by its layout toggle.
const SECTIONS = {
  advisories: '[data-section="advisories"]',
  discussion: '[data-section="synopsis"]',
  skewt: '[data-section="skewt"]',
  observed: '[data-section="observed-glance"]',
};
const RENDER_SETTLE_MS = 2000;
const MAP_SETTLE_MS = 5000;

mkdirSync(opts.out, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: Number(opts.width), height: Number(opts.height) },
  deviceScaleFactor: Number(opts.scale),
  colorScheme: opts.appearance === 'dark' ? 'dark' : 'light',
});
await context.addCookies([{
  name: 'flyfun_auth', value: token, url: opts.base, httpOnly: true, secure: opts.base.startsWith('https'),
}]);
// A first-time visitor is offered the guided tours: mark them offered
// (ts/tour/tour-storage.ts keys) so no banner sits over the briefing.
await context.addInitScript(() => {
  try {
    localStorage.setItem('wb_tour_offered', '1');
    localStorage.setItem('wb_tour_offered_flights', '1');
  } catch { /* storage blocked: the banner just shows */ }
});
const page = await context.newPage();
const failures = [];
const captured = [];

async function step(screen, fn) {
  try {
    await fn();
    captured.push(screen);
  } catch (e) {
    failures.push(`${screen}: ${String(e.message ?? e).split('\n')[0]}`);
  }
}

/** A section of the open briefing: shown, expanded, scrolled to, then shot alone. */
async function shootSection(screen, selector, settle = RENDER_SETTLE_MS) {
  const el = page.locator(selector).first();
  await el.waitFor({ state: 'visible', timeout: 20_000 });
  await el.evaluate((node) => node.classList.remove('collapsed'));
  await el.scrollIntoViewIfNeeded();
  await page.waitForTimeout(settle);
  await el.screenshot({ path: join(opts.out, `${screen}.png`) });
}

if (want('flights')) {
  await step('flights', async () => {
    await page.goto(`${opts.base}/index.html`, { waitUntil: 'networkidle' });
    await page.locator('#flight-list > *').first().waitFor({ timeout: 20_000 });
    await page.waitForTimeout(RENDER_SETTLE_MS);
    await page.screenshot({ path: join(opts.out, 'flights.png') });
  });
}

await page.goto(`${opts.base}/briefing.html?flight=${encodeURIComponent(opts.flight)}`, { waitUntil: 'networkidle' });
await page.waitForFunction(() => {
  const banner = document.getElementById('assessment-banner');
  return banner && !/Loading/.test(banner.textContent ?? '');
}, null, { timeout: 30_000 });

if (want('briefing')) {
  await step('briefing', async () => {
    await page.waitForTimeout(RENDER_SETTLE_MS);
    await page.screenshot({ path: join(opts.out, 'briefing.png') });
  });
}
for (const screen of ['advisories', 'observed', 'discussion']) {
  if (!want(screen)) continue;
  // Observed exists only on flight day: a hidden section is a skip, not a failure.
  if (screen === 'observed' && !(await page.locator(SECTIONS.observed).first().isVisible())) continue;
  await step(screen, () => shootSection(screen, SECTIONS[screen]));
}
for (const [screen, layout, settle] of [['cross-section', 'cross-section', RENDER_SETTLE_MS], ['map', 'map', MAP_SETTLE_MS]]) {
  if (!want(screen)) continue;
  await step(screen, async () => {
    await page.locator('#viz-section').waitFor({ state: 'visible', timeout: 20_000 });
    await page.locator(`[data-layout="${layout}"]`).first().click();
    await shootSection(screen, '#viz-section', settle);
  });
}
if (want('skewt')) await step('skewt', () => shootSection('skewt', SECTIONS.skewt));

await browser.close();
console.log(`captured ${captured.join(', ') || 'nothing'}`);
if (failures.length) {
  console.error(`screens not captured: ${failures.join('; ')}`);
  process.exit(1);
}
