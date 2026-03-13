/** Help page entry point — user info if signed in. Content is static HTML, no auth required. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { renderUserInfo } from './utils';
import { initTheme } from './theme';
import { initI18n, t } from './i18n/i18n';

async function init(): Promise<void> {
  await initI18n();
  initTheme();
  const user = await fetchCurrentUser();
  if (user) {
    renderUserInfo(user, 'help');
    document.getElementById('getting-access')?.remove();
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

init();
