/** Admin page entry point — user management and usage overview. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { fetchAdminUsers, approveUser, type AdminUser, type AdminSummary } from './adapters/admin-adapter';
import { renderUserInfo, escapeHtml, formatDate } from './utils';

async function init(): Promise<void> {
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  renderUserInfo(user);

  await loadUsers();
}

async function loadUsers(): Promise<void> {
  const summaryBar = document.getElementById('summary-bar')!;
  const pendingSection = document.getElementById('pending-section')!;
  const pendingList = document.getElementById('pending-list')!;
  const usersBody = document.getElementById('users-tbody')!;
  const errorEl = document.getElementById('error-message')!;

  try {
    const response = await fetchAdminUsers();
    const { summary, users } = response;

    // Summary bar
    renderSummaryBar(summaryBar, summary);

    // Pending approvals
    const pending = users.filter(u => !u.approved);
    if (pending.length > 0) {
      pendingSection.style.display = '';
      pendingList.innerHTML = pending.map(renderPendingCard).join('');
      pendingList.querySelectorAll('.btn-approve').forEach(btn => {
        btn.addEventListener('click', handleApprove);
      });
    } else {
      pendingSection.style.display = 'none';
    }

    // All users table
    usersBody.innerHTML = users.map(renderUserRow).join('');
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

function renderSummaryBar(el: HTMLElement, s: AdminSummary): void {
  const tokens = s.total_tokens >= 1000
    ? `~${Math.round(s.total_tokens / 1000)}K`
    : String(s.total_tokens);
  el.style.display = '';
  el.innerHTML = `
    <div class="summary-card"><div class="value">${s.total_users}</div><div class="label">Users</div></div>
    <div class="summary-card"><div class="value">${s.total_briefings}</div><div class="label">Briefings (Mo)</div></div>
    <div class="summary-card"><div class="value">${tokens}</div><div class="label">Tokens (Mo)</div></div>
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

function renderUserRow(u: AdminUser): string {
  const status = u.approved
    ? '<span class="badge badge-green">Active</span>'
    : '<span class="badge badge-amber">Pending</span>';
  const lastLogin = u.last_login_at ? formatDate(u.last_login_at) : '-';
  const m = u.usage_month;
  const tokens = m.total_tokens >= 1000
    ? `~${Math.round(m.total_tokens / 1000)}K`
    : String(m.total_tokens);
  const approveBtn = u.approved
    ? ''
    : `<button class="btn btn-primary btn-approve" style="font-size:0.75rem;padding:0.2rem 0.5rem;" data-user-id="${escapeHtml(u.id)}">Approve</button>`;
  return `
    <tr>
      <td>${escapeHtml(u.display_name)}</td>
      <td>${escapeHtml(u.email)}</td>
      <td>${status} ${approveBtn}</td>
      <td>${lastLogin}</td>
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
