/**
 * What a trip card's header reports as busy (#602).
 *
 * The regression this pins: a leg refreshed outside a trip run showed nothing
 * on the card. The member card carried the badge, but a collapsed card — the
 * default — hides its legs, so the trip looked idle while one of its legs was
 * being refreshed from the briefing page, Siri, the scheduler or MCP.
 */

import { describe, expect, it } from 'vitest';
import { tripCardRefresh } from '../../ts/helpers/trip-refresh-indicator';

const IDLE = { refresh: null };
const RUNNING = { refresh: { active: true, message: 'Refreshing leg 2 of 3' } };

describe('tripCardRefresh', () => {
  it('reports nothing when neither the trip nor any leg is busy', () => {
    expect(tripCardRefresh(IDLE, ['a', 'b'], {})).toBeNull();
  });

  it('reports a member leg refreshed outside a trip run', () => {
    // The regression. Without this the collapsed card renders as idle.
    expect(tripCardRefresh(IDLE, ['a', 'b'], { b: { status: 'refreshing' } }))
      .toEqual({ kind: 'leg', status: 'refreshing' });
  });

  it('reports a queued leg too', () => {
    expect(tripCardRefresh(IDLE, ['a'], { a: { status: 'queued' } }))
      .toEqual({ kind: 'leg', status: 'queued' });
  });

  it('prefers a refreshing leg over a merely queued one', () => {
    const indicator = tripCardRefresh(
      IDLE, ['a', 'b'], { a: { status: 'queued' }, b: { status: 'refreshing' } },
    );
    expect(indicator).toEqual({ kind: 'leg', status: 'refreshing' });
  });

  it("ignores a refresh on a flight that is not one of this trip's legs", () => {
    // Guards the other direction: the map is global, so a card must not light
    // up for someone else's flight refreshing at the same time.
    expect(tripCardRefresh(IDLE, ['a'], { zzz: { status: 'refreshing' } })).toBeNull();
  });

  it('prefers the trip run, whose message is richer than a leg status', () => {
    const indicator = tripCardRefresh(
      RUNNING, ['a'], { a: { status: 'refreshing' } },
    );
    expect(indicator).toEqual({ kind: 'trip', message: 'Refreshing leg 2 of 3' });
  });

  it('still reports a trip run with no message', () => {
    expect(tripCardRefresh({ refresh: { active: true } }, ['a'], {}))
      .toEqual({ kind: 'trip', message: null });
  });

  it('treats an inactive trip-refresh block as no run at all', () => {
    expect(tripCardRefresh({ refresh: { active: false } }, ['a'], {})).toBeNull();
  });
});
