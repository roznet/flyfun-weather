/** Tests for the client-side altitude-diff primitive (#259).
 *
 * Mirrors the backend `diff_altitude_rows`: improved = severity drops,
 * worsened = severity rises, UNAVAILABLE ignored. Also covers the delta-note
 * formatter and the status overlay used to make the lever instant.
 */

import { describe, it, expect } from 'vitest';
import {
  diffAltitudeRows,
  rowForAltitude,
  nearestRow,
  formatAltitudeDeltaNote,
  overlayAltitudeStatuses,
} from '../../ts/helpers/altitude-diff';
import type { AltitudeAdvisoryRow, AltitudeTableResult } from '../../ts/types/advisories';

function row(altitude_ft: number, statuses: AltitudeAdvisoryRow['statuses']): AltitudeAdvisoryRow {
  return { altitude_ft, statuses, red_count: 0, amber_count: 0, green_count: 0 };
}

const names = { icing_escape: 'Icing Escape', headwind: 'Headwind', vmc_cruise: 'VMC Cruise' };

const table: AltitudeTableResult = {
  rows: [
    row(8000, { icing_escape: 'amber', headwind: 'green', vmc_cruise: 'green' }),
    row(4000, { icing_escape: 'green', headwind: 'amber', vmc_cruise: 'green' }),
  ],
  advisory_ids: ['icing_escape', 'headwind', 'vmc_cruise'],
  advisory_names: names,
  cruise_altitude_ft: 8000,
  flight_ceiling_ft: 10000,
  step_ft: 2000,
  best_below_cruise: 4000,
  best_above_cruise: null,
};

describe('diffAltitudeRows', () => {
  it('classifies improved / worsened / unchanged by severity', () => {
    const d = diffAltitudeRows(table.rows[0], table.rows[1], names);
    expect(d.improved.map(c => c.advisory_id)).toEqual(['icing_escape']);
    expect(d.improved[0].from).toBe('amber');
    expect(d.improved[0].to).toBe('green');
    expect(d.worsened.map(c => c.advisory_id)).toEqual(['headwind']);
    expect(d.unchanged).toEqual(['vmc_cruise']);
  });

  it('ignores advisories that are UNAVAILABLE on either side', () => {
    const a = row(8000, { icing_escape: 'unavailable', headwind: 'green' });
    const b = row(4000, { icing_escape: 'red', headwind: 'unavailable' });
    const d = diffAltitudeRows(a, b);
    expect(d.improved).toEqual([]);
    expect(d.worsened).toEqual([]);
    expect(d.unchanged).toEqual([]);
  });
});

describe('rowForAltitude / nearestRow', () => {
  it('finds exact rows and null on miss', () => {
    expect(rowForAltitude(table, 4000)?.altitude_ft).toBe(4000);
    expect(rowForAltitude(table, 5000)).toBeNull();
    expect(rowForAltitude(table, null)).toBeNull();
  });

  it('snaps to the nearest row between table steps', () => {
    expect(nearestRow(table, 4900)?.altitude_ft).toBe(4000);
    expect(nearestRow(table, 7000)?.altitude_ft).toBe(8000);
  });
});

describe('formatAltitudeDeltaNote', () => {
  it('returns null at the planned altitude', () => {
    expect(formatAltitudeDeltaNote(table, 8000, 8000)).toBeNull();
  });

  it('names the specific improve/worsen trade-off', () => {
    const note = formatAltitudeDeltaNote(table, 4000, 8000);
    expect(note).toContain('improves Icing Escape (AMBER→GREEN)');
    expect(note).toContain('worsens Headwind (GREEN→AMBER)');
  });
});

describe('overlayAltitudeStatuses', () => {
  it('updates only altitude-dependent statuses, leaving others untouched', () => {
    const manifest = {
      advisories: [
        { advisory_id: 'icing_escape', aggregate_status: 'amber' as const },
        { advisory_id: 'airport_wind', aggregate_status: 'red' as const },
      ],
    };
    const out = overlayAltitudeStatuses(manifest, table, 4000);
    expect(out.advisories.find(a => a.advisory_id === 'icing_escape')!.aggregate_status).toBe('green');
    // airport_wind isn't in the table → preserved.
    expect(out.advisories.find(a => a.advisory_id === 'airport_wind')!.aggregate_status).toBe('red');
  });
});
