import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FIXTURES = join(__dirname, 'fixtures', 'egtf_eglf');

// Use a future date so the flight shows in the "upcoming" section, not collapsed under "past"
const FUTURE_DATE = new Date(Date.now() + 3 * 86400_000).toISOString().slice(0, 10);
const FLIGHT_ID = `egtf_eglf-${FUTURE_DATE}-45ed`;
const TIMESTAMP = '2026-02-25T16:10:07.255073+00:00';

function fixture(name: string) {
  const data = JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
  return data;
}

function allGreenAdvisoriesFixture() {
  const manifest = fixture('advisories.json');
  manifest.advisories = manifest.advisories.map((advisory: any) => ({
    ...advisory,
    aggregate_status: 'green',
    aggregate_detail: 'No significant conditions',
    representative_model: advisory.per_model[0]?.model ?? null,
    aggregate_mitigations: [],
    per_model: advisory.per_model.map((model: any) => ({
      ...model,
      status: 'green',
      detail: 'No significant conditions',
      data_state: 'complete',
      evidence_regions: [],
      affected_points: 0,
      affected_pct: 0,
      affected_nm: 0,
      affected_mod_points: 0,
      affected_mod_pct: 0,
      affected_mod_nm: 0,
      mitigations: [],
    })),
  }));
  return manifest;
}

/** Patch flight-facing dates to use FUTURE_DATE so flights aren't in the past.
 *  Only patches target_date and departure_time — NOT fetch_timestamp or other metadata. */
function patchFlightDates(obj: Record<string, any>): Record<string, any> {
  const out = { ...obj };
  if (out.target_date) out.target_date = FUTURE_DATE;
  if (out.departure_time && typeof out.departure_time === 'string') {
    out.departure_time = out.departure_time.replace(/\d{4}-\d{2}-\d{2}/, FUTURE_DATE);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Full flight lifecycle test
// ---------------------------------------------------------------------------

test('flight lifecycle: create → view → save settings → altitude overlay → delete', async ({ page }) => {
  const enc = encodeURIComponent;
  const flightData = patchFlightDates({ ...fixture('flight.json'), id: FLIGHT_ID });
  const packMetaData = { ...fixture('pack_meta.json'), flight_id: FLIGHT_ID };
  // The shared #223 fixture intentionally contains representative, partial,
  // unavailable, and legacy states. This end-to-end lifecycle scenario needs
  // an all-green planned-altitude baseline before exercising the altitude table.
  const advisoriesData = allGreenAdvisoriesFixture();

  for (const advisory of advisoriesData.advisories) {
    const advisoryLabel = advisory.advisory_id;
    expect(advisory.aggregate_status, `${advisoryLabel} aggregate status`).toBe('green');
    expect(advisory.aggregate_detail, `${advisoryLabel} aggregate detail`).toBe('No significant conditions');

    const representative = advisory.per_model.find(
      (model: any) => model.model === advisory.representative_model,
    );
    expect(representative, `${advisoryLabel} representative model`).toBeDefined();
    expect(representative.status, `${advisoryLabel} representative status`).toBe(advisory.aggregate_status);
    expect(representative.detail, `${advisoryLabel} representative detail`).toBe(advisory.aggregate_detail);

    for (const model of advisory.per_model) {
      const modelLabel = `${advisoryLabel}/${model.model}`;
      expect(model.status, `${modelLabel} status`).toBe('green');
      expect(model.detail, `${modelLabel} detail`).toBe('No significant conditions');
      expect(model.data_state, `${modelLabel} data state`).toBe('complete');
      expect(model.evidence_regions, `${modelLabel} evidence regions`).toEqual([]);
      expect(model.affected_points, `${modelLabel} affected points`).toBe(0);
      expect(model.affected_pct, `${modelLabel} affected percent`).toBe(0);
      expect(model.affected_nm, `${modelLabel} affected distance`).toBe(0);
    }
  }

  for (const advisory of advisoriesData.advisories) {
    expect(advisory.aggregate_mitigations, `${advisory.advisory_id} aggregate mitigations`).toEqual([]);
    for (const model of advisory.per_model) {
      const modelLabel = `${advisory.advisory_id}/${model.model}`;
      expect(model.affected_mod_points, `${modelLabel} moderate affected points`).toBe(0);
      expect(model.affected_mod_pct, `${modelLabel} moderate affected percent`).toBe(0);
      expect(model.affected_mod_nm, `${modelLabel} moderate affected distance`).toBe(0);
      expect(model.mitigations, `${modelLabel} mitigations`).toEqual([]);
    }
  }

  // Precomputed altitude table (#259). The lever no longer triggers a full
  // recalc — it indexes this cached table client-side via overlayAltitudeStatuses
  // for instant per-altitude statuses (the old #recalc-advisories-btn is gone).
  // At the 2000ft cruise everything is green; at 8000ft turbulence goes RED and
  // cloud_top AMBER, so dragging the lever there must repaint those cards.
  const altitudeTableData = {
    rows: [
      { altitude_ft: 2000, statuses: { turbulence: 'green', cloud_top: 'green' }, red_count: 0, amber_count: 0, green_count: 13 },
      { altitude_ft: 4000, statuses: { turbulence: 'amber', cloud_top: 'green' }, red_count: 0, amber_count: 1, green_count: 12 },
      { altitude_ft: 8000, statuses: { turbulence: 'red', cloud_top: 'amber' }, red_count: 1, amber_count: 1, green_count: 11 },
    ],
    advisory_ids: ['turbulence', 'cloud_top'],
    advisory_names: { turbulence: 'Turbulence', cloud_top: 'Cloud Tops' },
    cruise_altitude_ft: 2000,
    flight_ceiling_ft: 18000,
    step_ft: 2000,
    best_below_cruise: 2000,
    best_above_cruise: null,
  };

  // --- Mutable state flags ---
  let flightsCreated = false;

  // --- Mock user auth ---
  await page.route('**/auth/me', route =>
    route.fulfill({
      json: {
        id: 'test-user-001',
        email: 'test@example.com',
        name: 'Test User',
        approved: true,
        is_admin: false,
        setup_completed: true,
      },
    }),
  );

  // --- Mock user profiles ---
  await page.route('**/api/user/profiles', route => {
    if (route.request().method() === 'GET') {
      // Only handle exact path, not sub-paths like /profiles/1
      const url = route.request().url();
      if (/\/profiles\/\d/.test(url)) return route.fallthrough();
      return route.fulfill({
        json: [{
          id: 1,
          name: 'Default',
          is_default: true,
          settings: {
            cruise_altitude_ft: 8000,
            flight_ceiling_ft: 18000,
            speed_kt: null,
            models: ['ecmwf', 'gfs', 'icon', 'ukmo', 'meteofrance'],
            advisory_models: ['ecmwf', 'gfs', 'icon', 'ukmo', 'meteofrance'],
            gramet_enabled: true,
            llm_digest_enabled: true,
            icing_severity_enhance: false,
            icing_method: 'ogimet_dd',
            cloud_method: 'dd',
            convective_method: 'thermo',
            flight_rules: 'vfr_ifr',
            advisories: { enabled: null, params: null, aggregation: 'majority' },
          },
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-01-01T00:00:00',
        }],
      });
    }
    return route.fallthrough();
  });

  // --- Mock PUT /api/user/profiles/1 (save settings) ---
  await page.route('**/api/user/profiles/1', route => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      return route.fulfill({
        json: {
          id: 1,
          name: 'Default',
          is_default: true,
          settings: body.settings ?? {},
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-03-01T00:00:00',
        },
      });
    }
    return route.fallthrough();
  });

  // --- Mock model catalog ---
  await page.route('**/api/models', route =>
    route.fulfill({
      json: [
        { key: 'ecmwf', name: 'ECMWF', default: true },
        { key: 'gfs', name: 'GFS', default: true },
        { key: 'icon', name: 'ICON', default: true },
        { key: 'ukmo', name: 'UKMO', default: true },
        { key: 'meteofrance', name: 'Météo-France', default: true },
      ],
    }),
  );

  // --- Mock user preferences ---
  await page.route('**/api/user/preferences', route => {
    const url = route.request().url();
    // Let sub-paths (like /preferences/advisories/catalog) fall through
    if (url.includes('/preferences/') || url.endsWith('/preferences/')) return route.fallthrough();
    return route.fulfill({
      json: {
        defaults: { cruise_altitude_ft: 8000, flight_ceiling_ft: 18000, models: null, advisory_models: null },
        digest_config: { config_name: null },
        advisories: { enabled: null, params: null, aggregation: 'majority' },
        has_autorouter_creds: false,
        gramet_enabled: true,
        llm_digest_enabled: true,
        icing_severity_enhance: false,
        icing_method: 'ogimet_dd',
        cloud_method: 'dd',
        convective_method: 'thermo',
      },
    });
  });

  // --- Mock advisory catalog ---
  await page.route('**/api/user/preferences/advisories/catalog', route =>
    route.fulfill({ json: advisoriesData.catalog }),
  );

  // --- Mock interpret-route ---
  await page.route('**/api/flights/interpret-route', route => {
    const body = route.request().postDataJSON();
    const raw = (body?.raw_route ?? '').toUpperCase();
    const tokens = raw.split(/[\s,\-/]+/).filter(Boolean);
    const known = new Set(['EGTF', 'EGLF', 'EGBJ', 'LFOV', 'EGTK', 'LSGS', 'LFPB']);
    const interpreted = tokens.filter(t => known.has(t));
    const skipped = tokens.filter(t => !known.has(t));
    return route.fulfill({
      json: {
        original_tokens: tokens,
        interpreted,
        skipped,
        off_route: [],
        waypoints: interpreted.map(icao => ({
          icao,
          name: icao,
          lat: 51.0,
          lon: -1.0,
          timezone: 'Europe/London',
        })),
      },
    });
  });

  // --- Mock flights list ---
  // Regex (not a glob) so it also matches the paginated `?past_limit=…` query
  // string the store now sends; bare `**/api/flights` would miss it.
  await page.route(/\/api\/flights(\?.*)?$/, route => {
    const url = route.request().url();
    const method = route.request().method();

    // Only match the flights list endpoint, not sub-paths
    if (url.includes(`/flights/${FLIGHT_ID}`) || url.includes('/flights/route-distance'))
      return route.fallthrough();

    if (method === 'POST') {
      flightsCreated = true;
      return route.fulfill({ json: flightData });
    }
    // GET — return empty or with flight depending on state
    return route.fulfill({ json: flightsCreated ? [flightData] : [] });
  });

  // --- Mock route distance ---
  await page.route('**/api/flights/route-distance', route =>
    route.fulfill({ json: { total_distance_nm: 9.2 } }),
  );

  // --- Mock active refreshes ---
  await page.route('**/api/refresh/active', route =>
    route.fulfill({ json: [] }),
  );

  // --- Mock flight detail ---
  await page.route(`**/api/flights/${FLIGHT_ID}`, route => {
    const url = route.request().url();
    const method = route.request().method();
    if (url.includes('/packs')) return route.fallthrough();
    if (method === 'DELETE') {
      flightsCreated = false;
      return route.fulfill({ status: 204, body: '' });
    }
    return route.fulfill({ json: flightData });
  });

  // --- Mock packs list ---
  await page.route(`**/api/flights/${FLIGHT_ID}/packs`, route => {
    const url = route.request().url();
    const afterPacks = url.split('/packs')[1];
    if (afterPacks && afterPacks !== '' && afterPacks !== '/')
      return route.fallthrough();
    return route.fulfill({ json: fixture('packs.json').map((p: any) => ({ ...p, flight_id: FLIGHT_ID })) });
  });

  // --- Mock pack metadata ---
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/${enc(TIMESTAMP)}`, route => {
    const url = route.request().url();
    const afterTs = url.split(enc(TIMESTAMP))[1];
    if (afterTs && afterTs !== '' && afterTs !== '/')
      return route.fallthrough();
    return route.fulfill({ json: packMetaData });
  });

  // --- Mock packs/latest ---
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/latest`, route =>
    route.fulfill({ json: packMetaData }),
  );

  // --- Mock snapshot ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/snapshot`, route =>
    route.fulfill({ json: fixture('snapshot.json') }),
  );

  // --- Mock route analyses ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/route-analyses`, route =>
    route.fulfill({ json: fixture('route_analyses.json') }),
  );

  // --- Mock advisories (baked pack manifest) ---
  // The GET serves the frozen route_advisories.json; it does NOT re-apply the
  // current profile aggregation, so a settings change won't retroactively worsen
  // a baked pack (only a refresh would). Let the sub-paths fall through to their
  // own routes.
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories`, route => {
    const url = route.request().url();
    if (url.includes('/recalculate') || url.includes('/altitude-table') || url.includes('/alt'))
      return route.fallthrough();
    return route.fulfill({ json: advisoriesData });
  });

  // --- Mock advisories recalculate (wind-overlay path) ---
  // The lever's debounced release fires this only to refresh the route-graph
  // wind overlay; advisory card statuses come from the cached altitude table, so
  // keep this inert (baseline manifest, no overlay) to stay hermetic.
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories/recalculate`, route =>
    route.fulfill({ json: { manifest: advisoriesData, wind_overlay: null } }),
  );

  // --- Mock precomputed altitude table (cached GET + on-demand POST sweep) ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories/altitude-table**`, route =>
    route.fulfill({ json: altitudeTableData }),
  );

  // --- Mock elevation ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/elevation`, route =>
    route.fulfill({ json: fixture('elevation.json') }),
  );

  // --- Mock digest ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/digest/json`, route =>
    route.fulfill({ json: fixture('digest.json') }),
  );

  // --- Mock gramet (404) ---
  await page.route(`**/packs/${enc(TIMESTAMP)}/gramet**`, route =>
    route.fulfill({ status: 404, json: { detail: 'GRAMET not available' } }),
  );

  // --- Mock freshness ---
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/freshness`, route =>
    route.fulfill({ json: { is_fresh: true, reason: 'fixture data' } }),
  );

  // --- Mock refresh status ---
  await page.route(`**/api/flights/${FLIGHT_ID}/packs/refresh/status`, route =>
    route.fulfill({ json: { active: false } }),
  );

  // --- Mock usage (non-critical, can 404) ---
  await page.route('**/api/user/usage', route =>
    route.fulfill({ status: 404, json: { detail: 'not available' } }),
  );

  // --- Mock credits (non-critical, can 404) ---
  await page.route('**/api/user/credits**', route =>
    route.fulfill({ status: 404, json: { detail: 'not available' } }),
  );

  // --- Mock aircraft list ---
  await page.route('**/api/user/aircraft', route =>
    route.fulfill({ json: [] }),
  );

  // --- Mock DWD overview and skewt (non-critical) ---
  await page.route('**/dwd-overview**', route =>
    route.fulfill({ status: 404, json: { detail: 'not available' } }),
  );
  await page.route('**/skewt/**', route =>
    route.fulfill({ status: 404, json: { detail: 'not available' } }),
  );
  await page.route('**/hodograph/**', route =>
    route.fulfill({ status: 404, json: { detail: 'not available' } }),
  );

  // =========================================================================
  // Phase 1: Create a flight on the flights page
  // =========================================================================

  await page.goto('/');

  // Should show empty state
  await expect(page.locator('.empty-state')).toContainText('No flights yet');

  // Fill in the flight creation form
  await page.fill('#input-waypoints', 'EGTF EGLF');
  await page.fill('#input-date', FUTURE_DATE);
  await page.selectOption('#input-hour', '17');
  await page.selectOption('#input-minute', '0');
  await page.fill('#input-altitude', '2000');
  // Provide a non-zero duration so the zero-duration confirm popup doesn't fire
  await page.selectOption('#input-duration-hours', '1');

  // Submit the form — triggers POST and navigates to briefing
  await page.click('#create-flight-form button[type="submit"]');

  // =========================================================================
  // Phase 2: Verify briefing page shows all-green advisories
  // =========================================================================

  await page.waitForURL(/briefing\.html/);


  // Verify route info is visible
  await expect(page.getByText('EGTF')).toBeVisible();

  // Wait for advisories to render
  await expect(page.locator('#advisories-wrapper')).toBeVisible();

  // Under majority aggregation, all advisories are GREEN
  await expect(page.locator('.advisory-summary .badge-green')).toBeVisible();
  // No red or amber badges should be present
  await expect(page.locator('.advisory-summary .badge-red')).not.toBeVisible();
  await expect(page.locator('.advisory-summary .badge-amber')).not.toBeVisible();

  // =========================================================================
  // Phase 3: Settings round-trip — change aggregation to "worst" and save.
  // (Standalone coverage: a baked pack is NOT re-evaluated from this; only a
  // refresh would. Phase 4 asserts the pack stays green.)
  // =========================================================================

  await page.click('a[href="/settings.html"]');
  await page.waitForURL(/settings\.html/);

  // Change aggregation mode
  await page.selectOption('#advisory-aggregation', 'worst');

  // Submit settings form
  await page.click('#settings-form button[type="submit"]');

  // Verify success message
  await expect(page.locator('#status-message')).toContainText('Settings saved');

  // =========================================================================
  // Phase 4: Return to briefing and drive the altitude lever — the precomputed
  // table overlays worse statuses client-side (no recalc button anymore, #259).
  // =========================================================================

  // Navigate back to flights list
  await page.click('a[href="/"]');
  await page.waitForURL(/\/$/);

  // Flight card may be under "Past flights" if departure is in the past
  const pastToggle = page.locator('button:has-text("Past flights")');
  if (await pastToggle.isVisible()) {
    await pastToggle.click();
  }

  // Flight card should now be visible
  await expect(page.locator('.flight-card')).toBeVisible();

  // Click "Briefing" on the flight card
  await page.click('.flight-card button:has-text("Briefing")');
  await page.waitForURL(/briefing\.html/);

  // Wait for advisories to load. The baked pack is unaffected by the saved
  // aggregation change (no refresh ran), so it's still all-green at 2000ft cruise.
  await expect(page.locator('#advisories-wrapper')).toBeVisible();
  await expect(page.locator('.advisory-summary .badge-green')).toBeVisible();
  await expect(page.locator('.advisory-summary .badge-red')).not.toBeVisible();

  // Drag the altitude lever from 2000ft cruise to 8000ft. `input` fires during
  // drag (sets the override → re-render with the table overlay); `change` fires
  // on release (debounced wind overlay). Dispatch both to mimic a real drag+drop.
  const slider = page.locator('#advisory-alt-slider');
  await expect(slider).toBeVisible();
  await slider.evaluate((el, val) => {
    const input = el as HTMLInputElement;
    input.value = String(val);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }, 8000);

  // At 8000ft the cached table marks turbulence RED and cloud_top AMBER — the
  // overlay must repaint those cards and the summary counts without a refetch.
  await expect(page.locator('.advisory-card[data-advisory="turbulence"].advisory-red')).toBeVisible();
  await expect(page.locator('.advisory-card[data-advisory="cloud_top"].advisory-amber')).toBeVisible();
  await expect(page.locator('.advisory-summary .badge-red')).toBeVisible();
  await expect(page.locator('.advisory-summary .badge-amber')).toBeVisible();

  // The delta note (sibling below the toolbar) reports the altitude-vs-planned
  // change live from the same table.
  await expect(page.locator('#advisory-alt-delta')).toContainText('8000ft');
  await expect(page.locator('#advisory-alt-delta')).toContainText('worsens');

  // =========================================================================
  // Phase 5: Delete the flight
  // =========================================================================

  // Navigate to flights list
  await page.click('a[href="/"]');
  await page.waitForURL(/\/$/);

  // Expand past flights if collapsed
  const pastToggle2 = page.locator('button:has-text("Past flights")');
  if (await pastToggle2.isVisible()) {
    await pastToggle2.click();
  }

  // Flight card should exist
  await expect(page.locator('.flight-card')).toBeVisible();

  // Accept the confirm dialog before clicking delete
  page.on('dialog', dialog => dialog.accept());

  // Click delete
  await page.click('.flight-card button:has-text("Delete")');

  // Should return to empty state
  await expect(page.locator('.empty-state')).toContainText('No flights yet');
});
