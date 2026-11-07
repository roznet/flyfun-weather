import { describe, expect, it } from 'vitest';
import { parseServiceStatus } from '../../ts/service-status';

const maintenance = JSON.stringify({
  service_status: 'maintenance',
  detail: 'FlyFun Weather is down for planned maintenance. Expected back around 14:30Z.',
  until: '2026-11-07T14:30:00Z',
  updated_at: '2026-11-07T13:00:00Z',
});

describe('parseServiceStatus', () => {
  it('reads the Caddy maintenance body', () => {
    const s = parseServiceStatus(503, maintenance);
    expect(s?.kind).toBe('maintenance');
    expect(s?.message).toMatch(/^FlyFun Weather is down for planned maintenance/);
    expect(s?.until?.toISOString()).toBe('2026-11-07T14:30:00.000Z');
  });

  it('reads the generic outage body on a 502', () => {
    const s = parseServiceStatus(502, JSON.stringify({
      service_status: 'unavailable', detail: 'Temporarily unavailable.', until: null,
    }));
    expect(s).toEqual({ kind: 'unavailable', message: 'Temporarily unavailable.', until: null });
  });

  it('leaves app errors alone', () => {
    // FastAPI's own 500/503 carry detail but no service_status.
    expect(parseServiceStatus(503, '{"detail":"Pack not ready"}')).toBeNull();
    expect(parseServiceStatus(500, '<html>Internal error</html>')).toBeNull();
    // Never on a 4xx, even with the shape.
    expect(parseServiceStatus(409, maintenance)).toBeNull();
    expect(parseServiceStatus(503, 'null')).toBeNull();
    expect(parseServiceStatus(503, '{"service_status":"weird"}')).toBeNull();
  });

  it('falls back to a generic sentence and ignores a bad until', () => {
    const s = parseServiceStatus(503, '{"service_status":"maintenance","detail":"","until":"soon"}');
    expect(s?.message).toMatch(/temporarily unavailable/);
    expect(s?.until).toBeNull();
  });
});

describe('observeFetchForServiceStatus', () => {
  it('shows and clears the banner from plain fetch, and returns responses untouched', async () => {
    const { observeFetchForServiceStatus } = await import('../../ts/service-status');
    const nodes: Record<string, { id: string; textContent: string; dataset: Record<string, string>; remove: () => void }> = {};
    const g = globalThis as unknown as Record<string, unknown>;
    const saved = { window: g.window, document: g.document, location: g.location };
    const queue: Response[] = [
      new Response(maintenance, { status: 503 }),
      new Response('{"ok":true}', { status: 200 }),
      new Response('{"detail":"Server busy"}', { status: 503 }),
    ];
    const win = { fetch: async () => queue.shift()! };
    g.window = win;
    g.location = { href: 'https://weather.flyfun.aero/maps.html', origin: 'https://weather.flyfun.aero' };
    g.document = {
      body: { prepend: (el: { id: string }) => { nodes[el.id] = el as never; } },
      getElementById: (id: string) => nodes[id] ?? null,
      createElement: () => {
        const el = { id: '', className: '', textContent: '', dataset: {} as Record<string, string>,
          setAttribute: () => {}, remove: () => { delete nodes[el.id]; } };
        return el;
      },
    };
    try {
      observeFetchForServiceStatus();
      const r1 = await win.fetch('/api/maps/now' as never);
      expect(r1.status).toBe(503);
      expect(await r1.text()).toBe(maintenance); // body still readable by the caller
      expect(nodes['service-status-banner']?.textContent).toMatch(/planned maintenance/);
      await win.fetch('/api/maps/now' as never);
      expect(nodes['service-status-banner']).toBeUndefined();
      await win.fetch('/api/maps/now' as never); // app's own 503: no banner
      expect(nodes['service-status-banner']).toBeUndefined();
    } finally {
      g.window = saved.window; g.document = saved.document; g.location = saved.location;
    }
  });
});
