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
