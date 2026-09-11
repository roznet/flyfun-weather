/** Per-leg refresh on the trip page.
 *
 * What this pins: a remaining leg can be refreshed on its own (the point — the
 * next leg is re-briefed repeatedly on the day without paying for the chain),
 * but never while a trip run owns the chain; the page re-reads the trip
 * whenever any leg's refresh settles, however fast it was; and a finished trip
 * run's readout stops being shown once a leg has been refreshed after it.
 */

import { describe, it, expect } from 'vitest';
import {
  legRefreshButton,
  settledLegIds,
  tripRunMessage,
} from '../../ts/helpers/trip-leg-refresh';

describe('legRefreshButton', () => {
  it('offers a remaining idle leg', () => {
    expect(legRefreshButton({ state: 'remaining' }, false, undefined))
      .toEqual({ visible: true, enabled: true });
  });

  it('is disabled, not hidden, while a trip run is in flight', () => {
    // The server would refuse it with a 409 (leg_is_claimed) anyway.
    expect(legRefreshButton({ state: 'remaining' }, true, undefined))
      .toEqual({ visible: true, enabled: false });
  });

  it('is disabled while this leg is already queued or refreshing', () => {
    for (const status of ['queued', 'refreshing']) {
      expect(legRefreshButton({ state: 'remaining' }, false, { status }))
        .toEqual({ visible: true, enabled: false });
    }
  });

  it('is not offered on a flown or cancelled leg', () => {
    for (const state of ['flown', 'cancelled']) {
      expect(legRefreshButton({ state }, false, undefined).visible).toBe(false);
    }
  });
});

describe('settledLegIds', () => {
  const entry = { status: 'refreshing' };

  it('reports a leg that dropped out of the active set', () => {
    expect(settledLegIds({ a: entry, b: entry }, { b: entry })).toEqual(['a']);
  });

  it('reports a leg that went queued -> gone between two ticks', () => {
    expect(settledLegIds({ a: { status: 'queued' } }, {})).toEqual(['a']);
  });

  it('reports nothing while a leg only changes status or a new one starts', () => {
    expect(settledLegIds({ a: { status: 'queued' } }, { a: entry, b: entry })).toEqual([]);
  });
});

describe('tripRunMessage', () => {
  const message = '1 of 2 legs had new data; 1 already current.';
  // The run's own packs predate its finish: it briefs a leg, then closes out.
  const runLegs = [
    { fetch_timestamp: '2026-09-11T06:20:55.287015Z' },
    { fetch_timestamp: '2026-09-10T22:22:15.191874Z' },
  ];
  const finished = { active: false, message, finished_at: '2026-09-11T06:21:30.100000+00:00' };

  it('shows progress while a run is live', () => {
    expect(tripRunMessage({ active: true, message: 'Refreshing leg 2 of 2…' }, runLegs))
      .toBe('Refreshing leg 2 of 2…');
  });

  it('keeps the final line after a run while the legs are still its packs', () => {
    expect(tripRunMessage(finished, runLegs)).toBe(message);
  });

  it('drops it once a leg has been refreshed after the run finished', () => {
    // The Le Mans case: leg 2 re-briefed on its own at 15:38, and the morning
    // run's "1 already current" was still being shown about it.
    const legs = [runLegs[0], { fetch_timestamp: '2026-09-11T15:38:02.944533Z' }];
    expect(tripRunMessage(finished, legs)).toBe('');
  });

  it('says nothing when the finish time is unknown', () => {
    expect(tripRunMessage({ active: false, message }, runLegs)).toBe('');
  });

  it('says nothing without a run or a message', () => {
    expect(tripRunMessage(null, runLegs)).toBe('');
    expect(tripRunMessage({ active: false, message: '' }, runLegs)).toBe('');
  });
});
