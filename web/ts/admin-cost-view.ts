/**
 * Admin cost view — renders into the Cost tab on the admin page.
 * Imported by admin-main.ts; lazily invoked on first tab activation by
 * ``initCostTab()``.
 *
 * Two sections:
 *  1. Program cost table — Fixed / Variable / Usage rows by 7d, 30d, 365d
 *     columns; fixed prorated per rate-card version, variable from the
 *     ledger (incl. per-call LLM features), the margin as "Extra".
 *     Below it, "Other LLM spend" (#741) by category / day / user with its
 *     own 7d/30d toggle.
 *  2. Rate-card editor — edit fixed costs (incl. itemized subscriptions),
 *     token rates, margin; saving creates a new versioned config.
 */

import {
  fetchCostReport, fetchCostTable, fetchCostConfig, fetchCostConfigHistory, updateCostConfig,
  type CostTable, type CostConfigData, type CostConfigVersion, type OtherLlmSpend,
} from './adapters/admin-adapter';
import { escapeHtml, formatDate, usd4, signedUsd4 } from './utils';

type Window = '7d' | '30d';

let costInitialized = false;
let reportWindow: Window = '30d';
// Working copy of subscription line items while editing the rate card.
let subItems: Array<{ name: string; amount: number }> = [];

const usd = (n: number) => `$${n.toFixed(2)}`;

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
      void loadOtherLlm();
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
  await Promise.all([loadTable(), loadOtherLlm()]);
}

async function loadTable(): Promise<void> {
  try {
    renderTable(await fetchCostTable());
  } catch (err) {
    showError(`Failed to load cost table: ${err}`);
  }
}

/** The windowed program report now only feeds "Other LLM spend" detail. */
async function loadOtherLlm(): Promise<void> {
  try {
    const r = await fetchCostReport(reportWindow);
    renderOtherLlm(r?.other_llm ?? null);
  } catch (err) {
    showError(`Failed to load cost report: ${err}`);
  }
}

function columnHeader(c: CostTable['columns'][number]): string {
  // A window reaching back before the first rate card only covers part of it.
  const partial = c.covered_days < c.days - 0.5;
  const since = new Date(Date.now() - c.covered_days * 86_400_000)
    .toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  return `<th class="num">Last ${c.days}d${partial ? `<br><span class="muted" style="font-weight:400;font-size:0.75rem;">since ${since}</span>` : ''}</th>`;
}

function renderTable(t: CostTable | null): void {
  const head = document.getElementById('cost-table-head')!;
  const body = document.getElementById('cost-table-body')!;
  const note = document.getElementById('cost-report-note')!;

  if (!t) {
    head.innerHTML = '';
    body.innerHTML = '<tr><td class="muted">No active cost config — set the rate card below.</td></tr>';
    note.textContent = '';
    return;
  }

  const cells = (values: Array<number | null>, fmt: (n: number) => string) =>
    values.map((v) => `<td class="num">${v == null ? '–' : fmt(v)}</td>`).join('');
  const row = (cls: string, label: string, values: Array<number | null>, fmt: (n: number) => string, labelCls = '') =>
    `<tr class="${cls}"><td class="${labelCls}">${escapeHtml(label)}</td>${cells(values, fmt)}</tr>`;
  const blank = `<td></td>`.repeat(t.columns.length);

  head.innerHTML = `<tr><th></th>${t.columns.map(columnHeader).join('')}</tr>`;

  let html = '';
  for (const sec of t.sections) {
    html += row('cost-section', sec.label, sec.values, usd);
    for (const r of sec.rows) {
      html += row('', r.label, r.values, usd, 'cost-line');
      // Reporting-only: the saving is already netted off inside the token line.
      if (r.label.startsWith('LLM tokens') && t.cache_saving_usd.some((v) => v != null)) {
        html += row('cost-aside muted', 'of which cache saved', t.cache_saving_usd, signedUsd4, 'cost-line');
      }
    }
  }
  html += row('cost-total', 'Total', t.total, usd);
  html += `<tr class="cost-section"><td>Usage</td>${blank}</tr>`;
  for (const u of t.usage) {
    html += row('', u.label, u.values, u.kind === 'count' ? (n) => String(n) : usd4, 'cost-line');
  }
  body.innerHTML = html;

  note.innerHTML = 'Fixed cost is prorated from each rate-card version over the part of the window it was active '
    + '(monthly &times; days/30). Variable is the LLM and storage cost actually charged. '
    + `Extra is ${t.margin_percent}% of everything else. Cost per briefing / user divide the total.`;
}

/** Rows shown per table before the rest is cut (30d of daily rows per
 *  category, and the heaviest users). */
const OTHER_LLM_MAX_ROWS = 31;

function renderOtherLlm(o: OtherLlmSpend | null): void {
  const el = document.getElementById('cost-other-llm');
  if (!el) return;
  if (!o) {
    el.innerHTML = '<p class="muted" style="font-size:0.85rem;">Shown with the program report.</p>';
    return;
  }
  if (o.calls === 0) {
    el.innerHTML = '<p class="muted" style="font-size:0.85rem;">No non-briefing LLM calls in this window.</p>';
    return;
  }

  const cards = o.by_category.map((c) => `
    <div class="summary-card"><div class="value">${usd4(c.cost_usd)}</div><div class="label">${escapeHtml(c.category)} (${c.calls})</div></div>`).join('');

  const table = (head: string, rows: string[]): string => `
    <div style="overflow-x:auto;margin-top:0.5rem;">
      <table class="admin-table">
        <thead><tr>${head}</tr></thead>
        <tbody>${rows.slice(0, OTHER_LLM_MAX_ROWS).join('')}</tbody>
      </table>
      ${rows.length > OTHER_LLM_MAX_ROWS
        ? `<p class="muted" style="font-size:0.8rem;">Showing ${OTHER_LLM_MAX_ROWS} of ${rows.length} rows; the totals above cover all of them.</p>`
        : ''}
    </div>`;

  const dayRows = o.by_day.map((d) => `
    <tr><td>${escapeHtml(d.date)}</td><td>${escapeHtml(d.category)}</td><td class="num">${d.calls}</td><td class="num">${usd4(d.cost_usd)}</td></tr>`);
  const userRows = o.by_user.map((u) => `
    <tr>
      <td><a href="/user-costs.html?user=${encodeURIComponent(u.user_id)}">${escapeHtml(u.email ?? u.user_id)}</a></td>
      <td class="num">${u.calls}</td><td class="num">${usd4(u.cost_usd)}</td>
    </tr>`);

  el.innerHTML = `
    <div class="summary-bar">
      <div class="summary-card"><div class="value">${usd4(o.total_usd)}</div><div class="label">Total (${o.calls} calls)</div></div>
      ${cards}
    </div>
    <details style="margin-top:0.5rem;"><summary style="cursor:pointer;font-size:0.85rem;">By day</summary>
      ${table('<th>Day (UTC)</th><th>Category</th><th class="num">Calls</th><th class="num">Cost</th>', dayRows)}
    </details>
    <details style="margin-top:0.5rem;"><summary style="cursor:pointer;font-size:0.85rem;">By user</summary>
      ${table('<th>User</th><th class="num">Calls</th><th class="num">Cost</th>', userRows)}
    </details>`;
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
        ${numField('Extra %', 'margin_percent', cfg.margin_percent, '1')}
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
        <thead><tr><th>Ver</th><th>Active from</th><th>Active until</th><th class="num">Droplet</th><th class="num">Subs</th><th class="num">Extra</th></tr></thead>
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
