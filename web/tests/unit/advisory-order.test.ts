import { describe, it, expect } from 'vitest';
import { advisoryBand, isCompactBand, compareAdvisories, ADVISORY_PRIORITY } from '../../ts/helpers/advisory-order';
import type { RouteAdvisoryResult, AdvisoryStatus } from '../../ts/types/advisories';

/** Minimal advisory factory: aggregate status + the per-model statuses. */
function adv(
  id: string,
  aggregate: AdvisoryStatus,
  models: AdvisoryStatus[],
): RouteAdvisoryResult {
  return {
    advisory_id: id,
    aggregate_status: aggregate,
    aggregate_detail: '',
    per_model: models.map(status => ({ model: status + '-m', status, detail: '' })),
    parameters_used: {},
  } as RouteAdvisoryResult;
}

describe('advisoryBand', () => {
  it('classifies red / amber / unavailable by aggregate status', () => {
    expect(advisoryBand(adv('a', 'red', ['red', 'green']))).toBe('red');
    expect(advisoryBand(adv('a', 'amber', ['amber', 'green']))).toBe('amber');
    expect(advisoryBand(adv('a', 'unavailable', ['unavailable']))).toBe('unavailable');
  });

  it('splits green into clear (all models green) vs mixed (a model dissents)', () => {
    expect(advisoryBand(adv('a', 'green', ['green', 'green', 'green']))).toBe('green-clear');
    expect(advisoryBand(adv('a', 'green', ['green', 'amber', 'green']))).toBe('green-mixed');
    expect(advisoryBand(adv('a', 'green', ['red', 'green']))).toBe('green-mixed');
  });
});

describe('isCompactBand', () => {
  it('compacts unanimous green and unavailable; full cards for the rest', () => {
    expect(isCompactBand('green-clear')).toBe(true);
    expect(isCompactBand('unavailable')).toBe(true);
    expect(isCompactBand('red')).toBe(false);
    expect(isCompactBand('amber')).toBe(false);
    expect(isCompactBand('green-mixed')).toBe(false);
  });
});

describe('compareAdvisories', () => {
  it('orders red → amber → green-mixed → green-clear → unavailable', () => {
    const list = [
      adv('clear', 'green', ['green', 'green']),
      adv('unavail', 'unavailable', ['unavailable']),
      adv('red', 'red', ['red']),
      adv('mixed', 'green', ['green', 'amber']),
      adv('amber', 'amber', ['amber']),
    ];
    const order = [...list].sort(compareAdvisories).map(a => a.advisory_id);
    expect(order).toEqual(['red', 'amber', 'mixed', 'clear', 'unavail']);
  });

  it('orders within a band by operational priority, regardless of input order', () => {
    // Scrambled, all the same band (unanimous green) -> reordered by priority.
    const list = [
      adv('convective', 'green', ['green']),
      adv('llws', 'green', ['green']),
      adv('ifr_feasibility', 'green', ['green']),
      adv('airport_wind', 'green', ['green']),
      adv('mountain_wind', 'green', ['green']),
    ];
    const order = [...list].sort(compareAdvisories).map(a => a.advisory_id);
    expect(order).toEqual([
      'ifr_feasibility', 'airport_wind', 'convective', 'mountain_wind', 'llws',
    ]);
  });

  it('keeps the confirmed icing / wind sub-orders', () => {
    const icing = ['freezing_precip', 'fiki_icing', 'icing_escape']
      .map(id => adv(id, 'green', ['green']));
    expect([...icing].sort(compareAdvisories).map(a => a.advisory_id))
      .toEqual(['fiki_icing', 'icing_escape', 'freezing_precip']);

    const wind = ['llws', 'turbulence', 'mountain_wind']
      .map(id => adv(id, 'green', ['green']));
    expect([...wind].sort(compareAdvisories).map(a => a.advisory_id))
      .toEqual(['turbulence', 'mountain_wind', 'llws']);
  });

  it('ADVISORY_PRIORITY has no duplicates', () => {
    expect(new Set(ADVISORY_PRIORITY).size).toBe(ADVISORY_PRIORITY.length);
  });

  it('sorts the full priority list back into its declared order', () => {
    // Reverse the list (all same band) and confirm the comparator restores it —
    // exercises the real list end-to-end, not synthetic IDs.
    const reversed = [...ADVISORY_PRIORITY].reverse().map(id => adv(id, 'green', ['green']));
    const order = reversed.sort(compareAdvisories).map(a => a.advisory_id);
    expect(order).toEqual(ADVISORY_PRIORITY);
  });

  it('sorts unlisted advisories last within their band, stably', () => {
    const list = [
      adv('zzz_unlisted_a', 'green', ['green']),
      adv('airport_wind', 'green', ['green']),
      adv('zzz_unlisted_b', 'green', ['green']),
    ];
    const order = [...list].sort(compareAdvisories).map(a => a.advisory_id);
    // Listed advisory first; unlisted keep their input (registry) order after.
    expect(order).toEqual(['airport_wind', 'zzz_unlisted_a', 'zzz_unlisted_b']);
  });
});
