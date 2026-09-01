import { describe, expect, it } from 'vitest';

import { temsiTabLabel, temsiValidityOffset } from '../../ts/utils/temsi';

describe('temsiTabLabel', () => {
  it('shows zone and validity hour', () => {
    expect(temsiTabLabel('france', '2026-08-31T15Z', false)).toBe('France 15Z');
    expect(temsiTabLabel('euroc', '2026-08-31T18Z', false)).toBe('Europe 18Z');
  });

  it('adds the day when validities straddle midnight UTC', () => {
    // An 02Z departure is offered 31/23Z and 01/02Z; a bare "23Z" next to
    // "02Z" would read as the same night in the wrong order.
    expect(temsiTabLabel('france', '2026-08-31T23Z', true)).toBe('France 31/23Z');
    expect(temsiTabLabel('france', '2026-09-01T02Z', true)).toBe('France 01/02Z');
  });

  it('falls back to the raw cycle rather than rendering a wrong hour', () => {
    expect(temsiTabLabel('france', 'garbage', false)).toBe('France garbage');
  });

  it('passes an unknown zone through instead of blanking the label', () => {
    expect(temsiTabLabel('antilles', '2026-08-31T15Z', false)).toBe('antilles 15Z');
  });
});

describe('temsiValidityOffset', () => {
  const etd = Date.parse('2026-09-01T18:30:00Z');

  it('stays silent when the chart is essentially at departure', () => {
    expect(temsiValidityOffset(Date.parse('2026-09-01T18:00:00Z'), etd)).toBeNull();
    expect(temsiValidityOffset(Date.parse('2026-09-01T18:59:00Z'), etd)).toBeNull();
  });

  it('names the gap in both directions', () => {
    expect(temsiValidityOffset(Date.parse('2026-09-01T15:00:00Z'), etd))
      .toBe('3.5 h before departure');
    expect(temsiValidityOffset(Date.parse('2026-09-01T21:00:00Z'), etd))
      .toBe('2.5 h after departure');
  });

  it('keeps half hours rather than rounding a 2h30 gap down to 2 h', () => {
    // Validities land on whole hours and departures often do not, so integer
    // rounding would understate nearly every gap the picker shows.
    expect(temsiValidityOffset(Date.parse('2026-09-01T16:00:00Z'), etd))
      .toBe('2.5 h before departure');
  });

  it('drops the decimal when the gap is a whole number of hours', () => {
    const onTheHour = Date.parse('2026-09-01T18:00:00Z');
    expect(temsiValidityOffset(Date.parse('2026-09-01T15:00:00Z'), onTheHour))
      .toBe('3 h before departure');
  });
});
