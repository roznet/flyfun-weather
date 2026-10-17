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

/** Click the tour's Next button until the popover shows `title`. Under reduced
 *  motion driver.js swaps the popover inside the click, but ignores a Next that
 *  lands in the one-frame transition — so press again if the title didn't move. */
async function advanceTo(page: import('@playwright/test').Page, title: string) {
  const heading = page.locator('.driver-popover-title');
  for (let i = 0; i < 12; i++) {
    const current = await heading.textContent();
    if (current === title) return;
    await expect(async () => {
      await page.locator('.driver-popover-next-btn').click();
      await expect(heading).not.toHaveText(current ?? '', { timeout: 500 });
    }).toPass({ intervals: [0], timeout: 5000 });
  }
  throw new Error(`tour never reached step "${title}"`);
}

/** The tour's re-highlight after a re-render lands a frame or two later. */
async function expectActiveClass(page: import('@playwright/test').Page, cls: string) {
  await expect.poll(async () => (await activeElementInfo(page))?.cls ?? '').toContain(cls);
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



test.describe('Briefing tour — cross-section steps', () => {
  // The tours drop driver.js's 400 ms highlight animation under reduced motion,
  // so steps can be driven without waiting it out.
  test.use({ contextOptions: { reducedMotion: 'reduce' } });

  test.beforeEach(async ({ page }) => {
    await mockBriefingApi(page);
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    await expect(page.locator('#assessment-banner').getByText('GREEN', { exact: true })).toBeVisible();
    await page.locator('#tour-btn').click();
    await expect(page.locator('.driver-popover-title')).toBeVisible();
  });

  test('help step opens a family and highlights its About button', async ({ page }) => {
    // Switch to Compare on the model/theme step (its toolbar holds the toggle).
    await advanceTo(page, 'Compare models, themes & Windy');
    await page.locator('[data-layout="compare"]').first().click({ force: true });
    await expect(page.locator('.viz-about-btn')).toHaveCount(0); // compare hides the bar

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
    await expect(page.locator('.viz-about-btn')).toHaveCount(0);

    await advanceTo(page, 'Toggle layers');
    await expectActiveClass(page, 'viz-layer-toggles');
  });



  test('highlighting a rail control does not collapse it', async ({ page }) => {
    // The real bug, and the reason it looked intermittent.
    //
    // driver.js sets `overflow: hidden !important` on the highlighted
    // element's parent (`:has(> .driver-active-element)`). A flex item's
    // `min-height: auto` only resolves to the content size while `overflow` is
    // `visible` — any other value makes the automatic minimum 0. `.briefing-rail`
    // is a flex column, so the moment the tour highlighted the Compact/Full
    // Details toggle its parent `.rail-controls` became shrinkable and an
    // overflowing rail squeezed it to 0px, clipping the toggle out of sight.
    //
    // The precondition is an OVERFLOWING rail, which is why it never showed up
    // in these tests before: the fixture's rail is short. Force it with a small
    // viewport.
    await page.setViewportSize({ width: 1280, height: 500 });

    // Guard the test itself: with a roomy rail it would pass regardless.
    await expect.poll(() => page.evaluate(() => {
      const rail = document.querySelector('.briefing-rail');
      return !!rail && rail.scrollHeight > rail.clientHeight;
    }), { message: 'rail is not overflowing — test proves nothing' }).toBe(true);

    await advanceTo(page, 'Compact vs. full details');

    const state = await page.evaluate(() => {
      const el = document.getElementById('display-mode-toggle')!;
      const par = el.parentElement!;
      return {
        active: el.classList.contains('driver-active-element'),
        parentH: Math.round(par.getBoundingClientRect().height),
        elH: Math.round(el.getBoundingClientRect().height),
      };
    });
    expect(state.active, 'toggle is not the highlighted element').toBe(true);
    expect(state.elH).toBeGreaterThan(0);
    expect(
      state.parentH,
      `.rail-controls collapsed to ${state.parentH}px, clipping the toggle away`,
    ).toBeGreaterThanOrEqual(state.elH);
  });

  /** Re-enter the briefing in FULL display mode and restart the tour.
   *
   *  Compact is the default, and it has no detail row by design — one on/off
   *  per family, method chosen for you. The two tests below are about the
   *  detail row surviving a re-render, which only exists in full. */
  async function restartInFullMode(page: import('@playwright/test').Page) {
    await page.addInitScript(() => localStorage.setItem('wb_displayMode', 'full'));
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    await expect(page.locator('#assessment-banner').getByText('GREEN', { exact: true })).toBeVisible();
    await page.locator('#tour-btn').click();
    await expect(page.locator('.driver-popover-title')).toBeVisible();
  }

  test('layers step survives opening a family', async ({ page }) => {
    await restartInFullMode(page);
    // Opening a family swaps the bar's whole subtree and changes NO store
    // state, so the store subscription the tour used to rely on never fires.
    // Without the re-render event the cutout latches onto a detached node and
    // every later click lands on the dimmed overlay.
    await advanceTo(page, 'Toggle layers');
    await page.locator('.viz-family[data-family]').first().click({ force: true });
    await expect(page.locator('.viz-layer-detail')).toBeVisible();
    await expectActiveClass(page, 'viz-layer-toggles');
  });

  test('layers step stays highlighted across multiple pill toggles', async ({ page }) => {
    await restartInFullMode(page);
    await advanceTo(page, 'Toggle layers');
    await page.locator('.viz-family[data-family]').first().click({ force: true });
    await expect(page.locator('.viz-layer-detail')).toBeVisible();

    const pills = page.locator('.viz-layer-detail .viz-pill[data-layer-id]:not([disabled])');
    const n = Math.min(3, await pills.count());
    expect(n).toBeGreaterThan(0);
    for (let j = 0; j < n; j++) {
      // The toggle rebuilds the bar: mark the current node so the check below
      // only passes once the highlight sits on its replacement.
      await page.locator('.viz-layer-toggles').evaluate(el => { el.dataset.stale = '1'; });
      await pills.nth(j).click({ force: true });
      await expect(page.locator('.viz-layer-toggles:not([data-stale])')).toHaveClass(/driver-active-element/);
    }
  });
});
