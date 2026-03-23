/** Admin page entry point — user management, agent management, and usage overview. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  fetchAdminUsers, approveUser, createAgent, createAgentToken,
  revokeAgent, fetchAdminFeedback, fetchAdminMetrics, fetchHubUsers,
  updateFeedbackStatus, saveFeedbackReply, sendFeedbackReply, saveFeedbackNotes,
  type AdminUser, type AdminSummary, type AdminPeriod, type FeedbackEntry,
  type FeedbackStatus, type AdminMetrics, type AdminMetricsWindow, type HubResponse,
} from './adapters/admin-adapter';
import { renderUserInfo, escapeHtml, formatDate } from './utils';
import { initTheme } from './theme';
import { initI18n } from './i18n/i18n';

let currentPeriod: AdminPeriod = '30d';

async function init(): Promise<void> {
  await initI18n();
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

let perfLoaded = false;
let systemsLoaded = false;
let systemsPeriod: AdminPeriod = '30d';
let systemsSort: 'last_active' | 'total_cost' = 'last_active';
let systemsData: HubResponse | null = null;

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
      // Lazy-load tabs
      if (tabId === 'tab-performance' && !perfLoaded) {
        perfLoaded = true;
        loadPerformance();
      }
      if (tabId === 'tab-systems' && !systemsLoaded) {
        systemsLoaded = true;
        setupSystemsPeriodToggle();
        loadSystems();
      }
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

const STATUS_BADGES: Record<FeedbackStatus, { label: string; color: string }> = {
  pending: { label: 'To Review', color: '#6c757d' },
  ready: { label: 'AI Ready', color: '#2563eb' },
  replied: { label: 'Replied', color: '#16a34a' },
  ignored: { label: 'Ignored', color: '#9ca3af' },
};

const CLASSIFICATION_LABELS: Record<string, string> = {
  BUG_FIXABLE: 'Bug (Fixable)',
  RESPOND_ONLY: 'Respond Only',
  NEEDS_INVESTIGATION: 'Needs Investigation',
  DEFER_TO_HUMAN: 'Defer to Human',
};

async function loadFeedback(): Promise<void> {
  const container = document.getElementById('feedback-list')!;
  try {
    const entries = await fetchAdminFeedback();
    if (entries.length === 0) {
      container.innerHTML = '<p class="muted" style="text-align:center;padding:2rem;">No feedback yet.</p>';
      return;
    }
    const outstanding = entries.filter(e => e.status === 'pending' || e.status === 'ready');
    const archived = entries.filter(e => e.status === 'replied' || e.status === 'ignored');

    let html = '';
    if (outstanding.length > 0) {
      html += `<h3 style="margin:0 0 12px;">Outstanding (${outstanding.length})</h3>`;
      html += outstanding.map(renderFeedbackCard).join('');
    } else {
      html += '<p class="muted" style="padding:1rem 0;">No outstanding feedback.</p>';
    }
    if (archived.length > 0) {
      html += `
        <details style="margin-top:24px;">
          <summary style="cursor:pointer;font-weight:600;color:var(--text-secondary, #6c757d);margin-bottom:12px;">
            Archived (${archived.length})
          </summary>
          ${archived.map(renderFeedbackCard).join('')}
        </details>`;
    }
    container.innerHTML = html;
    attachFeedbackHandlers(container);
  } catch (err) {
    container.innerHTML = `<p style="color:#dc3545;text-align:center;padding:1rem;">Failed to load feedback: ${err}</p>`;
  }
}

function renderStatusBadge(status: FeedbackStatus): string {
  const { label, color } = STATUS_BADGES[status] ?? { label: status, color: '#6c757d' };
  return `<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;color:#fff;background:${color};">${label}</span>`;
}

function renderFeedbackCard(fb: FeedbackEntry): string {
  const date = fb.created_at ? formatDate(fb.created_at) : '-';
  const userName = fb.user_name || fb.user_email || '-';
  const category = CATEGORY_LABELS[fb.category] ?? fb.category;
  const isArchived = fb.status === 'replied' || fb.status === 'ignored';
  const briefingLink = fb.flight_id
    ? (fb.pack_timestamp
      ? `<a href="/briefing.html?flight=${encodeURIComponent(fb.flight_id)}&t=${encodeURIComponent(fb.pack_timestamp)}" target="_blank" style="color:#2563eb;font-size:12px;">View Briefing</a>`
      : `<a href="/briefing.html?flight=${encodeURIComponent(fb.flight_id)}" target="_blank" style="color:#2563eb;font-size:12px;">View Briefing</a>`)
    : '';

  const classificationBadge = fb.classification
    ? `<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;background:#e0e7ff;color:#3730a3;margin-left:6px;">${CLASSIFICATION_LABELS[fb.classification] ?? fb.classification}</span>`
    : '';
  const confidenceText = fb.confidence != null
    ? `<span style="font-size:11px;color:var(--text-secondary, #6c757d);margin-left:6px;">${Math.round(fb.confidence * 100)}% confidence</span>`
    : '';

  let detailsHtml = '';

  // AI analysis section
  if (fb.ai_analysis) {
    detailsHtml += `
      <div style="margin-top:12px;">
        <label style="font-size:12px;font-weight:600;color:var(--text-secondary, #6c757d);display:block;margin-bottom:4px;">AI Analysis</label>
        <div style="background:var(--bg-tertiary, #f0f4ff);border-radius:6px;padding:10px;font-size:13px;white-space:pre-wrap;">${escapeHtml(fb.ai_analysis)}</div>
      </div>`;
  }

  // Reply section
  if (!isArchived) {
    const replyValue = escapeHtml(fb.admin_reply || '');
    detailsHtml += `
      <div style="margin-top:12px;">
        <label style="font-size:12px;font-weight:600;color:var(--text-secondary, #6c757d);display:block;margin-bottom:4px;">Reply</label>
        <textarea class="fb-reply" data-id="${fb.id}" rows="4" style="width:100%;box-sizing:border-box;border:1px solid var(--border-color, #dee2e6);border-radius:6px;padding:8px;font-size:13px;resize:vertical;">${replyValue}</textarea>
      </div>`;
  } else if (fb.admin_reply) {
    detailsHtml += `
      <div style="margin-top:12px;">
        <label style="font-size:12px;font-weight:600;color:var(--text-secondary, #6c757d);display:block;margin-bottom:4px;">Reply${fb.replied_at ? ` (sent ${formatDate(fb.replied_at)})` : ''}</label>
        <div style="background:var(--bg-tertiary, #f8f9fa);border-radius:6px;padding:10px;font-size:13px;white-space:pre-wrap;">${escapeHtml(fb.admin_reply)}</div>
      </div>`;
  }

  // Notes section
  if (!isArchived) {
    const notesValue = escapeHtml(fb.admin_notes || '');
    detailsHtml += `
      <div style="margin-top:12px;">
        <label style="font-size:12px;font-weight:600;color:var(--text-secondary, #6c757d);display:block;margin-bottom:4px;">Admin Notes</label>
        <textarea class="fb-notes" data-id="${fb.id}" rows="2" style="width:100%;box-sizing:border-box;border:1px solid var(--border-color, #dee2e6);border-radius:6px;padding:8px;font-size:13px;resize:vertical;">${notesValue}</textarea>
      </div>`;
  } else if (fb.admin_notes) {
    detailsHtml += `
      <div style="margin-top:12px;">
        <label style="font-size:12px;font-weight:600;color:var(--text-secondary, #6c757d);display:block;margin-bottom:4px;">Admin Notes</label>
        <div style="background:var(--bg-tertiary, #f8f9fa);border-radius:6px;padding:10px;font-size:13px;white-space:pre-wrap;">${escapeHtml(fb.admin_notes)}</div>
      </div>`;
  }

  // Action buttons
  let actionsHtml = '';
  if (!isArchived) {
    actionsHtml = `
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
        <button class="fb-send btn-sm" data-id="${fb.id}" style="background:#2563eb;color:#fff;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px;font-weight:500;">Send Reply</button>
        <button class="fb-mark-replied btn-sm" data-id="${fb.id}" style="background:var(--bg-tertiary, #e5e7eb);color:var(--text-primary, #1a1a2e);border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px;">Mark Replied</button>
        <button class="fb-ignore btn-sm" data-id="${fb.id}" style="background:var(--bg-tertiary, #e5e7eb);color:var(--text-secondary, #6c757d);border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px;">Ignore</button>
        <span class="fb-action-status" data-id="${fb.id}" style="font-size:12px;align-self:center;"></span>
      </div>`;
  }

  return `
    <details class="fb-card" style="border:1px solid var(--border-color, #dee2e6);border-radius:8px;margin-bottom:10px;overflow:hidden;">
      <summary style="padding:12px 16px;cursor:pointer;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        ${renderStatusBadge(fb.status)}
        ${classificationBadge}
        ${confidenceText}
        <span style="font-weight:500;">${escapeHtml(userName)}</span>
        <span style="color:var(--text-secondary, #6c757d);font-size:13px;">${category}</span>
        <span style="color:var(--text-secondary, #6c757d);font-size:12px;margin-left:auto;">${date}</span>
        ${briefingLink}
      </summary>
      <div style="padding:0 16px 16px;">
        <div style="background:var(--bg-secondary, #f8f9fa);border-radius:6px;padding:10px;margin-top:4px;font-size:13px;white-space:pre-wrap;">${escapeHtml(fb.comment)}</div>
        <div style="font-size:12px;color:var(--text-secondary, #6c757d);margin-top:6px;">${escapeHtml(fb.user_email)} ${fb.flight_id ? '&middot; ' + escapeHtml(fb.flight_id) : ''}</div>
        ${detailsHtml}
        ${actionsHtml}
      </div>
    </details>`;
}

function attachFeedbackHandlers(container: HTMLElement): void {
  // Send Reply
  container.querySelectorAll('.fb-send').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      const id = Number((e.currentTarget as HTMLElement).dataset.id);
      const replyEl = container.querySelector(`.fb-reply[data-id="${id}"]`) as HTMLTextAreaElement | null;
      const notesEl = container.querySelector(`.fb-notes[data-id="${id}"]`) as HTMLTextAreaElement | null;
      const statusEl = container.querySelector(`.fb-action-status[data-id="${id}"]`) as HTMLElement | null;
      const reply = replyEl?.value?.trim();
      if (!reply) {
        if (statusEl) { statusEl.textContent = 'Please enter a reply first.'; statusEl.style.color = '#dc3545'; }
        return;
      }
      try {
        if (notesEl?.value) await saveFeedbackNotes(id, notesEl.value);
        const result = await sendFeedbackReply(id, reply);
        if (statusEl) { statusEl.textContent = `Sent to ${result.sent_to}`; statusEl.style.color = '#16a34a'; }
        setTimeout(() => loadFeedback(), 1500);
      } catch (err) {
        if (statusEl) { statusEl.textContent = `Error: ${err}`; statusEl.style.color = '#dc3545'; }
      }
    });
  });

  // Mark Replied (no email)
  container.querySelectorAll('.fb-mark-replied').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      const id = Number((e.currentTarget as HTMLElement).dataset.id);
      const notesEl = container.querySelector(`.fb-notes[data-id="${id}"]`) as HTMLTextAreaElement | null;
      const replyEl = container.querySelector(`.fb-reply[data-id="${id}"]`) as HTMLTextAreaElement | null;
      try {
        if (replyEl?.value) await saveFeedbackReply(id, replyEl.value);
        if (notesEl?.value) await saveFeedbackNotes(id, notesEl.value);
        await updateFeedbackStatus(id, 'replied');
        loadFeedback();
      } catch (err) {
        const statusEl = container.querySelector(`.fb-action-status[data-id="${id}"]`) as HTMLElement | null;
        if (statusEl) { statusEl.textContent = `Error: ${err}`; statusEl.style.color = '#dc3545'; }
      }
    });
  });

  // Ignore
  container.querySelectorAll('.fb-ignore').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      const id = Number((e.currentTarget as HTMLElement).dataset.id);
      const notesEl = container.querySelector(`.fb-notes[data-id="${id}"]`) as HTMLTextAreaElement | null;
      try {
        if (notesEl?.value) await saveFeedbackNotes(id, notesEl.value);
        await updateFeedbackStatus(id, 'ignored');
        loadFeedback();
      } catch (err) {
        const statusEl = container.querySelector(`.fb-action-status[data-id="${id}"]`) as HTMLElement | null;
        if (statusEl) { statusEl.textContent = `Error: ${err}`; statusEl.style.color = '#dc3545'; }
      }
    });
  });
}

function setupAgentCreateButton(): void {
  document.getElementById('btn-create-agent')?.addEventListener('click', handleCreateAgent);
}

let currentLimit = 25;

async function loadUsers(): Promise<void> {
  const summaryBar = document.getElementById('summary-bar')!;
  const pendingSection = document.getElementById('pending-section')!;
  const pendingList = document.getElementById('pending-list')!;
  const usersBody = document.getElementById('users-tbody')!;
  const agentsBody = document.getElementById('agents-tbody')!;
  const agentsSection = document.getElementById('agents-section')!;
  const errorEl = document.getElementById('error-message')!;

  try {
    const response = await fetchAdminUsers(currentPeriod, currentLimit);
    const { summary, total_humans, users } = response;

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

    // "Showing X of Y" footer
    const footer = document.getElementById('users-footer')!;
    if (currentLimit > 0 && humans.length < total_humans) {
      footer.innerHTML = `Showing ${humans.length} of ${total_humans} users — <a href="#" id="btn-show-all">Show all</a>`;
      footer.style.display = '';
      document.getElementById('btn-show-all')!.addEventListener('click', (e) => {
        e.preventDefault();
        currentLimit = 0;
        loadUsers();
      });
    } else if (currentLimit === 0 && total_humans > 25) {
      footer.innerHTML = `Showing all ${total_humans} users — <a href="#" id="btn-show-less">Show less</a>`;
      footer.style.display = '';
      document.getElementById('btn-show-less')!.addEventListener('click', (e) => {
        e.preventDefault();
        currentLimit = 25;
        loadUsers();
      });
    } else {
      footer.style.display = 'none';
    }
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

function fmtSec(v: number | null): string {
  if (v == null) return '-';
  if (v < 60) return `${v.toFixed(1)}s`;
  const mins = Math.floor(v / 60);
  const secs = Math.round(v % 60);
  return `${String(mins).padStart(2, '0')}m${String(secs).padStart(2, '0')}s`;
}

async function loadPerformance(): Promise<void> {
  const container = document.getElementById('perf-content')!;
  container.innerHTML = '<p class="muted" style="text-align:center;padding:2rem;">Loading metrics...</p>';
  try {
    const m = await fetchAdminMetrics();
    container.innerHTML = renderPerformance(m);
  } catch (err) {
    container.innerHTML = `<p style="color:#dc3545;text-align:center;padding:1rem;">Failed to load metrics: ${err}</p>`;
  }
}

function renderPerformance(m: AdminMetrics): string {
  const windowRow = (label: string, w: AdminMetricsWindow): string => `
    <tr>
      <td>${label}</td>
      <td class="num">${w.total_refreshes}</td>
      <td class="num">${fmtSec(w.avg_elapsed_seconds)}</td>
      <td class="num">${fmtSec(w.p95_elapsed_seconds)}</td>
      <td class="num">${fmtSec(w.avg_queue_wait_seconds)}</td>
      <td class="num">${fmtSec(w.max_queue_wait_seconds)}</td>
    </tr>`;

  const triggerCells = Object.entries(m.last_24h.by_trigger)
    .map(([k, v]) => `<div class="summary-card"><div class="value">${v}</div><div class="label">${escapeHtml(k)}</div></div>`)
    .join('');

  return `
    <h3>Live Queue</h3>
    <div class="summary-bar">
      <div class="summary-card"><div class="value">${m.current.active_refreshes}</div><div class="label">Active</div></div>
      <div class="summary-card"><div class="value">${m.current.queued_refreshes}</div><div class="label">Queued</div></div>
    </div>

    <h3>Refresh Timing</h3>
    <div style="overflow-x:auto;">
      <table class="admin-table">
        <thead>
          <tr>
            <th>Window</th>
            <th style="text-align:center;">Refreshes</th>
            <th style="text-align:center;">Avg Time</th>
            <th style="text-align:center;">P95 Time</th>
            <th style="text-align:center;">Avg Queue Wait</th>
            <th style="text-align:center;">Max Queue Wait</th>
          </tr>
        </thead>
        <tbody>
          ${windowRow('Last 24h', m.last_24h)}
          ${windowRow('Last 7d', m.last_7d)}
          ${windowRow('Last 30d', m.last_30d)}
        </tbody>
      </table>
    </div>

    <h3 style="margin-top:1.5rem;">By Trigger (24h)</h3>
    <div class="summary-bar">
      ${triggerCells || '<p class="muted">No refreshes in the last 24 hours.</p>'}
    </div>`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, i);
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`;
}

// --- Systems tab (cross-app hub) ---

const SERVICE_LABELS: Record<string, string> = {
  'flyfun-weather': 'Weather',
  'flyfun-maps': 'Maps',
  'flyfun-forms': 'Forms',
};

function setupSystemsPeriodToggle(): void {
  document.querySelectorAll('#systems-period-toggle .toggle-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const period = (btn as HTMLElement).dataset.period as AdminPeriod;
      if (period === systemsPeriod) return;
      systemsPeriod = period;
      document.querySelectorAll('#systems-period-toggle .toggle-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      loadSystems();
    });
  });
}

async function loadSystems(): Promise<void> {
  const tbody = document.getElementById('systems-tbody')!;
  tbody.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:2rem;">Loading...</td></tr>';
  try {
    systemsData = await fetchHubUsers(systemsPeriod);
    renderSystems();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:2rem;color:var(--danger);">Failed to load: ${err}</td></tr>`;
  }
}

function sortSystemsUsers(users: HubResponse['users']): HubResponse['users'] {
  return [...users].sort((a, b) => {
    if (systemsSort === 'last_active') {
      const ta = a.last_active || '';
      const tb = b.last_active || '';
      return tb.localeCompare(ta);
    }
    return b.total_cost_usd - a.total_cost_usd;
  });
}

function renderSystems(): void {
  const data = systemsData;
  if (!data) return;

  // Collect all service names across all users
  const allServices = new Set<string>();
  for (const u of data.users) {
    for (const svc of Object.keys(u.services)) {
      allServices.add(svc);
    }
  }
  const services = [...allServices].sort();

  // Summary bar
  const summaryEl = document.getElementById('systems-summary')!;
  summaryEl.style.display = '';
  summaryEl.innerHTML = `
    <div class="summary-card"><div class="value">${data.totals.users}</div><div class="label">Users</div></div>
    <div class="summary-card"><div class="value">$${data.totals.cost_usd.toFixed(2)}</div><div class="label">Total Cost</div></div>
    <div class="summary-card"><div class="value">${data.totals.actions}</div><div class="label">Actions</div></div>
    ${services.map(svc => {
      const total = data.users.reduce((sum, u) => sum + (u.services[svc]?.cost_usd || 0), 0);
      return `<div class="summary-card"><div class="value">$${total.toFixed(2)}</div><div class="label">${SERVICE_LABELS[svc] || svc}</div></div>`;
    }).join('')}`;

  const sortIcon = (col: string) => systemsSort === col ? ' ▼' : '';

  // Table header with clickable sort columns
  const thead = document.getElementById('systems-thead')!;
  thead.innerHTML = `<tr>
    <th>Name</th>
    <th>Email</th>
    <th class="sortable-th" data-sort-col="last_active" style="cursor:pointer;">Last Active${sortIcon('last_active')}</th>
    ${services.map(svc => `<th style="text-align:center;">${SERVICE_LABELS[svc] || svc}</th>`).join('')}
    <th class="sortable-th" data-sort-col="total_cost" style="text-align:right;cursor:pointer;">Total${sortIcon('total_cost')}</th>
    <th></th>
  </tr>`;

  // Wire up sort clicks
  thead.querySelectorAll('.sortable-th').forEach(th => {
    th.addEventListener('click', () => {
      systemsSort = (th as HTMLElement).dataset.sortCol as typeof systemsSort;
      renderSystems();
    });
  });

  // Table body
  const tbody = document.getElementById('systems-tbody')!;
  const sorted = sortSystemsUsers(data.users);
  if (sorted.length === 0) {
    tbody.innerHTML = `<tr><td colspan="${4 + services.length + 1}" class="muted" style="text-align:center;padding:2rem;">No cost data for this period.</td></tr>`;
    return;
  }
  tbody.innerHTML = sorted.map(u => {
    const svcCells = services.map(svc => {
      const s = u.services[svc];
      if (!s) return '<td class="num muted">-</td>';
      return `<td class="num">$${s.cost_usd.toFixed(2)} <span class="muted">(${s.count})</span></td>`;
    }).join('');

    const lastActive = u.last_active
      ? formatDate(u.last_active)
      : '<span class="muted">-</span>';

    // Detail links
    const links = services
      .filter(svc => data.app_registry[svc] && u.services[svc])
      .map(svc => {
        const url = data.app_registry[svc]!.replace('{user_id}', encodeURIComponent(u.id));
        return `<a href="${url}" style="font-size:0.75rem;">${SERVICE_LABELS[svc] || svc}</a>`;
      })
      .join(' ');

    return `<tr>
      <td>${escapeHtml(u.display_name)}</td>
      <td class="muted">${escapeHtml(u.email)}</td>
      <td>${lastActive}</td>
      ${svcCells}
      <td style="text-align:right;font-weight:600;">$${u.total_cost_usd.toFixed(2)}</td>
      <td>${links}</td>
    </tr>`;
  }).join('');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
