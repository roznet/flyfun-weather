/** Help page entry point — user info if signed in. Content is static HTML, no auth required. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { renderUserInfo } from './utils';

async function init(): Promise<void> {
  const user = await fetchCurrentUser();
  if (user) {
    renderUserInfo(user, 'help');
    document.getElementById('getting-access')?.remove();
  } else {
    const info = document.getElementById('user-info');
    if (info) {
      info.innerHTML = `
        <a href="/" class="btn-settings" title="Flights">Flights</a>
        <a href="/settings.html" class="btn-settings" title="Settings">Settings</a>
        <span class="btn-settings nav-current">Help</span>
        <a href="/login.html" class="btn-settings">Sign in</a>
      `;
    }
  }
}

init();
