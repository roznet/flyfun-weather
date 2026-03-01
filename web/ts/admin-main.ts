/** Admin page entry point — user management, agent management, and usage overview. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  fetchAdminUsers, approveUser, createAgent, createAgentToken,
  revokeAgent, fetchAdminFeedback,
  type AdminUser, type AdminSummary, type AdminPeriod, type FeedbackEntry,
} from './adapters/admin-adapter';
import { renderUserInfo, escapeHtml, formatDate } from './utils';
import { initTheme } from './theme';

let currentPeriod: AdminPeriod = '30d';

async function init(): Promise<void> {
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  initTheme();
  renderUserInfo(user, 'admin');

  setupAgentCreateButton();
  setupTabs();
  setupPeriodToggle();
  // Load both tabs in parallel
  await Promise.all([loadUsers(), loadFeedback()]);
}

function setupPeriodToggle(): void {
  document.querySelectorAll('#period-toggle .toggle-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const period = (btn as HTMLElement).dataset.period as AdminPeriod;
      if (period === currentPeriod) return;
      currentPeriod = period;
      document.querySelectorAll('#period-toggle .toggle-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      loadUsers();
    });
  });
}

function setupTabs(): void {
  document.querySelectorAll('.settings-tabs .tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tabId = (btn as HTMLElement).dataset.tab;
      if (!tabId) return;
      // Update button active state
      document.querySelectorAll('.settings-tabs .tab-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      // Show selected panel, hide others
      document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
      document.getElementById(tabId)?.classList.add('active');
    });
  });
}

const CATEGORY_LABELS: Record<string, string> = {
  data_issue: 'Data Issue',
  too_conservative: 'Too Conservative',
  too_optimistic: 'Too Optimistic',
  incorrect_interpretation: 'Incorrect Interpretation',
  other: 'Other Bug/Issue',
};

async function loadFeedback(): Promise<void> {
  const container = document.getElementById('feedback-list')!;
  try {
    const entries = await fetchAdminFeedback();
    if (entries.length === 0) {
      container.innerHTML = '<p class="muted" style="text-align:center;padding:2rem;">No feedback yet.</p>';
      return;
    }
    const rows = entries.map(renderFeedbackRow).join('');
    container.innerHTML = `
      <div style="overflow-x:auto;">
        <table class="admin-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>User</th>
              <th>Flight</th>
              <th>Category</th>
              <th>Comment</th>
              <th>Briefing</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  } catch (err) {
    container.innerHTML = `<p style="color:#dc3545;text-align:center;padding:1rem;">Failed to load feedback: ${err}</p>`;
  }
}

function renderFeedbackRow(fb: FeedbackEntry): string {
  const date = fb.created_at ? formatDate(fb.created_at) : '-';
  const userName = fb.user_name || fb.user_email || '-';
  const category = CATEGORY_LABELS[fb.category] ?? fb.category;
  const comment = escapeHtml(fb.comment.length > 120 ? fb.comment.slice(0, 120) + '...' : fb.comment);
  const briefingLink = fb.pack_timestamp
    ? `<a href="/briefing.html?flight=${encodeURIComponent(fb.flight_id)}&t=${encodeURIComponent(fb.pack_timestamp)}" target="_blank">View</a>`
    : `<a href="/briefing.html?flight=${encodeURIComponent(fb.flight_id)}" target="_blank">View</a>`;
  return `
    <tr>
      <td style="white-space:nowrap;">${date}</td>
      <td>${escapeHtml(userName)}</td>
      <td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(fb.flight_id)}">${escapeHtml(fb.flight_id)}</td>
      <td>${category}</td>
      <td style="max-width:300px;" title="${escapeHtml(fb.comment)}">${comment}</td>
      <td>${briefingLink}</td>
    </tr>`;
}

function setupAgentCreateButton(): void {
  document.getElementById('btn-create-agent')?.addEventListener('click', handleCreateAgent);
}

async function loadUsers(): Promise<void> {
  const summaryBar = document.getElementById('summary-bar')!;
  const pendingSection = document.getElementById('pending-section')!;
  const pendingList = document.getElementById('pending-list')!;
  const usersBody = document.getElementById('users-tbody')!;
  const agentsBody = document.getElementById('agents-tbody')!;
  const agentsSection = document.getElementById('agents-section')!;
  const errorEl = document.getElementById('error-message')!;

  try {
    const response = await fetchAdminUsers(currentPeriod);
    const { summary, users } = response;

    const humans = users.filter(u => u.type !== 'agent');
    const agents = users.filter(u => u.type === 'agent');

    // Summary bar
    renderSummaryBar(summaryBar, summary);

    // Pending approvals
    const pending = humans.filter(u => !u.approved);
    if (pending.length > 0) {
      pendingSection.style.display = '';
      pendingList.innerHTML = pending.map(renderPendingCard).join('');
      pendingList.querySelectorAll('.btn-approve').forEach(btn => {
        btn.addEventListener('click', handleApprove);
      });
    } else {
      pendingSection.style.display = 'none';
    }

    // Agents section
    if (agents.length > 0) {
      agentsSection.style.display = '';
      agentsBody.innerHTML = agents.map(renderAgentRow).join('');
      agentsBody.querySelectorAll('.btn-revoke-agent').forEach(btn => {
        btn.addEventListener('click', handleRevokeAgent);
      });
      agentsBody.querySelectorAll('.btn-new-token').forEach(btn => {
        btn.addEventListener('click', handleNewToken);
      });
    } else {
      agentsBody.innerHTML = '<tr><td colspan="7" class="muted" style="text-align:center;padding:1rem;">No agents yet</td></tr>';
    }

    // All users table (humans only)
    usersBody.innerHTML = humans.map(renderUserRow).join('');
    usersBody.querySelectorAll('.btn-approve').forEach(btn => {
      btn.addEventListener('click', handleApprove);
    });
  } catch (err) {
    errorEl.textContent = `Failed to load users: ${err}`;
    errorEl.style.display = 'block';
  }
}

async function handleApprove(e: Event): Promise<void> {
  const btn = e.currentTarget as HTMLButtonElement;
  const userId = btn.dataset.userId!;
  btn.disabled = true;
  btn.textContent = 'Approving...';

  try {
    await approveUser(userId);
    await loadUsers();
  } catch (err) {
    btn.textContent = 'Failed';
    btn.disabled = false;
    const errorEl = document.getElementById('error-message')!;
    errorEl.textContent = `Failed to approve user: ${err}`;
    errorEl.style.display = 'block';
  }
}

async function handleCreateAgent(): Promise<void> {
  const name = prompt('Agent name (e.g. "Claude Desktop", "Cursor"):');
  if (!name) return;

  const btn = document.getElementById('btn-create-agent') as HTMLButtonElement;
  btn.disabled = true;

  try {
    const result = await createAgent(name);
    showTokenModal(result.token, result.name);
    await loadUsers();
  } catch (err) {
    alert(`Failed to create agent: ${err}`);
  } finally {
    btn.disabled = false;
  }
}

async function handleRevokeAgent(e: Event): Promise<void> {
  const btn = e.currentTarget as HTMLButtonElement;
  const userId = btn.dataset.userId!;
  const name = btn.dataset.agentName || 'this agent';

  if (!confirm(`Revoke agent "${name}"? This will disable all its tokens immediately.`)) return;

  btn.disabled = true;
  try {
    await revokeAgent(userId);
    await loadUsers();
  } catch (err) {
    btn.disabled = false;
    alert(`Failed to revoke agent: ${err}`);
  }
}

async function handleNewToken(e: Event): Promise<void> {
  const btn = e.currentTarget as HTMLButtonElement;
  const userId = btn.dataset.userId!;
  const agentName = btn.dataset.agentName || '';

  const tokenName = prompt('Token label (optional):', agentName);
  if (tokenName === null) return;

  btn.disabled = true;
  try {
    const result = await createAgentToken(userId, tokenName);
    showTokenModal(result.token, tokenName || agentName);
    await loadUsers();
  } catch (err) {
    alert(`Failed to create token: ${err}`);
  } finally {
    btn.disabled = false;
  }
}

function showTokenModal(token: string, name: string): void {
  // Remove existing modal if any
  document.getElementById('token-modal')?.remove();

  const modal = document.createElement('div');
  modal.id = 'token-modal';
  modal.className = 'token-modal-overlay';
  modal.innerHTML = `
    <div class="token-modal">
      <h3>API Token Created</h3>
      <p>Token for <strong>${escapeHtml(name)}</strong>. Copy it now — it won't be shown again.</p>
      <div class="token-display">
        <code id="token-value">${escapeHtml(token)}</code>
      </div>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:1rem;">
        <button class="btn" id="btn-copy-token">Copy</button>
        <button class="btn btn-primary" id="btn-close-modal">Done</button>
      </div>
    </div>`;

  document.body.appendChild(modal);

  document.getElementById('btn-copy-token')!.addEventListener('click', () => {
    navigator.clipboard.writeText(token).then(() => {
      const copyBtn = document.getElementById('btn-copy-token')!;
      copyBtn.textContent = 'Copied!';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
    });
  });

  document.getElementById('btn-close-modal')!.addEventListener('click', () => {
    modal.remove();
  });

  modal.addEventListener('click', (ev) => {
    if (ev.target === modal) modal.remove();
  });
}

function periodLabel(): string {
  return currentPeriod === '30d' ? '30d' : 'All';
}

function renderSummaryBar(el: HTMLElement, s: AdminSummary): void {
  const tokens = s.total_tokens >= 1000
    ? `~${Math.round(s.total_tokens / 1000)}K`
    : String(s.total_tokens);
  const pl = periodLabel();
  el.style.display = '';
  el.innerHTML = `
    <div class="summary-card"><div class="value">${s.total_users}</div><div class="label">Users</div></div>
    <div class="summary-card"><div class="value">${s.total_briefings}</div><div class="label">Briefings (${pl})</div></div>
    <div class="summary-card"><div class="value">${tokens}</div><div class="label">Tokens (${pl})</div></div>
    <div class="summary-card"><div class="value">${formatBytes(s.total_disk_bytes)}</div><div class="label">Disk</div></div>
  `;
}

function renderPendingCard(u: AdminUser): string {
  const created = u.created_at ? formatDate(u.created_at) : 'Unknown';
  return `
    <div class="flight-card">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div>
          <strong>${escapeHtml(u.display_name)}</strong>
          <span class="muted" style="margin-left:0.5rem;">${escapeHtml(u.email)}</span>
          <span class="muted" style="margin-left:0.5rem;">Signed up ${created}</span>
        </div>
        <button class="btn btn-primary btn-approve" data-user-id="${escapeHtml(u.id)}">Approve</button>
      </div>
    </div>`;
}

function renderAgentRow(u: AdminUser): string {
  const status = u.approved
    ? '<span class="badge badge-green">Active</span>'
    : '<span class="badge badge-amber">Revoked</span>';
  const lastUsed = u.token_last_used ? formatDate(u.token_last_used) : (u.last_active_at ? formatDate(u.last_active_at) : '-');
  const created = u.created_at ? formatDate(u.created_at) : '-';
  const m = u.usage;
  const tokens = m.total_tokens >= 1000
    ? `~${Math.round(m.total_tokens / 1000)}K`
    : String(m.total_tokens);
  const actions = u.approved
    ? `<button class="btn btn-new-token" style="font-size:0.75rem;padding:0.2rem 0.5rem;" data-user-id="${escapeHtml(u.id)}" data-agent-name="${escapeHtml(u.display_name)}">New Token</button>
       <button class="btn btn-danger btn-revoke-agent" style="font-size:0.75rem;padding:0.2rem 0.5rem;margin-left:0.25rem;" data-user-id="${escapeHtml(u.id)}" data-agent-name="${escapeHtml(u.display_name)}">Revoke</button>`
    : '<span class="muted">Revoked</span>';
  const costsHref = `/user-costs.html?user=${encodeURIComponent(u.id)}`;

  return `
    <tr>
      <td><a href="${costsHref}">${escapeHtml(u.display_name)}</a></td>
      <td>${status}</td>
      <td class="num">${u.active_tokens ?? 0} / ${u.token_count ?? 0}</td>
      <td>${created}</td>
      <td>${lastUsed}</td>
      <td class="num">${m.briefings}</td>
      <td class="num">${tokens}</td>
      <td>${actions}</td>
    </tr>`;
}

function renderUserRow(u: AdminUser): string {
  const status = u.approved
    ? '<span class="badge badge-green">Active</span>'
    : '<span class="badge badge-amber">Pending</span>';
  const lastActive = u.last_active_at ?? u.last_login_at;
  const lastActiveLabel = lastActive ? formatDate(lastActive) : '-';
  const m = u.usage;
  const tokens = m.total_tokens >= 1000
    ? `~${Math.round(m.total_tokens / 1000)}K`
    : String(m.total_tokens);
  const approveBtn = u.approved
    ? ''
    : `<button class="btn btn-primary btn-approve" style="font-size:0.75rem;padding:0.2rem 0.5rem;" data-user-id="${escapeHtml(u.id)}">Approve</button>`;
  const costsHref = `/user-costs.html?user=${encodeURIComponent(u.id)}`;
  return `
    <tr>
      <td><a href="${costsHref}">${escapeHtml(u.display_name)}</a></td>
      <td>${escapeHtml(u.email)}</td>
      <td>${status} ${approveBtn}</td>
      <td>${lastActiveLabel}</td>
      <td class="num">${m.briefings}</td>
      <td class="num">${m.gramet}</td>
      <td class="num">${m.llm_digest}</td>
      <td class="num">${tokens}</td>
      <td class="num">${formatBytes(u.disk_usage_bytes)}</td>
    </tr>`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, i);
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
