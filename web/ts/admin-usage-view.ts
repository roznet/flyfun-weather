/**
 * Admin usage analytics view — renders into the Usage Analytics tab on
 * the admin page. Imported by admin-main.ts; lazily invoked on first
 * tab activation by ``initUsageAnalyticsTab()``.
 *
 * Reads from rollup tables only via the admin API — cheap even when the
 * raw events table is large. Read-only; no mutations.
 */

import { escapeHtml, apiFetch } from './utils';

interface FeatureRow {
  feature: string;
  briefings_with_feature: number;
  briefings_total: number;
  total_uses: number;
  /** Float with one decimal — e.g. 66.7. Server-side rounded. */
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

interface TimeseriesPoint {
  day: string;
  briefings_opened: number;
  new_users: number;
}

interface TimeseriesResponse {
  series: TimeseriesPoint[];
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
  const loading = document.getElementById('ua-loading')!;
  const page = document.getElementById('ua-content')!;
  const errBox = document.getElementById('ua-error-message') as HTMLElement;
  loading.style.display = '';
  page.style.display = 'none';
  errBox.style.display = 'none';

  try {
    const [summary, shape, timeseries, digest] = await Promise.all([
      apiFetch<SummaryResponse>(`/admin/usage/summary?days=${days}`),
      apiFetch<ShapeResponse>(`/admin/usage/briefing-shape?days=${days}`),
      apiFetch<TimeseriesResponse>(`/admin/usage/timeseries?days=${days}`),
      apiFetch<DigestResponse>('/admin/usage/digest'),
    ]);
    renderSummary(summary);
    renderTimeseries(timeseries);
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
      (f) => {
        const pct = Number(f.attachment_pct);
        const pctStr = Number.isInteger(pct) ? pct.toFixed(0) : pct.toFixed(1);
        return `
        <tr>
          <td>${escapeHtml(f.feature)}</td>
          <td>
            <span class="attachment-bar"><span style="width:${Math.min(pct, 100)}%"></span></span>
            ${pctStr}%
          </td>
          <td class="num">${f.briefings_with_feature.toLocaleString()} / ${f.briefings_total.toLocaleString()}</td>
          <td class="num">${f.total_uses.toLocaleString()}</td>
        </tr>`;
      },
    )
    .join('');
}

function renderTimeseries(ts: TimeseriesResponse): void {
  const container = document.getElementById('timeseries-chart');
  if (!container) return;
  const series = ts.series;
  if (series.length === 0) {
    container.innerHTML = '<p class="muted">No activity in this window.</p>';
    return;
  }

  // Two-row stacked bar chart, inline SVG — keep it dependency-free.
  const width = Math.max(series.length * 18, 320);
  const height = 140;
  const padL = 36;
  const padR = 8;
  const padT = 8;
  const padB = 28;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;
  const max = Math.max(
    1,
    ...series.map((p) => Math.max(p.briefings_opened, p.new_users)),
  );
  const barW = Math.max(1, (plotW / series.length) - 2);

  const yTicks = [0, Math.ceil(max / 2), max];
  const yLines = yTicks
    .map((v) => {
      const y = padT + plotH - (v / max) * plotH;
      return `
        <line x1="${padL}" y1="${y}" x2="${padL + plotW}" y2="${y}"
              stroke="var(--border)" stroke-dasharray="2 3" />
        <text x="${padL - 4}" y="${y + 3}" text-anchor="end"
              font-size="10" fill="var(--text-muted)">${v}</text>`;
    })
    .join('');

  const bars = series
    .map((p, i) => {
      const xBase = padL + i * (plotW / series.length);
      const xBriefings = xBase;
      const xUsers = xBase + barW / 2;
      const hBriefings = (p.briefings_opened / max) * plotH;
      const hUsers = (p.new_users / max) * plotH;
      const yB = padT + plotH - hBriefings;
      const yU = padT + plotH - hUsers;
      const title = `${p.day}: ${p.briefings_opened} briefings, ${p.new_users} new users`;
      return `
        <g>
          <title>${escapeHtml(title)}</title>
          <rect x="${xBriefings}" y="${yB}" width="${barW / 2}" height="${hBriefings}"
                fill="var(--accent, #2563eb)" />
          <rect x="${xUsers}" y="${yU}" width="${barW / 2}" height="${hUsers}"
                fill="var(--green, #059669)" />
        </g>`;
    })
    .join('');

  // X-axis labels: every Nth day so they don't collide.
  const labelStep = Math.max(1, Math.ceil(series.length / 8));
  const labels = series
    .map((p, i) => {
      if (i % labelStep !== 0 && i !== series.length - 1) return '';
      const x = padL + i * (plotW / series.length) + barW / 2;
      const dayLabel = p.day.slice(5); // MM-DD
      return `<text x="${x}" y="${height - padB + 14}" text-anchor="middle"
              font-size="10" fill="var(--text-muted)">${dayLabel}</text>`;
    })
    .join('');

  const legend = `
    <g transform="translate(${padL},${padT - 2})" font-size="10" fill="var(--text-muted)">
      <rect x="0" y="-2" width="10" height="8" fill="var(--accent, #2563eb)" />
      <text x="14" y="6">Briefings opened</text>
      <rect x="120" y="-2" width="10" height="8" fill="var(--green, #059669)" />
      <text x="134" y="6">New users</text>
    </g>`;

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height + 18}" role="img"
         style="max-width:100%; height:auto; min-width:${Math.min(width, 600)}px;">
      ${legend}
      ${yLines}
      ${bars}
      ${labels}
    </svg>`;
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

/**
 * Wire the window-size selector and trigger the first load. Idempotent —
 * safe to call again (e.g. if the tab is reopened), though the lazy-load
 * gate in admin-main.ts means this normally fires only once per page.
 */
export async function initUsageAnalyticsTab(): Promise<void> {
  const select = document.getElementById('window-select') as HTMLSelectElement | null;
  if (!select) return;
  const days = () => parseInt(select.value, 10) || 30;
  if (!select.dataset.bound) {
    select.dataset.bound = '1';
    select.addEventListener('change', () => loadAll(days()));
  }
  await loadAll(days());
}
