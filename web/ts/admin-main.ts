/** Admin page entry point — user management, agent management, and usage overview. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  fetchAdminUsers, approveUser, createAgent, createAgentToken,
  revokeAgent, fetchAdminFeedback, fetchAdminMetrics, fetchHubUsers,
  fetchApiUsage, updateFeedbackStatus, saveFeedbackReply, sendFeedbackReply,
  saveFeedbackNotes, reopenFeedback,
  type AdminUser, type AdminSummary, type AdminPeriod, type FeedbackEntry,
  type FeedbackStatus, type AdminMetrics, type AdminMetricsWindow, type HubResponse, type ApiUsageResponse,
} from './adapters/admin-adapter';
import { redirectToLogin, renderUserInfo, escapeHtml, formatDate } from './utils';
import { initTheme } from './theme';
import { initI18n } from './i18n/i18n';

let currentPeriod: AdminPeriod = '30d';

async function init(): Promise<void> {
  await initI18n();
  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    return;
  }
  initTheme();
  renderUserInfo(user, 'admin');

  setupAgentCreateButton();
  setupTabs();
  setupPeriodToggle();
  // Load both tabs in parallel
  // Load API usage first so summary bar can include the API call count
  await loadApiUsage();
  await Promise.all([loadUsers(), loadFeedback()]);
}

function setupPeriodToggle(): void {
  document.querySelectorAll('#period-toggle .toggle-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const period = (btn as HTMLElement).dataset.period as AdminPeriod;
      if (period === currentPeriod) return;
      currentPeriod = period;
      currentOffset = 0;
      document.querySelectorAll('#period-toggle .toggle-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      loadUsers();
    });
  });
}

let perfLoaded = false;
let systemsLoaded = false;
let messagesLoaded = false;
let systemsPeriod: AdminPeriod = '30d';
let systemsSort: 'last_active' | 'total_cost' = 'last_active';
let systemsData: HubResponse | null = null;
let apiUsageData: ApiUsageResponse | null = null;

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
      if (tabId === 'tab-messages' && !messagesLoaded) {
        messagesLoaded = true;
        loadAdminMessages();
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
          <summary style="cursor:pointer;font-weight:600;color:var(--text-muted);margin-bottom:12px;">
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
    ? `<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;background:var(--surface);color:var(--primary);border:1px solid var(--border);margin-left:6px;">${CLASSIFICATION_LABELS[fb.classification] ?? fb.classification}</span>`
    : '';
  const confidenceText = fb.confidence != null
    ? `<span style="font-size:11px;color:var(--text-muted);margin-left:6px;">${Math.round(fb.confidence * 100)}% confidence</span>`
    : '';

  let detailsHtml = '';

  // AI analysis section
  if (fb.ai_analysis) {
    detailsHtml += `
      <div style="margin-top:12px;">
        <label style="font-size:12px;font-weight:600;color:var(--text-muted);display:block;margin-bottom:4px;">AI Analysis</label>
        <div style="background:var(--surface);border-radius:6px;padding:10px;font-size:13px;white-space:pre-wrap;max-height:150px;overflow-y:auto;">${escapeHtml(fb.ai_analysis)}</div>
      </div>`;
  }

  // Reply section
  if (!isArchived) {
    const replyValue = escapeHtml(fb.admin_reply || '');
    detailsHtml += `
      <div style="margin-top:12px;">
        <label style="font-size:12px;font-weight:600;color:var(--text-muted);display:block;margin-bottom:4px;">Reply</label>
        <textarea class="fb-reply" data-id="${fb.id}" rows="8" style="width:100%;box-sizing:border-box;border:1px solid var(--border);border-radius:6px;padding:8px;font-size:13px;resize:vertical;background:var(--surface);color:var(--text);">${replyValue}</textarea>
      </div>`;
  } else if (fb.admin_reply) {
    detailsHtml += `
      <div style="margin-top:12px;">
        <label style="font-size:12px;font-weight:600;color:var(--text-muted);display:block;margin-bottom:4px;">Reply${fb.replied_at ? ` (sent ${formatDate(fb.replied_at)})` : ''}</label>
        <div style="background:var(--surface);border-radius:6px;padding:10px;font-size:13px;white-space:pre-wrap;">${escapeHtml(fb.admin_reply)}</div>
      </div>`;
  }

  // Notes section
  if (!isArchived) {
    const notesValue = escapeHtml(fb.admin_notes || '');
    detailsHtml += `
      <div style="margin-top:12px;">
        <label style="font-size:12px;font-weight:600;color:var(--text-muted);display:block;margin-bottom:4px;">Admin Notes</label>
        <textarea class="fb-notes" data-id="${fb.id}" rows="2" style="width:100%;box-sizing:border-box;border:1px solid var(--border);border-radius:6px;padding:8px;font-size:13px;resize:vertical;background:var(--surface);color:var(--text);">${notesValue}</textarea>
      </div>`;
  } else if (fb.admin_notes) {
    detailsHtml += `
      <div style="margin-top:12px;">
        <label style="font-size:12px;font-weight:600;color:var(--text-muted);display:block;margin-bottom:4px;">Admin Notes</label>
        <div style="background:var(--surface);border-radius:6px;padding:10px;font-size:13px;white-space:pre-wrap;">${escapeHtml(fb.admin_notes)}</div>
      </div>`;
  }

  // Action buttons
  let actionsHtml = '';
  if (!isArchived) {
    actionsHtml = `
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
        <button class="fb-send btn-sm" data-id="${fb.id}" style="background:#2563eb;color:#fff;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px;font-weight:500;">Send Reply</button>
        <button class="fb-mark-replied btn-sm" data-id="${fb.id}" style="background:var(--bg);color:var(--text);border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px;">Mark Replied</button>
        <button class="fb-ignore btn-sm" data-id="${fb.id}" style="background:var(--bg);color:var(--text-muted);border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px;">Ignore</button>
        <span class="fb-action-status" data-id="${fb.id}" style="font-size:12px;align-self:center;"></span>
      </div>`;
  } else {
    actionsHtml = `
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
        <button class="fb-reopen btn-sm" data-id="${fb.id}" style="background:var(--bg);color:var(--primary);border:1px solid var(--border);border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px;">Reply Again</button>
        <span class="fb-action-status" data-id="${fb.id}" style="font-size:12px;align-self:center;"></span>
      </div>`;
  }

  return `
    <details class="fb-card" style="border:1px solid var(--border);border-radius:8px;margin-bottom:10px;overflow:hidden;">
      <summary style="padding:12px 16px;cursor:pointer;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        ${renderStatusBadge(fb.status)}
        ${classificationBadge}
        ${confidenceText}
        <span style="font-weight:500;">${escapeHtml(userName)}</span>
        <span style="color:var(--text-muted);font-size:13px;">${category}</span>
        <span style="color:var(--text-muted);font-size:12px;margin-left:auto;">${date}</span>
        ${briefingLink}
      </summary>
      <div style="padding:0 16px 16px;">
        <div style="background:var(--surface);border-radius:6px;padding:10px;margin-top:4px;font-size:13px;white-space:pre-wrap;">${escapeHtml(fb.comment)}</div>
        <div style="font-size:12px;color:var(--text-muted);margin-top:6px;">${escapeHtml(fb.user_email)} ${fb.flight_id ? '&middot; ' + escapeHtml(fb.flight_id) : ''}</div>
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

  // Reply Again (re-open archived feedback)
  container.querySelectorAll('.fb-reopen').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      const id = Number((e.currentTarget as HTMLElement).dataset.id);
      const statusEl = container.querySelector(`.fb-action-status[data-id="${id}"]`) as HTMLElement | null;
      try {
        await reopenFeedback(id);
        loadFeedback();
      } catch (err) {
        if (statusEl) { statusEl.textContent = `Error: ${err}`; statusEl.style.color = '#dc3545'; }
      }
    });
  });
}

function setupAgentCreateButton(): void {
  document.getElementById('btn-create-agent')?.addEventListener('click', handleCreateAgent);
}

const PAGE_SIZE = 25;
let currentOffset = 0;

async function loadUsers(): Promise<void> {
  const summaryBar = document.getElementById('summary-bar')!;
  const pendingSection = document.getElementById('pending-section')!;
  const pendingList = document.getElementById('pending-list')!;
  const usersBody = document.getElementById('users-tbody')!;
  const agentsBody = document.getElementById('agents-tbody')!;
  const agentsSection = document.getElementById('agents-section')!;
  const errorEl = document.getElementById('error-message')!;

  try {
    const response = await fetchAdminUsers(currentPeriod, PAGE_SIZE, currentOffset);
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

    // Pagination footer
    const footer = document.getElementById('users-footer')!;
    if (total_humans > PAGE_SIZE) {
      const pageStart = currentOffset + 1;
      const pageEnd = Math.min(currentOffset + humans.length, total_humans);
      const parts: string[] = [`Showing ${pageStart}–${pageEnd} of ${total_humans} users`];
      const links: string[] = [];
      if (currentOffset > 0) {
        links.push(`<a href="#" id="btn-prev-page">\u2190 Previous</a>`);
      }
      if (currentOffset + PAGE_SIZE < total_humans) {
        links.push(`<a href="#" id="btn-next-page">Next \u2192</a>`);
      }
      if (links.length > 0) parts.push(links.join(' | '));
      footer.innerHTML = parts.join(' — ');
      footer.style.display = '';
      document.getElementById('btn-prev-page')?.addEventListener('click', (e) => {
        e.preventDefault();
        currentOffset = Math.max(0, currentOffset - PAGE_SIZE);
        loadUsers();
      });
      document.getElementById('btn-next-page')?.addEventListener('click', (e) => {
        e.preventDefault();
        currentOffset += PAGE_SIZE;
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
  const apiCalls = apiUsageData
    ? (currentPeriod === 'all' ? apiUsageData.all_time : apiUsageData.last_30d).total_calls
    : 0;
  el.style.display = '';
  el.innerHTML = `
    <div class="summary-card"><div class="value">${s.total_users}</div><div class="label">Users</div></div>
    <div class="summary-card"><div class="value">${s.total_briefings}</div><div class="label">Briefings (${pl})</div></div>
    <div class="summary-card"><div class="value">${tokens}</div><div class="label">Tokens (${pl})</div></div>
    <div class="summary-card"><div class="value">${apiCalls.toLocaleString()}</div><div class="label">API Calls (${pl})</div></div>
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

// --- Admin Messages (What's New) ---

const MSG_CATEGORIES = ['feature', 'change', 'fix'];
const MSG_CATEGORY_LABELS: Record<string, string> = { feature: 'New Feature', change: 'Change', fix: 'Fix' };

async function loadAdminMessages(): Promise<void> {
  const { adminListMessages } = await import('./adapters/messages-adapter');
  const container = document.getElementById('messages-admin-list')!;
  try {
    const messages = await adminListMessages();
    if (messages.length === 0) {
      container.innerHTML = '<p class="muted" style="text-align:center;padding:2rem;">No messages yet. Click "New Message" to create one.</p>';
    } else {
      container.innerHTML = `<table class="admin-table">
        <thead><tr>
          <th>Date</th><th>Category</th><th>Title</th><th style="max-width:300px">Body</th><th>Actions</th>
        </tr></thead>
        <tbody>${messages.map(m => `<tr data-id="${m.id}">
          <td>${escapeHtml(m.date)}</td>
          <td><span class="message-category message-category-${escapeHtml(m.category)}">${escapeHtml(MSG_CATEGORY_LABELS[m.category] || m.category)}</span></td>
          <td>${escapeHtml(m.title)}</td>
          <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(m.body)}">${escapeHtml(m.body.length > 80 ? m.body.slice(0, 80) + '...' : m.body)}</td>
          <td>
            <button class="btn" style="font-size:0.75rem;padding:0.2rem 0.5rem;" onclick="window._editMsg(${m.id})">Edit</button>
            <button class="btn btn-danger" style="font-size:0.75rem;padding:0.2rem 0.5rem;" onclick="window._deleteMsg(${m.id})">Delete</button>
          </td>
        </tr>`).join('')}</tbody>
      </table>`;
    }
  } catch (err) {
    container.innerHTML = `<p class="muted" style="text-align:center;color:#dc3545;">Failed to load messages: ${escapeHtml(String(err))}</p>`;
  }

  // Wire up create button
  document.getElementById('btn-create-message')?.addEventListener('click', () => showMessageModal());
}

function showMessageModal(existing?: { id: number; date: string; title: string; body: string; category: string }): void {
  document.getElementById('msg-modal')?.remove();

  const isEdit = !!existing;
  const today = new Date().toISOString().slice(0, 10);
  const catOptions = MSG_CATEGORIES.map(c =>
    `<option value="${c}" ${existing?.category === c ? 'selected' : ''}>${MSG_CATEGORY_LABELS[c]}</option>`
  ).join('');

  const overlay = document.createElement('div');
  overlay.id = 'msg-modal';
  overlay.className = 'token-modal-overlay';
  overlay.innerHTML = `
    <div class="token-modal">
      <h3>${isEdit ? 'Edit Message' : 'New Message'}</h3>
      <div style="display:flex;flex-direction:column;gap:0.5rem;margin-top:0.75rem;">
        <label style="font-size:0.85rem;font-weight:500;">Date</label>
        <input type="date" id="msg-date" value="${existing?.date || today}" style="padding:0.4rem;border:1px solid var(--border);border-radius:4px;">
        <label style="font-size:0.85rem;font-weight:500;">Category</label>
        <select id="msg-category" style="padding:0.4rem;border:1px solid var(--border);border-radius:4px;">${catOptions}</select>
        <label style="font-size:0.85rem;font-weight:500;">Title</label>
        <input type="text" id="msg-title" value="${escapeHtml(existing?.title || '')}" placeholder="Short title" style="padding:0.4rem;border:1px solid var(--border);border-radius:4px;">
        <label style="font-size:0.85rem;font-weight:500;">Body (Markdown)</label>
        <textarea id="msg-body" rows="6" style="padding:0.4rem;border:1px solid var(--border);border-radius:4px;resize:vertical;font-family:inherit;" placeholder="Supports **bold**, *italic*, \`code\`, [links](url), and - list items">${escapeHtml(existing?.body || '')}</textarea>
        <div id="msg-error" style="color:#dc3545;font-size:0.8rem;min-height:1.2em;"></div>
        <div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-top:0.5rem;">
          <button class="btn" id="msg-cancel">Cancel</button>
          <button class="btn btn-primary" id="msg-save">${isEdit ? 'Save' : 'Create'}</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.getElementById('msg-cancel')!.addEventListener('click', () => overlay.remove());

  document.getElementById('msg-save')!.addEventListener('click', async () => {
    const date = (document.getElementById('msg-date') as HTMLInputElement).value;
    const title = (document.getElementById('msg-title') as HTMLInputElement).value.trim();
    const body = (document.getElementById('msg-body') as HTMLTextAreaElement).value.trim();
    const category = (document.getElementById('msg-category') as HTMLSelectElement).value;
    const errorEl = document.getElementById('msg-error')!;

    if (!date || !title || !body) {
      errorEl.textContent = 'Date, title, and body are required.';
      return;
    }

    try {
      if (isEdit) {
        const { adminUpdateMessage } = await import('./adapters/messages-adapter');
        await adminUpdateMessage(existing!.id, { date, title, body, category });
      } else {
        const { adminCreateMessage } = await import('./adapters/messages-adapter');
        await adminCreateMessage({ date, title, body, category });
      }
      overlay.remove();
      loadAdminMessages();
    } catch (err) {
      errorEl.textContent = `Failed: ${String(err)}`;
    }
  });
}

// Expose to onclick handlers in table
(window as any)._editMsg = async (id: number) => {
  const { adminListMessages } = await import('./adapters/messages-adapter');
  const messages = await adminListMessages();
  const msg = messages.find(m => m.id === id);
  if (msg) showMessageModal(msg);
};

(window as any)._deleteMsg = async (id: number) => {
  if (!confirm('Delete this message?')) return;
  const { adminDeleteMessage } = await import('./adapters/messages-adapter');
  try {
    await adminDeleteMessage(id);
    loadAdminMessages();
  } catch (err) {
    alert(`Failed to delete: ${String(err)}`);
  }
};

// --- API Usage summary ---

async function loadApiUsage(): Promise<void> {
  try {
    apiUsageData = await fetchApiUsage();
  } catch {
    // Non-critical — summary bar will show 0
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
