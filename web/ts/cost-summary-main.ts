/**
 * Cost summary page — program-wide cost (what it costs to run the service)
 * plus the viewing user's own usage. Admin-gated for now; designed to later
 * move into Settings with a donation link and a public program endpoint.
 */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { fetchCostReport, type CostReport } from './adapters/admin-adapter';
import { fetchCostSummary, type CostSummary } from './adapters/credits-adapter';
import { redirectToLogin, renderUserInfo, escapeHtml, formatDate } from './utils';
import { initTheme } from './theme';
import { initI18n } from './i18n/i18n';

const usd = (n: number) => `$${n.toFixed(2)}`;
const usd4 = (n: number) => `$${n.toFixed(4)}`;

const COMPOSITION: Array<{ key: 'fixed_prorated_usd' | 'variable_usd' | 'margin_usd'; label: string; color: string }> = [
  { key: 'fixed_prorated_usd', label: 'Fixed', color: '#6366f1' },
  { key: 'variable_usd', label: 'Variable', color: '#2563eb' },
  { key: 'margin_usd', label: 'Margin', color: '#d97706' },
];

async function init(): Promise<void> {
  await initI18n();
  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    return;
  }
  initTheme();
  renderUserInfo(user, 'cost-summary');

  document.getElementById('loading')!.style.display = 'none';

  if (!user.is_admin) {
    document.getElementById('gate')!.style.display = '';
    return;
  }

  try {
    const [report, summary] = await Promise.all([fetchCostReport('30d'), fetchCostSummary()]);
    document.getElementById('page-content')!.style.display = '';
    renderProgram(report);
    renderPersonal(summary);
  } catch (err) {
    showError(`Failed to load cost summary: ${err}`);
  }
}

function showError(msg: string): void {
  const el = document.getElementById('error-message')!;
  el.textContent = msg;
  el.style.display = 'block';
}

function renderProgram(r: CostReport | null): void {
  const note = document.getElementById('program-window-note')!;
  const summary = document.getElementById('program-summary')!;
  const tbody = document.getElementById('program-fixed-tbody')!;
  const bar = document.getElementById('program-composition')!;
  const legend = document.getElementById('program-legend')!;

  if (!r) {
    note.textContent = '';
    summary.innerHTML = '<p class="muted">No cost data available yet.</p>';
    tbody.innerHTML = '';
    return;
  }

  note.textContent = `Based on the last ${r.window_days} days. Includes a ${r.margin_percent}% margin.`;

  summary.innerHTML = `
    <div class="summary-card"><div class="value">${usd(r.total_usd)}</div><div class="label">Total / month</div></div>
    <div class="summary-card"><div class="value">${usd(r.fixed_prorated_usd)}</div><div class="label">Fixed</div></div>
    <div class="summary-card"><div class="value">${usd(r.variable_usd)}</div><div class="label">Variable</div></div>
    <div class="summary-card"><div class="value">${usd4(r.cost_per_briefing_usd)}</div><div class="label">Per briefing</div></div>
    <div class="summary-card"><div class="value">${usd(r.cost_per_user_usd)}</div><div class="label">Per user</div></div>
    <div class="summary-card"><div class="value">${r.num_briefings}</div><div class="label">Briefings (30d)</div></div>
    <div class="summary-card"><div class="value">${r.num_users}</div><div class="label">Active users (30d)</div></div>`;

  tbody.innerHTML = r.fixed_lines.map((ln) => `
    <tr><td>${escapeHtml(ln.label)}</td><td class="num">${usd(ln.monthly_usd)}</td></tr>`).join('') + `
    <tr style="font-weight:600;border-top:2px solid var(--border);">
      <td>Total fixed</td><td class="num">${usd(r.fixed_monthly_usd)}</td>
    </tr>`;

  const total = r.total_usd;
  if (total > 0) {
    bar.innerHTML = COMPOSITION.map((c) => {
      const val = r[c.key];
      const pct = (val / total) * 100;
      return `<div class="bar-segment" style="width:${pct.toFixed(1)}%;background:${c.color};" title="${c.label}: ${usd(val)}">${pct >= 10 ? c.label : ''}</div>`;
    }).join('');
    legend.innerHTML = COMPOSITION.map((c) =>
      `<div class="cost-legend-item"><span class="cost-legend-swatch" style="background:${c.color};"></span>${c.label}: ${usd(r[c.key])}</div>`
    ).join('');
  } else {
    bar.innerHTML = '';
    legend.innerHTML = '';
  }
}

function renderPersonal(s: CostSummary): void {
  document.getElementById('personal-summary')!.innerHTML = `
    <div class="summary-card"><div class="value">${usd(s.cost_this_week_usd)}</div><div class="label">This week</div></div>
    <div class="summary-card"><div class="value">${usd(s.cost_this_month_usd)}</div><div class="label">This month</div></div>
    <div class="summary-card"><div class="value">${usd(s.total_cost_usd)}</div><div class="label">Total</div></div>
    <div class="summary-card"><div class="value">${s.total_briefings}</div><div class="label">Your briefings</div></div>`;

  const tbody = document.getElementById('personal-tx-tbody')!;
  const txns = s.recent_transactions.filter((t) => t.cost_usd > 0);
  if (txns.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" class="muted" style="text-align:center;padding:1rem;">No briefings yet.</td></tr>';
    return;
  }
  tbody.innerHTML = txns.map((t) => `
    <tr>
      <td style="white-space:nowrap;">${t.timestamp ? formatDate(t.timestamp) : '-'}</td>
      <td>${escapeHtml(t.description)}</td>
      <td class="num">${usd4(t.cost_usd)}</td>
    </tr>`).join('');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
