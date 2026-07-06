import { describe, it, expect, vi, afterEach } from 'vitest';
import { refreshBriefingStream } from '../../ts/adapters/api-adapter';
import type { RefreshStreamEvent } from '../../ts/adapters/api-adapter';

// Build a fake fetch Response whose body streams the given SSE frames, so we can
// exercise refreshBriefingStream's terminal-state logic without a real server.
function streamResponse(frames: string[]): { ok: true; body: ReadableStream<Uint8Array> } {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) controller.enqueue(enc.encode(f));
      controller.close();
    },
  });
  return { ok: true, body };
}

function sse(event: Record<string, unknown>): string {
  return `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('refreshBriefingStream terminal handling', () => {
  it('returns null on a pending-coverage complete (pack: null) — not a thrown drop', async () => {
    // This is the regression: a `complete` event with no pack (beyond-horizon
    // flight) must be a defined terminal state, not mistaken for a dropped stream.
    vi.stubGlobal('fetch', vi.fn(async () => streamResponse([
      sse({ type: 'complete', pack: null, refresh_decision: { mode: 'none', reason: 'pending_coverage', available_date: '2026-07-27' } }),
    ])));
    const events: RefreshStreamEvent[] = [];
    const result = await refreshBriefingStream('egtk_lsgs-2026-08-05-54cd', (e) => events.push(e));
    expect(result).toBeNull();
    expect(events.at(-1)?.type).toBe('complete');
  });

  it('returns the pack on a normal complete', async () => {
    const pack = { fetch_timestamp: '2026-07-06T00:00:00Z', has_digest: true };
    vi.stubGlobal('fetch', vi.fn(async () => streamResponse([
      sse({ type: 'progress', progress: 50 }),
      sse({ type: 'complete', pack }),
    ])));
    const result = await refreshBriefingStream('fid', () => {});
    expect(result).toMatchObject({ fetch_timestamp: '2026-07-06T00:00:00Z' });
  });

  it('still throws the drop error when NO complete event arrives', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => streamResponse([
      sse({ type: 'progress', progress: 50 }),
    ])));
    await expect(refreshBriefingStream('fid', () => {})).rejects.toThrow(
      'Refresh stream ended without completion',
    );
  });
});
