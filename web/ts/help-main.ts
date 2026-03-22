/** Help page entry point — user info if signed in. Content is static HTML, no auth required. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { submitFeedback } from './adapters/api-adapter';
import { renderUserInfo } from './utils';
import { initTheme } from './theme';
import { initI18n, t } from './i18n/i18n';

async function init(): Promise<void> {
  await initI18n();
  initTheme();
  // Translate the page title
  const h1 = document.querySelector('h1');
  if (h1) h1.textContent = t('nav.help');
  const user = await fetchCurrentUser();
  if (user) {
    renderUserInfo(user, 'help');
    document.getElementById('getting-access')?.remove();
    // Show feedback button for signed-in users
    const feedbackInline = document.getElementById('feedback-inline');
    if (feedbackInline) feedbackInline.style.display = '';
    document.getElementById('help-feedback-btn')?.addEventListener('click', showFeedbackModal);
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
}

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
      await submitFeedback({
        flight_id: '',
        pack_timestamp: '',
        category: categoryEl.value,
        comment,
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
