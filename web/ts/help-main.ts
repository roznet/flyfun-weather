/** Help page entry point — auth check + user info only. Content is static HTML. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { renderUserInfo } from './utils';

async function init(): Promise<void> {
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  renderUserInfo(user);
}

init();
