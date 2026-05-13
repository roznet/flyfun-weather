/**
 * Admin usage analytics page entry point.
 *
 * Reads from rollup tables only via the admin API — cheap even when the
 * raw events table is large. Read-only; no mutations.
 */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { redirectToLogin, renderUserInfo, escapeHtml, apiFetch } from './utils';
import { initTheme } from './theme';
import { initI18n } from './i18n/i18n';

interface FeatureRow {
  feature: string;
  briefings_with_feature: number;
  briefings_total: number;
  total_uses: number;
  attachment_pct: number;
}

interface SummaryResponse {
  window: { start: string; end: string; days: number };
  totals: {
    unique_anons: number;
    new_anons: number;
    briefings_opened: number;
    briefings_created: number;
    briefings_refreshes: number;
  };
  features: FeatureRow[];
}

interface ShapeBucket {
  key: string | null;
  count: number;
}

interface ShapeResponse {
  by_region: ShapeBucket[];
  by_distance: ShapeBucket[];
  by_route_points: ShapeBucket[];
  by_lead_time: ShapeBucket[];
  by_model_count: ShapeBucket[];
  by_alternate_etd: ShapeBucket[];
  by_seq: ShapeBucket[];
}

interface DigestResponse {
  text: string;
}

async function loadAll(days: number): Promise<void> {
  const loading = document.getElementById('loading')!;
  const page = document.getElementById('page-content')!;
  const errBox = document.getElementById('error-message') as HTMLElement;
  loading.style.display = '';
  page.style.display = 'none';
  errBox.style.display = 'none';

  try {
    const [summary, shape, digest] = await Promise.all([
      apiFetch<SummaryResponse>(`/admin/usage/summary?days=${days}`),
      apiFetch<ShapeResponse>(`/admin/usage/briefing-shape?days=${days}`),
      apiFetch<DigestResponse>('/admin/usage/digest'),
    ]);
    renderSummary(summary);
    renderShape(shape);
    renderDigest(digest);
    loading.style.display = 'none';
    page.style.display = '';
  } catch (err) {
    loading.style.display = 'none';
    errBox.textContent = `Failed to load usage data: ${String(err)}`;
    errBox.style.display = '';
  }
}

function renderSummary(s: SummaryResponse): void {
  const range = document.getElementById('window-range');
  if (range) range.textContent = `${s.window.start} → ${s.window.end}`;

  const cards: Array<{ label: string; value: number }> = [
    { label: 'Unique users', value: s.totals.unique_anons },
    { label: 'New users', value: s.totals.new_anons },
    { label: 'Briefings opened', value: s.totals.briefings_opened },
    { label: 'Briefings created', value: s.totals.briefings_created },
    { label: 'Refreshes', value: s.totals.briefings_refreshes },
  ];
  const totalsEl = document.getElementById('totals')!;
  totalsEl.innerHTML = cards
    .map(
      (c) => `
        <div class="usage-card">
          <div class="value">${c.value.toLocaleString()}</div>
          <div class="label">${escapeHtml(c.label)}</div>
        </div>`,
    )
    .join('');

  const tbody = document.getElementById('feature-tbody')!;
  if (s.features.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="muted">No briefings opened in this window.</td></tr>';
    return;
  }
  tbody.innerHTML = s.features
    .map(
      (f) => `
        <tr>
          <td>${escapeHtml(f.feature)}</td>
          <td>
            <span class="attachment-bar"><span style="width:${Math.min(f.attachment_pct, 100)}%"></span></span>
            ${f.attachment_pct}%
          </td>
          <td class="num">${f.briefings_with_feature.toLocaleString()} / ${f.briefings_total.toLocaleString()}</td>
          <td class="num">${f.total_uses.toLocaleString()}</td>
        </tr>`,
    )
    .join('');
}

function renderShape(shape: ShapeResponse): void {
  const grid = document.getElementById('shape-grid')!;
  const labels: Array<[keyof ShapeResponse, string]> = [
    ['by_region', 'Region'],
    ['by_distance', 'Distance bucket'],
    ['by_route_points', 'Route points'],
    ['by_lead_time', 'Lead time at creation'],
    ['by_model_count', 'Model count'],
    ['by_alternate_etd', 'Alternate departure time'],
    ['by_seq', 'Briefing sequence (1 = first, 2+ = refresh)'],
  ];
  grid.innerHTML = labels
    .map(([key, label]) => {
      const buckets = shape[key] || [];
      if (buckets.length === 0) {
        return `<div class="shape-card"><h4>${escapeHtml(label)}</h4><div class="muted" style="font-size:0.85rem;">no data</div></div>`;
      }
      const rows = buckets
        .map((b) => `
          <div class="shape-row">
            <span class="key">${escapeHtml(formatKey(key, b.key))}</span>
            <span class="count">${b.count.toLocaleString()}</span>
          </div>`)
        .join('');
      return `<div class="shape-card"><h4>${escapeHtml(label)}</h4>${rows}</div>`;
    })
    .join('');
}

function formatKey(dim: keyof ShapeResponse, key: string | null): string {
  if (key === null || key === '') return '(unknown)';
  if (dim === 'by_alternate_etd') {
    return key === '1' || key === 'true' ? 'yes' : 'no';
  }
  return key;
}

function renderDigest(d: DigestResponse): void {
  const pre = document.getElementById('digest-text')!;
  pre.textContent = d.text;
}

async function main(): Promise<void> {
  initTheme();
  await initI18n();

  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    return;
  }
  if (!user.is_admin) {
    const err = document.getElementById('error-message') as HTMLElement;
    err.textContent = 'Admin access required.';
    err.style.display = '';
    document.getElementById('loading')!.style.display = 'none';
    return;
  }
  renderUserInfo(user, 'admin');

  const select = document.getElementById('window-select') as HTMLSelectElement;
  const days = () => parseInt(select.value, 10) || 30;
  select.addEventListener('change', () => loadAll(days()));
  await loadAll(days());
}

void main();
