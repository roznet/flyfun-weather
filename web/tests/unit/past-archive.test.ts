/** Past finder chips → archive query (#1032). The server filters and counts;
 *  this pins the mapping and the chip list every client must mirror. */

import { describe, it, expect } from 'vitest';
import {
  archiveParams, EMPTY_PAST_FILTER, HAZARD_CHIPS, OUTCOME_CHIPS, pastFilterActive, toggleValue,
} from '../../ts/helpers/past-archive';

describe('archiveParams', () => {
  it('sends only the limit for an empty filter', () => {
    expect(archiveParams(EMPTY_PAST_FILTER, { limit: 20, offset: 0 }).toString()).toBe('limit=20');
  });

  it('maps search, chips, source, outcome and hide', () => {
    const p = archiveParams(
      { q: ' LFAT ', tags: ['ICE', 'TS'], source: 'reported', outcomes: ['flown'], hideInactive: true },
      { limit: 20, offset: 40 },
    );
    expect(Object.fromEntries(p)).toEqual({
      q: 'LFAT', tags: 'ICE,TS', source: 'reported', outcome: 'flown',
      hide: 'cancelled,monitoring', limit: '20', offset: '40',
    });
  });
});

describe('chip helpers', () => {
  it('toggles a value in place', () => {
    expect(toggleValue(['ICE'], 'TS')).toEqual(['ICE', 'TS']);
    expect(toggleValue(['ICE', 'TS'], 'ICE')).toEqual(['TS']);
  });

  it('knows when the past list is narrowed', () => {
    expect(pastFilterActive(EMPTY_PAST_FILTER)).toBe(false);
    expect(pastFilterActive({ ...EMPTY_PAST_FILTER, hideInactive: true })).toBe(true);
    expect(pastFilterActive({ ...EMPTY_PAST_FILTER, q: '  ' })).toBe(false);
  });

  it('keeps the chip order shared with iOS and Android', () => {
    // Same order on all three clients (#1032): change one, change them all.
    expect(HAZARD_CHIPS.map(c => c.tag)).toEqual(['ICE', 'TS', 'IMC', 'WIND', 'TURB', 'FRZ', 'VIS']);
    expect(OUTCOME_CHIPS.map(c => c.key)).toEqual(['flown', 'cancelled', 'monitoring', 'debriefed', 'differs']);
  });
});
