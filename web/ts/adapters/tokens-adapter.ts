/** Tokens adapter — user-facing API token management for MCP access. */

export interface TokenListItem {
  id: number;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

export interface TokenCreateResponse {
  id: number;
  name: string;
  token: string;
  created_at: string;
}

export async function fetchTokens(): Promise<TokenListItem[]> {
  const resp = await fetch('/api/tokens');
  if (!resp.ok) return [];
  return resp.json();
}

export async function createToken(name: string): Promise<TokenCreateResponse> {
  const resp = await fetch('/api/tokens', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: 'Failed to create token' }));
    throw new Error(body.detail || 'Failed to create token');
  }
  return resp.json();
}

export async function revokeToken(tokenId: number): Promise<void> {
  const resp = await fetch(`/api/tokens/${tokenId}`, { method: 'DELETE' });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: 'Failed to revoke token' }));
    throw new Error(body.detail || 'Failed to revoke token');
  }
}
