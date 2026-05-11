/** Render the Data Sources & Models table from the public catalog endpoint.
 *
 * The table is grouped by NWP model family (ECMWF, GFS, ICON, …) so that
 * the hybrid pattern (e.g. ICON-EU direct + ICON-Global via Open-Meteo)
 * is visible at a glance.  Each row shows the static description plus
 * the latest observed run and the end of the forecast horizon — so
 * pilots reading the help page can see at-a-glance what the briefing
 * server actually has available right now.
 */

import { fetchDataSources, type DataSourceEntry } from './adapters/data-sources-adapter';
import { escapeHtml } from './utils';
import { t } from './i18n/i18n';

/** Group rows by the marker-store model name. */
function groupByModel(sources: DataSourceEntry[]): Map<string, DataSourceEntry[]> {
  const groups = new Map<string, DataSourceEntry[]>();
  for (const s of sources) {
    const list = groups.get(s.model) || [];
    list.push(s);
    groups.set(s.model, list);
  }
  // Within a model: primary sounding first, then enrichments/bases, then others.
  const roleOrder: Record<string, number> = {
    'primary-sounding': 0,
    'cloud-enrichment': 1,
    'surface-base': 2,
    'primary': 3,
  };
  for (const list of groups.values()) {
    list.sort((a, b) =>
      (roleOrder[a.role] ?? 9) - (roleOrder[b.role] ?? 9),
    );
  }
  return groups;
}

/** Order top-level model groups so the most important ones come first. */
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

/** Format an ISO datetime as `DD MMM HH:MM UTC` for the table. */
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

/** Render the horizon column: max horizon if uniform, else min–max range. */
function fmtHorizon(entry: DataSourceEntry): string {
  const hours = Object.values(entry.horizon_hours);
  if (hours.length === 0) return '—';
  const min = Math.min(...hours);
  const max = Math.max(...hours);
  const fmt = (h: number) => h >= 48 ? `${Math.round(h / 24)} d` : `${Math.round(h)} h`;
  return min === max ? fmt(max) : `${fmt(min)}–${fmt(max)}`;
}

/** Return the uniform spacing in hours between sorted cycle inits, or null
 *  if the spacing isn't uniform. Length-based heuristics (e.g. "4 cycles
 *  means every 6h") would silently mislabel a 3- or 6-cycle source. */
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
  // Non-uniform or ≤2 cycles: list the init hours explicitly.
  const list = cycles.map(c => `${String(c).padStart(2, '0')}Z`).join(', ');
  return cycles.length >= 2 ? `${cycles.length}× / day (${list})` : list;
}

function fmtLevels(n: number | null): string {
  if (n === null) return '—';
  return `${n} levels`;
}

function roleBadge(role: string): string {
  const labelKey = `dataSources.role.${role.replace(/-/g, '_')}`;
  const fallback = role;
  const label = t(labelKey) !== labelKey ? t(labelKey) : fallback;
  return `<span class="data-source-role data-source-role-${escapeHtml(role)}">${escapeHtml(label)}</span>`;
}

function healthDot(health: string): string {
  if (health === 'unknown') return '';
  const cls = `data-source-health data-source-health-${escapeHtml(health)}`;
  const labelKey = `dataSources.health.${health}`;
  const label = t(labelKey) !== labelKey ? t(labelKey) : health;
  return `<span class="${cls}" title="${escapeHtml(label)}"></span>`;
}

/** Only http/https URLs are allowed as href; defends against a future
 *  registry typo that would otherwise yield a `javascript:` link. */
function safeHref(url: string): string | null {
  return /^https?:\/\//.test(url) ? url : null;
}

function renderRow(entry: DataSourceEntry, isFirst: boolean, modelLabel: string): string {
  const safeUrl = safeHref(entry.provider_url);
  const providerCell = safeUrl
    ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener">${escapeHtml(entry.provider_label)}</a>`
    : escapeHtml(entry.provider_label);
  const modelCell = isFirst
    ? `<td class="data-source-model">${escapeHtml(modelLabel)}</td>`
    : `<td class="data-source-model-cont"></td>`;
  return `
    <tr class="data-source-row">
      ${modelCell}
      <td>${escapeHtml(entry.model_label)} ${roleBadge(entry.role)}</td>
      <td>${providerCell}</td>
      <td>${escapeHtml(entry.resolution || '—')}</td>
      <td>${escapeHtml(entry.coverage || '—')}</td>
      <td>${fmtLevels(entry.pressure_levels)}</td>
      <td>${fmtCycles(entry.cycles)}</td>
      <td>${fmtHorizon(entry)}</td>
      <td class="data-source-live">${fmtUtc(entry.latest_init)} ${healthDot(entry.marker_health)}</td>
      <td class="data-source-live">${fmtUtc(entry.horizon_end)}</td>
      <td class="data-source-live">${fmtUtc(entry.next_expected)}</td>
    </tr>
    <tr class="data-source-description-row">
      <td></td>
      <td colspan="10" class="data-source-description">${escapeHtml(entry.description || '')}</td>
    </tr>
  `;
}

/** Return the modelLabel that should appear for a group of rows.
 *
 * If every row has the same model_label we use that; otherwise we use
 * the marker-store model name in upper-case (the "family" label).
 */
function familyLabel(rows: DataSourceEntry[]): string {
  const labels = new Set(rows.map(r => r.model_label));
  if (labels.size === 1) return rows[0].model_label;
  // Different variants under the same family — e.g. ICON-EU + ICON-Global
  // both share marker-store model "icon" / "icon_eu".  Use a family name.
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

/** Build the full data-sources table HTML. */
function renderTable(sources: DataSourceEntry[], generatedAt: string): string {
  const groups = groupByModel(sources);
  const modelKeys = orderedModelKeys(groups);

  const bodyRows: string[] = [];
  for (const model of modelKeys) {
    const rows = groups.get(model)!;
    const label = familyLabel(rows);
    rows.forEach((entry, idx) => {
      bodyRows.push(renderRow(entry, idx === 0, label));
    });
  }

  return `
    <p class="muted data-sources-intro">
      ${t('dataSources.intro')}
    </p>
    <div class="data-sources-table-wrap">
      <table class="help-table data-sources-table">
        <thead>
          <tr>
            <th>${t('dataSources.col.family')}</th>
            <th>${t('dataSources.col.variant')}</th>
            <th>${t('dataSources.col.provider')}</th>
            <th>${t('dataSources.col.resolution')}</th>
            <th>${t('dataSources.col.coverage')}</th>
            <th>${t('dataSources.col.levels')}</th>
            <th>${t('dataSources.col.cycles')}</th>
            <th>${t('dataSources.col.horizon')}</th>
            <th>${t('dataSources.col.latestRun')}</th>
            <th>${t('dataSources.col.covers')}</th>
            <th>${t('dataSources.col.nextUpdate')}</th>
          </tr>
        </thead>
        <tbody>
          ${bodyRows.join('')}
        </tbody>
      </table>
    </div>
    <p class="muted data-sources-footer">${t('dataSources.footer', { ts: fmtUtc(generatedAt) })}</p>
  `;
}

/** Mount the data-sources table into a host element (replaces its content). */
export async function mountDataSourcesTable(host: HTMLElement): Promise<void> {
  host.innerHTML = `<p class="muted">${t('dataSources.loading')}</p>`;
  try {
    const resp = await fetchDataSources();
    host.innerHTML = renderTable(resp.sources, resp.generated_at);
  } catch (err) {
    host.innerHTML = `<p class="muted">${escapeHtml(t('dataSources.loadFailed'))}</p>`;
  }
}
