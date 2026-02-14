/** Auth adapter — current user check and logout. */

export interface CurrentUser {
  id: string;
  email: string;
  name: string;
  approved: boolean;
  is_admin: boolean;
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
