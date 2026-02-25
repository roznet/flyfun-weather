/** User Costs page entry point — admin view of per-user cost attribution. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  fetchUserCosts,
  type UserCostsResponse,
  type UserCostTransaction,
  type UserCostBreakdown,
} from './adapters/admin-adapter';
import { renderUserInfo, escapeHtml, formatDate, formatTime, formatAlt } from './utils';

// Cost category colors (stable across renders)
const COST_COLORS: Record<string, string> = {
  token_cost_usd: '#2563eb',
  infra_share_usd: '#6366f1',
  subscription_share_usd: '#0891b2',
  storage_cost_usd: '#059669',
  margin_usd: '#d97706',
};

const COST_LABELS: Record<string, string> = {
  token_cost_usd: 'LLM Tokens',
  infra_share_usd: 'Infrastructure',
  subscription_share_usd: 'Subscriptions',
  storage_cost_usd: 'Storage',
  margin_usd: 'Margin',
};

async function init(): Promise<void> {
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  renderUserInfo(user, 'user-costs');

  const params = new URLSearchParams(window.location.search);
  const userId = params.get('user');
  if (!userId) {
    showError('No user ID specified. Go back to the admin page.');
    return;
  }

  await loadUserCosts(userId);
}

function showError(msg: string): void {
  const el = document.getElementById('error-message')!;
  el.textContent = msg;
  el.style.display = 'block';
  document.getElementById('loading')!.style.display = 'none';
}

async function loadUserCosts(userId: string): Promise<void> {
  try {
    const data = await fetchUserCosts(userId);

    document.getElementById('loading')!.style.display = 'none';
    document.getElementById('page-content')!.style.display = '';

    renderUserHeader(data);
    renderSummaryCards(data);
    renderRecentFlights(data);
    renderCostDistribution(data.cost_breakdown, data.summary.avg_cost_per_briefing, data.summary.total_briefings);
    renderTransactions(data.transactions);
    setupBreakdownToggles();
  } catch (err) {
    showError(`Failed to load user costs: ${err}`);
  }
}

function renderUserHeader(data: UserCostsResponse): void {
  const u = data.user;
  const status = u.approved
    ? '<span class="badge badge-green">Active</span>'
    : '<span class="badge badge-amber">Pending</span>';
  const memberSince = u.created_at ? formatDate(u.created_at) : 'Unknown';
  const lastActive = u.last_active_at ? formatDate(u.last_active_at) : 'Never';

  const balance = data.credit_balance;
  const balClass = balance > 100 ? 'positive' : balance > 0 ? 'low' : 'negative';

  document.getElementById('user-card')!.innerHTML = `
    <div class="user-card-left">
      <div class="user-name-display">${escapeHtml(u.display_name)} ${status}</div>
      <div class="user-email">${escapeHtml(u.email)}</div>
      <div class="user-meta">Member since ${memberSince} &middot; Last active ${lastActive}</div>
    </div>
    <div class="user-card-right">
      <div class="credit-balance ${balClass}">${balance.toFixed(1)}</div>
      <div class="credit-label">Credits</div>
    </div>`;
}

function renderSummaryCards(data: UserCostsResponse): void {
  const s = data.summary;
  document.getElementById('summary-bar')!.innerHTML = `
    <div class="summary-card"><div class="value">${s.credits_used_today.toFixed(1)}</div><div class="label">Credits Today</div></div>
    <div class="summary-card"><div class="value">${s.credits_used_month.toFixed(1)}</div><div class="label">Credits This Month</div></div>
    <div class="summary-card"><div class="value">${s.total_credits_charged.toFixed(1)}</div><div class="label">Total Charged</div></div>
    <div class="summary-card"><div class="value">${s.total_briefings}</div><div class="label">Total Briefings</div></div>`;
}

function renderRecentFlights(data: UserCostsResponse): void {
  const tbody = document.getElementById('flights-tbody')!;
  if (data.recent_flights.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:1rem;">No flights yet</td></tr>';
    return;
  }
  tbody.innerHTML = data.recent_flights.map(f => {
    const date = f.target_date;
    const time = formatTime(f.target_time_utc);
    const alt = formatAlt(f.cruise_altitude_ft);
    const briefingHref = `/briefing.html?flight=${encodeURIComponent(f.flight_id)}`;
    return `
      <tr>
        <td>${escapeHtml(f.route_name || f.flight_id)}</td>
        <td>${date}</td>
        <td>${time}</td>
        <td>${alt}</td>
        <td><a href="${briefingHref}">View</a></td>
      </tr>`;
  }).join('');
}

function renderCostDistribution(bd: UserCostBreakdown, avgCost: number, totalBriefings: number): void {
  const container = document.getElementById('cost-distribution')!;
  const total = bd.total_usd;

  if (total <= 0) {
    container.innerHTML = '<p class="muted" style="padding:0.5rem;">No cost data yet.</p>';
    return;
  }

  const keys = Object.keys(COST_LABELS) as (keyof typeof COST_LABELS)[];
  // Build bar segments
  const segments = keys
    .filter(k => (bd as Record<string, number>)[k] > 0)
    .map(k => {
      const val = (bd as Record<string, number>)[k];
      const pct = (val / total) * 100;
      return `<div class="bar-segment" style="width:${pct.toFixed(1)}%;background:${COST_COLORS[k]}" title="${COST_LABELS[k]}: $${val.toFixed(4)}">${pct >= 8 ? pct.toFixed(0) + '%' : ''}</div>`;
    })
    .join('');

  const legendItems = keys.map(k => {
    const val = (bd as Record<string, number>)[k];
    return `<div class="cost-legend-item"><span class="cost-legend-swatch" style="background:${COST_COLORS[k]}"></span>${COST_LABELS[k]}: $${val.toFixed(4)}</div>`;
  }).join('');

  const avgUsd = totalBriefings > 0 ? (total / totalBriefings).toFixed(4) : '0.0000';

  container.innerHTML = `
    <div class="cost-bar-container">
      <div class="cost-stacked-bar">${segments}</div>
      <div class="cost-legend">${legendItems}</div>
      <div class="cost-avg">Total: <strong>$${total.toFixed(4)}</strong> USD across ${totalBriefings} briefings &middot; Average: <strong>${avgCost.toFixed(2)}</strong> credits / <strong>$${avgUsd}</strong> USD per briefing</div>
    </div>`;
}

function renderTransactions(transactions: UserCostTransaction[]): void {
  const tbody = document.getElementById('tx-tbody')!;
  if (transactions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;padding:1rem;">No transactions yet</td></tr>';
    return;
  }
  tbody.innerHTML = transactions.map(tx => {
    const date = tx.timestamp ? formatDate(tx.timestamp) : '-';
    const amountClass = tx.amount < 0 ? 'amount-neg' : 'amount-pos';
    const amountStr = tx.amount < 0 ? tx.amount.toFixed(2) : `+${tx.amount.toFixed(1)}`;
    const expandBtn = tx.breakdown
      ? `<button class="tx-expand-btn" data-tx-id="${tx.id}">details</button>`
      : '';

    let breakdownRow = '';
    if (tx.breakdown) {
      const bd = tx.breakdown;
      const items = [
        ['LLM Tokens', bd.token_cost_usd],
        ['Infra', bd.infra_share_usd],
        ['Subscriptions', bd.subscription_share_usd],
        ['Storage', bd.storage_cost_usd],
        ['Margin', bd.margin_usd],
        ['Total USD', bd.total_usd],
        ['Credits', bd.credits_charged],
      ]
        .filter(([, v]) => v !== undefined)
        .map(([label, v]) => `<div><span class="bd-label">${label}</span><br><span class="bd-value">${typeof v === 'number' ? (label === 'Credits' ? v.toFixed(2) : '$' + v.toFixed(4)) : v}</span></div>`)
        .join('');
      breakdownRow = `<tr class="tx-breakdown" data-tx-detail="${tx.id}"><td colspan="6"><div class="breakdown-grid">${items}</div></td></tr>`;
    }

    const flightLink = tx.flight_id
      ? ` <a href="/briefing.html?flight=${encodeURIComponent(tx.flight_id)}" style="font-size:0.75rem;">briefing</a>`
      : '';

    return `
      <tr>
        <td style="white-space:nowrap;">${date}</td>
        <td>${escapeHtml(tx.category)}</td>
        <td>${escapeHtml(tx.description)}${flightLink}</td>
        <td class="num ${amountClass}">${amountStr}</td>
        <td class="num">${tx.balance_after.toFixed(1)}</td>
        <td>${expandBtn}</td>
      </tr>${breakdownRow}`;
  }).join('');
}

function setupBreakdownToggles(): void {
  document.getElementById('tx-table')?.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.tx-expand-btn') as HTMLButtonElement | null;
    if (!btn) return;
    const txId = btn.dataset.txId;
    const detailRow = document.querySelector(`[data-tx-detail="${txId}"]`);
    if (detailRow) {
      detailRow.classList.toggle('open');
      btn.textContent = detailRow.classList.contains('open') ? 'hide' : 'details';
    }
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
