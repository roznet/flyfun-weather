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
import type {
  AltitudeAdvisoryRow,
  AltitudeTableResult,
  ModelAdvisoryResult,
  RouteAdvisoriesManifest,
  RouteAdvisoryResult,
} from '../../ts/types/advisories';

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

function modelResult(model: string): ModelAdvisoryResult {
  return {
    model,
    status: 'amber',
    detail: `${model} detail`,
    affected_points: 1,
    total_points: 2,
    affected_pct: 50,
    affected_nm: 10,
    total_nm: 20,
    data_state: 'complete',
    primary_method_id: 'nwp',
    evidence_regions: [{
      start_point_index: 0,
      end_point_index: 1,
      severity: 'amber',
      reason_code: 'test_region',
      method_id: 'nwp',
    }],
  };
}

function advisory(advisoryId: string, status: RouteAdvisoryResult['aggregate_status']): RouteAdvisoryResult {
  return {
    advisory_id: advisoryId,
    aggregate_status: status,
    aggregate_detail: `${advisoryId} detail`,
    representative_model: 'gfs',
    per_model: [modelResult('gfs'), modelResult('ecmwf')],
    parameters_used: {},
  };
}

function manifest(): RouteAdvisoriesManifest {
  return {
    advisories: [
      advisory('icing_escape', 'amber'),
      advisory('airport_wind', 'red'),
    ],
    catalog: [],
    route_name: 'EGTF EGLF',
    cruise_altitude_ft: 8000,
    flight_ceiling_ft: 10000,
    total_distance_nm: 20,
    models: ['gfs', 'ecmwf'],
    airport_conditions: null,
  };
}

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

  it('reports "same advisory picture" when altitudes differ but statuses do not', () => {
    const sameTable: AltitudeTableResult = {
      ...table,
      rows: [
        row(8000, { icing_escape: 'amber', headwind: 'green' }),
        row(6000, { icing_escape: 'amber', headwind: 'green' }),
      ],
    };
    const note = formatAltitudeDeltaNote(sameTable, 6000, 8000);
    expect(note).toBe('At 6000ft vs planned 8000ft: same advisory picture.');
  });
});

describe('overlayAltitudeStatuses', () => {
  it('updates only altitude-dependent statuses, leaving others untouched', () => {
    const input = manifest();
    const out = overlayAltitudeStatuses(input, table, 4000);
    expect(out.advisories.find(a => a.advisory_id === 'icing_escape')!.aggregate_status).toBe('green');
    // airport_wind isn't in the table → preserved.
    expect(out.advisories.find(a => a.advisory_id === 'airport_wind')!.aggregate_status).toBe('red');
  });

  it('clears model attribution and evidence even when the row status is unchanged', () => {
    const input = manifest();
    const out = overlayAltitudeStatuses(input, table, 8000);
    const icing = out.advisories.find(a => a.advisory_id === 'icing_escape')!;

    expect(icing.aggregate_status).toBe('amber');
    expect(icing.representative_model).toBeNull();
    expect(icing.per_model).toEqual(input.advisories[0].per_model.map(model => ({
      ...model,
      data_state: null,
      primary_method_id: null,
      evidence_regions: [],
    })));
  });

  it('preserves the complete advisory object when it is absent from the altitude row', () => {
    const input = manifest();
    const airportWind = input.advisories.find(a => a.advisory_id === 'airport_wind')!;
    const out = overlayAltitudeStatuses(input, table, 4000);

    expect(out.advisories.find(a => a.advisory_id === 'airport_wind')).toBe(airportWind);
  });
});
