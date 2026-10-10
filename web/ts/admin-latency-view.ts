/**
 * Observed latency (#751) — a section of the admin Performance tab.
 *
 * How long a report takes from the time printed on it (METAR obs, TAF issue,
 * SIGMET valid-from) to a pilot's screen, per hop: fetched, available (the
 * live tick committed it), highlight written, delivered (a client received
 * that version), and end to end. Server: ``GET /admin/live-latency``
 * (``tasks/live_timing.latency_report``). The render functions are pure so
 * they are unit-tested without a DOM.
 */

import { fetchLiveLatency, type LiveLatencyReport, type LatencyStat } from './adapters/admin-adapter';
import { escapeHtml } from './utils';

/** Seconds as "42.0s", "3m05s" or "1h02m". */
export function fmtLatency(v: number | null): string {
  if (v == null) return '-';
  if (v < 60) return `${v.toFixed(1)}s`;
  if (v < 3600) {
    const m = Math.floor(v / 60);
    return `${m}m${String(Math.round(v % 60)).padStart(2, '0')}s`;
  }
  const h = Math.floor(v / 3600);
  return `${h}h${String(Math.round((v % 3600) / 60)).padStart(2, '0')}m`;
}

function statCells(s: LatencyStat | undefined): string {
  if (!s || s.n === 0) return '<td class="num">0</td><td class="num">-</td><td class="num">-</td><td class="num">-</td>';
  return `<td class="num">${s.n}</td><td class="num">${fmtLatency(s.p50)}</td>`
    + `<td class="num">${fmtLatency(s.p95)}</td><td class="num">${fmtLatency(s.max)}</td>`;
}

const HEAD = '<th style="text-align:center;">n</th><th style="text-align:center;">p50</th>'
  + '<th style="text-align:center;">p95</th><th style="text-align:center;">max</th>';

/** The window summary (one row per hop) and the daily trend of one hop. */
export function renderLiveLatency(r: LiveLatencyReport, hopKey: string | null): string {
  if (r.hops.length === 0) {
    return `<h3>Observed latency</h3><p class="muted">No live ticks recorded in the last ${r.days} days.</p>`;
  }
  const selected = hopKey && r.hops.some((h) => h.key === hopKey) ? hopKey : defaultHop(r);
  const rows = r.hops.map((h) => `
      <tr${h.key === selected ? ' class="selected"' : ''}>
        <td>${escapeHtml(h.label)}</td>${statCells(h)}
      </tr>`).join('');
  const options = r.hops.map((h) =>
    `<option value="${escapeHtml(h.key)}"${h.key === selected ? ' selected' : ''}>${escapeHtml(h.label)}</option>`,
  ).join('');
  const daily = r.daily.map((d) => `
      <tr><td>${escapeHtml(d.day)}</td>${statCells(d.hops[selected])}</tr>`).join('');
  const dropped = Object.entries(r.counts.dropped)
    .map(([k, v]) => `${escapeHtml(k)}: ${v}`).join(', ');
  return `
    <h3 style="margin-top:1.5rem;">Observed latency (last ${r.days} days)</h3>
    <p class="muted">From the time on the report to the pilot's screen. ${r.counts.ticks} ticks,
      ${r.counts.tick_rows} flight rows, ${r.counts.deliveries} deliveries.${dropped ? ` Not counted: ${dropped}.` : ''}</p>
    <div style="overflow-x:auto;">
      <table class="admin-table">
        <thead><tr><th>Hop</th>${HEAD}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <h3 style="margin-top:1.5rem;">Daily trend</h3>
    <select id="live-latency-hop">${options}</select>
    <div style="overflow-x:auto;">
      <table class="admin-table">
        <thead><tr><th>Day (UTC)</th>${HEAD}</tr></thead>
        <tbody>${daily}</tbody>
      </table>
    </div>`;
}

/** The headline: METAR end to end on iOS, else the first end-to-end hop. */
export function defaultHop(r: LiveLatencyReport): string {
  const keys = r.hops.map((h) => h.key);
  return keys.find((k) => k === 'end_to_end:metar:ios')
    ?? keys.find((k) => k.startsWith('end_to_end:'))
    ?? keys[0];
}

export async function loadLiveLatency(container: HTMLElement): Promise<void> {
  container.innerHTML = '<p class="muted" style="text-align:center;padding:1rem;">Loading observed latency...</p>';
  try {
    const report = await fetchLiveLatency(30);
    const draw = (hop: string | null) => {
      container.innerHTML = renderLiveLatency(report, hop);
      container.querySelector<HTMLSelectElement>('#live-latency-hop')
        ?.addEventListener('change', (e) => draw((e.target as HTMLSelectElement).value));
    };
    draw(null);
  } catch (err) {
    container.innerHTML = `<p style="color:#dc3545;text-align:center;padding:1rem;">Failed to load observed latency: ${escapeHtml(String(err))}</p>`;
  }
}
