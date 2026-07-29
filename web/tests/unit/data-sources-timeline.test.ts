/** Tests for the data-sources timeline's schedule maths.
 *
 * The rendering is DOM work verified by eye; what needs pinning down here is
 * the part that is easy to get subtly wrong and impossible to spot on screen:
 * which runs land inside the window, which cycle counts as a short run, which
 * run gets the realised marker, and whether local-time conversion actually
 * follows daylight saving instead of a frozen offset.
 */

import { describe, it, expect } from 'vitest';
import {
  runsInWindow,
  localHour,
  zoneOffsetHours,
} from '../../ts/data-sources-timeline';
import type { DataSourceEntry } from '../../ts/adapters/data-sources-adapter';

const HOUR = 3_600_000;

/** A minimal entry — only the fields the schedule maths reads. */
function entry(over: Partial<DataSourceEntry> = {}): DataSourceEntry {
  return {
    key: 'ecmwf:direct',
    model: 'ecmwf',
    model_label: 'ECMWF IFS',
    provider_label: 'ECMWF',
    provider_url: '',
    role: 'primary-sounding',
    resolution: '0.25°',
    coverage: 'Global',
    pressure_levels: 20,
    description: '',
    cycles: [0, 6, 12, 18],
    horizon_hours: { '0': 168, '6': 144, '12': 168, '18': 144 },
    delivery_offset_hours: { '0': 6.667, '6': 6.667, '12': 6.667, '18': 6.667 },
    latest_init: null,
    published_at: null,
    next_expected: null,
    horizon_end: null,
    marker_health: 'ok',
    ...over,
  };
}

describe('runsInWindow', () => {
  const t0 = Date.UTC(2026, 6, 29, 0); // 29 Jul 2026 00:00Z
  const t1 = t0 + 36 * HOUR;

  it('emits one run per cycle whose delivery falls in the window', () => {
    const runs = runsInWindow(entry(), t0, t1);
    const inits = runs.map((r) => new Date(r.init).toISOString());
    // The 28th 18Z run delivers at 00:40Z on the 29th, inside the window, so
    // it must appear even though its init precedes t0.
    expect(inits).toContain('2026-07-28T18:00:00.000Z');
    expect(inits).toContain('2026-07-29T00:00:00.000Z');
    expect(inits).toContain('2026-07-29T12:00:00.000Z');
    // The window closes at 30 Jul 12:00Z. That run sits exactly on the edge
    // and is kept; the next one is wholly outside and must not appear.
    expect(inits).toContain('2026-07-30T12:00:00.000Z');
    expect(inits).not.toContain('2026-07-30T18:00:00.000Z');
  });

  it('computes expected delivery as init + the cycle offset', () => {
    const runs = runsInWindow(entry(), t0, t1);
    const run = runs.find((r) => r.init === Date.UTC(2026, 6, 29, 0));
    expect(run).toBeDefined();
    expect(run!.expected - run!.init).toBeCloseTo(6.667 * HOUR, -2);
  });

  it('marks cycles below the source max horizon as short runs', () => {
    const runs = runsInWindow(entry(), t0, t1);
    const byCycle = new Map(runs.map((r) => [r.cycle, r.short]));
    expect(byCycle.get(0)).toBe(false);
    expect(byCycle.get(12)).toBe(false);
    expect(byCycle.get(6)).toBe(true);
    expect(byCycle.get(18)).toBe(true);
  });

  it('treats a uniform horizon as all-full, never all-short', () => {
    const runs = runsInWindow(
      entry({ horizon_hours: { '0': 384, '6': 384, '12': 384, '18': 384 } }),
      t0, t1,
    );
    expect(runs.length).toBeGreaterThan(0);
    expect(runs.every((r) => !r.short)).toBe(true);
  });

  it('attaches the realised time to the latest run only', () => {
    const runs = runsInWindow(
      entry({
        latest_init: '2026-07-29T00:00:00+00:00',
        published_at: '2026-07-29T06:36:12+00:00',
      }),
      t0, t1,
    );
    const withActual = runs.filter((r) => r.actual !== null);
    expect(withActual).toHaveLength(1);
    expect(withActual[0].init).toBe(Date.UTC(2026, 6, 29, 0));
    expect(withActual[0].actual).toBe(Date.parse('2026-07-29T06:36:12+00:00'));
  });

  it('leaves every run unrealised when the marker has no publish time', () => {
    // GEM sits in this state in production: an init is known, the publish
    // wallclock is not. A missing time must not silently become the epoch.
    const runs = runsInWindow(
      entry({ latest_init: '2026-07-29T00:00:00+00:00', published_at: null }),
      t0, t1,
    );
    expect(runs.every((r) => r.actual === null)).toBe(true);
  });

  it('returns nothing for a source with no configured cycles', () => {
    expect(runsInWindow(entry({ cycles: [] }), t0, t1)).toEqual([]);
  });

  it('handles an 8-cycle source without dropping the intermediate runs', () => {
    const iconEu = entry({
      key: 'icon_eu:dwd',
      cycles: [0, 3, 6, 9, 12, 15, 18, 21],
      horizon_hours: {
        '0': 120, '3': 78, '6': 120, '9': 78,
        '12': 120, '15': 78, '18': 120, '21': 78,
      },
      delivery_offset_hours: {
        '0': 3, '3': 3, '6': 3, '9': 3, '12': 3, '15': 3, '18': 3, '21': 3,
      },
    });
    const runs = runsInWindow(iconEu, t0, t1);
    // 36h at 3-hourly cadence — 12 runs land inside, plus the one initiated
    // just before t0 that still delivers inside it.
    expect(runs.length).toBeGreaterThanOrEqual(12);
    expect(runs.filter((r) => r.short).length).toBeGreaterThan(0);
    expect(runs.filter((r) => !r.short).length).toBeGreaterThan(0);
  });
});

describe('local time conversion', () => {
  it('reads the hour in the target zone, not the host zone', () => {
    const ms = Date.UTC(2026, 6, 29, 12); // 12:00Z, July
    expect(localHour(ms, 'UTC')).toBe(12);
    expect(localHour(ms, 'Europe/Paris')).toBe(14); // CEST
    expect(localHour(ms, 'America/New_York')).toBe(8); // EDT
    expect(localHour(ms, 'America/Los_Angeles')).toBe(5); // PDT
  });

  it('wraps midnight to 0 rather than 24', () => {
    expect(localHour(Date.UTC(2026, 6, 29, 0), 'UTC')).toBe(0);
    // 22:00Z in July is midnight in Paris.
    expect(localHour(Date.UTC(2026, 6, 29, 22), 'Europe/Paris')).toBe(0);
  });

  it('follows the European DST transition', () => {
    // Europe switches on the last Sunday of March: 2026-03-29 01:00Z.
    const before = Date.UTC(2026, 2, 29, 0);
    const after = Date.UTC(2026, 2, 29, 12);
    expect(zoneOffsetHours(before, 'Europe/Paris')).toBe(1); // CET
    expect(zoneOffsetHours(after, 'Europe/Paris')).toBe(2); // CEST
  });

  it('follows the US DST transition, which is a different date', () => {
    // The US switches on the second Sunday of March — three weeks earlier
    // than Europe, which is exactly the window where the usual Europe/US
    // offset is one hour off its normal value.
    const before = Date.UTC(2026, 2, 8, 0);
    const after = Date.UTC(2026, 2, 8, 12);
    expect(zoneOffsetHours(before, 'America/New_York')).toBe(-5); // EST
    expect(zoneOffsetHours(after, 'America/New_York')).toBe(-4); // EDT
    // Same instant, Europe has not switched yet.
    expect(zoneOffsetHours(after, 'Europe/Paris')).toBe(1);
  });

  it('reports whole-hour and fractional offsets', () => {
    const ms = Date.UTC(2026, 6, 29, 12);
    expect(zoneOffsetHours(ms, 'UTC')).toBe(0);
    expect(zoneOffsetHours(ms, 'Asia/Kolkata')).toBeCloseTo(5.5, 5);
  });
});
