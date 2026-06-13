/** Help page entry point — tabs (Help & Guide / What's New), user info, feedback. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { fetchMessages, markMessagesSeen, type SystemMessage } from './adapters/messages-adapter';
import { submitFeedback } from './adapters/api-adapter';
import { renderUserInfo, checkMessagesBadge, escapeHtml } from './utils';
import { initTheme } from './theme';
import { initI18n, t } from './i18n/i18n';
import { mountDataSourcesTable } from './data-sources-table';
import { initInfoPopup } from './components/info-popup';
import { buildDemoTourUrl } from './tour/demo-config';
import { track, EVENTS } from './analytics/track';

let isSignedIn = false;

async function init(): Promise<void> {
  await initI18n();
  initTheme();
  initInfoPopup();

  const h1 = document.querySelector('h1');
  if (h1) h1.textContent = t('nav.help');

  initTabs();
  renderTourCta();

  const user = await fetchCurrentUser();
  if (user) {
    isSignedIn = true;
    renderUserInfo(user, 'help');
    document.getElementById('getting-access')?.remove();
    // Show feedback button for signed-in users
    const feedbackInline = document.getElementById('feedback-inline');
    if (feedbackInline) feedbackInline.style.display = '';
    document.getElementById('help-feedback-btn')?.addEventListener('click', showFeedbackModal);

    // Load unseen count for the tab badge
    loadTabBadge();
  } else {
    const info = document.getElementById('user-info');
    if (info) {
      info.innerHTML = `
        <a href="/" class="btn-settings" title="${t('nav.flights')}">${t('nav.flights')}</a>
        <a href="/settings.html" class="btn-settings" title="${t('nav.settings')}">${t('nav.settings')}</a>
        <span class="btn-settings nav-current">${t('nav.help')}</span>
        <a href="/login.html" class="btn-settings">${t('nav.signIn')}</a>
      `;
    }
  }

  // Render the data-driven Data Sources & Models table in summary mode for
  // the guide tab. The full-detail table mounts lazily on tab switch.
  const dataSourcesHost = document.getElementById('data-sources-table-host');
  if (dataSourcesHost) {
    mountDataSourcesTable(dataSourcesHost as HTMLElement, 'summary');
  }

  // In-page links from the guide to the full Data Sources tab.
  document.querySelectorAll<HTMLAnchorElement>('a[data-tab-link]').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const tab = a.dataset.tabLink;
      if (tab) switchTab(tab, 'link');
    });
  });

  // Check if URL has ?tab=... to auto-switch. This is also the shareable
  // deep link target (e.g. /whats-new redirects to ?tab=whats-new).
  const params = new URLSearchParams(window.location.search);
  const initialTab = params.get('tab');
  if (initialTab === 'whats-new' || initialTab === 'data-sources') {
    switchTab(initialTab, 'deeplink');
  }
}

function renderTourCta(): void {
  const host = document.getElementById('help-tour-cta-host');
  if (!host) return;
  const url = buildDemoTourUrl();
  if (!url) return;
  host.innerHTML = `
    <div class="help-tour-cta">
      <a class="btn btn-primary" href="${escapeHtml(url)}">${escapeHtml(t('help.startTourBtn'))}</a>
      <p class="muted">${escapeHtml(t('help.startTourHint'))}</p>
    </div>
  `;
}

// --- Tab management ---

function initTabs(): void {
  const tabBar = document.getElementById('help-tabs');
  if (!tabBar) return;

  tabBar.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.tab-btn') as HTMLElement | null;
    if (!btn) return;
    const tab = btn.dataset.tab;
    if (tab) switchTab(tab, 'tab');
  });
}

let messagesLoaded = false;
let fullDataSourcesLoaded = false;

/** How the user arrived at a tab — used as a low-cardinality analytics dim. */
type TabSource = 'tab' | 'link' | 'deeplink';

/** Keep the URL query param in sync with the active tab so copying the
 *  address bar (or bookmarking) captures the tab rather than landing on the
 *  default guide. The default 'guide' tab is represented as a clean URL. */
function syncTabUrl(tab: string): void {
  try {
    const url = new URL(window.location.href);
    if (tab === 'guide') {
      url.searchParams.delete('tab');
    } else {
      url.searchParams.set('tab', tab);
    }
    window.history.replaceState(null, '', url.toString());
  } catch {
    // non-critical — analytics/URL sync must never break tab switching
  }
}

function switchTab(tab: string, source: TabSource = 'tab'): void {
  // Update tab buttons
  document.querySelectorAll('.help-tabs .tab-btn').forEach(btn => {
    btn.classList.toggle('active', (btn as HTMLElement).dataset.tab === tab);
  });
  // Update tab panels
  document.querySelectorAll('.tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `tab-${tab}`);
  });

  syncTabUrl(tab);

  if (tab === 'whats-new') {
    track(EVENTS.HELP_WHATS_NEW_OPENED, { source });
  }

  if (tab === 'whats-new' && !messagesLoaded) {
    messagesLoaded = true;
    loadMessages();
  }
  if (tab === 'data-sources' && !fullDataSourcesLoaded) {
    fullDataSourcesLoaded = true;
    const host = document.getElementById('data-sources-full-host');
    if (host) mountDataSourcesTable(host as HTMLElement, 'full');
  }
}

// --- Messages ---

async function loadTabBadge(): Promise<void> {
  try {
    const { fetchMessagesStatus } = await import('./adapters/messages-adapter');
    const status = await fetchMessagesStatus();
    const badge = document.getElementById('whats-new-badge');
    if (badge) {
      badge.classList.toggle('visible', status.unseen_count > 0);
    }
  } catch {
    // non-critical
  }
}

async function loadMessages(): Promise<void> {
  const container = document.getElementById('messages-container');
  if (!container) return;

  try {
    const messages = await fetchMessages();
    if (messages.length === 0) {
      container.innerHTML = `<p class="messages-empty">${t('messages.empty')}</p>`;
      return;
    }

    // Render messages newest-first
    const sorted = [...messages].sort((a, b) => b.date.localeCompare(a.date));
    container.innerHTML = sorted.map(renderMessageCard).join('');

    // Expand the newest message by default
    container.querySelector('.message-card')?.classList.remove('collapsed');

    // Toggle collapse on header click
    container.addEventListener('click', (e) => {
      const header = (e.target as HTMLElement).closest('.message-card-header');
      if (header) header.closest('.message-card')?.classList.toggle('collapsed');
    });

    // Mark as seen for signed-in users
    if (isSignedIn) {
      try {
        await markMessagesSeen();
        // Clear both the tab badge and nav badge
        document.getElementById('whats-new-badge')?.classList.remove('visible');
        document.getElementById('nav-messages-badge')?.classList.remove('visible');
      } catch {
        // non-critical
      }
    }
  } catch {
    container.innerHTML = `<p class="messages-empty">${t('messages.loadFailed')}</p>`;
  }
}

function renderMessageCard(msg: SystemMessage): string {
  const categoryLabel = t(`messages.category.${msg.category}`);
  const categoryClass = `message-category message-category-${escapeHtml(msg.category)}`;
  const formattedDate = formatMessageDate(msg.date);
  const bodyHtml = renderSimpleMarkdown(msg.body);

  return `
    <div class="message-card collapsed">
      <div class="message-card-header" role="button" tabindex="0">
        <span class="message-card-chevron">&#x25B6;</span>
        <span class="${categoryClass}">${escapeHtml(categoryLabel)}</span>
        <span class="message-card-title">${escapeHtml(msg.title)}</span>
        <span class="message-card-date">${escapeHtml(formattedDate)}</span>
      </div>
      <div class="message-card-body">${bodyHtml}</div>
    </div>`;
}

function formatMessageDate(iso: string): string {
  try {
    const d = new Date(iso + 'T00:00:00Z');
    return d.toLocaleDateString('en-GB', {
      day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
    });
  } catch {
    return iso;
  }
}

/** Minimal markdown renderer: bold, italic, inline code, links, paragraphs, lists. */
function renderSimpleMarkdown(md: string): string {
  // Escape HTML first, then apply markdown
  let html = escapeHtml(md);

  // Bold: **text** or __text__
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');

  // Italic: *text* or _text_ (but not inside **)
  html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');

  // Inline code: `code`
  html = html.replace(/`(.+?)`/g, '<code>$1</code>');

  // Links: [text](url) — only allow http(s) URLs to prevent javascript: XSS
  html = html.replace(/\[(.+?)\]\((https?:\/\/.+?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  // Unordered list items: lines starting with "- "
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

  // Paragraphs: split on double newlines
  html = html
    .split(/\n{2,}/)
    .map(p => p.trim())
    .filter(p => p)
    .map(p => p.startsWith('<ul>') || p.startsWith('<ol>') ? p : `<p>${p}</p>`)
    .join('\n');

  // Single newlines within paragraphs → <br>
  html = html.replace(/(?<!\n)\n(?!\n)/g, '<br>');

  return html;
}

// --- Feedback modal (unchanged) ---

function showFeedbackModal(): void {
  document.getElementById('feedback-modal')?.remove();

  const categories: [string, string][] = [
    ['data_issue', t('feedback.cat.dataIssue')],
    ['too_conservative', t('feedback.cat.tooConservative')],
    ['too_optimistic', t('feedback.cat.tooOptimistic')],
    ['incorrect_interpretation', t('feedback.cat.incorrectInterpretation')],
    ['other', t('feedback.cat.other')],
  ];
  const options = categories.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');

  const overlay = document.createElement('div');
  overlay.id = 'feedback-modal';
  overlay.className = 'feedback-modal-overlay';
  overlay.innerHTML = `
    <div class="feedback-modal">
      <h3>${t('feedback.title')}</h3>
      <p class="muted" style="margin:0 0 0.75rem;">${t('feedback.subtitle')}</p>
      <label for="feedback-category" style="font-weight:500;font-size:0.85rem;">${t('feedback.categoryLabel')}</label>
      <select id="feedback-category" style="width:100%;padding:0.4rem;margin:0.25rem 0 0.75rem;border:1px solid var(--border);border-radius:4px;">
        ${options}
      </select>
      <label for="feedback-comment" style="font-weight:500;font-size:0.85rem;">${t('feedback.commentLabel')}</label>
      <textarea id="feedback-comment" rows="4" style="width:100%;padding:0.4rem;margin:0.25rem 0 0;border:1px solid var(--border);border-radius:4px;resize:vertical;font-family:inherit;" placeholder="${t('feedback.commentPlaceholder')}"></textarea>
      <label style="display:flex;align-items:center;gap:0.4rem;font-size:0.85rem;margin-top:0.5rem;cursor:pointer;">
        <input type="checkbox" id="feedback-contact-ok" checked> ${t('feedback.contactOk')}
      </label>
      <div id="feedback-error" style="color:#dc3545;font-size:0.8rem;min-height:1.2em;margin-top:0.25rem;"></div>
      <div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-top:0.75rem;">
        <button class="btn" id="feedback-cancel">${t('feedback.cancel')}</button>
        <button class="btn btn-primary" id="feedback-submit">${t('feedback.submit')}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const commentEl = document.getElementById('feedback-comment') as HTMLTextAreaElement;
  const categoryEl = document.getElementById('feedback-category') as HTMLSelectElement;
  const errorEl = document.getElementById('feedback-error')!;
  const submitBtn = document.getElementById('feedback-submit') as HTMLButtonElement;

  function dismiss() { overlay.remove(); }

  overlay.addEventListener('click', (e) => { if (e.target === overlay) dismiss(); });
  document.getElementById('feedback-cancel')!.addEventListener('click', dismiss);

  submitBtn.addEventListener('click', async () => {
    const comment = commentEl.value.trim();
    if (!comment) {
      errorEl.textContent = t('feedback.errorEmpty');
      return;
    }
    try {
      submitBtn.disabled = true;
      submitBtn.textContent = t('feedback.submitting');
      const contactOk = (document.getElementById('feedback-contact-ok') as HTMLInputElement | null)?.checked ?? false;
      await submitFeedback({
        flight_id: '',
        pack_timestamp: '',
        category: categoryEl.value,
        comment,
        target: 'general',
        contact_ok: contactOk,
      });
      const modal = overlay.querySelector('.feedback-modal')!;
      modal.innerHTML = `
        <h3>${t('feedback.thanks')}</h3>
        <p class="muted">${t('feedback.submitted')}</p>`;
      setTimeout(dismiss, 2000);
    } catch (err) {
      errorEl.textContent = t('feedback.failedSubmit', { error: String(err) });
      submitBtn.disabled = false;
      submitBtn.textContent = t('feedback.submit');
    }
  });
}

init();
