import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FIXTURES = join(__dirname, 'fixtures', 'egtf_eglf');
const FUTURE_DATE = new Date(Date.now() + 3 * 86400_000).toISOString().slice(0, 10);
const FLIGHT_ID = `egtf_eglf-${FUTURE_DATE}-45ed`;
const TIMESTAMP = '2026-02-25T16:10:07.255073+00:00';

function fixture(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

/**
 * Intercept all API calls the briefing page makes and return fixture data.
 * This avoids hitting the real backend / external services.
 */
async function mockBriefingApi(page: import('@playwright/test').Page) {
  const enc = encodeURIComponent;

  // GET /api/flights/{id}
  await page.route(`**/api/flights/${FLIGHT_ID}`, route => {
    if (route.request().url().includes('/packs'))
      return route.fallthrough();            // let more-specific routes handle /packs/*
    const flight = { ...fixture('flight.json'), id: FLIGHT_ID, departure_time: `${FUTURE_DATE}T17:00:00+00:00`, target_date: FUTURE_DATE };
    return route.fulfill({ json: flight });
  });

  // GET /api/flights/{id}/packs
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, route => {
    // Only match the packs list, not sub-paths like /packs/{ts}/snapshot
    const url = route.request().url();
    const afterPacks = url.split('/packs')[1];
    if (afterPacks && afterPacks !== '' && afterPacks !== '/')
      return route.fallthrough();
    return route.fulfill({ json: fixture('packs.json') });
  });

  // GET /api/flights/{id}/packs/{ts}  (pack metadata)
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/${enc(TIMESTAMP)}`, route => {
    const url = route.request().url();
    const afterTs = url.split(enc(TIMESTAMP))[1];
    if (afterTs && afterTs !== '' && afterTs !== '/')
      return route.fallthrough();
    return route.fulfill({ json: fixture('pack_meta.json') });
  });

  // GET /api/flights/{id}/packs/{ts}/snapshot
  await page.route(`**/packs/${enc(TIMESTAMP)}/snapshot`, route =>
    route.fulfill({ json: fixture('snapshot.json') })
  );

  // GET /api/flights/{id}/packs/{ts}/route-analyses
  await page.route(`**/packs/${enc(TIMESTAMP)}/route-analyses`, route =>
    route.fulfill({ json: fixture('route_analyses.json') })
  );

  // GET /api/flights/{id}/packs/{ts}/advisories
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories`, route =>
    route.fulfill({ json: fixture('advisories.json') })
  );

  // GET /api/flights/{id}/packs/{ts}/elevation
  await page.route(`**/packs/${enc(TIMESTAMP)}/elevation`, route =>
    route.fulfill({ json: fixture('elevation.json') })
  );

  // GET /api/flights/{id}/packs/{ts}/digest/json
  await page.route(`**/packs/${enc(TIMESTAMP)}/digest/json`, route =>
    route.fulfill({ json: fixture('digest.json') })
  );

  // Gramet — not configured, return 404
  await page.route(`**/packs/${enc(TIMESTAMP)}/gramet**`, route =>
    route.fulfill({ status: 404, json: { detail: 'GRAMET not available' } })
  );

  // Freshness check — return a simple response so the page doesn't trigger refresh
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/freshness`, route =>
    route.fulfill({ json: { is_fresh: true, reason: 'fixture data' } })
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Briefing page', () => {

  test.beforeEach(async ({ page }) => {
    await mockBriefingApi(page);
  });

  test('loads and displays the briefing', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    // Assessment badge should show GREEN. Scope to the banner: the sidebar rail
    // now also renders the overall level (`.rail-overall-level`), so a bare
    // getByText('GREEN') matches two elements and trips strict mode.
    await expect(
      page.locator('#assessment-banner').getByText('GREEN', { exact: true }),
    ).toBeVisible();

    // Route summary should appear
    await expect(page.getByText('EGTF → EGLF')).toBeVisible();
  });

  test('displays digest sections', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    // The digest contains a synoptic overview and other sections.
    // Target the section heading specifically — in the sidebar layout (now the
    // default) the scroll-spy nav also renders a "Synopsis" rail label, so a
    // loose getByText would match two elements.
    await expect(page.getByRole('heading', { name: 'Synopsis' })).toBeVisible();
  });

  test('shows advisories', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    // Advisories section heading should render
    await expect(page.getByRole('heading', { name: 'Route Advisories' })).toBeVisible();
  });

  test('handles missing GRAMET gracefully', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    // Should show an autorouter prompt or "not available" message
    // rather than a broken image
    await expect(page.locator('img[src*="gramet"]')).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// Sidebar layout — now the default. These assert the shell that
// sidebar-layout.ts builds around the (unchanged) section rendering.
// ---------------------------------------------------------------------------

test.describe('Briefing sidebar layout', () => {

  test.beforeEach(async ({ page }) => {
    await mockBriefingApi(page);
  });

  test('is the default layout — builds the rail + main shell', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    // The two-column shell is built and the container is tagged.
    await expect(page.locator('.container.layout-sidebar')).toBeVisible();
    await expect(page.locator('.briefing-shell .briefing-rail')).toBeVisible();
    await expect(page.locator('.briefing-shell .briefing-main')).toBeVisible();

    // The scroll-spy SECTIONS nav is generated in the rail.
    await expect(page.locator('.rail-nav .rail-nav-title')).toHaveText('Sections');
  });

  test('rail surfaces the glance summary and a section nav item', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    // Overall assessment chip mirrors the main-pane assessment (GREEN fixture).
    // Auto-retrying assertion — the chip is (re)built by a MutationObserver once
    // the assessment banner has rendered.
    await expect(page.locator('.rail-summary .rail-overall')).toContainText('GREEN');

    // The Synopsis section has a derived nav entry that can focus/scroll to it.
    await expect(page.locator('.rail-nav-item[data-nav-key="synopsis"]')).toBeVisible();
  });

  test('focus mode isolates a section and can be exited', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);

    const shell = page.locator('.briefing-shell');
    await expect(shell).not.toHaveAttribute('data-focus', /.+/);

    // Click the focus glyph on the Synopsis nav item.
    await page.locator('.rail-nav-item[data-nav-key="synopsis"] .rail-nav-focus').click();
    await expect(shell).toHaveAttribute('data-focus', 'synopsis');
    await expect(page.locator('.rail-focus-bar')).toBeVisible();

    // Exiting focus clears the attribute and hides the bar.
    await page.locator('.rail-exit-focus').click();
    await expect(shell).not.toHaveAttribute('data-focus', /.+/);
    await expect(page.locator('.rail-focus-bar')).toBeHidden();
  });

  test('classic opt-out renders no sidebar shell', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}&layout=classic`);

    // Wait for the briefing to render, then assert the shell was never built.
    await expect(page.getByRole('heading', { name: 'Synopsis' })).toBeVisible();
    await expect(page.locator('.briefing-shell')).toHaveCount(0);
    await expect(page.locator('.container.layout-sidebar')).toHaveCount(0);

    // The toolbar offers a way back to the sidebar layout.
    await expect(page.locator('#layout-optin-btn')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Cross-section layer bar (#591). The unit tests cover the HTML composition;
// these cover the parts only a real browser can: that opening a family does
// not close on the re-render its own toggle triggers, that the hint slot
// actually fills, and that the panel stays two rows while you work in it.
// ---------------------------------------------------------------------------

test.describe('Cross-section layer bar', () => {

  /** Open the cross-section section — the controls are not rendered until it
   *  is expanded (the tour does the same dance in `ensureSectionExpanded`). */
  async function openCrossSection(page: import('@playwright/test').Page) {
    const section = page.locator('[data-section="cross-section"]');
    await expect(section).toBeVisible();
    if (await section.evaluate((el) => el.classList.contains('collapsed'))) {
      await section.locator('h3').first().click();
    }
    await expect(page.locator('.viz-layer-bar')).toBeVisible();
  }

  test.beforeEach(async ({ page }) => {
    await mockBriefingApi(page);
    // Compact is the DEFAULT display mode, and it deliberately has no detail
    // row — one on/off per family, method chosen for you. Everything below
    // except the compact case is about full mode, so seed it before the app
    // reads localStorage on boot.
    await page.addInitScript(() => localStorage.setItem('wb_displayMode', 'full'));
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    await openCrossSection(page);
  });

  test('rests as one row of families with no controls showing', async ({ page }) => {
    await expect(page.locator('.viz-family[data-family]').first()).toBeVisible();
    await expect(page.locator('.viz-layer-detail')).toHaveCount(0);
    await expect(page.locator('.viz-pill')).toHaveCount(0);
  });

  test('opens one family at a time, swapping rather than stacking', async ({ page }) => {
    const families = page.locator('.viz-family[data-family]');
    await families.first().click();
    await expect(page.locator('.viz-layer-detail')).toHaveCount(1);

    await families.nth(1).click();
    // The row is REPLACED, not added to — that is what holds the panel height.
    await expect(page.locator('.viz-layer-detail')).toHaveCount(1);

    await families.nth(1).click();
    await expect(page.locator('.viz-layer-detail')).toHaveCount(0);
  });

  test('stays open while you toggle, so you can compare against the chart', async ({ page }) => {
    // The regression this pins is the whole reason the open family is read back
    // off the DOM: toggling a layer re-renders the entire controls container,
    // and if the open row were not carried across it would slam shut on every
    // click — making DD-versus-NWP comparison impossible.
    await page.locator('.viz-family[data-family="clouds"]').click();
    const pills = page.locator('.viz-layer-detail .viz-pill:not([disabled])');
    const n = Math.min(3, await pills.count());
    expect(n).toBeGreaterThan(0);

    for (let i = 0; i < n; i++) {
      await pills.nth(i).click();
      await expect(page.locator('.viz-layer-detail[data-detail-family="clouds"]')).toBeVisible();
    }
  });

  test('None clears a group and the chip says so', async ({ page }) => {
    await page.locator('.viz-family[data-family="icing"]').click();
    const none = page.locator('.viz-layer-detail .viz-pill-none').first();
    await none.click();

    const group = page.locator('.viz-layer-detail .viz-pills').first();
    await expect(group.locator('.viz-pill[data-layer-id][aria-pressed="true"]')).toHaveCount(0);
    await expect(page.locator('.viz-family[data-family="icing"]')).toHaveClass(/is-off/);
  });

  test('the hint slot fills from whatever you point at', async ({ page }) => {
    // This is where twenty ⓘ glyphs went, so it has to actually work.
    await page.locator('.viz-family[data-family="icing"]').click();
    const hint = page.locator('.viz-hint-text');
    const atRest = await hint.textContent();

    await page.locator('.viz-layer-detail .viz-pill[data-layer-id]').first().hover();
    await expect(hint).not.toHaveText(atRest ?? '');
  });

  test('About opens a comparison and closes again', async ({ page }) => {
    await page.locator('.viz-family[data-family="icing"]').click();
    await page.locator('.viz-about-btn').click();

    const about = page.locator('.viz-family-about[data-about-family="icing"]');
    await expect(about).toBeVisible();
    // Cards come from the metrics catalog, and each links into the full entry.
    await expect(about.locator('.viz-about-card').first()).toBeVisible();
    await expect(about.locator('.viz-about-more').first()).toBeVisible();

    await about.locator('.viz-about-close').click();
    await expect(about).toHaveCount(0);
    // ...and the family it explained is still open underneath.
    await expect(page.locator('.viz-layer-detail[data-detail-family="icing"]')).toBeVisible();
  });

  test('compact mode is one on/off per family, with nothing to expand', async ({ page, context }) => {
    // The default a user actually lands on. Compact makes the method decision
    // for them, so a detail row here would be a bug rather than a feature.
    await context.clearCookies();
    await page.addInitScript(() => localStorage.setItem('wb_displayMode', 'compact'));
    await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
    await openCrossSection(page);

    const toggles = page.locator('.viz-family-toggle[data-family-toggle]');
    await expect(toggles.first()).toBeVisible();
    await expect(page.locator('.viz-family[data-family]')).toHaveCount(0);

    const first = toggles.first();
    const before = await first.getAttribute('aria-pressed');
    await first.click();
    await expect(first).not.toHaveAttribute('aria-pressed', before ?? '');
    // Still nothing to expand.
    await expect(page.locator('.viz-layer-detail')).toHaveCount(0);
  });
});
