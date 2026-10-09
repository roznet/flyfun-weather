/** The trip title rule (#728): the pilot's name first, the chain beneath it,
 *  never the chain twice. Mirrors `TripTests.swift`'s header tests. */

import { describe, it, expect } from 'vitest';
import { tripHeading } from '../../ts/helpers/trip-heading';

const trip = (name: string, chain = 'EGTF → LSGS → EGTF') => ({
  name,
  summary: { chain_label: chain },
});

describe('tripHeading', () => {
  it('titles a named trip with its name and the chain beneath', () => {
    expect(tripHeading(trip('Alpine tour'))).toEqual({
      title: 'Alpine tour',
      subtitle: 'EGTF → LSGS → EGTF',
    });
  });

  it('titles an unnamed trip with the chain once', () => {
    expect(tripHeading(trip(''))).toEqual({ title: 'EGTF → LSGS → EGTF', subtitle: null });
  });

  it('treats a whitespace name as unnamed', () => {
    expect(tripHeading(trip('  ')).subtitle).toBeNull();
  });

  it('does not repeat a name that is the chain', () => {
    expect(tripHeading(trip('EGTF → LSGS → EGTF')).subtitle).toBeNull();
  });

  it('never titles an unnamed trip with no chain blank', () => {
    expect(tripHeading({ ...trip('', ''), display_name: 'New trip' }).title).toBe('New trip');
    expect(tripHeading(trip('', '')).title).toBe('Trip');
  });

  it('drops the subtitle when there is no chain', () => {
    expect(tripHeading(trip('Alpine tour', ''))).toEqual({ title: 'Alpine tour', subtitle: null });
  });
});
