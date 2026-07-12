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
const SECOND_TIMESTAMP = '2026-02-25T17:10:07.255073+00:00';

function fixture(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

function routeFrontsFixture() {
  const crossing = {
    lat: 51.31,
    lon: -0.66,
    distance_km: 8,
    gradient: 4.1,
    neg_laplacian: 0.9,
    advection: 0.3,
    tfp_before: -0.4,
    tfp_after: 0.5,
    delta_theta_e: 5.2,
    kind: 'warm',
    intensity: 'classical',
    co_location: 'wet',
    weather_top_ft: 9000,
    persistence: 0.8,
    vertical_levels: 2,
  };
  const analysis = (model: string) => ({
    model,
    level_hPa: 850,
    hour: 0,
    crossings: model === 'ecmwf' ? [crossing] : [],
    nearest: model === 'ecmwf'
      ? { ...crossing, on_track: true, trend: 'steady' }
      : null,
  });
  return {
    schema_version: 1,
    route_name: 'egtf_eglf',
    generated_at: '2026-07-10T09:00:00Z',
    primary_level_hPa: 850,
    per_model_primary_hPa: { ecmwf: 850, gfs: 850 },
    levels: [850],
    gate_config: { name: 'default' },
    models: ['ecmwf', 'gfs'],
    per_model: { ecmwf: [analysis('ecmwf')], gfs: [analysis('gfs')] },
    front_chains: {},
    models_without_snapshot: [],
    snapshot_inits: {
      ecmwf: '2026-07-10T00:00:00Z',
      gfs: '2026-07-10T00:00:00Z',
    },
    notes: [],
  };
}

function frontAxisFixture(model = 'ecmwf') {
  return {
    model,
    init_time: '2026-07-10T00:00:00Z',
    valid_time: '2026-07-10T00:00:00Z',
    level: 850,
    hour: 0,
    stride_hours: 3,
    gate: 'default',
    gate_config: {},
    fronts: [{
      kind: 'cold',
      length_km: 180,
      mean_gradient: 4,
      mean_delta_theta_e: 5,
      coordinates: [[-0.72, 51.31], [-1.65, 51.31]],
    }],
  };
}

interface MockBriefingRefs {
  advisories: { current: any };
  routeFronts: { current: any | null };
  airportRequests: URL[];
  flightOverrides?: Record<string, unknown>;
  packOverrides?: Record<string, unknown>;
  altAdvisories?: any;
}

/**
 * Intercept all API calls the briefing page makes and return fixture data.
 * This avoids hitting the real backend / external services.
 */
async function mockBriefingApi(
  page: import('@playwright/test').Page,
  refs: MockBriefingRefs = {
    advisories: { current: fixture('advisories.json') },
    routeFronts: { current: routeFrontsFixture() },
    airportRequests: [],
  },
): Promise<MockBriefingRefs> {
  const enc = encodeURIComponent;

  // GET /api/flights/{id}
  await page.route(`**/api/flights/${FLIGHT_ID}`, route => {
    if (route.request().url().includes('/packs'))
      return route.fallthrough();            // let more-specific routes handle /packs/*
    const flight = {
      ...fixture('flight.json'),
      id: FLIGHT_ID,
      departure_time: `${FUTURE_DATE}T17:00:00+00:00`,
      target_date: FUTURE_DATE,
      ...refs.flightOverrides,
    };
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
    return route.fulfill({
      json: { ...fixture('pack_meta.json'), ...refs.packOverrides },
    });
  });

  // GET /api/flights/{id}/packs/{ts}/snapshot
  await page.route(`**/packs/${enc(TIMESTAMP)}/snapshot`, route =>
    route.fulfill({ json: fixture('snapshot.json') })
  );

  // GET /api/flights/{id}/packs/{ts}/route-analyses
  await page.route(`**/packs/${enc(TIMESTAMP)}/route-analyses`, route => {
    const analyses = clone(fixture('route_analyses.json'));
    // The representative action fixture exercises native DD-vs-NWP cloud
    // comparison. Mark both route models' native source as available (an empty
    // array means "model says clear", while null means the source is absent).
    for (const point of analyses.analyses) {
      for (const model of ['ecmwf', 'gfs']) {
        if (point.sounding?.[model]) point.sounding[model].nwp_cloud_layers = [];
      }
    }
    return route.fulfill({ json: analyses });
  });

  // GET /api/flights/{id}/packs/{ts}/advisories
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories`, route =>
    route.fulfill({ json: refs.advisories.current })
  );

  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories/alt`, route => (
    refs.altAdvisories
      ? route.fulfill({ json: refs.altAdvisories })
      : route.fulfill({ status: 404, json: { detail: 'Alternate advisories unavailable' } })
  ));

  // GET /api/flights/{id}/packs/{ts}/route-fronts
  await page.route(`**/packs/${enc(TIMESTAMP)}/route-fronts`, route => {
    if (!refs.routeFronts.current) {
      return route.fulfill({ status: 404, json: { detail: 'Fronts unavailable' } });
    }
    return route.fulfill({ json: refs.routeFronts.current });
  });

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

  await page.route('**/api/user/profiles', route => route.fulfill({ json: [] }));
  await page.route('**/api/user/preferences/advisories/catalog', route =>
    route.fulfill({ json: refs.advisories.current.catalog })
  );
  await page.route('**/api/user/preferences', route => route.fulfill({ json: {
    defaults: { cruise_altitude_ft: null, flight_ceiling_ft: null, models: null, advisory_models: null },
    digest_config: { config_name: null },
    advisories: { enabled: null, params: null, aggregation: 'majority' },
    has_autorouter_creds: false,
    autorouter_mode: 'oauth',
    gramet_enabled: false,
    llm_digest_enabled: true,
    icing_severity_enhance: false,
    icing_method: 'ogimet_nwp',
    cloud_method: 'nwp',
    convective_method: 'nwp',
    locale: 'en',
    units_region: 'uk',
    display_currency: 'GBP',
    synoptic_forecast_map_enabled: false,
    defer_email_for_model_update: false,
    pirep_can_view: false,
    pirep_can_publish: false,
    donations_enabled: false,
  } }));
  await page.route('**/api/hewson-map/fronts**', route =>
    route.fulfill({ json: { fronts: [] } })
  );

  // Finite SSE stream for the briefing airport-profile drawer.
  await page.route('**/api/maps/airport-profile?**', route => {
    const url = new URL(route.request().url());
    refs.airportRequests.push(url);
    const startHour = url.searchParams.get('start_hour')
      ?? '2026-07-10T10:00:00Z';
    const windowH = Number(url.searchParams.get('window_h') ?? '3');
    const meta = {
      icao: url.searchParams.get('icao') ?? 'EGTF',
      lat: 51.348099,
      lon: -0.558889,
      elevation_ft: 80,
      model: url.searchParams.get('model') ?? 'ecmwf',
      start_hour: startHour,
      window_h: windowH,
      hours: Array.from({ length: windowH + 1 }, (_, hour) => (
        new Date(Date.parse(startHour) + hour * 3_600_000).toISOString()
      )),
    };
    const body = `event: meta\ndata: ${JSON.stringify(meta)}\n\n`
      + `event: complete\ndata: {}\n\n`;
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      headers: { 'cache-control': 'no-cache' },
      body,
    });
  });

  return refs;
}

async function useFullBriefingMode(
  page: import('@playwright/test').Page,
  vizSettings?: Record<string, unknown>,
): Promise<void> {
  await page.addInitScript((settings) => {
    localStorage.setItem('wb_displayMode', 'full');
    localStorage.setItem('wb_selectedModel', 'gfs');
    if (settings) localStorage.setItem('wb_vizSettings', JSON.stringify(settings));
    else localStorage.removeItem('wb_vizSettings');
  }, vizSettings ?? null);
}

async function openBriefing(page: import('@playwright/test').Page): Promise<void> {
  await page.goto(`/briefing.html?flight=${FLIGHT_ID}`);
  await expect(page.getByRole('heading', { name: 'Route Advisories' })).toBeVisible();
  await expect(page.locator('[data-advisory="cloud_top"]')).toBeVisible();
}

async function instrumentCrossSectionAlphaWrites(
  page: import('@playwright/test').Page,
): Promise<void> {
  await page.addInitScript(() => {
    const descriptor = Object.getOwnPropertyDescriptor(
      CanvasRenderingContext2D.prototype,
      'globalAlpha',
    );
    const writes: number[] = [];
    (window as any).__crossSectionAlphaWrites = writes;
    if (!descriptor?.get || !descriptor.set) return;
    Object.defineProperty(CanvasRenderingContext2D.prototype, 'globalAlpha', {
      configurable: descriptor.configurable,
      enumerable: descriptor.enumerable,
      get(this: CanvasRenderingContext2D) {
        return descriptor.get!.call(this);
      },
      set(this: CanvasRenderingContext2D, value: number) {
        if (
          Math.abs(value - 0.22) < Number.EPSILON
          && this.canvas.closest('#viz-canvas-container')
        ) {
          writes.push(value);
        }
        descriptor.set!.call(this, value);
      },
    });
  });
}

async function crossSectionDimAlphaWrites(
  page: import('@playwright/test').Page,
): Promise<number> {
  return page.evaluate(() => (
    (window as any).__crossSectionAlphaWrites as number[] | undefined
  )?.length ?? 0);
}

async function instrumentSkewtTitles(
  page: import('@playwright/test').Page,
): Promise<void> {
  await page.addInitScript(() => {
    const titles: string[] = [];
    const textWrites: string[] = [];
    const settlements: Array<{
      model: string;
      outcome: 'success' | 'error';
      url: string;
    }> = [];
    (window as any).__skewtTitles = titles;
    (window as any).__skewtTextWrites = textWrites;
    (window as any).__soundingSettlements = settlements;
    const originalFillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (
      text: string,
      x: number,
      y: number,
      maxWidth?: number,
    ): void {
      if (this.canvas.closest('#skewt-canvas-container')) {
        const value = String(text);
        textWrites.push(value);
        if (value.includes(' — ')) titles.push(value);
      }
      if (maxWidth === undefined) originalFillText.call(this, text, x, y);
      else originalFillText.call(this, text, x, y, maxWidth);
    };

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args): Promise<Response> => {
      const input = args[0];
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
      const isSounding = url.includes('/sounding-profile/');
      const model = isSounding
        ? decodeURIComponent(new URL(url, window.location.href).pathname.split('/').pop() ?? '')
        : '';
      try {
        const response = await originalFetch(...args);
        if (!isSounding) return response;
        const originalJson = response.json.bind(response);
        Object.defineProperty(response, 'json', {
          configurable: true,
          value: async () => {
            try {
              const data = await originalJson();
              window.setTimeout(() => settlements.push({ model, outcome: 'success', url }), 0);
              return data;
            } catch (error) {
              window.setTimeout(() => settlements.push({ model, outcome: 'error', url }), 0);
              throw error;
            }
          },
        });
        return response;
      } catch (error) {
        if (isSounding) {
          window.setTimeout(() => settlements.push({ model, outcome: 'error', url }), 0);
        }
        throw error;
      }
    };
  });
}

async function skewtTitles(
  page: import('@playwright/test').Page,
): Promise<string[]> {
  return page.evaluate(() => [...((window as any).__skewtTitles ?? [])]);
}

async function skewtTextWrites(
  page: import('@playwright/test').Page,
): Promise<string[]> {
  return page.evaluate(() => [...((window as any).__skewtTextWrites ?? [])]);
}

async function soundingSettlementCount(
  page: import('@playwright/test').Page,
  model: string,
  outcome: 'success' | 'error',
): Promise<number> {
  return page.evaluate(({ settledModel, settledOutcome }) => (
    ((window as any).__soundingSettlements ?? []) as Array<{
      model: string;
      outcome: 'success' | 'error';
    }>
  ).filter(entry => (
    entry.model === settledModel && entry.outcome === settledOutcome
  )).length, { settledModel: model, settledOutcome: outcome });
}

async function soundingSettlementCountForPack(
  page: import('@playwright/test').Page,
  timestamp: string,
  outcome: 'success' | 'error' = 'success',
): Promise<number> {
  return page.evaluate(({ encodedTimestamp, settledOutcome }) => (
    ((window as any).__soundingSettlements ?? []) as Array<{
      outcome: 'success' | 'error';
      url: string;
    }>
  ).filter(entry => (
    entry.outcome === settledOutcome && entry.url.includes(encodedTimestamp)
  )).length, {
    encodedTimestamp: encodeURIComponent(timestamp),
    settledOutcome: outcome,
  });
}

function soundingProfile(model: string) {
  const level = (
    pressure_hpa: number,
    altitude_ft: number,
    temperature_c: number,
  ) => ({
    pressure_hpa,
    altitude_ft,
    temperature_c,
    dewpoint_c: temperature_c - 3,
    wind_speed_kt: 12,
    wind_direction_deg: 240,
    relative_humidity_pct: 75,
    dewpoint_depression_c: 3,
    wet_bulb_c: temperature_c - 1,
    theta_e_k: 290,
    lapse_rate_c_per_km: 6,
    icing_index: 0,
    icing_index_nwp: 0,
    sfip_100: 0,
    cloud_liquid_water_g_m3: 0,
    ice_mixing_ratio_g_kg: 0,
    cloud_area_fraction_pct: 0,
    richardson_number: 1,
    omega_pa_s: 0,
    w_fpm: 0,
  });
  return {
    point_index: 0,
    lat: 51.348099,
    lon: -0.558889,
    distance_from_origin_nm: 0,
    waypoint_icao: 'EGTF',
    model,
    time: '2026-07-10T10:00:00Z',
    levels: [
      level(1000, 300, 15),
      level(900, 3_000, 5),
    ],
    cruise_altitude_ft: 8_000,
    track_deg: 240,
    label: 'EGTF',
    indices: {},
    parcel_path: [],
    cloud_layers: [],
    nwp_cloud_layers: [],
    icing_zones: [],
    icing_ogimet_nwp_zones: [],
    sfip_zones: [],
    inversion_layers: [],
    convective: null,
  };
}

async function instrumentRouteMapFits(
  page: import('@playwright/test').Page,
): Promise<void> {
  await page.addInitScript(() => {
    const browserWindow = window as any;
    browserWindow.__routeMapFitCalls = [];

    Object.defineProperty(window, 'L', {
      configurable: true,
      get: () => undefined,
      set: (leaflet: any) => {
        const original = leaflet.Map.prototype.fitBounds;
        leaflet.Map.prototype.fitBounds = function (
          bounds: unknown,
          options: unknown,
        ) {
          const isRouteMap = this.getContainer?.()?.id === 'map-container';
          const normalized = isRouteMap ? leaflet.latLngBounds(bounds) : null;
          const result = original.call(this, bounds, options);
          if (normalized) {
            browserWindow.__routeMap = this;
            browserWindow.__routeMapFitCalls.push({
              west: normalized.getWest(),
              east: normalized.getEast(),
              south: normalized.getSouth(),
              north: normalized.getNorth(),
            });
          }
          return result;
        };

        Object.defineProperty(window, 'L', {
          configurable: true,
          writable: true,
          value: leaflet,
        });
      },
    });
  });
}

async function instrumentHeldAnimationFrames(
  page: import('@playwright/test').Page,
): Promise<void> {
  await page.addInitScript(() => {
    const browserWindow = window as any;
    const originalRequest = window.requestAnimationFrame.bind(window);
    const originalCancel = window.cancelAnimationFrame.bind(window);
    const held = new Map<number, FrameRequestCallback>();
    let holding = false;
    let nextHeldId = 1_000_000_000;

    window.requestAnimationFrame = (callback: FrameRequestCallback): number => {
      if (!holding) return originalRequest(callback);
      const id = nextHeldId;
      nextHeldId += 1;
      held.set(id, callback);
      return id;
    };
    window.cancelAnimationFrame = (id: number): void => {
      if (!held.delete(id)) originalCancel(id);
    };
    browserWindow.__holdAnimationFrames = (): void => { holding = true; };
    browserWindow.__heldAnimationFrameCount = (): number => held.size;
    browserWindow.__releaseAnimationFrames = (): void => {
      holding = false;
      const callbacks = [...held.values()];
      held.clear();
      for (const callback of callbacks) originalRequest(callback);
    };
  });
}

const evidencePaths = (page: import('@playwright/test').Page) =>
  page.locator('.leaflet-wb-advisory-evidence-pane path');

async function focusPixelCount(
  page: import('@playwright/test').Page,
  selector: string,
): Promise<number> {
  return page.locator(selector).evaluateAll((canvases) => {
    const colors = [[180, 83, 9], [185, 28, 28], [21, 128, 61]];
    let matches = 0;
    for (const node of canvases) {
      const canvas = node as HTMLCanvasElement;
      const context = canvas.getContext('2d');
      if (!context || canvas.width === 0 || canvas.height === 0) continue;
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let offset = 0; offset < pixels.length; offset += 4) {
        if (colors.some(([r, g, b]) => (
          Math.abs(pixels[offset] - r) <= 2
          && Math.abs(pixels[offset + 1] - g) <= 2
          && Math.abs(pixels[offset + 2] - b) <= 2
          && pixels[offset + 3] > 200
        ))) matches += 1;
      }
    }
    return matches;
  });
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

    // Assessment badge should show GREEN
    await expect(page.getByText('GREEN', { exact: true })).toBeVisible();

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

test.describe('Advisory evidence actions', () => {
  test('aggregate focus uses only representative-model geometry', async ({ page }) => {
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator('[data-advisory="cloud_top"] .advisory-action-btn').click();
    await expect(page.locator('#advisory-focus-banner')).toContainText('Cloud Tops');
    await expect(page.locator('#advisory-focus-banner')).toContainText('ECMWF');
    await page.locator('#viz-controls [data-layout="split"]').click();

    await expect(evidencePaths(page)).toHaveCount(2);
    await expect(evidencePaths(page).locator('[stroke="#16a34a"]')).toHaveCount(0);
  });

  test('per-model keyboard action focuses the requested model', async ({ page }) => {
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator(
      '[data-advisory="cloud_top"] .adv-model-badge[data-model="gfs"]',
    ).click();
    const action = page.getByRole('button', { name: 'Show on chart: GFS' });
    await action.focus();
    await action.press('Enter');

    await expect(page.locator('#advisory-focus-banner .advisory-focus-model'))
      .toHaveText('GFS');
  });

  test('manual model selection clears advisory focus', async ({ page }) => {
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator('[data-advisory="cloud_top"] .advisory-action-btn').click();
    await page.locator('#viz-controls [data-layout="split"]').click();
    await expect.poll(() => focusPixelCount(page, '#viz-canvas-container canvas')).toBeGreaterThan(0);
    await expect.poll(() => focusPixelCount(page, '#route-graph-container canvas')).toBeGreaterThan(0);
    await expect(evidencePaths(page)).toHaveCount(2);

    await page.locator('#viz-model-select').selectOption('gfs');

    await expect(page.locator('#advisory-focus-banner')).toBeHidden();
    await expect.poll(() => focusPixelCount(page, '#viz-canvas-container canvas')).toBe(0);
    await expect.poll(() => focusPixelCount(page, '#route-graph-container canvas')).toBe(0);
    await expect(evidencePaths(page)).toHaveCount(0);
  });

  test('manual layer edit retains evidence but clears emphasis', async ({ page }) => {
    await instrumentCrossSectionAlphaWrites(page);
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator('[data-advisory="cloud_top"] .advisory-action-btn').click();
    await expect.poll(() => focusPixelCount(page, '#viz-canvas-container canvas')).toBeGreaterThan(0);
    await expect.poll(() => crossSectionDimAlphaWrites(page)).toBeGreaterThan(0);
    await page.evaluate(() => {
      ((window as any).__crossSectionAlphaWrites as number[]).length = 0;
    });
    await page.locator('input[data-layer-id="freezing-level"]').click();

    await expect(page.locator('#advisory-focus-banner')).toBeVisible();
    await expect.poll(() => focusPixelCount(page, '#viz-canvas-container canvas')).toBeGreaterThan(0);
    await expect(page.locator('#viz-preset-select')).toHaveValue('');
    expect(await crossSectionDimAlphaWrites(page)).toBe(0);
  });

  test('unresolved focus cannot retain visualization emphasis', async ({ page }) => {
    await instrumentCrossSectionAlphaWrites(page);
    await useFullBriefingMode(page);
    const advisories = clone(fixture('advisories.json'));
    const altAdvisories = clone(advisories);
    altAdvisories.advisories = altAdvisories.advisories.filter(
      (advisory: any) => advisory.advisory_id !== 'cloud_top',
    );
    await mockBriefingApi(page, {
      advisories: { current: advisories },
      routeFronts: { current: routeFrontsFixture() },
      airportRequests: [],
      flightOverrides: {
        alt_departure_time: `${FUTURE_DATE}T20:00:00+00:00`,
      },
      packOverrides: { has_alt_advisories: true },
      altAdvisories,
    });
    await openBriefing(page);
    await expect(page.locator('[data-alt-toggle="alt"]')).toBeVisible();

    await page.locator('[data-advisory="cloud_top"] .advisory-action-btn').click();
    await expect.poll(() => crossSectionDimAlphaWrites(page)).toBeGreaterThan(0);
    await page.evaluate(() => {
      ((window as any).__crossSectionAlphaWrites as number[]).length = 0;
    });
    await page.locator('[data-alt-toggle="alt"]').click();

    await expect(page.locator('[data-advisory="cloud_top"]')).toHaveCount(0);
    await expect(page.locator('#advisory-focus-banner')).toBeHidden();
    expect(await crossSectionDimAlphaWrites(page)).toBe(0);
  });

  test('alternate-time view clears focus from every visualization surface', async ({ page }) => {
    await useFullBriefingMode(page, { layout: 'split' });
    const advisories = clone(fixture('advisories.json'));
    await mockBriefingApi(page, {
      advisories: { current: advisories },
      routeFronts: { current: routeFrontsFixture() },
      airportRequests: [],
      flightOverrides: {
        alt_departure_time: `${FUTURE_DATE}T20:00:00+00:00`,
      },
      packOverrides: { has_alt_advisories: true },
      altAdvisories: clone(advisories),
    });
    await openBriefing(page);
    await expect(page.locator('[data-alt-toggle="alt"]')).toBeVisible();

    await page.locator('[data-advisory="cloud_top"] .advisory-action-btn').click();
    await expect(page.locator('#advisory-focus-banner')).toBeVisible();
    await expect.poll(() => focusPixelCount(page, '#viz-canvas-container canvas'))
      .toBeGreaterThan(0);
    await expect.poll(() => focusPixelCount(page, '#route-graph-container canvas'))
      .toBeGreaterThan(0);
    await expect(evidencePaths(page)).toHaveCount(2);

    await page.locator('[data-alt-toggle="alt"]').click();

    await expect(page.locator('#advisory-focus-banner')).toBeHidden();
    await expect.poll(() => focusPixelCount(page, '#viz-canvas-container canvas')).toBe(0);
    await expect.poll(() => focusPixelCount(page, '#route-graph-container canvas')).toBe(0);
    await expect(evidencePaths(page)).toHaveCount(0);
  });

  test('forecast confidence opens Compare with every model', async ({ page }) => {
    await useFullBriefingMode(page, {
      layout: 'cross-section',
      compareModels: { ecmwf: false, gfs: false },
    });
    await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator('[data-advisory="model_agreement"] .advisory-action-btn').click();

    await expect(page.locator('#viz-layout-wrapper')).toHaveClass(/layout-compare/);
    await expect(page.locator('[data-compare-model="ecmwf"]')).toHaveClass(/active/);
    await expect(page.locator('[data-compare-model="gfs"]')).toHaveClass(/active/);
    await expect(page.locator('#compare-layer-select')).toHaveValue('freezing-level');
  });

  test('DD versus NWP stays within one model', async ({ page }) => {
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator('[data-advisory="dd_nwp_agreement"] .advisory-action-btn').click();

    await expect(page.locator('#viz-layout-wrapper')).not.toHaveClass(/layout-compare/);
    await expect(page.locator('[data-cloud-source="dd"]')).toBeChecked();
    await expect(page.locator('[data-cloud-source="nwp"]')).toBeChecked();
    await expect(page.locator('[data-cloud-style]')).toHaveValue('square');
    await expect(page.locator('#advisory-focus-banner')).toContainText('DD ↔ NWP');
  });

  test('late dynamic Skew-T responses cannot replace the current advisory model', async ({ page }) => {
    await instrumentSkewtTitles(page);
    await useFullBriefingMode(page);
    await page.addInitScript(() => localStorage.setItem('wb_selectedModel', 'ecmwf'));
    await mockBriefingApi(page);

    let releaseEcmwf!: () => void;
    const ecmwfGate = new Promise<void>((resolve) => { releaseEcmwf = resolve; });
    let ecmwfRequested = 0;
    await page.route('**/sounding-profile/**', async (route) => {
      const model = decodeURIComponent(
        new URL(route.request().url()).pathname.split('/').pop() ?? '',
      );
      if (model === 'ecmwf') {
        ecmwfRequested += 1;
        await ecmwfGate;
      }
      await route.fulfill({ json: soundingProfile(model) });
    });

    await openBriefing(page);
    await expect.poll(() => ecmwfRequested).toBeGreaterThan(0);
    expect(ecmwfRequested).toBe(1);

    await page.locator('[data-advisory="dd_nwp_agreement"] .advisory-action-btn').click();
    await expect.poll(async () => (await skewtTitles(page)).at(-1))
      .toBe('EGTF — GFS');

    releaseEcmwf();
    await expect.poll(() => soundingSettlementCount(page, 'ecmwf', 'success'))
      .toBe(1);
    expect((await skewtTitles(page)).at(-1)).toBe('EGTF — GFS');
  });

  test('late dynamic Skew-T failures cannot clear the current advisory model', async ({ page }) => {
    await instrumentSkewtTitles(page);
    await useFullBriefingMode(page);
    await page.addInitScript(() => localStorage.setItem('wb_selectedModel', 'ecmwf'));
    await mockBriefingApi(page);

    let releaseEcmwf!: () => void;
    const ecmwfGate = new Promise<void>((resolve) => { releaseEcmwf = resolve; });
    let ecmwfRequested = 0;
    await page.route('**/sounding-profile/**', async (route) => {
      const model = decodeURIComponent(
        new URL(route.request().url()).pathname.split('/').pop() ?? '',
      );
      if (model === 'ecmwf') {
        ecmwfRequested += 1;
        await ecmwfGate;
        await route.abort('failed');
        return;
      }
      await route.fulfill({ json: soundingProfile(model) });
    });

    await openBriefing(page);
    await expect.poll(() => ecmwfRequested).toBeGreaterThan(0);
    expect(ecmwfRequested).toBe(1);

    await page.locator('[data-advisory="dd_nwp_agreement"] .advisory-action-btn').click();
    await expect.poll(async () => (await skewtTitles(page)).at(-1))
      .toBe('EGTF — GFS');
    const placeholder = 'Click a point on the cross-section to view its Skew-T';
    const placeholderCount = (await skewtTextWrites(page))
      .filter(text => text === placeholder).length;

    releaseEcmwf();
    await expect.poll(() => soundingSettlementCount(page, 'ecmwf', 'error'))
      .toBe(1);
    expect((await skewtTitles(page)).at(-1)).toBe('EGTF — GFS');
    expect((await skewtTextWrites(page)).filter(text => text === placeholder))
      .toHaveLength(placeholderCount);
  });

  test('compare Skew-T ignores old-pack responses and refreshes primary identity', async ({ page }) => {
    await instrumentSkewtTitles(page);
    await useFullBriefingMode(page);
    await page.addInitScript(() => localStorage.setItem('wb_selectedModel', 'ecmwf'));
    await mockBriefingApi(page);

    const secondPack = {
      ...fixture('pack_meta.json'),
      fetch_timestamp: SECOND_TIMESTAMP,
      has_digest: false,
      has_advisories: false,
    };
    await page.unroute(`**/api/flights/${FLIGHT_ID}/packs`);
    await page.route(`**/api/flights/${FLIGHT_ID}/packs`, (route) => {
      const url = route.request().url();
      const afterPacks = url.split('/packs')[1];
      if (afterPacks && afterPacks !== '' && afterPacks !== '/') {
        return route.fallthrough();
      }
      return route.fulfill({
        json: [fixture('pack_meta.json'), secondPack],
      });
    });
    const secondEncoded = encodeURIComponent(SECOND_TIMESTAMP);
    await page.route(`**/packs/${secondEncoded}`, (route) => {
      const afterTimestamp = route.request().url().split(secondEncoded)[1];
      if (afterTimestamp && afterTimestamp !== '' && afterTimestamp !== '/') {
        return route.fallthrough();
      }
      return route.fulfill({ json: secondPack });
    });
    await page.route(`**/packs/${secondEncoded}/snapshot`, route => (
      route.fulfill({ json: fixture('snapshot.json') })
    ));
    await page.route(`**/packs/${secondEncoded}/route-analyses`, route => (
      route.fulfill({ json: fixture('route_analyses.json') })
    ));
    await page.route(`**/packs/${secondEncoded}/elevation`, route => (
      route.fulfill({ json: fixture('elevation.json') })
    ));
    await page.route(`**/packs/${secondEncoded}/route-fronts`, route => (
      route.fulfill({ json: routeFrontsFixture() })
    ));

    let gateOldCompare = false;
    let releaseOldCompare!: () => void;
    const oldCompareGate = new Promise<void>((resolve) => { releaseOldCompare = resolve; });
    let oldCompareRequests = 0;
    let newCompareRequests = 0;
    await page.route('**/sounding-profile/**', async (route) => {
      const url = new URL(route.request().url());
      const model = decodeURIComponent(url.pathname.split('/').pop() ?? '');
      const isSecondPack = url.pathname.includes(secondEncoded);
      if (!isSecondPack && gateOldCompare) {
        oldCompareRequests += 1;
        await oldCompareGate;
        await route.fulfill({ json: { ...soundingProfile(model), label: `OLD ${model.toUpperCase()}` } });
        return;
      }
      if (isSecondPack) {
        newCompareRequests += 1;
        await route.fulfill({ json: { ...soundingProfile(model), label: `NEW ${model.toUpperCase()}` } });
        return;
      }
      await route.fulfill({ json: soundingProfile(model) });
    });

    await openBriefing(page);
    await expect.poll(() => soundingSettlementCountForPack(page, TIMESTAMP))
      .toBeGreaterThan(0);
    gateOldCompare = true;
    await page.locator('#skewt-view-compare').click();
    await expect.poll(() => oldCompareRequests).toBeGreaterThanOrEqual(2);
    await page.evaluate(() => new Promise<void>((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    }));
    expect(oldCompareRequests).toBe(2);

    await page.locator('#history-select').selectOption(SECOND_TIMESTAMP);
    await expect.poll(() => newCompareRequests).toBe(2);
    await expect.poll(() => soundingSettlementCountForPack(page, SECOND_TIMESTAMP))
      .toBe(2);
    await expect.poll(async () => (await skewtTitles(page)).at(-1))
      .toBe('NEW ECMWF — Compare');
    expect((await skewtTextWrites(page)).filter(text => text.endsWith('★')).at(-1))
      .toBe('ECMWF ★');

    await page.locator('#viz-model-select').selectOption('gfs');
    await expect.poll(() => newCompareRequests).toBe(4);
    await expect.poll(() => soundingSettlementCountForPack(page, SECOND_TIMESTAMP))
      .toBe(4);
    await expect.poll(async () => (await skewtTitles(page)).at(-1))
      .toBe('NEW GFS — Compare');
    expect((await skewtTextWrites(page)).filter(text => text.endsWith('★')).at(-1))
      .toBe('GFS ★');

    releaseOldCompare();
    await expect.poll(() => soundingSettlementCountForPack(page, TIMESTAMP))
      .toBeGreaterThanOrEqual(3);
    expect((await skewtTitles(page)).at(-1)).toBe('NEW GFS — Compare');
    expect((await skewtTextWrites(page)).filter(text => text.endsWith('★')).at(-1))
      .toBe('GFS ★');
  });

  test('airport profile uses endpoint times and visible model fallback', async ({ page }) => {
    await useFullBriefingMode(page);
    const refs = await mockBriefingApi(page);
    await openBriefing(page);

    const invokingButton = page.locator('[data-advisory="airport_wind"] .advisory-action-btn');
    await invokingButton.click();

    const drawer = page.locator('.briefing-airport-profile-drawer');
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole('tab', { name: /Departure/ })).toBeFocused();
    await expect(drawer.locator('.briefing-airport-endpoint-time')).toContainText('17:00');
    await expect(drawer.locator('.briefing-airport-fallback-note')).toContainText('France');
    await expect(drawer.locator('.briefing-airport-fallback-note')).toContainText('ECMWF');
    await drawer.locator('.ap-settings-btn').click();
    const panelSettings = drawer.locator('.ap-settings-drawer');
    await expect(panelSettings).toBeVisible();
    expect(await drawer.evaluate((root, settingsSelector) => {
      const settings = root.querySelector(settingsSelector as string);
      if (!(settings instanceof HTMLElement)) return false;
      const rootRect = root.getBoundingClientRect();
      const settingsRect = settings.getBoundingClientRect();
      return settingsRect.left >= rootRect.left
        && settingsRect.right <= rootRect.right;
    }, '.ap-settings-drawer')).toBe(true);
    await expect.poll(() => refs.airportRequests.length).toBe(1);
    expect(refs.airportRequests[0].searchParams.get('start_hour'))
      .toBe(`${FUTURE_DATE}T17:00:00.000Z`);

    await drawer.getByRole('tab', { name: /Arrival/ }).click();
    await expect(drawer.locator('.briefing-airport-endpoint-time')).toContainText('18:00');
    await expect.poll(() => refs.airportRequests.length).toBe(2);
    expect(refs.airportRequests[1].searchParams.get('start_hour'))
      .toBe(`${FUTURE_DATE}T18:00:00.000Z`);

    await drawer.locator('.ap-model-sel').selectOption('gfs');
    await expect.poll(() => refs.airportRequests.length).toBe(3);
    expect(refs.airportRequests[2].searchParams.get('model')).toBe('gfs');
    expect(refs.airportRequests[2].searchParams.get('start_hour'))
      .toBe(`${FUTURE_DATE}T18:00:00.000Z`);
    await expect(drawer.locator('.ap-panel-title'))
      .toContainText(`EGLF · GFS · 18:00Z`);
    await expect(drawer.locator('.briefing-airport-fallback-note')).toContainText('GFS');
    await expect(drawer.locator('.briefing-airport-fallback-note')).not.toContainText('ECMWF');

    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect(invokingButton).toBeFocused();
  });

  test('airport profile cached model switch rebinds open Skew-T controls', async ({ page }) => {
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await page.unroute('**/api/maps/airport-profile?**');
    const profileModels: string[] = [];
    await page.route('**/api/maps/airport-profile?**', async (route) => {
      const url = new URL(route.request().url());
      const model = url.searchParams.get('model') ?? 'ecmwf';
      const startHour = url.searchParams.get('start_hour')
        ?? `${FUTURE_DATE}T17:00:00.000Z`;
      profileModels.push(model);
      const meta = {
        icao: url.searchParams.get('icao') ?? 'EGTF',
        lat: 51.348099,
        lon: -0.558889,
        elevation_ft: 80,
        model,
        start_hour: startHour,
        window_h: 3,
        hours: [startHour],
      };
      const levels = {
        label: 'Fetching forecasts',
        hours: [{
          time: startHour,
          temperature_2m_c: 12,
          dewpoint_2m_c: 9,
          wind_speed_10m_kt: 8,
          wind_direction_10m_deg: 240,
          wind_gusts_10m_kt: 12,
          cape_jkg: 0,
          cloud_cover_pct: 30,
          cloud_cover_low_pct: 20,
          freezing_level_m: 2_000,
          visibility_m: 10_000,
          pressure_levels: [
            {
              pressure_hpa: 1000,
              altitude_ft: 300,
              temperature_c: 12,
              dewpoint_c: 9,
              wind_speed_kt: 8,
              wind_direction_deg: 240,
              relative_humidity_pct: 75,
              cloud_area_fraction_pct: 20,
            },
            {
              pressure_hpa: 900,
              altitude_ft: 3_000,
              temperature_c: 4,
              dewpoint_c: 1,
              wind_speed_kt: 15,
              wind_direction_deg: 250,
              relative_humidity_pct: 70,
              cloud_area_fraction_pct: 10,
            },
          ],
        }],
      };
      const body = `event: meta\ndata: ${JSON.stringify(meta)}\n\n`
        + `event: levels\ndata: ${JSON.stringify(levels)}\n\n`
        + 'event: complete\ndata: {}\n\n';
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'cache-control': 'no-cache' },
        body,
      });
    });
    await openBriefing(page);

    const firstProfileResponse = page.waitForResponse(response => (
      response.url().includes('/api/maps/airport-profile')
    ));
    await page.locator('[data-advisory="airport_wind"] .advisory-action-btn').click();
    await (await firstProfileResponse).finished();

    const drawer = page.locator('.briefing-airport-profile-drawer');
    const settingsButton = drawer.locator('.ap-settings-btn');
    await settingsButton.click();
    await expect(drawer.locator('input[data-overlay="clouds-dd"]')).not.toBeChecked();

    const secondProfileResponse = page.waitForResponse(response => (
      response.url().includes('/api/maps/airport-profile')
      && new URL(response.url()).searchParams.get('model') === 'gfs'
    ));
    const modelSelect = drawer.locator('.ap-model-sel');
    await modelSelect.selectOption('gfs');
    await (await secondProfileResponse).finished();
    await expect(drawer.locator('.ap-panel-title')).toContainText('GFS');
    await expect(drawer.locator('input[data-overlay="clouds-dd"]')).not.toBeChecked();
    await page.evaluate(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));

    await modelSelect.selectOption('ecmwf');
    await expect(drawer.locator('.ap-panel-title')).toContainText('ECMWF');
    expect(profileModels).toEqual(['ecmwf', 'gfs']);

    const overlay = drawer.locator('input[data-overlay="clouds-dd"]');
    await overlay.check();
    await expect(overlay).toBeChecked();
    await drawer.locator('.ap-drawer-close').click();
    await expect(drawer.locator('.ap-settings-drawer')).toBeHidden();
    await settingsButton.click();

    await expect(drawer.locator('input[data-overlay="clouds-dd"]')).toBeChecked();
  });

  test('airport profile tabs expose one panel and keyboard-select endpoints', async ({ page }) => {
    await useFullBriefingMode(page);
    const refs = await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator('[data-advisory="airport_wind"] .advisory-action-btn').click();

    const drawer = page.locator('.briefing-airport-profile-drawer');
    const departureTab = drawer.getByRole('tab', { name: /Departure/ });
    const arrivalTab = drawer.getByRole('tab', { name: /Arrival/ });
    const panel = drawer.getByRole('tabpanel');
    await expect(panel).toHaveCount(1);
    await expect(departureTab)
      .toHaveAttribute('id', 'briefing-airport-profile-departure-tab');
    await expect(arrivalTab)
      .toHaveAttribute('id', 'briefing-airport-profile-arrival-tab');
    await expect(panel)
      .toHaveAttribute('id', 'briefing-airport-profile-tabpanel');
    await expect(departureTab)
      .toHaveAttribute('aria-controls', 'briefing-airport-profile-tabpanel');
    await expect(arrivalTab)
      .toHaveAttribute('aria-controls', 'briefing-airport-profile-tabpanel');
    await expect(panel)
      .toHaveAttribute('aria-labelledby', 'briefing-airport-profile-departure-tab');

    await departureTab.press('ArrowLeft');
    await expect(arrivalTab).toBeFocused();
    await expect(arrivalTab).toHaveAttribute('aria-selected', 'true');
    await expect(panel)
      .toHaveAttribute('aria-labelledby', 'briefing-airport-profile-arrival-tab');
    await expect(drawer.locator('.briefing-airport-endpoint-time')).toContainText('18:00');
    await expect.poll(() => refs.airportRequests.length).toBe(2);
    expect(refs.airportRequests[1].searchParams.get('start_hour'))
      .toBe(`${FUTURE_DATE}T18:00:00.000Z`);

    await arrivalTab.press('ArrowRight');
    await expect(departureTab).toBeFocused();
    await expect.poll(() => refs.airportRequests.length).toBe(3);
    expect(refs.airportRequests[2].searchParams.get('start_hour'))
      .toBe(`${FUTURE_DATE}T17:00:00.000Z`);

    await departureTab.press('End');
    await expect(arrivalTab).toBeFocused();
    await expect.poll(() => refs.airportRequests.length).toBe(4);
    expect(refs.airportRequests[3].searchParams.get('start_hour'))
      .toBe(`${FUTURE_DATE}T18:00:00.000Z`);

    await arrivalTab.press('Home');
    await expect(departureTab).toBeFocused();
    await expect.poll(() => refs.airportRequests.length).toBe(5);
    expect(refs.airportRequests[4].searchParams.get('start_hour'))
      .toBe(`${FUTURE_DATE}T17:00:00.000Z`);
  });

  test('airport profile Tab order skips the inactive endpoint tab', async ({ page }) => {
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator('[data-advisory="airport_wind"] .advisory-action-btn').click();

    const drawer = page.locator('.briefing-airport-profile-drawer');
    const departureTab = drawer.getByRole('tab', { name: /Departure/ });
    const arrivalTab = drawer.getByRole('tab', { name: /Arrival/ });
    const panelClose = drawer.locator('.ap-panel-close');

    await expect(departureTab).toBeFocused();
    await departureTab.press('Tab');
    await expect(panelClose).toBeFocused();
    await panelClose.press('Shift+Tab');
    await expect(departureTab).toBeFocused();

    await departureTab.press('ArrowRight');
    await expect(arrivalTab).toBeFocused();
    await arrivalTab.press('Tab');
    await expect(panelClose).toBeFocused();
    await panelClose.press('Shift+Tab');
    await expect(arrivalTab).toBeFocused();
  });

  test('airport profile layer info popup remains inside the modal boundary', async ({ page }) => {
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator('[data-advisory="airport_wind"] .advisory-action-btn').click();
    const drawer = page.locator('.briefing-airport-profile-drawer');
    await drawer.locator('.ap-settings-btn').click();
    const infoButton = drawer.locator('[data-group-info]').first();
    await expect(infoButton).toBeVisible();
    await infoButton.click();

    const backdrop = page.locator('.metric-popup-backdrop');
    const popupClose = backdrop.locator('.metric-popup-close');
    await expect(backdrop).toHaveClass(/active/);
    expect(await backdrop.evaluate(element => (element as HTMLElement).inert))
      .toBe(false);
    await popupClose.focus();
    await expect(popupClose).toBeFocused();
    const drawerClose = drawer.locator('.briefing-airport-profile-close');
    await popupClose.press('Tab');
    await expect(drawerClose).toBeFocused();
    await drawerClose.press('Shift+Tab');
    await expect(popupClose).toBeFocused();
    await popupClose.press('Enter');

    await expect(backdrop).not.toHaveClass(/active/);
    await expect(infoButton).toBeFocused();
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole('tab', { name: /Departure/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await page.evaluate(() => {
      const portal = document.createElement('div');
      portal.id = 'dynamic-modal-portal';
      portal.dataset.modalPortal = 'true';
      portal.dataset.modalPortalActive = 'true';
      const button = document.createElement('button');
      button.textContent = 'Dynamic portal action';
      portal.appendChild(button);
      document.body.appendChild(portal);
    });
    const dynamicPortal = page.locator('#dynamic-modal-portal');
    expect(await dynamicPortal.evaluate(element => (element as HTMLElement).inert))
      .toBe(false);
    const dynamicPortalButton = dynamicPortal.getByRole('button');
    await dynamicPortalButton.focus();
    await expect(dynamicPortalButton).toBeFocused();
    expect(await page.evaluate(() => {
      const openDrawer = document.querySelector('.briefing-airport-profile-drawer');
      return Array.from(document.body.children)
        .filter(child => (
          child !== openDrawer
          && !(child as HTMLElement).hasAttribute('data-modal-portal')
        ))
        .every(child => (child as HTMLElement).inert);
    })).toBe(true);
  });

  test('airport profile modal contains focus and restores sibling state', async ({ page }) => {
    await page.route('**/dist/briefing.js', async (route) => {
      const response = await route.fetch();
      const source = await response.text();
      const declaration = 'const airportProfileDrawer = new BriefingAirportProfileDrawer();';
      expect(source).toContain(declaration);
      await route.fulfill({
        response,
        body: source.replace(
          declaration,
          'const airportProfileDrawer = window.__airportProfileDrawer = '
            + 'new BriefingAirportProfileDrawer();',
        ),
      });
    });
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await openBriefing(page);
    await page.evaluate(() => {
      const normalSibling = document.createElement('button');
      normalSibling.id = 'modal-normal-sibling';
      normalSibling.textContent = 'Normal sibling';
      document.body.appendChild(normalSibling);

      const preInertSibling = document.createElement('button');
      preInertSibling.id = 'modal-pre-inert-sibling';
      preInertSibling.textContent = 'Pre-inert sibling';
      preInertSibling.inert = true;
      document.body.appendChild(preInertSibling);
    });

    const invokingButton = page.locator(
      '[data-advisory="airport_wind"] .advisory-action-btn',
    );
    await invokingButton.click();

    const drawer = page.locator('.briefing-airport-profile-drawer');
    expect(await page.evaluate(() => {
      const openDrawer = document.querySelector('.briefing-airport-profile-drawer');
      return Array.from(document.body.children)
        .filter(child => (
          child !== openDrawer
          && !(child as HTMLElement).hasAttribute('data-modal-portal')
          && !(child as HTMLElement).inert
        ))
        .map(child => child.tagName);
    })).toEqual([]);

    await page.evaluate(() => {
      const dynamicSibling = document.createElement('button');
      dynamicSibling.id = 'modal-dynamic-sibling';
      dynamicSibling.textContent = 'Dynamic sibling';
      document.body.appendChild(dynamicSibling);
    });
    await expect.poll(() => page.locator('#modal-dynamic-sibling').evaluate(
      element => (element as HTMLElement).inert,
    )).toBe(true);

    await page.evaluate(() => {
      document.body.tabIndex = -1;
      document.body.focus();
    });
    await expect(drawer.getByRole('tab', { name: /Departure/ })).toBeFocused();

    await page.evaluate(() => {
      const original = document.querySelector(
        '[data-advisory="airport_wind"] .advisory-action-btn',
      );
      original?.replaceWith(original.cloneNode(true));
    });
    await page.keyboard.press('Escape');

    await expect(drawer).toBeHidden();
    await expect(invokingButton).toBeFocused();
    expect(await page.locator('#modal-normal-sibling').evaluate(
      element => (element as HTMLElement).inert,
    )).toBe(false);
    expect(await page.locator('#modal-pre-inert-sibling').evaluate(
      element => (element as HTMLElement).inert,
    )).toBe(true);
    expect(await page.locator('#modal-dynamic-sibling').evaluate(
      element => (element as HTMLElement).inert,
    )).toBe(false);

    await page.evaluate(() => document.body.focus());
    await expect(page.locator('body')).toBeFocused();

    await page.evaluate(() => {
      (document.querySelector('#modal-normal-sibling') as HTMLElement).inert = true;
      (document.querySelector('#modal-pre-inert-sibling') as HTMLElement).inert = false;
    });
    await invokingButton.click();
    expect(await page.evaluate(() => {
      const openDrawer = document.querySelector('.briefing-airport-profile-drawer');
      return Array.from(document.body.children)
        .filter(child => (
          child !== openDrawer
          && !(child as HTMLElement).hasAttribute('data-modal-portal')
          && !(child as HTMLElement).inert
        ))
        .length;
    })).toBe(0);
    await drawer.locator('.ap-settings-btn').click();
    await drawer.locator('[data-group-info]').first().click();
    await expect(page.locator('.metric-popup-backdrop')).toHaveClass(/active/);
    await page.evaluate(() => (window as any).__airportProfileDrawer.destroy());
    await expect(drawer).toHaveCount(0);
    await expect(page.locator('.metric-popup-backdrop')).not.toHaveClass(/active/);
    expect(await page.locator('#modal-normal-sibling').evaluate(
      element => (element as HTMLElement).inert,
    )).toBe(true);
    expect(await page.locator('#modal-pre-inert-sibling').evaluate(
      element => (element as HTMLElement).inert,
    )).toBe(false);
    expect(await page.locator('#modal-dynamic-sibling').evaluate(
      element => (element as HTMLElement).inert,
    )).toBe(false);

    await page.evaluate(() => document.body.focus());
    await expect(page.locator('body')).toBeFocused();
    expect(await page.evaluate(async () => {
      const afterDestroy = document.createElement('button');
      afterDestroy.id = 'modal-after-destroy-sibling';
      document.body.appendChild(afterDestroy);
      await new Promise(resolve => setTimeout(resolve, 0));
      return afterDestroy.inert;
    })).toBe(false);
  });

  test('airport profile close controls use the active locale', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('wb_locale', 'de'));
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator('[data-advisory="airport_wind"] .advisory-action-btn').click();

    const drawer = page.locator('.briefing-airport-profile-drawer');
    const outerClose = drawer.locator('.briefing-airport-profile-close');
    await expect(outerClose).toHaveAttribute('aria-label', 'Schließen');
    await expect(outerClose).toHaveAttribute('title', 'Schließen');

    const panelClose = drawer.locator('.ap-panel-close');
    await expect(panelClose).toHaveAttribute('aria-label', 'Schließen');
    await expect(panelClose).toHaveAttribute('title', 'Schließen');

    await drawer.locator('.ap-settings-btn').click();
    const settingsClose = drawer.locator('.ap-drawer-close');
    await expect(settingsClose).toHaveAttribute('aria-label', 'Schließen');
    await expect(settingsClose).toHaveAttribute('title', 'Schließen');
  });

  test('fronts action does not fit late axes after fronts are hidden', async ({ page }) => {
    await instrumentRouteMapFits(page);
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await page.unroute('**/api/hewson-map/fronts**');
    let releaseAxis!: () => void;
    const axisGate = new Promise<void>((resolve) => { releaseAxis = resolve; });
    let markAxisRequested!: () => void;
    const axisRequested = new Promise<void>((resolve) => { markAxisRequested = resolve; });
    await page.route('**/api/hewson-map/fronts**', async (route) => {
      markAxisRequested();
      await axisGate;
      await route.fulfill({
        json: {
          model: 'ecmwf',
          init_time: '2026-07-10T00:00:00Z',
          valid_time: '2026-07-10T00:00:00Z',
          level: 850,
          hour: 0,
          stride_hours: 3,
          gate: 'default',
          gate_config: {},
          fronts: [{
            kind: 'cold',
            length_km: 180,
            mean_gradient: 4,
            mean_delta_theta_e: 5,
            coordinates: [[-0.72, 51.31], [-1.65, 51.31]],
          }],
        },
      });
    });
    await openBriefing(page);

    await page.locator('[data-advisory="fronts"] .advisory-action-btn').click();
    await axisRequested;
    await expect.poll(() => page.evaluate(() => (
      (window as any).__routeMapFitCalls as unknown[]
    ).length)).toBe(1);

    const frontsToggle = page.locator('#map-fronts-toggle');
    await frontsToggle.click();
    await expect(frontsToggle).not.toBeChecked();
    const fitCountBeforeRelease = await page.evaluate(() => (
      (window as any).__routeMapFitCalls as unknown[]
    ).length);
    const axisResponse = page.waitForResponse(response => (
      response.url().includes('/api/hewson-map/fronts')
    ));
    releaseAxis();
    const response = await axisResponse;
    await response.finished();
    await page.evaluate(() => new Promise<void>((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    }));

    expect(await page.evaluate(() => (
      (window as any).__routeMapFitCalls as unknown[]
    ).length)).toBe(fitCountBeforeRelease);
  });

  test('fronts action fit intent stays cancelled after fronts are toggled off and on', async ({ page }) => {
    await instrumentRouteMapFits(page);
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await page.unroute('**/api/hewson-map/fronts**');
    let releaseAxis!: () => void;
    const axisGate = new Promise<void>((resolve) => { releaseAxis = resolve; });
    let markAxisRequested!: () => void;
    const axisRequested = new Promise<void>((resolve) => { markAxisRequested = resolve; });
    await page.route('**/api/hewson-map/fronts**', async (route) => {
      markAxisRequested();
      await axisGate;
      const model = new URL(route.request().url()).searchParams.get('model') ?? 'ecmwf';
      await route.fulfill({ json: frontAxisFixture(model) });
    });
    await openBriefing(page);

    await page.locator('[data-advisory="fronts"] .advisory-action-btn').click();
    await axisRequested;
    const frontsToggle = page.locator('#map-fronts-toggle');
    await expect(frontsToggle).toBeChecked();
    await frontsToggle.click();
    await expect(frontsToggle).not.toBeChecked();
    await frontsToggle.click();
    await expect(frontsToggle).toBeChecked();
    const fitCountBeforeRelease = await page.evaluate(() => (
      (window as any).__routeMapFitCalls as unknown[]
    ).length);

    const axisResponse = page.waitForResponse(response => (
      response.url().includes('/api/hewson-map/fronts')
    ));
    releaseAxis();
    const response = await axisResponse;
    await response.finished();
    await page.evaluate(() => new Promise<void>((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    }));

    expect(await page.evaluate(() => (
      (window as any).__routeMapFitCalls as unknown[]
    ).length)).toBe(fitCountBeforeRelease);
  });

  test('fronts action fit intent stays cancelled across a model ABA before its frame', async ({ page }) => {
    await instrumentRouteMapFits(page);
    await instrumentHeldAnimationFrames(page);
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await page.unroute('**/api/hewson-map/fronts**');
    let releaseAxis!: () => void;
    const axisGate = new Promise<void>((resolve) => { releaseAxis = resolve; });
    let markAxisRequested!: () => void;
    const axisRequested = new Promise<void>((resolve) => { markAxisRequested = resolve; });
    await page.route('**/api/hewson-map/fronts**', async (route) => {
      markAxisRequested();
      await axisGate;
      const model = new URL(route.request().url()).searchParams.get('model') ?? 'ecmwf';
      await route.fulfill({ json: frontAxisFixture(model) });
    });
    await openBriefing(page);

    await page.locator('[data-advisory="fronts"] .advisory-action-btn').click();
    await axisRequested;
    const fitCountBeforeRelease = await page.evaluate(() => (
      (window as any).__routeMapFitCalls as unknown[]
    ).length);
    await page.evaluate(() => (window as any).__holdAnimationFrames());

    const axisResponse = page.waitForResponse(response => (
      response.url().includes('/api/hewson-map/fronts')
    ));
    releaseAxis();
    const response = await axisResponse;
    await response.finished();
    await expect.poll(() => page.evaluate(() => (
      (window as any).__heldAnimationFrameCount()
    ))).toBeGreaterThan(0);

    const modelSelect = page.locator('#viz-model-select');
    await modelSelect.selectOption('gfs');
    await expect(modelSelect).toHaveValue('gfs');
    await modelSelect.selectOption('ecmwf');
    await expect(modelSelect).toHaveValue('ecmwf');

    await page.evaluate(() => (window as any).__releaseAnimationFrames());
    await page.evaluate(() => new Promise<void>((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    }));

    expect(await page.evaluate(() => (
      (window as any).__routeMapFitCalls as unknown[]
    ).length)).toBe(fitCountBeforeRelease);
  });

  test('fronts action fits available data and disables without artifact', async ({ page }) => {
    await instrumentRouteMapFits(page);
    await useFullBriefingMode(page);
    const refs = await mockBriefingApi(page);
    await page.unroute('**/api/hewson-map/fronts**');
    let releaseAxis!: () => void;
    const axisGate = new Promise<void>((resolve) => { releaseAxis = resolve; });
    let markAxisRequested!: () => void;
    const axisRequested = new Promise<void>((resolve) => { markAxisRequested = resolve; });
    await page.route('**/api/hewson-map/fronts**', async (route) => {
      markAxisRequested();
      await axisGate;
      await route.fulfill({
        json: {
          model: 'ecmwf',
          init_time: '2026-07-10T00:00:00Z',
          valid_time: '2026-07-10T00:00:00Z',
          level: 850,
          hour: 0,
          stride_hours: 3,
          gate: 'default',
          gate_config: {},
          fronts: [{
            kind: 'cold',
            length_km: 180,
            mean_gradient: 4,
            mean_delta_theta_e: 5,
            coordinates: [[-0.72, 51.31], [-1.65, 51.31]],
          }],
        },
      });
    });
    await openBriefing(page);

    await page.locator('[data-advisory="fronts"] .advisory-action-btn').click();
    await axisRequested;
    await expect(page.locator('#viz-layout-wrapper')).toHaveClass(/layout-map/);
    await expect(page.locator('#map-fronts-toggle')).toBeChecked();
    await expect(page.locator('#map-container.leaflet-container')).toBeVisible();
    await expect.poll(() => page.evaluate(() => (
      (window as any).__routeMapFitCalls as unknown[]
    ).length)).toBe(1);
    expect(await page.evaluate(() => (
      (window as any).__routeMap.getBounds().contains([51.31, -1.65])
    ))).toBe(false);

    releaseAxis();

    const renderedAxis = page.locator(
      '#map-container .leaflet-overlay-pane '
      + 'path[stroke="#2563eb"][stroke-width="3"][stroke-opacity="0.72"][fill="none"]',
    );
    await expect(renderedAxis).toHaveCount(1);
    await expect.poll(() => page.evaluate(() => (
      (window as any).__routeMapFitCalls as Array<{ west: number }>
    ).filter(call => call.west <= -1.6).length)).toBe(1);
    await expect.poll(() => page.evaluate(() => (
      (window as any).__routeMapFitCalls as unknown[]
    ).length)).toBe(2);
    await expect.poll(() => page.evaluate(() => (
      (window as any).__routeMap.getBounds().contains([51.31, -1.65])
    ))).toBe(true);

    refs.routeFronts.current = null;
    await page.reload();
    await expect(page.locator('[data-advisory="fronts"]')).toBeVisible();
    const disabled = page.locator('[data-advisory="fronts"] .advisory-action-btn');
    await expect(disabled).toHaveAttribute('aria-disabled', 'true');
    await expect(page.locator('[data-advisory="fronts"] .advisory-action-unavailable'))
      .toContainText(/Fronts.*unavailable/);
  });

  test('fronts action stays disabled when every result is unavailable', async ({ page }) => {
    await useFullBriefingMode(page);
    const refs: MockBriefingRefs = {
      advisories: { current: clone(fixture('advisories.json')) },
      routeFronts: { current: routeFrontsFixture() },
      airportRequests: [],
    };
    const fronts = refs.advisories.current.advisories.find(
      (advisory: any) => advisory.advisory_id === 'fronts',
    );
    fronts.aggregate_status = 'unavailable';
    for (const result of fronts.per_model) {
      result.status = 'unavailable';
      result.data_state = 'unavailable';
      result.evidence_regions = [];
    }
    await mockBriefingApi(page, refs);
    await openBriefing(page);

    const action = page.locator('[data-advisory="fronts"] .advisory-action-btn');
    await expect(action).toBeDisabled();
    await expect(action).toHaveAttribute('aria-disabled', 'true');
    await expect(page.locator('[data-advisory="fronts"] .advisory-action-unavailable'))
      .toContainText(/Fronts.*unavailable/);

    await action.evaluate((button: HTMLButtonElement) => button.click());
    await expect(page.locator('#viz-layout-wrapper')).not.toHaveClass(/layout-map/);
    await expect(page.locator('#map-container.leaflet-container')).toHaveCount(0);
  });

  test('legacy and unavailable packs never fabricate a halo', async ({ page }) => {
    await useFullBriefingMode(page);
    const refs: MockBriefingRefs = {
      advisories: { current: clone(fixture('advisories.json')) },
      routeFronts: { current: routeFrontsFixture() },
      airportRequests: [],
    };
    const cloud = refs.advisories.current.advisories.find(
      (advisory: any) => advisory.advisory_id === 'cloud_top',
    );
    const ecmwf = cloud.per_model.find((model: any) => model.model === 'ecmwf');
    delete ecmwf.data_state;
    delete ecmwf.evidence_regions;
    await mockBriefingApi(page, refs);
    await openBriefing(page);

    await page.locator('[data-advisory="cloud_top"] .advisory-action-btn').click();
    await expect(page.locator('#viz-preset-select')).toHaveValue('clouds');
    await expect(page.locator('[data-cloud-source="nwp"]')).toBeChecked();
    await expect(page.locator('input[data-layer-id="freezing-level"]')).toBeChecked();
    await page.locator('#viz-controls [data-layout="split"]').click();
    await expect(page.locator('#map-container.leaflet-container')).toBeVisible();
    await expect(page.locator('#advisory-focus-banner'))
      .toContainText('Older briefing — location unavailable');
    await expect(evidencePaths(page)).toHaveCount(0);

    refs.advisories.current = clone(fixture('advisories.json'));
    const unavailableCloud = refs.advisories.current.advisories.find(
      (advisory: any) => advisory.advisory_id === 'cloud_top',
    );
    unavailableCloud.per_model.find((model: any) => model.model === 'ecmwf').data_state = 'unavailable';
    await page.reload();
    await page.locator('[data-advisory="cloud_top"] .advisory-action-btn').click();
    await expect(page.locator('#viz-preset-select')).toHaveValue('clouds');
    await expect(page.locator('[data-cloud-source="nwp"]')).toBeChecked();
    await expect(page.locator('input[data-layer-id="freezing-level"]')).toBeChecked();
    await page.locator('#viz-controls [data-layout="split"]').click();
    await expect(page.locator('#map-container.leaflet-container')).toBeVisible();
    await expect(page.locator('#advisory-focus-banner')).toContainText('Location unavailable');
    await expect(evidencePaths(page)).toHaveCount(0);
  });

  test('legacy advisory without a valid model still applies its generic preset', async ({ page }) => {
    await useFullBriefingMode(page, { layout: 'map' });
    await page.addInitScript(() => {
      (window as any).__scrollTargets = [];
      Element.prototype.scrollIntoView = function scrollIntoView(): void {
        (window as any).__scrollTargets.push((this as Element).id);
      };
    });
    const refs: MockBriefingRefs = {
      advisories: { current: clone(fixture('advisories.json')) },
      routeFronts: { current: routeFrontsFixture() },
      airportRequests: [],
    };
    const cloud = refs.advisories.current.advisories.find(
      (advisory: any) => advisory.advisory_id === 'cloud_top',
    );
    cloud.representative_model = null;
    cloud.per_model = [];
    await mockBriefingApi(page, refs);
    await openBriefing(page);

    await expect(page.locator('#viz-model-select')).toHaveValue('gfs');
    await expect(page.locator('#viz-layout-wrapper')).toHaveClass(/layout-map/);
    await page.evaluate(() => { (window as any).__scrollTargets.length = 0; });
    await page.locator('[data-advisory="cloud_top"] .advisory-action-btn').click();

    await expect(page.locator('#viz-preset-select')).toHaveValue('clouds');
    await expect(page.locator('[data-cloud-source="nwp"]')).toBeChecked();
    await expect(page.locator('input[data-layer-id="freezing-level"]')).toBeChecked();
    await expect(page.locator('#viz-layout-wrapper')).toHaveClass(/layout-split/);
    await expect(page.locator('#viz-model-select')).toHaveValue('gfs');
    await expect(page.locator('#advisory-focus-banner')).toBeHidden();
    await expect(evidencePaths(page)).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => (
      (window as any).__scrollTargets.includes('viz-section')
    ))).toBe(true);
  });

  test('invalid requested and representative models do not fabricate attribution', async ({ page }) => {
    await useFullBriefingMode(page);
    const refs: MockBriefingRefs = {
      advisories: { current: clone(fixture('advisories.json')) },
      routeFronts: { current: routeFrontsFixture() },
      airportRequests: [],
    };
    const cloud = refs.advisories.current.advisories.find(
      (advisory: any) => advisory.advisory_id === 'cloud_top',
    );
    cloud.representative_model = 'ukmo';
    await mockBriefingApi(page, refs);
    await openBriefing(page);

    await page.locator(
      '[data-advisory="cloud_top"] .adv-model-badge[data-model="ukmo"]',
    ).click();
    await page.getByRole('button', { name: 'Show on chart: UK Met Office' }).click();

    await expect(page.locator('#viz-preset-select')).toHaveValue('clouds');
    await expect(page.locator('#viz-model-select')).toHaveValue('gfs');
    await expect(page.locator('#advisory-focus-banner'))
      .toContainText('Older briefing — evidence model unavailable');
    await page.keyboard.press('Escape');
    await page.locator('#viz-controls [data-layout="split"]').click();
    await expect(evidencePaths(page)).toHaveCount(0);
  });

  test('partial evidence has text and non-colour cues', async ({ page }) => {
    await useFullBriefingMode(page);
    await mockBriefingApi(page);
    await openBriefing(page);

    await page.locator('[data-advisory="turbulence"] .advisory-action-btn').click();
    await page.locator('#viz-controls [data-layout="split"]').click();

    const banner = page.locator('#advisory-focus-banner');
    await expect(banner).toContainText('Partial data');
    await expect(banner).toHaveAttribute('aria-label', /Partial data/);
    await expect(banner).toHaveClass(/advisory-focus--partial/);
    await expect(banner.locator('.advisory-focus-partial-key')).toBeVisible();
    await expect(evidencePaths(page).first()).toHaveAttribute('stroke-dasharray', /24/);
  });
});
