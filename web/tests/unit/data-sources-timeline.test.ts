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
  planRows,
  alignUp,
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

describe('planRows', () => {
  /** The real catalog order: a model's variants are NOT contiguous. */
  function catalogOrder(): DataSourceEntry[] {
    const spec: Array<[string, string, string]> = [
      ['ecmwf:direct', 'ecmwf', 'primary-sounding'],
      ['gfs:noaa', 'gfs', 'cloud-enrichment'],
      ['icon_eu:dwd', 'icon_eu', 'primary-sounding'],
      ['icon_d2:dwd', 'icon_d2', 'primary-sounding'],
      ['gfs:openmeteo', 'gfs', 'primary'],
      ['ecmwf:openmeteo', 'ecmwf', 'surface-base'],
      ['icon:openmeteo', 'icon', 'primary'],
      ['meteofrance:openmeteo', 'meteofrance', 'primary'],
      ['ukmo:openmeteo', 'ukmo', 'primary'],
      ['gem:openmeteo', 'gem', 'primary'],
    ];
    return spec.map(([key, model, role]) => entry({ key, model, role }));
  }

  it('puts every variant of a model together', () => {
    const rows = planRows(catalogOrder());
    const models = rows.map((r) => r.entry.model);
    // Each model must occupy one contiguous span — no model may reappear
    // after a different model has intervened.
    const firstSeen = new Map<string, number>();
    models.forEach((m, i) => { if (!firstSeen.has(m)) firstSeen.set(m, i); });
    for (const [m, start] of firstSeen) {
      const count = models.filter((x) => x === m).length;
      expect(models.slice(start, start + count).every((x) => x === m)).toBe(true);
    }
  });

  it('marks exactly one first-of-model row per model', () => {
    const rows = planRows(catalogOrder());
    const firsts = rows.filter((r) => r.isFirstOfModel).map((r) => r.entry.model);
    expect(firsts).toHaveLength(new Set(rows.map((r) => r.entry.model)).size);
    expect(new Set(firsts).size).toBe(firsts.length);
  });

  it('flags the group boundary on the first variant, not a later one', () => {
    // The shipped bug: gfs:openmeteo sat at index 4 under ICON-D2 with a thin
    // border, so the heavy rule opened the wrong family.
    const rows = planRows(catalogOrder());
    const gfs = rows.filter((r) => r.entry.model === 'gfs');
    expect(gfs).toHaveLength(2);
    expect(gfs[0].isFirstOfModel).toBe(true);
    expect(gfs[1].isFirstOfModel).toBe(false);
    // ...and they are adjacent in the final order.
    const idx = rows.findIndex((r) => r.entry.key === gfs[0].entry.key);
    expect(rows[idx + 1].entry.model).toBe('gfs');
  });

  it('keeps every source — grouping must not drop rows', () => {
    const sources = catalogOrder();
    const rows = planRows(sources);
    expect(rows).toHaveLength(sources.length);
    expect(new Set(rows.map((r) => r.entry.key)))
      .toEqual(new Set(sources.map((s) => s.key)));
  });

  it('orders a model\'s variants by role, primary sounding first', () => {
    const rows = planRows(catalogOrder());
    const ecmwf = rows.filter((r) => r.entry.model === 'ecmwf');
    expect(ecmwf.map((r) => r.entry.role)).toEqual([
      'primary-sounding', 'surface-base',
    ]);
  });
});

describe('alignUp', () => {
  const H = 3_600_000;

  it('lands the 3-hourly grid on absolute boundaries whatever hour it is', () => {
    // The shipped bug: the grid stepped 3h from the window start, which is the
    // current hour — so it only coincided with the ruler's labelled hours when
    // that hour was already a multiple of 3, and the 00/06/12/18Z emphasis
    // disappeared for the other two loads in three.
    for (const hour of [12, 13, 14, 23]) {
      const t0 = Date.UTC(2026, 6, 29, hour) - 24 * H;
      const start = alignUp(t0, 3 * H);
      expect(new Date(start).getUTCHours() % 3).toBe(0);
      expect(start).toBeGreaterThanOrEqual(t0);
      expect(start - t0).toBeLessThan(3 * H);
      // A synoptic line must appear within the first four grid steps.
      const hours = [0, 1, 2, 3].map((k) => new Date(start + k * 3 * H).getUTCHours());
      expect(hours.some((h) => h % 6 === 0)).toBe(true);
    }
  });

  it('is a no-op on a value already aligned', () => {
    const aligned = Date.UTC(2026, 6, 29, 12);
    expect(alignUp(aligned, 3 * H)).toBe(aligned);
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
