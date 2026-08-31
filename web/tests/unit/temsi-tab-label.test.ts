import { describe, expect, it } from 'vitest';

import { temsiTabLabel } from '../../ts/utils/temsi';

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
