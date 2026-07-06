/** Tests for shared utility functions: formatting, route titles, Windy URLs. */

import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import {
  escapeHtml, formatTime, formatDepartureTime, formatAlt,
  isFlightPast, flightTitle, flightRouteCompact,
  buildWindyUrl, errorToMessage, flightShareUrl,
  initBookingConfig, maxBookingLeadDays,
} from '../../ts/utils';

describe('escapeHtml', () => {
  it('escapes the five HTML entities', () => {
    expect(escapeHtml(`<a href="x" data-y='z'>&hello</a>`)).toBe(
      '&lt;a href=&quot;x&quot; data-y=&#x27;z&#x27;&gt;&amp;hello&lt;/a&gt;',
    );
  });

  it('handles empty string', () => {
    expect(escapeHtml('')).toBe('');
  });
});

describe('formatTime', () => {
  it('formats hour and minute with leading zeros and Z suffix', () => {
    expect(formatTime(8)).toBe('0800Z');
    expect(formatTime(9, 30)).toBe('0930Z');
    expect(formatTime(0, 0)).toBe('0000Z');
    expect(formatTime(23, 59)).toBe('2359Z');
  });
});

describe('formatDepartureTime', () => {
  it('extracts UTC hour/minute from ISO string', () => {
    expect(formatDepartureTime('2026-04-29T08:30:00Z')).toBe('0830Z');
    expect(formatDepartureTime('2026-04-29T14:00:00Z')).toBe('1400Z');
  });
});

describe('formatAlt', () => {
  it('renders sub-FL altitudes as plain feet', () => {
    expect(formatAlt(2500)).toBe('2500ft');
    expect(formatAlt(9999)).toBe('9999ft');
  });

  it('renders FL at 10000+ ft', () => {
    expect(formatAlt(10000)).toBe('FL100');
    expect(formatAlt(35000)).toBe('FL350');
  });

  it('uses ≥ 10000 boundary', () => {
    expect(formatAlt(9999)).toBe('9999ft');
    expect(formatAlt(10000)).toBe('FL100');
  });
});

describe('isFlightPast', () => {
  // Use departureTime ISO form (the modern path).
  it('returns true when start + duration is before now', () => {
    const past = new Date(Date.now() - 5 * 3600_000).toISOString();
    expect(isFlightPast('', 0, 1, past)).toBe(true);
  });

  it('returns false when start + duration is after now', () => {
    const future = new Date(Date.now() + 1 * 3600_000).toISOString();
    expect(isFlightPast('', 0, 5, future)).toBe(false);
  });

  it('returns false for unparseable input', () => {
    expect(isFlightPast('', 0, 1, 'garbage')).toBe(false);
  });

  it('legacy targetDate + targetTimeUtc form', () => {
    // 2020-01-01 00:00Z + 1hr is well past
    expect(isFlightPast('2020-01-01', 0, 1)).toBe(true);
  });
});

describe('flightTitle', () => {
  it('joins origin and destination with arrow', () => {
    expect(flightTitle(['LFPN', 'EGLF'])).toBe('LFPN → EGLF');
  });

  it('returns single waypoint as-is', () => {
    expect(flightTitle(['LFPN'])).toBe('LFPN');
  });

  it('skips middle waypoints', () => {
    expect(flightTitle(['LFPN', 'LFAT', 'EGLF'])).toBe('LFPN → EGLF');
  });

  it('returns empty for empty array', () => {
    expect(flightTitle([])).toBe('');
  });
});

describe('flightRouteCompact', () => {
  it('returns empty for no waypoints', () => {
    const r = flightRouteCompact([]);
    expect(r).toEqual({ html: '', fullText: '', isTruncated: false });
  });

  it('renders full route when within maxVisible', () => {
    const r = flightRouteCompact(['LFPN', 'LFAT', 'EGLF']);
    expect(r.isTruncated).toBe(false);
    expect(r.fullText).toBe('LFPN → LFAT → EGLF');
    expect(r.html).toBe('LFPN → LFAT → EGLF');
  });

  it('truncates long routes to head + ellipsis + tail', () => {
    const r = flightRouteCompact(['A', 'B', 'C', 'D', 'E', 'F']);
    expect(r.isTruncated).toBe(true);
    expect(r.html).toBe('A → B → … → E → F');
    expect(r.fullText).toBe('A → B → C → D → E → F');
  });

  it('escapes HTML in waypoint codes', () => {
    const r = flightRouteCompact(['<a>', 'EGLF']);
    expect(r.html).toBe('&lt;a&gt; → EGLF');
  });

  it('respects custom maxVisible threshold', () => {
    const r = flightRouteCompact(['A', 'B', 'C'], 2);
    expect(r.isTruncated).toBe(true);
  });
});

describe('buildWindyUrl', () => {
  it('GFS path includes gfs in path and query', () => {
    const url = buildWindyUrl(48.5, 2.0, '2026-04-29T12:00:00Z', 'gfs');
    expect(url).toContain('/gfs/meteogram');
    expect(url).toContain('?gfs,2026-04-29-12');
  });

  it('ICON path includes icon segment', () => {
    const url = buildWindyUrl(48.5, 2.0, '2026-04-29T12:00:00Z', 'icon');
    expect(url).toContain('/icon/meteogram');
  });

  it('falls back to bare meteogram for unknown / ECMWF model', () => {
    const url = buildWindyUrl(48.5, 2.0, '2026-04-29T12:00:00Z', 'ecmwf');
    expect(url).not.toContain('/ecmwf/');
    expect(url).toContain('/48.500/2.000/meteogram?');
    // Query must NOT have a model prefix
    expect(url).toMatch(/meteogram\?2026-04-29-12,/);
  });

  it('falls back to bare meteogram when model omitted', () => {
    const url = buildWindyUrl(48.5, 2.0, '2026-04-29T12:00:00Z');
    expect(url).toContain('/48.500/2.000/meteogram?');
  });

  it('always uses UTC time components from the ISO string', () => {
    // Provide a non-Z suffix; util appends 'Z' before parsing.
    const url = buildWindyUrl(48.5, 2.0, '2026-04-29T05:00:00');
    expect(url).toContain('2026-04-29-05');
  });

  it('rounds lat/lon to 3 decimals', () => {
    const url = buildWindyUrl(48.123456, 2.987654, '2026-04-29T12:00:00Z');
    expect(url).toContain('/48.123/2.988/');
  });

  it('respects zoom override', () => {
    const url = buildWindyUrl(48.5, 2.0, '2026-04-29T12:00:00Z', 'gfs', 6);
    expect(url).toContain(',6,i:pressure');
  });
});

describe('errorToMessage', () => {
  it('strips API <code>: prefix from messages', () => {
    expect(errorToMessage(new Error('API 404: not found'))).toBe('not found');
    expect(errorToMessage(new Error('API 422: invalid'))).toBe('invalid');
  });

  it('passes through messages without prefix', () => {
    expect(errorToMessage(new Error('boom'))).toBe('boom');
  });

  it('coerces non-Error values to string', () => {
    expect(errorToMessage('string err')).toBe('string err');
    expect(errorToMessage(null)).toBe('null');
  });
});

describe('flightShareUrl', () => {
  // vitest runs in `node` env, so `window` doesn't exist by default.
  // Stub just enough for ``${window.location.origin}`` to evaluate —
  // all assertions key off the path/query rather than the origin.
  const ORIGIN = 'http://localhost:8000';
  let originalWindow: unknown;
  beforeAll(() => {
    originalWindow = (globalThis as { window?: unknown }).window;
    (globalThis as { window: unknown }).window = { location: { origin: ORIGIN } };
  });
  afterAll(() => {
    (globalThis as { window?: unknown }).window = originalWindow;
  });

  const longId = 'lfpn-lfcs-lfrs-lfrh-lfrc-lfrg-2026-04-30-a3f2';

  it('uses /s/{code} when shareCode is provided', () => {
    const url = flightShareUrl(longId, null, 'aB3xy7Q9');
    expect(url).toMatch(/\/s\/aB3xy7Q9$/);
    // Short form should not include the verbose flight_id.
    expect(url).not.toContain(longId);
  });

  it('appends pack as query when shareCode is provided', () => {
    const url = flightShareUrl(longId, '2026-04-30T08:00:00Z', 'aB3xy7Q9');
    expect(url).toMatch(/\/s\/aB3xy7Q9\?pack=2026-04-30T08%3A00%3A00Z$/);
  });

  it('falls back to ?id= when no shareCode is given', () => {
    const url = flightShareUrl(longId);
    expect(url).toContain(`/flight.html?id=${encodeURIComponent(longId)}`);
  });

  it('falls back to /briefing.html?flight=...&pack=... when no shareCode', () => {
    const url = flightShareUrl(longId, '2026-04-30T08:00:00Z');
    expect(url).toContain('/briefing.html?');
    expect(url).toContain(`flight=${encodeURIComponent(longId)}`);
    expect(url).toContain('pack=2026-04-30T08');
  });

  it('treats empty shareCode as missing (falls back to long URL)', () => {
    // Defends against accidental `share_code=""` from an old cached
    // FlightResponse leaking through.
    const url = flightShareUrl(longId, null, '');
    expect(url).toContain('/flight.html?id=');
    expect(url).not.toContain('/s/');
  });
});

describe('maxBookingLeadDays', () => {
  it('returns the fallback (180) before config loads', () => {
    expect(maxBookingLeadDays()).toBe(180);
    expect(maxBookingLeadDays(90)).toBe(90);
  });

  it('returns the backend-served cap once config is initialised', () => {
    initBookingConfig({ max_booking_lead_days: 200, forecast_horizon_days: 9 });
    expect(maxBookingLeadDays()).toBe(200);
    // Restore the default so test ordering can't leak the override.
    initBookingConfig({ max_booking_lead_days: 180, forecast_horizon_days: 9 });
  });
});
