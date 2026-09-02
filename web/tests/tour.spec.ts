import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// Regression tests for the briefing tour's cross-section steps. The viz layout
// (Compare/Split/Map) is interactive during the tour and persisted to
// localStorage, so the layout-dependent steps must restore the cross-section
// view when resolving their target element — otherwise they highlight nothing.

const FIXTURES = join(__dirname, 'fixtures', 'egtf_eglf');
const FUTURE_DATE = new Date(Date.now() + 3 * 86400_000).toISOString().slice(0, 10);
const FLIGHT_ID = `egtf_eglf-${FUTURE_DATE}-45ed`;
const TIMESTAMP = '2026-02-25T16:10:07.255073+00:00';

function fixture(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

async function mockBriefingApi(page: import('@playwright/test').Page) {
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
  await page.route(`**/packs/${enc(TIMESTAMP)}/snapshot`, route => route.fulfill({ json: fixture('snapshot.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/route-analyses`, route => route.fulfill({ json: fixture('route_analyses.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories`, route => route.fulfill({ json: fixture('advisories.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/elevation`, route => route.fulfill({ json: fixture('elevation.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/digest/json`, route => route.fulfill({ json: fixture('digest.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/gramet**`, route => route.fulfill({ status: 404, json: { detail: 'no' } }));
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/freshness`, route => route.fulfill({ json: { is_fresh: true, reason: 'fixture' } }));
}

/** Click the tour's Next button until the popover shows `title`. Waits past the
 *  400ms driver animation between clicks so Next presses aren't dropped. */
async function advanceTo(page: import('@playwright/test').Page, title: string) {
  for (let i = 0; i < 12; i++) {
    const current = await page.locator('.driver-popover-title').textContent();
    if (current === title) return;
    await page.locator('.driver-popover-next-btn').click();
    await page.waitForTimeout(550);
  }
  throw new Error(`tour never reached step "${title}"`);
}

function activeElementInfo(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const el = document.querySelector('.driver-active-element');
    return el ? {
      tag: el.tagName,
      id: (el as HTMLElement).id,
      cls: el.className,
      about: el.getAttribute('data-family-about'),
    } : null;
  });
}


/** Vertical gap between driver.js's cutout and the control it should be over.
 *
 *  The overlay path is a full-screen rect followed by the hole: the SECOND
 *  `M x,y` is the cutout's top-left (offset by driver's stagePadding). Reading
 *  that specific coordinate matters — an earlier version of these tests looked
 *  for "any number in the path near the element's top", which the rounded-corner
 *  arcs satisfy by coincidence, so it passed with the fix disabled and proved
 *  nothing. */
async function cutoutGap(page: import('@playwright/test').Page, id: string) {
  return page.evaluate((elId) => {
    const el = document.getElementById(elId);
    const d = document.querySelector('.driver-overlay path')?.getAttribute('d') ?? '';
    const holes = [...d.matchAll(/M\s*(-?[\d.]+),\s*(-?[\d.]+)/g)];
    if (!el || holes.length < 2) return null;
    const cutoutTop = Number(holes[1][2]);
    const top = el.getBoundingClientRect().top;
    // driver's default stagePadding is 10, so the hole sits slightly above.
    return { gap: Math.abs((top - cutoutTop) - 10), top, cutoutTop };
  }, id);
}

test.describe('Briefing tour — cross-section steps', () => {
  test.beforeEach(async ({ page }) => {
    await mockBriefingApi(page);
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    await expect(page.getByText('GREEN', { exact: true })).toBeVisible();
    await page.locator('#tour-btn').click();
    await page.waitForTimeout(550);
  });

  test('help step opens a family and highlights its About button', async ({ page }) => {
    // Switch to Compare on the model/theme step (its toolbar holds the toggle).
    await advanceTo(page, 'Compare models, themes & Windy');
    await page.locator('[data-layout="compare"]').first().click({ force: true });
    await page.waitForTimeout(400);
    expect(await page.locator('.viz-about-btn').count()).toBe(0); // compare hides the bar

    // The per-layer ⓘ buttons are gone (#591): help is one "About …" button per
    // family, living in the detail row — which is CLOSED at rest. The step's
    // element resolver has to open a family first, or it highlights nothing.
    await advanceTo(page, 'What each method is');
    const active = await activeElementInfo(page);
    expect(active?.id).not.toBe('driver-dummy-element');
    expect(active?.about).toBeTruthy();
  });

  test('layers step highlights toggles after switching to Compare', async ({ page }) => {
    await advanceTo(page, 'Cross-section');
    await page.locator('[data-layout="compare"]').first().click({ force: true });
    await page.waitForTimeout(400);

    await advanceTo(page, 'Toggle layers');
    const active = await activeElementInfo(page);
    expect(active?.cls).toContain('viz-layer-toggles');
  });

  test('cutout follows the control when a banner pushes the page down', async ({ page }) => {
    // driver.js recomputes its cutout on resize and scroll only. A banner
    // inserted ABOVE the highlighted control (the stale-pack "Updates
    // available" bar arrives asynchronously) shifts the control without either
    // event, leaving the hole over blank space — the control looks like it
    // vanished. Reproduced deterministically here rather than racing the
    // freshness fixture.
    await advanceTo(page, 'Compact vs. full details');

    const shift = await page.evaluate(() => {
      const el = document.getElementById('display-mode-toggle')!;
      const before = el.getBoundingClientRect().top;
      const banner = document.createElement('div');
      banner.id = 'test-layout-shim';
      banner.style.height = '80px';
      document.querySelector('.briefing-container, body')!.prepend(banner);
      return before;
    });
    expect(shift).toBeGreaterThan(0);
    await page.waitForTimeout(400);

    const aligned = await cutoutGap(page, 'display-mode-toggle');
    expect(aligned, 'no cutout found').not.toBeNull();
    expect(
      aligned!.gap,
      `cutout left behind: control at y=${aligned!.top}, hole at y=${aligned!.cutoutTop}`,
    ).toBeLessThan(6);
  });

  test('cutout follows the control when the rail scrolls under it', async ({ page }) => {
    // The case the first fix missed, and the one actually hit in the browser.
    // `.briefing-rail` holds the display-mode toggle and is its own scroll
    // container (position: sticky, overflow-y: auto, capped max-height). Scroll
    // events do not bubble, so driver.js's window listener never hears this,
    // and nothing resizes — the rail's box is capped, body's is unchanged, and
    // the element's own box is identical. Only its POSITION moves.
    await advanceTo(page, 'Compact vs. full details');

    const moved = await page.evaluate(async () => {
      const rail = document.querySelector<HTMLElement>('.briefing-rail');
      const el = document.getElementById('display-mode-toggle')!;
      if (!rail) return { skipped: true, before: 0, after: 0 };
      const before = el.getBoundingClientRect().top;
      rail.scrollTop = rail.scrollTop + 120;
      await new Promise((r) => setTimeout(r, 300));
      return { skipped: false, before, after: el.getBoundingClientRect().top };
    });

    test.skip(moved.skipped, 'sidebar rail not present at this viewport');
    // Guard the test itself: if the rail could not scroll, it proves nothing.
    expect(Math.abs(moved.after - moved.before)).toBeGreaterThan(10);

    const aligned = await cutoutGap(page, 'display-mode-toggle');
    expect(aligned, 'no cutout found').not.toBeNull();
    expect(
      aligned!.gap,
      `cutout left behind: control at y=${aligned!.top}, hole at y=${aligned!.cutoutTop}`,
    ).toBeLessThan(6);
  });

  /** Re-enter the briefing in FULL display mode and restart the tour.
   *
   *  Compact is the default, and it has no detail row by design — one on/off
   *  per family, method chosen for you. The two tests below are about the
   *  detail row surviving a re-render, which only exists in full. */
  async function restartInFullMode(page: import('@playwright/test').Page) {
    await page.addInitScript(() => localStorage.setItem('wb_displayMode', 'full'));
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    await expect(page.getByText('GREEN', { exact: true })).toBeVisible();
    await page.locator('#tour-btn').click();
    await page.waitForTimeout(550);
  }

  test('layers step survives opening a family', async ({ page }) => {
    await restartInFullMode(page);
    // Opening a family swaps the bar's whole subtree and changes NO store
    // state, so the store subscription the tour used to rely on never fires.
    // Without the re-render event the cutout latches onto a detached node and
    // every later click lands on the dimmed overlay.
    await advanceTo(page, 'Toggle layers');
    await page.locator('.viz-family[data-family]').first().click({ force: true });
    await page.waitForTimeout(450);
    const active = await activeElementInfo(page);
    expect(active?.cls).toContain('viz-layer-toggles');
    await expect(page.locator('.viz-layer-detail')).toBeVisible();
  });

  test('layers step stays highlighted across multiple pill toggles', async ({ page }) => {
    await restartInFullMode(page);
    await advanceTo(page, 'Toggle layers');
    await page.locator('.viz-family[data-family]').first().click({ force: true });
    await page.waitForTimeout(450);

    const pills = page.locator('.viz-layer-detail .viz-pill[data-layer-id]:not([disabled])');
    const n = Math.min(3, await pills.count());
    expect(n).toBeGreaterThan(0);
    for (let j = 0; j < n; j++) {
      await pills.nth(j).click({ force: true });
      await page.waitForTimeout(450);
      const active = await activeElementInfo(page);
      expect(active?.cls).toContain('viz-layer-toggles');
    }
  });
});
