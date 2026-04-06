/** Verification dashboard entry point — model accuracy stats for admins. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  fetchVerificationStats,
  fetchVerificationAirports,
  type VerificationDigest,
  type VerificationPeriod,
  type VerificationSource,
  type CategoryAccuracyRow,
  type NotableMiss,
  type MissedWarning,
  type CategoryBiasStats,
} from './adapters/admin-adapter';
import { renderUserInfo, escapeHtml } from './utils';
import { initTheme } from './theme';
import { initI18n } from './i18n/i18n';
import { initInfoPopup, showPopupContent } from './components/info-popup';

let currentPeriod: VerificationPeriod = '24h';
let currentSource: VerificationSource = 'flight';
let currentCountry: string | undefined;
let currentIcao: string | undefined;
let airportsByCountry: Record<string, string[]> = {};

async function init(): Promise<void> {
  await initI18n();
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  initTheme();
  const isEmbed = new URLSearchParams(window.location.search).has('embed');
  if (isEmbed) {
    // Hide header when embedded in iframe
    const hdr = document.querySelector('.page-header') as HTMLElement | null;
    if (hdr) hdr.style.display = 'none';
  } else {
    renderUserInfo(user, 'maps');
  }
  setupPeriodToggle();
  setupSourceToggle();
  initInfoPopup();
  setupInfoButtons();
  await loadAirports();
  setupLocationFilter();
  await loadData();
}

function setupPeriodToggle(): void {
  document.querySelectorAll('#period-toggle .toggle-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const period = (btn as HTMLElement).dataset.period as VerificationPeriod;
      if (period === currentPeriod) return;
      currentPeriod = period;
      document.querySelectorAll('#period-toggle .toggle-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      loadData();
    });
  });
}

function setupSourceToggle(): void {
  document.querySelectorAll('#source-toggle .toggle-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const source = (btn as HTMLElement).dataset.source as VerificationSource;
      if (source === currentSource) return;
      currentSource = source;
      document.querySelectorAll('#source-toggle .toggle-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      loadData();
    });
  });
}

async function loadAirports(): Promise<void> {
  try {
    airportsByCountry = await fetchVerificationAirports();
  } catch {
    airportsByCountry = {};
  }
}

const COUNTRY_LABELS: Record<string, string> = {
  BI: 'Iceland (BI)', EB: 'Belgium (EB)', ED: 'Germany (ED)',
  EE: 'Estonia (EE)', EF: 'Finland (EF)', EG: 'United Kingdom (EG)',
  EH: 'Netherlands (EH)', EI: 'Ireland (EI)', EK: 'Denmark (EK)',
  EL: 'Luxembourg (EL)', EN: 'Norway (EN)', EP: 'Poland (EP)',
  ES: 'Sweden (ES)', EV: 'Latvia (EV)', EY: 'Lithuania (EY)',
  LA: 'Albania (LA)', LB: 'Bulgaria (LB)', LD: 'Croatia (LD)',
  LE: 'Spain (LE)', LF: 'France (LF)', LG: 'Greece (LG)',
  LH: 'Hungary (LH)', LI: 'Italy (LI)', LJ: 'Slovenia (LJ)',
  LK: 'Czech Republic (LK)', LO: 'Austria (LO)', LP: 'Portugal (LP)',
  LQ: 'Bosnia & Herz. (LQ)', LR: 'Romania (LR)', LS: 'Switzerland (LS)',
  LW: 'N. Macedonia (LW)', LY: 'Serbia (LY)', LZ: 'Slovakia (LZ)',
};

function setupLocationFilter(): void {
  const container = document.getElementById('location-filter');
  if (!container) return;

  const countries = Object.keys(airportsByCountry).sort();
  if (countries.length === 0) return;

  let html = '<select id="country-select" style="padding:0.4rem 0.6rem;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:0.8rem;">';
  html += '<option value="">All countries</option>';
  for (const c of countries) {
    html += `<option value="${c}">${escapeHtml(COUNTRY_LABELS[c] || c)}</option>`;
  }
  html += '</select>';

  html += ' <select id="airport-select" style="padding:0.4rem 0.6rem;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:0.8rem;display:none;">';
  html += '<option value="">All airports</option>';
  html += '</select>';

  container.innerHTML = html;

  const countryEl = document.getElementById('country-select') as HTMLSelectElement;
  const airportEl = document.getElementById('airport-select') as HTMLSelectElement;

  countryEl.addEventListener('change', () => {
    const val = countryEl.value || undefined;
    currentCountry = val;
    currentIcao = undefined;

    // Populate airport dropdown
    if (val && airportsByCountry[val]) {
      let opts = '<option value="">All airports</option>';
      for (const icao of airportsByCountry[val]) {
        opts += `<option value="${icao}">${icao}</option>`;
      }
      airportEl.innerHTML = opts;
      airportEl.style.display = '';
    } else {
      airportEl.style.display = 'none';
    }
    airportEl.value = '';
    loadData();
  });

  airportEl.addEventListener('change', () => {
    currentIcao = airportEl.value || undefined;
    loadData();
  });
}

async function loadData(): Promise<void> {
  try {
    const data = await fetchVerificationStats(currentPeriod, currentSource, currentCountry, currentIcao);
    renderSummary(data);
    renderAccuracy(data);
    renderBias(data);
    renderMisses(data);
    renderWind(data);
    renderMAE(data);
    renderHealth(data);
  } catch (err) {
    console.error('Failed to load verification stats', err);
  }
}

// ---------------------------------------------------------------------------
// Info button helper — uses the shared metric-popup modal
// ---------------------------------------------------------------------------

const SECTION_INFO: Record<string, string> = {
  accuracy: `
    <div class="popup-header"><h3>Flight Category Accuracy</h3></div>
    <p class="popup-vibe">How often does each model get the flight category right?</p>
    <div class="popup-section"><h4>Goal</h4>
      <p>Measure how often each NWP model correctly predicts the flight category (VFR, MVFR, IFR, LIFR) at airports along the route.</p>
    </div>
    <div class="popup-section"><h4>How It Works</h4>
      <p>Every 3 hours, we compare the model's forecast to the actual METAR observation at each airport. The percentage shows how often the predicted category matched the observed one.</p>
      <p><strong>D-0</strong> = forecast made same day, <strong>D-1</strong> = 1 day ahead, <strong>D-2</strong> = 2 days ahead, etc.</p>
      <p><strong>TAF</strong> = the official aerodrome forecast issued by human forecasters (always D-0).</p>
    </div>
    <div class="popup-section"><h4>Reading the Table</h4>
      <p>Each cell shows the match rate and sample count. Arrows indicate change vs the 7-day rolling average (\u25B2 improving, \u25BC declining).</p>
      <p>Green \u2265 80%, amber \u2265 60%, red &lt; 60%.</p>
    </div>`,

  bias: `
    <div class="popup-header"><h3>Category Bias</h3></div>
    <p class="popup-vibe">When a model gets it wrong, does it err on the safe side or the dangerous side?</p>
    <div class="popup-section"><h4>Optimistic (Opt)</h4>
      <p>Model predicted better conditions than what actually occurred. This is the <strong>dangerous direction</strong> for flight planning \u2014 the weather was worse than expected.</p>
    </div>
    <div class="popup-section"><h4>Pessimistic (Pess)</h4>
      <p>Model predicted worse conditions than actual. Conservative and safer, but can cause unnecessary cancellations or diversions.</p>
    </div>
    <div class="popup-section"><h4>Severity Levels</h4>
      <p><strong>+1</strong> = one category off (e.g. predicted VFR, actual MVFR)</p>
      <p><strong>+2</strong> = two categories off (e.g. predicted VFR, actual IFR)</p>
      <p><strong>+3</strong> = three categories off (e.g. predicted VFR, actual LIFR)</p>
      <p>Percentages are relative to total scores for that model/day combination.</p>
    </div>`,

  misses: `
    <div class="popup-header"><h3>Notable Misses</h3></div>
    <p class="popup-vibe">Individual cases where a model got the category significantly wrong.</p>
    <div class="popup-section"><h4>What's Shown</h4>
      <p>Category busts at D-0 and D-1, sorted by importance: optimistic misses first (safety-relevant), then by severity.</p>
    </div>
    <div class="popup-section"><h4>Severity Column</h4>
      <p><strong>\u26a0 +N</strong> = optimistic miss \u2014 actual was N categories worse than predicted. Red background for 2+ levels, amber for 1 level.</p>
      <p><strong>\u2193 +N</strong> = pessimistic miss \u2014 actual was N categories better than predicted (blue background). Only severity 2+ shown.</p>
    </div>
    <div class="popup-section"><h4>Ceiling \u0394</h4>
      <p>Difference between model ceiling and observed ceiling in feet. Negative means model predicted lower ceiling than actual.</p>
    </div>`,

  wind: `
    <div class="popup-header"><h3>Wind Advisory Accuracy</h3></div>
    <p class="popup-vibe">How well do models predict crosswind and gust conditions at airports?</p>
    <div class="popup-section"><h4>How It Works</h4>
      <p>Wind advisories are computed from crosswind and gust components relative to the active runway. Three levels: OK, CAUTION, WARNING.</p>
      <p>Match rate shows how often the model predicted the correct advisory level compared to actual METAR winds.</p>
    </div>
    <div class="popup-section"><h4>Missed Wind Warnings</h4>
      <p>Cases where actual conditions triggered a WARNING but the model failed to predict it \u2014 the most safety-critical wind misses.</p>
    </div>`,

  mae: `
    <div class="popup-header"><h3>Mean Absolute Error</h3></div>
    <p class="popup-vibe">On average, how far off is each model for key weather parameters?</p>
    <div class="popup-section"><h4>How It Works</h4>
      <p>For each observation, we compute |predicted \u2212 actual| and average across all data points. Lower values = more accurate model.</p>
    </div>
    <div class="popup-section"><h4>Parameters</h4>
      <p><strong>Ceiling (ft)</strong> \u2014 average error in cloud ceiling height. E.g., 500 means the model is off by ~500ft on average.</p>
      <p><strong>Visibility (m)</strong> \u2014 average error in visibility distance.</p>
      <p><strong>Wind (kt)</strong> \u2014 average error in wind speed.</p>
      <p><strong>Temp (\u00B0C)</strong> \u2014 average error in surface temperature. A good sanity check for overall model quality.</p>
    </div>
    <div class="popup-section popup-limitations"><h4>Limitations</h4>
      <p>Only D-0 and D-1 forecasts are shown. Longer range errors are expected to be larger and less informative for comparison.</p>
    </div>`,

  health: `
    <div class="popup-header"><h3>System Health</h3></div>
    <p class="popup-vibe">Operational metrics for the verification pipeline.</p>
    <div class="popup-section"><h4>Metrics</h4>
      <p><strong>Cycles</strong> \u2014 number of data collection runs in the selected period. Flight verification runs every 10 minutes; standalone runs at 06, 09, 12, 15, 18 UTC.</p>
      <p><strong>Avg Cycle</strong> \u2014 average time to complete one collection cycle (fetch METARs, store observations, score against models).</p>
    </div>`,
};

function infoBtn(sectionId: string): string {
  if (!SECTION_INFO[sectionId]) return '';
  return `<span class="info-btn" data-info="${sectionId}">i</span>`;
}

function setupInfoButtons(): void {
  document.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.info-btn[data-info]') as HTMLElement | null;
    if (!btn) return;
    const id = btn.dataset.info || '';
    const content = SECTION_INFO[id];
    if (content) showPopupContent(content);
  });
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

function renderSummary(data: VerificationDigest): void {
  const a = data.activity;
  const cards: { value: string | number; label: string }[] = [];

  if (currentSource === 'flight') {
    cards.push({ value: a.flights_verified, label: 'Flights' });
    cards.push({ value: a.flights_completed, label: 'Completed' });
  }
  cards.push({ value: a.airports_observed, label: 'Airports' });
  cards.push({ value: a.observations_collected, label: 'Observations' });
  cards.push({ value: a.cycles_run, label: 'Cycles' });
  cards.push({ value: fmtDuration(a.avg_cycle_duration_ms), label: 'Avg Cycle' });

  const el = document.getElementById('summary-bar')!;
  el.innerHTML = cards
    .map(
      (c) =>
        `<div class="summary-card"><div class="value">${c.value}</div><div class="label">${c.label}</div></div>`,
    )
    .join('');
}

function accuracyClass(pct: number | null): string {
  if (pct == null) return '';
  if (pct >= 80) return 'accuracy-green';
  if (pct >= 60) return 'accuracy-amber';
  return 'accuracy-red';
}

function fmtPct(pct: number | null): string {
  if (pct == null) return '—';
  return `${Math.round(pct)}%`;
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toISOString().slice(11, 16) + 'Z';
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}m${sec.toString().padStart(2, '0')}s`;
}

function renderAccuracy(data: VerificationDigest): void {
  const el = document.getElementById('accuracy-section')!;
  const todayMap = new Map<string, CategoryAccuracyRow>();
  const sevenMap = new Map<string, CategoryAccuracyRow>();
  const modelsSet = new Set<string>();
  const daysOut = [0, 1, 2, 3];

  for (const r of data.category_accuracy_today) {
    todayMap.set(`${r.model}:${r.days_out}`, r);
    modelsSet.add(r.model);
  }
  for (const r of data.category_accuracy_7d) {
    sevenMap.set(`${r.model}:${r.days_out}`, r);
    modelsSet.add(r.model);
  }

  const models = [...modelsSet].filter((m) => m !== 'TAF').sort();
  if (modelsSet.has('TAF')) models.push('TAF');

  if (models.length === 0) {
    el.innerHTML = `<h2>Flight Category Accuracy ${infoBtn('accuracy')}</h2><p class="empty-state">No accuracy data available.</p>`;
    return;
  }

  let html = `<h2>Flight Category Accuracy ${infoBtn('accuracy')}</h2>`;
  html += '<table class="admin-table"><thead><tr><th>Model</th>';
  for (const d of daysOut) html += `<th class="num">D-${d}</th>`;
  html += '</tr></thead><tbody>';

  for (const model of models) {
    html += `<tr><td style="font-weight:600">${escapeHtml(model.toUpperCase())}</td>`;
    for (const d of daysOut) {
      const key = `${model}:${d}`;
      const today = todayMap.get(key);
      const seven = sevenMap.get(key);
      const pct = today?.accuracy_pct ?? null;
      const n = today?.sample_count ?? 0;
      const cls = accuracyClass(pct);

      let trend = '';
      if (pct != null && seven?.accuracy_pct != null) {
        const diff = pct - seven.accuracy_pct;
        if (Math.abs(diff) >= 1) {
          const arrow = diff > 0 ? '\u25B2' : '\u25BC';
          const color = diff > 0 ? 'var(--green-text, #0f5132)' : 'var(--red-text, #dc2626)';
          trend = ` <span style="font-size:0.7rem;color:${color}">${arrow}</span>`;
        }
      }

      const cellText = n > 0 ? `${fmtPct(pct)} <span style="font-size:0.7rem;color:var(--text-muted)">(${n})</span>` : '—';
      html += `<td class="num ${cls}">${cellText}${trend}</td>`;
    }
    html += '</tr>';
  }

  html += '</tbody></table>';
  html += '<p style="font-size:0.75rem;color:var(--text-muted);margin-top:0.25rem">Arrows show change vs 7-day rolling average</p>';
  el.innerHTML = html;
}

function renderBias(data: VerificationDigest): void {
  const el = document.getElementById('bias-section');
  if (!el) return;
  const bias = data.category_bias;

  if (bias.length === 0) {
    el.innerHTML = `<h2>Category Bias ${infoBtn('bias')}</h2><p class="empty-state">No data available.</p>`;
    return;
  }

  let html = `<h2>Category Bias ${infoBtn('bias')}</h2>`;
  html += '<table class="admin-table"><thead><tr>';
  html += '<th>Model</th><th class="num">D-out</th><th class="num">Scores</th>';
  html += '<th class="num" title="Model predicted better than actual by 1 cat level">Opt +1</th>';
  html += '<th class="num" title="Model predicted better than actual by 2 cat levels">Opt +2</th>';
  html += '<th class="num" title="Model predicted better than actual by 3 cat levels">Opt +3</th>';
  html += '<th class="num" title="Model predicted worse than actual by 1 cat level">Pess +1</th>';
  html += '<th class="num" title="Model predicted worse than actual by 2 cat levels">Pess +2</th>';
  html += '<th class="num" title="Model predicted worse than actual by 3 cat levels">Pess +3</th>';
  html += '</tr></thead><tbody>';

  for (const b of bias) {
    const optTotal = b.optimistic_1 + b.optimistic_2 + b.optimistic_3;
    const pessTotal = b.pessimistic_1 + b.pessimistic_2 + b.pessimistic_3;
    const fmtCell = (n: number, danger: boolean) => {
      if (n === 0) return '<td class="num" style="color:var(--text-muted)">—</td>';
      const pct = ((n / b.total_scores) * 100).toFixed(1);
      const cls = danger && n > 0 ? ' style="color:#dc3545;font-weight:600"' : '';
      return `<td class="num"${cls}>${n} <small style="color:var(--text-muted)">(${pct}%)</small></td>`;
    };
    html += '<tr>';
    html += `<td style="font-weight:600">${escapeHtml(b.model.toUpperCase())}</td>`;
    html += `<td class="num">D-${b.days_out}</td>`;
    html += `<td class="num">${b.total_scores}</td>`;
    html += fmtCell(b.optimistic_1, true);
    html += fmtCell(b.optimistic_2, true);
    html += fmtCell(b.optimistic_3, true);
    html += fmtCell(b.pessimistic_1, false);
    html += fmtCell(b.pessimistic_2, false);
    html += fmtCell(b.pessimistic_3, false);
    html += '</tr>';
    // Summary row if model has both D-0 and D-1 — skip for now, per-row is clear
  }

  html += '</tbody></table>';
  el.innerHTML = html;
}

function renderMisses(data: VerificationDigest): void {
  const el = document.getElementById('misses-section')!;
  const misses = data.notable_misses;

  if (misses.length === 0) {
    el.innerHTML = `<h2>Notable Misses ${infoBtn('misses')}</h2><p class="empty-state">No significant category busts.</p>`;
    return;
  }

  let html = `<h2>Notable Misses ${infoBtn('misses')}</h2>`;
  html += '<table class="admin-table"><thead><tr>';
  for (const h of ['ICAO', 'Time', 'Model', 'D-out', 'Observed', 'Predicted', 'Severity', 'Ceiling \u0394']) {
    html += `<th>${h}</th>`;
  }
  html += '</tr></thead><tbody>';

  for (const m of misses) {
    const rowClass = m.direction === 'optimistic'
      ? (m.severity >= 2 ? 'miss-row-danger' : 'miss-row-warn')
      : 'miss-row-info';
    const ceiling = m.ceiling_delta_ft != null ? `${m.ceiling_delta_ft > 0 ? '+' : ''}${Math.round(m.ceiling_delta_ft)}ft` : '—';
    const sevLabel = m.direction === 'optimistic'
      ? `\u26a0 +${m.severity}`   // ⚠ optimistic
      : `\u2193 +${m.severity}`;   // ↓ pessimistic

    html += `<tr class="${rowClass}">`;
    html += `<td style="font-weight:600">${escapeHtml(m.icao)}</td>`;
    html += `<td>${fmtTime(m.observation_time)}</td>`;
    html += `<td>${escapeHtml(m.model.toUpperCase())}</td>`;
    html += `<td class="num">D-${m.days_out}</td>`;
    html += `<td style="font-weight:600">${escapeHtml(m.obs_category)}</td>`;
    html += `<td>${escapeHtml(m.model_category)}</td>`;
    html += `<td class="num">${sevLabel}</td>`;
    html += `<td class="num">${ceiling}</td>`;
    html += '</tr>';
  }

  html += '</tbody></table>';
  el.innerHTML = html;
}

function renderWind(data: VerificationDigest): void {
  const el = document.getElementById('wind-section')!;
  let html = `<h2>Wind Advisory Accuracy ${infoBtn('wind')}</h2>`;

  if (data.wind_advisory.length > 0) {
    html += '<table class="admin-table"><thead><tr><th>Model</th><th class="num">Match Rate</th><th class="num">Samples</th></tr></thead><tbody>';
    for (const w of data.wind_advisory) {
      const cls = accuracyClass(w.accuracy_pct);
      html += `<tr><td style="font-weight:600">${escapeHtml(w.model.toUpperCase())}</td>`;
      html += `<td class="num ${cls}">${fmtPct(w.accuracy_pct)}</td>`;
      html += `<td class="num">${w.sample_count}</td></tr>`;
    }
    html += '</tbody></table>';
  } else {
    html += '<p class="empty-state">No wind advisory data.</p>';
  }

  if (data.missed_warnings.length > 0) {
    html += '<h3 style="font-size:0.9rem;margin:1rem 0 0.5rem;color:var(--red-text, #dc2626)">Missed Wind Warnings</h3>';
    html += '<table class="admin-table"><thead><tr><th>ICAO</th><th>Time</th><th>Model</th><th>Predicted</th></tr></thead><tbody>';
    for (const w of data.missed_warnings) {
      html += `<tr class="miss-row-danger">`;
      html += `<td style="font-weight:600">${escapeHtml(w.icao)}</td>`;
      html += `<td>${fmtTime(w.observation_time)}</td>`;
      html += `<td>${escapeHtml(w.model.toUpperCase())}</td>`;
      html += `<td>${escapeHtml(w.model_wind_advisory)}</td>`;
      html += '</tr>';
    }
    html += '</tbody></table>';
  }

  el.innerHTML = html;
}

function renderMAE(data: VerificationDigest): void {
  const el = document.getElementById('mae-section')!;
  const mae = data.mae_stats;

  if (mae.length === 0) {
    el.innerHTML = `<h2>Mean Absolute Error (D-0 / D-1) ${infoBtn('mae')}</h2><p class="empty-state">No MAE data available.</p>`;
    return;
  }

  let html = `<h2>Mean Absolute Error (D-0 / D-1) ${infoBtn('mae')}</h2>`;
  html += '<table class="admin-table"><thead><tr>';
  for (const h of ['Model', 'D-out', 'Ceiling (ft)', 'Visibility (m)', 'Wind (kt)', 'Temp (\u00B0C)', 'Samples']) {
    html += `<th class="num">${h}</th>`;
  }
  html += '</tr></thead><tbody>';

  for (const m of mae) {
    html += '<tr>';
    html += `<td style="font-weight:600">${escapeHtml(m.model.toUpperCase())}</td>`;
    html += `<td class="num">D-${m.days_out}</td>`;
    html += `<td class="num">${m.ceiling_mae_ft != null ? Math.round(m.ceiling_mae_ft) : '—'}</td>`;
    html += `<td class="num">${m.visibility_mae_m != null ? Math.round(m.visibility_mae_m) : '—'}</td>`;
    html += `<td class="num">${m.wind_speed_mae_kt != null ? m.wind_speed_mae_kt.toFixed(1) : '—'}</td>`;
    html += `<td class="num">${m.temperature_mae_c != null ? m.temperature_mae_c.toFixed(1) : '—'}</td>`;
    html += `<td class="num">${m.sample_count}</td>`;
    html += '</tr>';
  }

  html += '</tbody></table>';
  el.innerHTML = html;
}

function renderHealth(data: VerificationDigest): void {
  const el = document.getElementById('health-section')!;
  const a = data.activity;

  let html = `<h2>System Health ${infoBtn('health')}</h2>`;
  html += '<table class="admin-table"><thead><tr><th>Metric</th><th class="num">Value</th></tr></thead><tbody>';
  html += `<tr><td>Verification cycles</td><td class="num">${a.cycles_run}</td></tr>`;
  html += `<tr><td>Avg cycle duration</td><td class="num">${fmtDuration(a.avg_cycle_duration_ms)}</td></tr>`;
  html += `<tr><td>Total observations stored</td><td class="num">${a.observations_collected}</td></tr>`;
  if (currentSource === 'flight') {
    html += `<tr><td>Flights with verification</td><td class="num">${a.flights_verified}</td></tr>`;
  }
  html += '</tbody></table>';

  el.innerHTML = html;
}

init();
