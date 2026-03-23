/** Auth adapter — current user check and logout. */

export interface CurrentUser {
  id: string;
  email: string;
  name: string;
  approved: boolean;
  is_admin: boolean;
  setup_completed: boolean;
}

export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  try {
    const resp = await fetch('/auth/me');
    if (!resp.ok) return null;
    return resp.json();
  } catch {
    return null;
  }
}

export async function logout(): Promise<void> {
  await fetch('/auth/logout', { method: 'POST' });
  window.location.href = '/login.html';
}

export async function deleteAccount(): Promise<void> {
  const resp = await fetch('/auth/account', { method: 'DELETE' });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(body.detail || `HTTP ${resp.status}`);
  }
  window.location.href = '/login.html';
}
