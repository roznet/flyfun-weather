/** Help page entry point — user info if signed in. Content is static HTML, no auth required. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { renderUserInfo } from './utils';

async function init(): Promise<void> {
  const user = await fetchCurrentUser();
  if (user) {
    renderUserInfo(user);
    document.getElementById('getting-access')?.remove();
  }
}

init();
