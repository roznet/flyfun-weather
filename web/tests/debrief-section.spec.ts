import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// Debrief section (#1024): after the live window the owner's briefing page
// opens its Observed group on the server's report. The egtf_eglf fixture with
// its departure 3 h ago; the report is the shared fixture built by the #1023
// server on a replay (app/fixtures/api/debrief-report.json). `/recap` is
// served as "not recorded" so this journey does not depend on the recap.

const FIXTURES = join(__dirname, 'fixtures', 'egtf_eglf');
const TIMESTAMP = '2026-02-25T16:10:07.255073+00:00';
const REPORT = JSON.parse(readFileSync(join(__dirname, '..', '..', 'app', 'fixtures', 'api', 'debrief-report.json'), 'utf-8'));

const fixture = (name: string) => JSON.parse(readFileSync(join(FIXTURES, name), 'utf-8'));
const iso = (d: Date) => d.toISOString().replace('.000Z', '+00:00');

async function mockApi(page: Page) {
  const departure = new Date(Math.floor((Date.now() - 180 * 60_000) / 60_000) * 60_000);
  const id = `egtf_eglf-${iso(departure).slice(0, 10)}-1024`;
  const enc = encodeURIComponent;
  const flight = fixture('flight.json');
  await page.route('**/auth/me', r => r.fulfill({ json: {
    id: flight.user_id, email: 't@e.com', name: 'T', approved: true, is_admin: false, setup_completed: true,
  } }));
  await page.route(`**/api/flights/${id}`, r => r.fulfill({ json: {
    ...flight, id, departure_time: iso(departure), target_date: iso(departure).slice(0, 10),
    role: 'owner', debrief: null, debrief_ready: true, questions_open: REPORT.questions.length,
  } }));
  const meta = { ...fixture('pack_meta.json'), fetch_timestamp: TIMESTAMP, days_out: 0 };
  await page.route(`**/api/flights/${id}/packs`, r => r.fulfill({ json: [meta] }));
  await page.route(`**/api/flights/${id}/packs/${enc(TIMESTAMP)}`, r => r.fulfill({ json: meta }));
  await page.route(`**/api/flights/${id}/packs/latest`, r => r.fulfill({ json: meta }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/snapshot`, r => r.fulfill({ json: fixture('snapshot.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/route-analyses`, r => r.fulfill({ json: fixture('route_analyses.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/advisories`, r => r.fulfill({ json: fixture('advisories.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/elevation`, r => r.fulfill({ json: fixture('elevation.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/digest/json`, r => r.fulfill({ json: fixture('digest.json') }));
  await page.route(`**/packs/${enc(TIMESTAMP)}/gramet**`, r => r.fulfill({ status: 404, json: { detail: 'none' } }));
  await page.route('**/api/observed/**', r => r.fulfill({ json: {} }));
  await page.route(`**/api/flights/${id}/live`, r => r.fulfill({ json: {
    flight_id: id, pack_timestamp: TIMESTAMP, live_updated_at: null, route_observations: null,
    observations_updated_at: null, route_sigmets: null, sigmets_updated_at: null, observed_conditions: null,
    observed_updated_at: null, last_refresh_delta: null, changes: null, storms: null, glance: null, ribbon: null,
  } }));
  await page.route(`**/api/flights/${id}/live/ticks**`, r => r.fulfill({ json: { flight_id: id, ticks: [] } }));
  await page.route(`**/api/flights/${id}/recap**`, r => r.fulfill({ json: {
    flight_id: id, status: 'not_recorded', message: 'Observations were not recorded for this flight.',
    cancelled: false, segments: [], waypoints: [], alerts: [], weather: [], storms: [], packs: [TIMESTAMP],
  } }));

  const saved: { debrief: unknown[]; answers: unknown[] } = { debrief: [], answers: [] };
  let report = structuredClone(REPORT);
  await page.route(`**/api/flights/${id}/debrief/report`, r => r.fulfill({ json: report }));
  await page.route(`**/api/flights/${id}/debrief/answers`, r => {
    const body = r.request().postDataJSON();
    saved.answers.push(body);
    report = structuredClone(report);
    for (const a of body.answers) {
      const q = report.questions.find((x: { id: string }) => x.id === a.question_id);
      q.answer = a.answer === null ? null : {
        kind: q.kind, answer: a.answer, avoided: !!a.avoided, note: null,
        answered_at: iso(new Date()), rules_version: '1',
      };
    }
    return r.fulfill({ json: report });
  });
  await page.route(`**/api/flights/${id}/debrief`, r => {
    const body = r.request().postDataJSON();
    saved.debrief.push(body);
    return r.fulfill({ json: {
      flight_id: id, decision: body.decision, reasons: body.reasons ?? [], outcomes: {}, note: body.note ?? null,
      form: body.form ?? 'legacy', created_at: iso(new Date()), updated_at: iso(new Date()),
    } });
  });
  return { id, saved };
}

test('the debrief section asks the server questions after a flown tap and stores one answer', async ({ page }) => {
  const { id, saved } = await mockApi(page);
  await page.goto(`/briefing.html?flight=${id}#debrief`);
  const section = page.locator('[data-testid="debrief-section"]');
  await expect(section).toBeVisible();
  await expect(section.locator('[data-testid="debrief-headline"]')).toHaveText(REPORT.summary.headline);
  await expect(section.locator('[data-testid="debrief-question"]')).toHaveCount(0);

  await section.locator('[data-testid="debrief-flown"]').click();
  await expect(section.locator('[data-testid="debrief-question"]')).toHaveCount(REPORT.questions.length);
  expect(saved.debrief).toEqual([{ decision: 'flown', reasons: [], note: null, form: 'questions' }]);

  const icing = REPORT.questions[0];
  await section.locator('[data-testid="debrief-avoided"]').first().check();
  await section.locator(`[data-question="${icing.id}"][data-answer="light"]`).click();
  await expect(section.locator(`[data-question="${icing.id}"][data-answer="light"]`)).toHaveClass(/active/);
  expect(saved.answers).toEqual([{ answers: [{ question_id: icing.id, answer: 'light', avoided: true }] }]);
  await expect(section.locator('[data-testid="debrief-avoided"]').first()).toBeChecked();

  // The details fold carries the server's lines.
  await section.locator('[data-testid="debrief-details"] summary').click();
  await expect(section.locator('[data-testid="debrief-details"] li')).toHaveCount(REPORT.summary.lines.length);
  await section.screenshot({ path: 'test-results/debrief-section.png' });
});
