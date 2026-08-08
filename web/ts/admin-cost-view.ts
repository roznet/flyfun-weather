/**
 * Admin cost view — renders into the Cost tab on the admin page.
 * Imported by admin-main.ts; lazily invoked on first tab activation by
 * ``initCostTab()``.
 *
 * Two sections:
 *  1. Program cost report (7d/30d) — real prorated fixed cost + actual
 *     variable cost from the ledger, plus per-briefing/per-user economics.
 *  2. Rate-card editor — edit fixed costs (incl. itemized subscriptions),
 *     token rates, margin; saving creates a new versioned config.
 */

import {
  fetchCostReport, fetchCostConfig, fetchCostConfigHistory, updateCostConfig,
  type CostReport, type CostConfigData, type CostConfigVersion,
} from './adapters/admin-adapter';
import { escapeHtml, formatDate } from './utils';

type Window = '7d' | '30d';

let costInitialized = false;
let reportWindow: Window = '30d';
// Working copy of subscription line items while editing the rate card.
let subItems: Array<{ name: string; amount: number }> = [];

const usd = (n: number) => `$${n.toFixed(2)}`;
const usd4 = (n: number) => `$${n.toFixed(4)}`;
// A cache "saving" is genuinely negative in a write-heavy window, and "$-0.03"
// reads as a typo — keep the sign in front of the currency.
const signedUsd4 = (n: number) => (n < 0 ? `-$${Math.abs(n).toFixed(4)}` : usd4(n));

export async function initCostTab(): Promise<void> {
  if (costInitialized) return;
  costInitialized = true;

  document.querySelectorAll('#cost-period-toggle .toggle-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const w = (btn as HTMLElement).dataset.window as Window;
      if (w === reportWindow) return;
      reportWindow = w;
      document.querySelectorAll('#cost-period-toggle .toggle-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      void loadReport();
    });
  });

  const historyWrap = document.getElementById('cost-config-history-wrap');
  historyWrap?.addEventListener('toggle', () => {
    if ((historyWrap as HTMLDetailsElement).open) void loadHistory();
  }, { once: true });

  await Promise.all([loadReport(), loadConfig()]);

  document.getElementById('cost-loading')!.style.display = 'none';
  document.getElementById('cost-content')!.style.display = '';
}

function showError(msg: string): void {
  const el = document.getElementById('cost-error') as HTMLElement;
  el.textContent = msg;
  el.style.display = 'block';
}

// --- Program report ---

async function loadReport(): Promise<void> {
  try {
    const r = await fetchCostReport(reportWindow);
    renderReport(r);
  } catch (err) {
    showError(`Failed to load cost report: ${err}`);
  }
}

function renderReport(r: CostReport | null): void {
  const summary = document.getElementById('cost-report-summary')!;
  const tbody = document.getElementById('cost-fixed-tbody')!;
  const note = document.getElementById('cost-report-note')!;

  if (!r) {
    summary.innerHTML = '<p class="muted">No active cost config — set the rate card below.</p>';
    tbody.innerHTML = '';
    note.textContent = '';
    return;
  }

  // Reporting-only: the saving is already netted off inside variable_usd, so
  // it gets its own card rather than a slot in the cost sequence. Absent when
  // no ledger row in the window carries the figure.
  const savingCard = r.cache_saving_usd == null
    ? ''
    : `<div class="summary-card"><div class="value">${signedUsd4(r.cache_saving_usd)}</div><div class="label">Cache saved</div></div>`;

  summary.innerHTML = `
    <div class="summary-card"><div class="value">${usd(r.fixed_prorated_usd)}</div><div class="label">Fixed</div></div>
    <div class="summary-card"><div class="value">${usd(r.variable_usd)}</div><div class="label">Variable</div></div>
    ${savingCard}
    <div class="summary-card"><div class="value">${usd(r.margin_usd)}</div><div class="label">Other costs (${r.margin_percent}%)</div></div>
    <div class="summary-card"><div class="value">${usd(r.total_usd)}</div><div class="label">Total</div></div>
    <div class="summary-card"><div class="value">${r.num_briefings}</div><div class="label">Briefings</div></div>
    <div class="summary-card"><div class="value">${r.num_users}</div><div class="label">Active users</div></div>
    <div class="summary-card"><div class="value">${usd4(r.cost_per_briefing_usd)}</div><div class="label">Per briefing</div></div>
    <div class="summary-card"><div class="value">${usd(r.cost_per_user_usd)}</div><div class="label">Per user</div></div>`;

  tbody.innerHTML = r.fixed_lines.map((ln) => `
    <tr>
      <td>${escapeHtml(ln.label)}</td>
      <td class="num">${usd(ln.monthly_usd)}</td>
      <td class="num">${usd(ln.prorated_usd)}</td>
    </tr>`).join('') + `
    <tr style="font-weight:600;border-top:2px solid var(--border);">
      <td>Total fixed</td>
      <td class="num">${usd(r.fixed_monthly_usd)}</td>
      <td class="num">${usd(r.fixed_prorated_usd)}</td>
    </tr>`;

  // Prompt-cache saving is reporting-only — it is already inside the token
  // figure above. `null`/absent means no row in the window recorded it (they
  // pre-date the field), which must not render as a measured $0.00.
  const saving = r.cache_saving_usd;
  const savingNote = saving == null
    ? ''
    : ` Prompt caching saved ${signedUsd4(saving)} of that token cost`
      + `${saving < 0 ? ' (negative: cache writes outran the reads in this window)' : ''}.`;

  note.innerHTML = `Fixed cost is prorated from the rate card (monthly &times; ${r.window_days}/30). `
    + `Variable is the actual LLM token (${usd4(r.variable_token_usd)}) + storage (${usd4(r.variable_storage_usd)}) `
    + `charged over the window.${savingNote} Total includes the ${r.margin_percent}% margin.`;
}

// --- Rate-card editor ---

async function loadConfig(): Promise<void> {
  try {
    const version = await fetchCostConfig();
    renderConfigForm(version);
  } catch (err) {
    showError(`Failed to load cost config: ${err}`);
  }
}

function numField(label: string, key: keyof CostConfigData, value: number, step: string): string {
  return `
    <label class="cost-field">
      <span>${label}</span>
      <input type="number" step="${step}" min="0" name="${key}" value="${value}">
    </label>`;
}

function renderConfigForm(version: CostConfigVersion | null): void {
  const status = document.getElementById('cost-config-status')!;
  const form = document.getElementById('cost-config-form') as HTMLFormElement;

  const cfg: CostConfigData = version?.config ?? {
    token_cost_per_1k_input: 0.003,
    token_cost_per_1k_output: 0.015,
    droplet_monthly_usd: 24,
    misc_monthly_usd: 2,
    subscriptions_monthly_usd: 30,
    subscription_details: { open_meteo: 30 },
    disk_cost_per_gb_monthly: 0.1,
    estimated_monthly_briefings: 500,
    margin_percent: 30,
  };

  status.innerHTML = version
    ? `Active since ${formatDate(version.active_from)} (version #${version.id}).`
    : 'No active config yet — saving will create the first version.';

  subItems = Object.entries(cfg.subscription_details ?? {}).map(([name, amount]) => ({ name, amount: Number(amount) }));

  form.innerHTML = `
    <div class="cost-grid">
      <fieldset class="cost-fieldset">
        <legend>Fixed monthly</legend>
        ${numField('Server / droplet ($/mo)', 'droplet_monthly_usd', cfg.droplet_monthly_usd, '0.01')}
        ${numField('Misc ($/mo)', 'misc_monthly_usd', cfg.misc_monthly_usd, '0.01')}
        <div class="cost-field">
          <span>Subscriptions ($/mo, itemized)</span>
          <div id="sub-items"></div>
          <button type="button" id="sub-add" class="btn" style="font-size:0.75rem;margin-top:0.4rem;">+ Add subscription</button>
          <div class="muted" style="font-size:0.8rem;margin-top:0.4rem;">Subtotal: <span id="sub-total">$0.00</span></div>
        </div>
      </fieldset>
      <fieldset class="cost-fieldset">
        <legend>Variable &amp; assumptions</legend>
        ${numField('Token $/1k input', 'token_cost_per_1k_input', cfg.token_cost_per_1k_input, '0.0001')}
        ${numField('Token $/1k output', 'token_cost_per_1k_output', cfg.token_cost_per_1k_output, '0.0001')}
        ${numField('Disk $/GB-month', 'disk_cost_per_gb_monthly', cfg.disk_cost_per_gb_monthly, '0.01')}
        ${numField('Est. monthly briefings', 'estimated_monthly_briefings', cfg.estimated_monthly_briefings, '1')}
        ${numField('Other costs %', 'margin_percent', cfg.margin_percent, '1')}
      </fieldset>
    </div>
    <div style="margin-top:0.75rem;display:flex;gap:0.75rem;align-items:center;">
      <button type="submit" class="btn btn-primary" style="font-size:0.85rem;">Save new version</button>
      <span id="cost-config-msg" class="muted" style="font-size:0.85rem;"></span>
    </div>`;

  renderSubItems();
  document.getElementById('sub-add')!.addEventListener('click', () => {
    subItems.push({ name: '', amount: 0 });
    renderSubItems();
  });
  form.onsubmit = (e) => { e.preventDefault(); void saveConfig(form); };
}

function renderSubItems(): void {
  const wrap = document.getElementById('sub-items')!;
  wrap.innerHTML = subItems.map((it, i) => `
    <div class="sub-row" data-i="${i}">
      <input type="text" class="sub-name" placeholder="name (e.g. ecmwf)" value="${escapeHtml(it.name)}">
      <input type="number" class="sub-amount" step="0.01" min="0" value="${it.amount}">
      <button type="button" class="sub-del btn" title="Remove">&times;</button>
    </div>`).join('');

  wrap.querySelectorAll('.sub-row').forEach((row) => {
    const i = Number((row as HTMLElement).dataset.i);
    row.querySelector('.sub-name')!.addEventListener('input', (e) => {
      subItems[i].name = (e.target as HTMLInputElement).value;
    });
    row.querySelector('.sub-amount')!.addEventListener('input', (e) => {
      subItems[i].amount = parseFloat((e.target as HTMLInputElement).value) || 0;
      updateSubTotal();
    });
    row.querySelector('.sub-del')!.addEventListener('click', () => {
      subItems.splice(i, 1);
      renderSubItems();
    });
  });
  updateSubTotal();
}

function updateSubTotal(): void {
  const total = subItems.reduce((s, it) => s + (it.amount || 0), 0);
  const el = document.getElementById('sub-total');
  if (el) el.textContent = usd(total);
}

async function saveConfig(form: HTMLFormElement): Promise<void> {
  const msg = document.getElementById('cost-config-msg')!;
  const data = new FormData(form);
  const num = (k: string) => parseFloat(String(data.get(k)));

  const details: Record<string, number> = {};
  for (const it of subItems) {
    const name = it.name.trim();
    if (!name) { msg.textContent = 'Subscription rows need a name.'; return; }
    if (name in details) { msg.textContent = `Duplicate subscription name: ${name}`; return; }
    details[name] = it.amount || 0;
  }

  const body: Partial<CostConfigData> = {
    token_cost_per_1k_input: num('token_cost_per_1k_input'),
    token_cost_per_1k_output: num('token_cost_per_1k_output'),
    droplet_monthly_usd: num('droplet_monthly_usd'),
    misc_monthly_usd: num('misc_monthly_usd'),
    disk_cost_per_gb_monthly: num('disk_cost_per_gb_monthly'),
    estimated_monthly_briefings: Math.round(num('estimated_monthly_briefings')),
    margin_percent: num('margin_percent'),
    subscription_details: details,
  };

  msg.textContent = 'Saving…';
  try {
    const version = await updateCostConfig(body);
    msg.textContent = `Saved — version #${version.id} active.`;
    renderConfigForm(version);
    await loadReport();
    // Force history reload next time it's opened.
    const wrap = document.getElementById('cost-config-history-wrap') as HTMLDetailsElement | null;
    if (wrap?.open) await loadHistory();
  } catch (err) {
    msg.textContent = `Failed to save: ${err}`;
  }
}

async function loadHistory(): Promise<void> {
  const el = document.getElementById('cost-config-history')!;
  el.innerHTML = '<p class="muted" style="font-size:0.85rem;">Loading…</p>';
  try {
    const versions = await fetchCostConfigHistory();
    if (versions.length === 0) {
      el.innerHTML = '<p class="muted" style="font-size:0.85rem;">No history.</p>';
      return;
    }
    el.innerHTML = `
      <table class="admin-table" style="margin-top:0.5rem;">
        <thead><tr><th>Ver</th><th>Active from</th><th>Active until</th><th class="num">Droplet</th><th class="num">Subs</th><th class="num">Other costs</th></tr></thead>
        <tbody>${versions.map((v) => `
          <tr>
            <td>#${v.id}</td>
            <td>${formatDate(v.active_from)}</td>
            <td>${v.active_until ? formatDate(v.active_until) : '<span class="badge badge-green">active</span>'}</td>
            <td class="num">${usd(v.config.droplet_monthly_usd)}</td>
            <td class="num">${usd(v.config.subscriptions_monthly_usd)}</td>
            <td class="num">${v.config.margin_percent}%</td>
          </tr>`).join('')}</tbody>
      </table>`;
  } catch (err) {
    el.innerHTML = `<p class="ua-error" style="display:block;">Failed to load history: ${err}</p>`;
  }
}
