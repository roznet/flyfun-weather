/** Tests for the synoptic-charts adapter's pure helpers (time matching +
 * projection-key mapping). */

import { describe, it, expect } from 'vitest';
import {
  pickChartForValidTime, projectionKeyFor, synopticChartUrl,
  type SynopticChartSource,
} from '../../ts/adapters/synoptic-charts-adapter';

function entry(id: string, offset_h: number, chart_type: string, valid_time: string | null) {
  return { id, offset_h, chart_type, native_size: null, valid_time };
}

const RUN = '2026-05-19T06Z'; // 06:00Z
const source: SynopticChartSource = {
  slug: 'metoffice',
  label: 'Met Office',
  run_cycle: RUN,
  issued_at: null,
  attribution_html: '',
  charts: [
    entry('ana', 0, 'colour', '2026-05-19T06:00:00Z'),
    entry('024', 24, 'colour', '2026-05-20T06:00:00Z'),
    entry('048', 48, 'colour', '2026-05-21T06:00:00Z'),
  ],
};

describe('pickChartForValidTime', () => {
  it('picks the closest chart by valid time', () => {
    const target = new Date('2026-05-20T05:00:00Z').getTime(); // ~ +23h
    const pick = pickChartForValidTime(source, target)!;
    expect(pick.chart.id).toBe('024');
    expect(pick.gapHours).toBeCloseTo(1, 5);
  });

  it('matches the analysis chart near issuance', () => {
    const target = new Date('2026-05-19T06:30:00Z').getTime();
    const pick = pickChartForValidTime(source, target)!;
    expect(pick.chart.id).toBe('ana');
  });

  it('clamps to the farthest chart beyond the horizon', () => {
    const target = new Date('2026-05-30T00:00:00Z').getTime();
    const pick = pickChartForValidTime(source, target)!;
    expect(pick.chart.id).toBe('048');
    expect(pick.gapHours).toBeGreaterThan(100);
  });

  it('returns null for an empty source', () => {
    expect(pickChartForValidTime({ ...source, charts: [] }, Date.now())).toBeNull();
  });

  it('tie-breaks toward the earlier offset', () => {
    const twoSided: SynopticChartSource = {
      ...source,
      charts: [
        entry('012', 12, 'colour', '2026-05-19T18:00:00Z'),
        entry('036', 36, 'colour', '2026-05-21T06:00:00Z'),
      ],
    };
    // Exactly midway between the two valid times -> earlier offset wins.
    const mid = (new Date('2026-05-19T18:00:00Z').getTime()
      + new Date('2026-05-21T06:00:00Z').getTime()) / 2;
    expect(pickChartForValidTime(twoSided, mid)!.chart.id).toBe('012');
  });
});

describe('projectionKeyFor / synopticChartUrl', () => {
  it('maps source + chart_type to the generated constant key', () => {
    expect(projectionKeyFor('dwd', 'analysis')).toBe('dwd-analysis');
    expect(projectionKeyFor('dwd', 'icon')).toBe('dwd-icon');
    expect(projectionKeyFor('metoffice', 'colour')).toBe('metoffice-colour');
  });

  it('builds an encoded bytes URL', () => {
    expect(synopticChartUrl('dwd', RUN, 'ana')).toBe(
      '/api/synoptic-charts/dwd/2026-05-19T06Z/ana',
    );
  });
});
