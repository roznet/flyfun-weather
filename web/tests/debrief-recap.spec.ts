import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// Debrief recap + single-tick replay (#769). Reuses the egtf_eglf fixture
// with its departure moved: 3 h ago (window past → the recap) or 30 min out
// (inside the window → live plus tick chips). `/recap`, `/live/ticks` and
// `/live/at` are synthetic: fictional times and words, the shapes the server
// serves (models/live.py RecapResponse / LiveTicksResponse / LiveTickResponse).

const FIXTURES = join(__dirname, 'fixtures', 'egtf_eglf');
const TIMESTAMP = '2026-02-25T16:10:07.255073+00:00';
const OLDER = '2026-02-25T06:10:07.255073+00:00';

function fixture(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
}

const iso = (d: Date) => d.toISOString().replace('.000Z', '+00:00');

interface Scenario {
  departure: Date;
  flightId: string;
  at: (min: number) => string;
}

function scenario(minutesFromNow: number): Scenario {
  // Whole minutes, so tick labels are stable.
  const departure = new Date(Math.floor((Date.now() + minutesFromNow * 60_000) / 60_000) * 60_000);
  const date = departure.toISOString().slice(0, 10);
  return {
    departure,
    flightId: `egtf_eglf-${date}-769${minutesFromNow < 0 ? 'p' : 'w'}`,
    at: (min: number) => iso(new Date(departure.getTime() + min * 60_000)),
  };
}

const hhmm = (s: string) => `${s.slice(11, 16)}Z`;

function ticks(sc: Scenario) {
  return [-20, 10, 30].map((m, i) => ({
    tick_at: sc.at(m), radar_time: sc.at(m - 8), pack_timestamp: TIMESTAMP,
    flown_nm: Math.max(0, m) * (100 / 60), progress_source: 'plan', alert_count: i === 2 ? 1 : 0,
    segments: [
      { from_nm: 0, to_nm: 50, radar_status: 'measured', radar_max_dbz: i === 1 ? 30 : null, lightning: false },
      { from_nm: 50, to_nm: 100, radar_status: 'measured', radar_max_dbz: i === 2 ? 46 : null, lightning: i === 2 },
    ],
  }));
}

function lane(sc: Scenario, pack: string, severity: string) {
  return {
    pack_timestamp: pack, chosen_by: pack === TIMESTAMP ? 'default' : 'request',
    planned_departure: sc.at(-45), available: true, v: 1, route_nm: 100,
    cruise_altitude_ft: 4000, models: ['gfs', 'ecmwf'], aggregation: 'worst',
    advisories: [
      { advisory_id: 'fiki_icing', aggregate_status: severity, representative_model: 'gfs',
        per_model: [
          { model: 'gfs', status: severity, ribbon: [[0, 50, 'green'], [50, 100, severity]] },
          { model: 'ecmwf', status: 'green', ribbon: [[0, 100, 'green']] },
        ] },
    ],
  };
}

function recap(sc: Scenario, pack: string | null) {
  return {
    flight_id: sc.flightId, note: 'Observed along the route at the time the aircraft was there.',
    status: 'ok', message: null, cancelled: false, time_base: 'planned', progress_source: 'plan',
    departed_at: iso(sc.departure), arrival_at: sc.at(60),
    ticks: 3, first_tick_at: sc.at(-20), last_tick_at: sc.at(30), capped: false,
    forecast: lane(sc, pack ?? TIMESTAMP, pack === OLDER ? 'red' : 'amber'),
    alerts: [{
      at: sc.at(30), event: 'raised', tier: 'alert', direction: 'worse', key: 'radar:seg1',
      kind: 'radar', role: 'route', icao: null, message: 'Heavy rain 6 NM right of track',
      aircraft_nm: 50,
    }],
    route_nm: 100, segment_nm: 50, radar_radius_nm: 10, weather_corridor_nm: 30, weather_bin_nm: 5,
    waypoints: [
      { icao: 'EGTF', along_nm: 0, eta: iso(sc.departure) },
      { icao: 'EGLF', along_nm: 100, eta: sc.at(60) },
    ],
    segments: [
      { index: 0, from_nm: 0, to_nm: 50, at: sc.at(15), status: 'observed', tick_at: sc.at(10),
        radar_time: sc.at(2), pack_timestamp: TIMESTAMP, gap_min: -13, weather_status: 'unavailable',
        radar_max_dbz: 30, radar_intensity: 'moderate', radar_status: 'measured', lightning: false,
        sigmet_ids: [], storm_ids: [] },
      { index: 1, from_nm: 50, to_nm: 100, at: sc.at(45), status: 'not_reached' },
    ],
    weather: [], storms: [], packs: [TIMESTAMP, OLDER],
  };
}

function glance(text: string, highlight: string | null, asOf: string) {
  return {
    as_of: asOf, headline: text, comparison: 'as_briefed',
    highlight: highlight ? { text: highlight, model: 'fixture', facts_hash: `h-${highlight}`, generated_at: asOf } : null,
    lines: [{ phase: 'enroute', icao: null, alert: false, passed: false, text, unavailable: [], sources: [], focus: null }],
  };
}

function ribbon(sc: Scenario, dbz: number | null) {
  return {
    route_nm: 100, flown_nm: 0, segment_nm: 50, radar_radius_nm: 10, radar_time: sc.at(2),
    departure_at: iso(sc.departure), arrival_at: sc.at(60), weather_status: 'unavailable',
    waypoints: [{ icao: 'EGTF', along_nm: 0, eta: iso(sc.departure) }, { icao: 'EGLF', along_nm: 100, eta: sc.at(60) }],
    segments: [
      { index: 0, from_nm: 0, to_nm: 50, radar_status: 'measured', radar_max_dbz: dbz, lightning: false },
      { index: 1, from_nm: 50, to_nm: 100, radar_status: 'measured', radar_max_dbz: null, lightning: false },
    ],
    stations: [], sigmets: [], weather: [],
  };
}

function liveLayer(sc: Scenario, withGlance: boolean) {
  return {
    flight_id: sc.flightId, pack_timestamp: TIMESTAMP, live_updated_at: withGlance ? iso(new Date()) : null,
    route_observations: null, observations_updated_at: null, route_sigmets: null, sigmets_updated_at: null,
    observed_conditions: null, observed_updated_at: null, last_refresh_delta: null, changes: null,
    storms: null,
    glance: withGlance ? glance('Live now: nothing within 30 NM', 'Live highlight line', iso(new Date())) : null,
    ribbon: withGlance ? ribbon(sc, null) : null,
  };
}

function tickView(sc: Scenario, tickAt: string) {
  return {
    ...liveLayer(sc, false),
    live_updated_at: tickAt, tick_at: tickAt, radar_time: tickAt, progress: { source: 'plan' },
    glance: glance(`Tick ${hhmm(tickAt)}: rain 4 NM left`, `Replayed highlight ${hhmm(tickAt)}`, tickAt),
    ribbon: ribbon(sc, 30),
  };
}

async function mockApi(page: Page, sc: Scenario, opts: { recapStatus?: string } = {}) {
  const id = sc.flightId;
  const enc = encodeURIComponent;
  await page.route(`**/api/flights/${id}`, route => route.fulfill({ json: {
    ...fixture('flight.json'), id, departure_time: iso(sc.departure), target_date: iso(sc.departure).slice(0, 10),
  } }));
  await page.route(`**/api/flights/${id}/packs`, route => route.fulfill({ json: [
    { ...fixture('pack_meta.json'), fetch_timestamp: TIMESTAMP, days_out: 0 },
    { ...fixture('pack_meta.json'), fetch_timestamp: OLDER, days_out: 0 },
  ] }));
  for (const ts of [TIMESTAMP, OLDER]) {
    await page.route(`**/api/flights/${id}/packs/${enc(ts)}`, r =>
      r.fulfill({ json: { ...fixture('pack_meta.json'), fetch_timestamp: ts, days_out: 0 } }));
    await page.route(`**/packs/${enc(ts)}/snapshot`, r => r.fulfill({ json: fixture('snapshot.json') }));
    await page.route(`**/packs/${enc(ts)}/route-analyses`, r => r.fulfill({ json: fixture('route_analyses.json') }));
    await page.route(`**/packs/${enc(ts)}/advisories`, r => r.fulfill({ json: fixture('advisories.json') }));
    await page.route(`**/packs/${enc(ts)}/elevation`, r => r.fulfill({ json: fixture('elevation.json') }));
    await page.route(`**/packs/${enc(ts)}/digest/json`, r => r.fulfill({ json: fixture('digest.json') }));
    await page.route(`**/packs/${enc(ts)}/gramet**`, r => r.fulfill({ status: 404, json: { detail: 'none' } }));
  }
  await page.route(`**/api/flights/${id}/packs/latest`, r =>
    r.fulfill({ json: { ...fixture('pack_meta.json'), fetch_timestamp: TIMESTAMP, days_out: 0 } }));
  await page.route('**/api/observed/**', r => r.fulfill({ json: {} }));
  await page.route(`**/api/flights/${id}/live`, r => r.fulfill({ json: liveLayer(sc, sc.departure.getTime() > Date.now() - 3600_000) }));
  await page.route(`**/api/flights/${id}/live/ticks**`, r =>
    r.fulfill({ json: { flight_id: id, ticks: ticks(sc) } }));
  await page.route(`**/api/flights/${id}/live/at**`, r => {
    const tick = new URL(r.request().url()).searchParams.get('tick') as string;
    return r.fulfill({ json: tickView(sc, tick) });
  });
  await page.route(`**/api/flights/${id}/recap**`, r => {
    const pack = new URL(r.request().url()).searchParams.get('pack');
    if (opts.recapStatus && opts.recapStatus !== 'ok') {
      return r.fulfill({ json: {
        ...recap(sc, null), status: opts.recapStatus, segments: [], waypoints: [], alerts: [],
        message: 'Observations were not recorded for this flight (flown before per-tick recording started).',
      } });
    }
    return r.fulfill({ json: recap(sc, pack) });
  });
}

test.describe('Debrief recap after the live window', () => {
  const sc = scenario(-180);
  test.beforeEach(async ({ page }) => { await mockApi(page, sc); });

  test('opens on the recap: caption, diagonal times, forecast lane, alert marker', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    const recapEl = page.locator('[data-testid="recap"]');
    await expect(recapEl).toBeVisible();
    await expect(page.locator('[data-testid="recap-caption"]')).toHaveText(
      `Recap · planned schedule · forecast for ${hhmm(sc.at(-45))} departure, planned ${hhmm(iso(sc.departure))}`);
    // The recap ribbon (radar strip here: the cells feed was dark).
    await expect(recapEl.locator('.ribbon-svg')).toBeVisible();
    // Each observed stretch says which radar frame it came from; the rest
    // says why there is nothing, never "no echo".
    const lanes = recapEl.locator('.recap-lanes-svg');
    await expect(lanes).toContainText(hhmm(sc.at(2)));
    await expect(lanes).toContainText('not recorded');
    await expect(lanes.locator('[data-testid="forecast-lane"] rect')).toHaveCount(2);
    await expect(lanes.locator('[data-severity="amber"]')).toHaveCount(1);
    await expect(lanes.locator('.recap-alert-worse')).toHaveCount(1);
    // The live ribbon of the last tick is not left reading "now" under it.
    await expect(page.locator('#observed-ribbon-section')).toBeHidden();
    await expect(page.locator('[data-testid="tick-chips"] .tick-chip')).toHaveCount(3);
    await page.locator('#observed-ribbon-wrapper').screenshot({ path: 'test-results/debrief-recap.png' });
  });

  test('choosing a tick re-renders the Observed section, highlight included, and goes back', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    await expect(page.locator('[data-testid="recap"]')).toBeVisible();
    const tick = sc.at(10);
    await page.locator(`[data-testid="tick-chips"] [data-tick="${tick}"]`).click();
    await expect(page.locator('[data-testid="mode-bar-text"]')).toHaveText(`Viewing ${hhmm(tick)}`);
    await expect(page.locator('[data-testid="glance-highlight"]')).toContainText(`Replayed highlight ${hhmm(tick)}`);
    await expect(page.locator('.glance-line').first()).toContainText(`Tick ${hhmm(tick)}`);
    // The tick's own ribbon replaces the recap while it is on screen.
    await expect(page.locator('[data-testid="recap"]')).toHaveCount(0);
    await expect(page.locator('#observed-ribbon-section .ribbon-svg')).toBeVisible();
    await expect(page.locator(`[data-tick="${tick}"].tick-chip-active`)).toHaveCount(1);
    await expect(page.locator('[data-testid="observed-mode-bar"]')).toContainText('stay live');

    await page.locator('[data-testid="mode-bar-back"]').click();
    await expect(page.locator('[data-testid="observed-mode-bar"]')).toBeHidden();
    await expect(page.locator('[data-testid="recap"]')).toBeVisible();
  });

  test('tapping a recap stretch opens the tick it came from', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    await page.locator(`.recap-lanes-svg [data-seg-tick="${sc.at(10)}"]`).click();
    await expect(page.locator('[data-testid="mode-bar-text"]')).toHaveText(`Viewing ${hhmm(sc.at(10))}`);
  });

  test('clicking a point on the route maps to the aircraft time there, then the last tick before it', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    // 75 NM is not recorded on the diagonal; the aircraft was there at +45
    // min on the plan, so the last tick at or before it is the +30 one.
    await page.locator('.recap-lanes-svg [data-recap-seg="1"]').click();
    await expect(page.locator('[data-testid="mode-bar-text"]')).toHaveText(`Viewing ${hhmm(sc.at(30))}`);
  });

  test('the pack picker redraws the forecast lane only', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    const lanes = page.locator('.recap-lanes-svg');
    await expect(lanes.locator('[data-severity="amber"]')).toHaveCount(1);
    const recapReq = page.waitForRequest((r) => r.url().includes('/recap?pack='));
    await page.locator('#history-select').selectOption(OLDER);
    expect(new URL((await recapReq).url()).searchParams.get('pack')).toBe(OLDER);
    await expect(page.locator('.recap-lanes-svg [data-severity="red"]')).toHaveCount(1);
    await expect(page.locator('.recap-lanes-svg')).toContainText(`pack ${hhmm(OLDER)}`);
    // Same diagonal: the observed lane did not change.
    await expect(page.locator('.recap-lanes-svg')).toContainText(hhmm(sc.at(2)));
  });

  test('the latest pack pick wins the forecast row, even when an earlier response lands last', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    await expect(page.locator('.recap-lanes-svg [data-severity="amber"]')).toHaveCount(1);
    // The OLDER pick answers slowly, the pick back to TIMESTAMP at once.
    let releaseOlder: () => void = () => {};
    const olderHeld = new Promise<void>((resolve) => { releaseOlder = resolve; });
    await page.route(`**/api/flights/${sc.flightId}/recap?pack=*`, async (r) => {
      const pack = new URL(r.request().url()).searchParams.get('pack');
      if (pack === OLDER) await olderHeld;
      return r.fulfill({ json: recap(sc, pack) });
    });
    await page.locator('#history-select').selectOption(OLDER);
    await page.locator('#history-select').selectOption(TIMESTAMP);
    await expect(page.locator('.recap-lanes-svg')).toContainText(`pack ${hhmm(TIMESTAMP)}`);
    releaseOlder();
    await page.waitForTimeout(300);
    await expect(page.locator('.recap-lanes-svg [data-severity="red"]')).toHaveCount(0);
    await expect(page.locator('.recap-lanes-svg [data-severity="amber"]')).toHaveCount(1);
  });

  test('a pack picked while the section is collapsed shows on expand', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    await expect(page.locator('.recap-lanes-svg [data-severity="amber"]')).toHaveCount(1);
    const heading = page.locator('#observed-ribbon-wrapper > h3');
    await heading.click();
    await expect(page.locator('#observed-ribbon-wrapper')).toHaveClass(/collapsed/);
    const recapReq = page.waitForResponse((r) => r.url().includes('/recap?pack='));
    await page.locator('#history-select').selectOption(OLDER);
    await recapReq;
    await heading.click();
    await expect(page.locator('.recap-lanes-svg [data-severity="red"]')).toHaveCount(1);
    await expect(page.locator('[data-testid="recap-caption"]')).toBeVisible();
  });

  test('a pack whose row fails to load says so, rather than keep the previous row', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    await expect(page.locator('.recap-lanes-svg [data-severity="amber"]')).toHaveCount(1);
    await page.route(`**/api/flights/${sc.flightId}/recap?pack=*`, r => r.fulfill({ status: 500, json: { detail: 'boom' } }));
    await page.locator('#history-select').selectOption(OLDER);
    await expect(page.locator('[data-testid="forecast-lane-empty"]')).toBeVisible();
    await expect(page.locator('.recap-lanes-svg')).toContainText(`pack ${hhmm(OLDER)}`);
  });

  test('the grid stacks one row per tick with the diagonal outlined', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    const toggle = page.locator('[data-grid-toggle]');
    if (!(await toggle.isChecked())) await toggle.check();
    const grid = page.locator('.grid-svg');
    await expect(grid.locator('.grid-row')).toHaveCount(3);
    await expect(grid.locator('.grid-diagonal')).toHaveCount(1);
    await page.locator('.recap-grid').screenshot({ path: 'test-results/debrief-grid.png' });
    await grid.locator(`[data-grid-tick="${sc.at(30)}"]`).click();
    await expect(page.locator('[data-testid="mode-bar-text"]')).toHaveText(`Viewing ${hhmm(sc.at(30))}`);
  });
});

test.describe('Debrief recap when nothing was recorded', () => {
  const sc = scenario(-190);
  test('says "Not recorded for this flight", never an empty "nothing observed"', async ({ page }) => {
    await mockApi(page, sc, { recapStatus: 'not_recorded' });
    await page.route(`**/api/flights/${sc.flightId}/live/ticks**`, r =>
      r.fulfill({ json: { flight_id: sc.flightId, ticks: [] } }));
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    const empty = page.locator('[data-testid="recap-empty"]');
    await expect(empty).toBeVisible();
    await expect(empty).toContainText('Not recorded for this flight');
    await expect(empty).toContainText('flown before per-tick recording started');
    await expect(page.locator('[data-testid="tick-chips"]')).toHaveCount(0);
  });
});

test.describe('Debrief recap that fails to load', () => {
  const sc = scenario(-200);
  test('says the recap is unavailable, not "not recorded", and hides the last tick\'s live ribbon', async ({ page }) => {
    await mockApi(page, sc);
    await page.route(`**/api/flights/${sc.flightId}/recap**`, r => r.fulfill({ status: 500, json: { detail: 'boom' } }));
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    const empty = page.locator('[data-testid="recap-empty"]');
    await expect(empty).toContainText('Recap unavailable');
    await expect(empty).not.toContainText('Not recorded');
    await expect(page.locator('#observed-ribbon-section')).toBeHidden();
  });
});

test.describe('Single-tick replay during the live window', () => {
  const sc = scenario(30);
  test.beforeEach(async ({ page }) => { await mockApi(page, sc); });

  test('a tick replaces the live view until "Back to live"', async ({ page }) => {
    await page.goto(`/briefing.html?flight=${sc.flightId}`);
    await expect(page.locator('[data-testid="glance-highlight"]')).toContainText('Live highlight line');
    // No recap during the window: live, plus the chips.
    await expect(page.locator('[data-testid="recap"]')).toHaveCount(0);
    const tick = sc.at(-20);
    await page.locator(`[data-testid="tick-chips"] [data-tick="${tick}"]`).click();
    await expect(page.locator('[data-testid="glance-highlight"]')).toContainText(`Replayed highlight ${hhmm(tick)}`);
    await expect(page.locator('[data-testid="mode-bar-back"]')).toHaveText('Back to live');
    // A replayed highlight is not rated: the log is for the line as met.
    await expect(page.locator('[data-hl-feedback]')).toHaveCount(0);
    await page.locator('[data-testid="mode-bar-back"]').click();
    await expect(page.locator('[data-testid="glance-highlight"]')).toContainText('Live highlight line');
    await expect(page.locator('[data-testid="observed-mode-bar"]')).toBeHidden();
    await expect(page.locator('[data-hl-feedback]')).toHaveCount(1);
  });
});
