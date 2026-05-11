/** Render the Data Sources & Models table from the public catalog endpoint.
 *
 * Two render modes:
 *   - 'summary' (help guide tab): trimmed columns — Variant, Provider,
 *     Coverage, Levels, Horizon — so the help page stays scannable.
 *   - 'full' (dedicated Data Sources tab): every column including the live
 *     state (latest run, covers until, next update).
 *
 * In both modes the per-source description and other metadata live behind
 * an (ⓘ) button that opens the shared info popup, instead of crowding the
 * table with a second row of subtext.
 *
 * The fetched catalog is cached at module scope so switching between the
 * two tabs doesn't re-hit the network.
 */

import { fetchDataSources, type DataSourceEntry, type DataSourcesResponse } from './adapters/data-sources-adapter';
import { showPopupContent } from './components/info-popup';
import { escapeHtml } from './utils';
import { t } from './i18n/i18n';

export type DataSourcesTableMode = 'summary' | 'full';

// --- Cache ---------------------------------------------------------------

let cached: DataSourcesResponse | null = null;
let inflight: Promise<DataSourcesResponse> | null = null;

async function loadCatalog(): Promise<DataSourcesResponse> {
  if (cached) return cached;
  if (inflight) return inflight;
  inflight = fetchDataSources().then((resp) => {
    cached = resp;
    inflight = null;
    return resp;
  }).catch((err) => {
    inflight = null;
    throw err;
  });
  return inflight;
}

// --- Grouping & ordering --------------------------------------------------

function groupByModel(sources: DataSourceEntry[]): Map<string, DataSourceEntry[]> {
  const groups = new Map<string, DataSourceEntry[]>();
  for (const s of sources) {
    const list = groups.get(s.model) || [];
    list.push(s);
    groups.set(s.model, list);
  }
  const roleOrder: Record<string, number> = {
    'primary-sounding': 0,
    'cloud-enrichment': 1,
    'surface-base': 2,
    'primary': 3,
  };
  for (const list of groups.values()) {
    list.sort((a, b) => (roleOrder[a.role] ?? 9) - (roleOrder[b.role] ?? 9));
  }
  return groups;
}

const MODEL_ORDER = ['ecmwf', 'icon_eu', 'icon', 'gfs', 'ukmo', 'meteofrance'];

function orderedModelKeys(groups: Map<string, DataSourceEntry[]>): string[] {
  const keys = Array.from(groups.keys());
  return keys.sort((a, b) => {
    const ia = MODEL_ORDER.indexOf(a);
    const ib = MODEL_ORDER.indexOf(b);
    if (ia === -1 && ib === -1) return a.localeCompare(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
}

// --- Formatters -----------------------------------------------------------

function fmtUtc(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const day = String(d.getUTCDate()).padStart(2, '0');
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const mon = months[d.getUTCMonth()];
  const h = String(d.getUTCHours()).padStart(2, '0');
  const m = String(d.getUTCMinutes()).padStart(2, '0');
  return `${day} ${mon} ${h}:${m} UTC`;
}

function fmtHorizon(entry: DataSourceEntry): string {
  const hours = Object.values(entry.horizon_hours);
  if (hours.length === 0) return '—';
  const min = Math.min(...hours);
  const max = Math.max(...hours);
  const fmt = (h: number) => h >= 48 ? `${Math.round(h / 24)} d` : `${Math.round(h)} h`;
  return min === max ? fmt(max) : `${fmt(min)}–${fmt(max)}`;
}

function uniformSpacing(cycles: number[]): number | null {
  if (cycles.length < 2) return null;
  const sorted = [...cycles].sort((a, b) => a - b);
  const gap = sorted[1] - sorted[0];
  for (let i = 2; i < sorted.length; i++) {
    if (sorted[i] - sorted[i - 1] !== gap) return null;
  }
  return gap;
}

function fmtCycles(cycles: number[]): string {
  if (cycles.length === 0) return '—';
  const gap = uniformSpacing(cycles);
  if (gap !== null && cycles.length >= 3) {
    return `${cycles.length}× / day (every ${gap}h)`;
  }
  const list = cycles.map(c => `${String(c).padStart(2, '0')}Z`).join(', ');
  return cycles.length >= 2 ? `${cycles.length}× / day (${list})` : list;
}

function fmtLevels(n: number | null): string {
  if (n === null) return '—';
  return String(n);
}

/** Split a resolution string into per-region parts. Handles three shapes:
 *    - "~6.5 km"                                → one part, region from coverage
 *    - "0.25° (~25 km)"                          → one part, region from coverage
 *    - "~11 km global, ~7 km over Europe (...)"  → two parts (Global / Europe)
 *    - "~25 km global, ~11 km over Europe, ~2.5 km over France (...)"
 *                                                → three parts
 *  Stripping (seamless) up front keeps each segment clean. */
function parseResolutionBreakdown(
  resolution: string,
  coverage: string,
): Array<{ region: string; value: string }> {
  if (!resolution) return [];
  const stripped = resolution.replace(/\s*\(seamless\)/gi, '').trim();
  const segments = stripped.split(',').map((s) => s.trim()).filter(Boolean);
  const parts: Array<{ region: string; value: string }> = [];
  for (const seg of segments) {
    const overMatch = seg.match(/^(.+?)\s+over\s+(.+)$/i);
    if (overMatch) {
      parts.push({ region: overMatch[2].trim(), value: overMatch[1].trim() });
      continue;
    }
    const globalMatch = seg.match(/^(.+?)\s+global$/i);
    if (globalMatch) {
      parts.push({ region: 'Global', value: globalMatch[1].trim() });
      continue;
    }
    parts.push({ region: '', value: seg });
  }
  // Single-segment entries with no region marker pick up their region from
  // the coverage string (cleaned of lat/lon parentheticals).
  if (parts.length === 1 && parts[0].region === '') {
    const cleanCov = coverage
      .replace(/\s*\([^)]*°[NSEW][^)]*\)/g, '')
      .trim();
    parts[0].region = cleanCov || 'Global';
  }
  return parts;
}

function renderResolutionCell(entry: DataSourceEntry): string {
  const parts = parseResolutionBreakdown(entry.resolution, entry.coverage);
  if (parts.length === 0) return '—';
  const classes = ['ds-stack-primary', 'ds-stack-secondary', 'ds-stack-tertiary'];
  const lines = parts.map((p, i) =>
    `<div class="${classes[i] ?? 'ds-stack-tertiary'}">`
    + `${escapeHtml(p.region)}: ${escapeHtml(p.value)}`
    + `</div>`,
  );
  return `<div class="ds-stack">${lines.join('')}</div>`;
}

function roleBadge(role: string): string {
  const labelKey = `dataSources.role.${role.replace(/-/g, '_')}`;
  const fallback = role;
  const label = t(labelKey) !== labelKey ? t(labelKey) : fallback;
  return `<span class="data-source-role data-source-role-${escapeHtml(role)}">${escapeHtml(label)}</span>`;
}

/** Plain-text role label (no badge box) for the full-table 3-line model cell. */
function roleText(role: string): string {
  const labelKey = `dataSources.role.${role.replace(/-/g, '_')}`;
  const label = t(labelKey) !== labelKey ? t(labelKey) : role;
  return escapeHtml(label);
}

/** Trim coverage / resolution strings for the compact display:
 *    - drop lat/lon parentheticals like "(29.5–70.5°N, 23.5°W–62.5°E)"
 *    - drop the "(seamless)" qualifier
 *    - drop the word "over " ("~7 km over Europe" → "~7 km Europe",
 *      "Global (best over UK/Europe)" → "Global (best UK/Europe)")
 *  The raw original string is still shown in the per-source popup. */
function simplifyGeo(s: string): string {
  if (!s) return s;
  let out = s;
  out = out.replace(/\s*\([^)]*°[NSEW][^)]*\)/g, '');
  out = out.replace(/\s*\(seamless\)/gi, '');
  out = out.replace(/\bover\s+/g, '');
  out = out.replace(/\s+/g, ' ').trim();
  return out;
}

function healthDot(health: string): string {
  if (health === 'unknown') return '';
  const cls = `data-source-health data-source-health-${escapeHtml(health)}`;
  const labelKey = `dataSources.health.${health}`;
  const label = t(labelKey) !== labelKey ? t(labelKey) : health;
  return `<span class="${cls}" title="${escapeHtml(label)}"></span>`;
}

function safeHref(url: string): string | null {
  return /^https?:\/\//.test(url) ? url : null;
}

// --- Row & table builders -------------------------------------------------

function infoButton(entryKey: string, label: string): string {
  return `<button class="data-source-info-btn"`
    + ` data-entry-key="${escapeHtml(entryKey)}"`
    + ` title="${escapeHtml(t('dataSources.infoButtonTitle'))}"`
    + ` aria-label="${escapeHtml(t('dataSources.infoButtonTitle'))} — ${escapeHtml(label)}">ⓘ</button>`;
}

function providerCell(entry: DataSourceEntry): string {
  const safeUrl = safeHref(entry.provider_url);
  return safeUrl
    ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener">${escapeHtml(entry.provider_label)}</a>`
    : escapeHtml(entry.provider_label);
}

function variantCell(entry: DataSourceEntry): string {
  return `${escapeHtml(entry.model_label)} ${roleBadge(entry.role)} ${infoButton(entry.key, entry.model_label)}`;
}

function renderRow(entry: DataSourceEntry, isFirst: boolean, modelLabel: string, mode: DataSourcesTableMode): string {
  if (mode === 'summary') {
    return `
      <tr class="data-source-row">
        <td>${variantCell(entry)}</td>
        <td>${providerCell(entry)}</td>
        <td>${escapeHtml(entry.coverage || '—')}</td>
        <td>${fmtLevels(entry.pressure_levels)}</td>
        <td>${fmtHorizon(entry)}</td>
      </tr>
    `;
  }
  // Full mode: collapse the 11 raw columns into 5 readable ones by stacking
  // related fields inside a single cell.
  //
  //   1. Model — name / role + ⓘ / provider (3 lines)
  //   2. Resolution / Coverage (2 lines, lat-lon and "seamless" stripped)
  //   3. Levels (1 line)
  //   4. Horizon / Covers until (2 lines — together they describe how far
  //      the run reaches in time)
  //   5. Cycles / Latest run / Next update (3 lines — together they
  //      describe the publication cadence)
  void isFirst; void modelLabel;  // family column dropped; redundant with model name
  const stacked2 = (primary: string, secondary: string): string =>
    `<div class="ds-stack">`
    + `<div class="ds-stack-primary">${primary}</div>`
    + `<div class="ds-stack-secondary">${secondary}</div>`
    + `</div>`;
  return `
    <tr class="data-source-row">
      <td>
        <div class="ds-stack">
          <div class="ds-stack-primary">${escapeHtml(entry.model_label)}</div>
          <div class="ds-stack-secondary">${roleText(entry.role)} ${infoButton(entry.key, entry.model_label)}</div>
          <div class="ds-stack-tertiary">${providerCell(entry)}</div>
        </div>
      </td>
      <td>${renderResolutionCell(entry)}</td>
      <td>${fmtLevels(entry.pressure_levels)}</td>
      <td>${stacked2(escapeHtml(fmtHorizon(entry)), fmtUtc(entry.horizon_end))}</td>
      <td class="data-source-live">
        <div class="ds-stack">
          <div class="ds-stack-primary">${escapeHtml(fmtCycles(entry.cycles))}</div>
          <div class="ds-stack-secondary">${fmtUtc(entry.latest_init)} ${healthDot(entry.marker_health)}</div>
          <div class="ds-stack-tertiary">${fmtUtc(entry.next_expected)}</div>
        </div>
      </td>
    </tr>
  `;
}

function familyLabel(rows: DataSourceEntry[]): string {
  const labels = new Set(rows.map(r => r.model_label));
  if (labels.size === 1) return rows[0].model_label;
  const families: Record<string, string> = {
    icon: 'ICON family',
    icon_eu: 'ICON-EU',
    ecmwf: 'ECMWF IFS',
    gfs: 'GFS',
    ukmo: 'UK Met Office',
    meteofrance: 'Météo-France',
  };
  return families[rows[0].model] || rows[0].model.toUpperCase();
}

function renderTable(sources: DataSourceEntry[], generatedAt: string, mode: DataSourcesTableMode): string {
  const groups = groupByModel(sources);
  const modelKeys = orderedModelKeys(groups);

  const bodyRows: string[] = [];
  for (const model of modelKeys) {
    const rows = groups.get(model)!;
    const label = familyLabel(rows);
    rows.forEach((entry, idx) => {
      bodyRows.push(renderRow(entry, idx === 0, label, mode));
    });
  }

  // Header rows mirror the cell layout: paired columns use a two-line label
  // with the secondary line styled the same as the secondary line in the row.
  const stackedHeader = (primary: string, ...rest: string[]): string =>
    `<div class="ds-stack">`
    + `<div class="ds-stack-primary">${escapeHtml(primary)}</div>`
    + rest.map(r => `<div class="ds-stack-secondary">${escapeHtml(r)}</div>`).join('')
    + `</div>`;
  const headerCells = mode === 'summary'
    ? `
      <th>${t('dataSources.col.variant')}</th>
      <th>${t('dataSources.col.provider')}</th>
      <th>${t('dataSources.col.coverage')}</th>
      <th>${t('dataSources.col.levels')}</th>
      <th>${t('dataSources.col.horizon')}</th>
    `
    : `
      <th>${stackedHeader(t('dataSources.col.model'), t('dataSources.col.role'), t('dataSources.col.provider'))}</th>
      <th>${t('dataSources.col.resolution')}</th>
      <th>${t('dataSources.col.levels')}</th>
      <th>${stackedHeader(t('dataSources.col.horizon'), t('dataSources.col.covers'))}</th>
      <th>${stackedHeader(t('dataSources.col.cycles'), t('dataSources.col.latestRun'), t('dataSources.col.nextUpdate'))}</th>
    `;

  const intro = mode === 'summary' ? t('dataSources.summary.intro') : t('dataSources.intro');
  const wrapClass = mode === 'summary' ? 'data-sources-table-wrap summary' : 'data-sources-table-wrap';
  const tableClass = mode === 'summary' ? 'help-table data-sources-table summary' : 'help-table data-sources-table';

  return `
    <p class="muted data-sources-intro">${intro}</p>
    <div class="${wrapClass}">
      <table class="${tableClass}">
        <thead><tr>${headerCells}</tr></thead>
        <tbody>${bodyRows.join('')}</tbody>
      </table>
    </div>
    <p class="muted data-sources-footer">${t('dataSources.footer', { ts: fmtUtc(generatedAt) })}</p>
  `;
}

// --- Info popup -----------------------------------------------------------

function buildPopupHtml(entry: DataSourceEntry): string {
  const provider = safeHref(entry.provider_url)
    ? `<a href="${escapeHtml(entry.provider_url)}" target="_blank" rel="noopener">${escapeHtml(entry.provider_label)}</a>`
    : escapeHtml(entry.provider_label);

  // Live-state rows only appear when the marker store has a value; for the
  // summary surface this gives the popup the missing context (latest run,
  // covers until, next update) without bloating the table itself.
  const liveRows: string[] = [];
  if (entry.latest_init) {
    liveRows.push(`<dt>${escapeHtml(t('dataSources.col.latestRun'))}</dt>`
      + `<dd>${escapeHtml(fmtUtc(entry.latest_init))} ${healthDot(entry.marker_health)}</dd>`);
  }
  if (entry.horizon_end) {
    liveRows.push(`<dt>${escapeHtml(t('dataSources.col.covers'))}</dt>`
      + `<dd>${escapeHtml(fmtUtc(entry.horizon_end))}</dd>`);
  }
  if (entry.next_expected) {
    liveRows.push(`<dt>${escapeHtml(t('dataSources.col.nextUpdate'))}</dt>`
      + `<dd>${escapeHtml(fmtUtc(entry.next_expected))}</dd>`);
  }

  return `
    <div class="data-source-popup">
      <h3>${escapeHtml(entry.model_label)} <span class="muted">${escapeHtml(t('dataSources.popup.via'))} ${provider}</span></h3>
      ${entry.description ? `<p>${escapeHtml(entry.description)}</p>` : ''}
      <dl class="data-source-popup-meta">
        <dt>${escapeHtml(t('dataSources.col.coverage'))}</dt>
        <dd>${escapeHtml(entry.coverage || '—')}</dd>
        <dt>${escapeHtml(t('dataSources.col.resolution'))}</dt>
        <dd>${escapeHtml(entry.resolution || '—')}</dd>
        <dt>${escapeHtml(t('dataSources.col.levels'))}</dt>
        <dd>${escapeHtml(fmtLevels(entry.pressure_levels))}</dd>
        <dt>${escapeHtml(t('dataSources.col.cycles'))}</dt>
        <dd>${escapeHtml(fmtCycles(entry.cycles))}</dd>
        <dt>${escapeHtml(t('dataSources.col.horizon'))}</dt>
        <dd>${escapeHtml(fmtHorizon(entry))}</dd>
        ${liveRows.join('')}
      </dl>
    </div>
  `;
}

function wireInfoButtons(host: HTMLElement, entries: DataSourceEntry[]): void {
  const byKey = new Map(entries.map(e => [e.key, e]));
  host.querySelectorAll<HTMLButtonElement>('.data-source-info-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const key = btn.dataset.entryKey;
      if (!key) return;
      const entry = byKey.get(key);
      if (entry) showPopupContent(buildPopupHtml(entry));
    });
  });
}

// --- Public mount ---------------------------------------------------------

export async function mountDataSourcesTable(
  host: HTMLElement,
  mode: DataSourcesTableMode = 'full',
): Promise<void> {
  host.innerHTML = `<p class="muted">${t('dataSources.loading')}</p>`;
  try {
    const resp = await loadCatalog();
    host.innerHTML = renderTable(resp.sources, resp.generated_at, mode);
    wireInfoButtons(host, resp.sources);
  } catch (err) {
    host.innerHTML = `<p class="muted">${escapeHtml(t('dataSources.loadFailed'))}</p>`;
  }
}
