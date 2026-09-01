/** Donate nudge — the two rules the client owns (issue #588).
 *
 * The server decides whether an ask exists and whether it may render today.
 * Two things it cannot decide are decided here, and both are load-bearing:
 * the chip never appears beside a RED assessment, and a suppressed view never
 * sends the `shown` ack (otherwise impressions burn on views that painted
 * nothing, and the pilot's four-impression budget drains unseen).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../ts/adapters/donations-adapter', () => ({
  fetchDonateNudge: vi.fn(),
  ackDonateNudge: vi.fn(),
}));
vi.mock('../../ts/analytics/track', () => ({ track: vi.fn() }));
vi.mock('../../ts/i18n/i18n', () => ({ t: (k: string) => k }));

import { ackDonateNudge, fetchDonateNudge } from '../../ts/adapters/donations-adapter';
import { track } from '../../ts/analytics/track';
import { EVENTS } from '../../ts/analytics/events';
import {
  initDonateNudge,
  shouldSuppressNudge,
  _resetDonateNudgeForTests,
} from '../../ts/managers/donate-nudge-ui';

const mockedFetch = vi.mocked(fetchDonateNudge);
const mockedAck = vi.mocked(ackDonateNudge);
const mockedTrack = vi.mocked(track);

/** A fake element with just the surface `render` touches.
 *
 * The node test env has no DOM, and pulling in jsdom for four assertions is not
 * worth it — but the render path has to be reachable, because "the chip painted
 * so the impression is real" is one of the rules under test. */
function fakeEl(): any {
  return {
    innerHTML: '',
    style: {} as Record<string, string>,
    hidden: true,
    contains: () => false,
    setAttribute: () => {},
    addEventListener: () => {},
    querySelector: () => fakeEl(),
  };
}

let slot: any;

/** Stub `document` with a single slot element, and hand it back for assertions. */
function stubSlot(): void {
  slot = fakeEl();
  vi.stubGlobal('document', {
    getElementById: () => slot,
    addEventListener: () => {},
  });
}

/** A server response that would paint a chip. */
function nudgeShowing() {
  return {
    show: true as const, kind: 'evergreen' as const, rung: 1, reason: 'show',
    summary: { briefing_count: 23, first_briefing_at: null,
               pilots_last_year: 0, briefings_last_year: 0 },
  };
}

/** Stub a slot whose children are distinct, so the chip's click handler can be
 *  captured and fired. `fakeEl`'s shared `querySelector` returns a fresh node
 *  every call, which is fine for the render-only tests above but not for one
 *  that has to toggle the popover the chip owns. */
function stubInteractiveSlot(): { clickChip: () => void } {
  const popover = fakeEl();
  const chip = fakeEl();
  let onClick: (() => void) | null = null;
  chip.addEventListener = (ev: string, fn: () => void) => {
    if (ev === 'click') onClick = fn;
  };
  slot = fakeEl();
  slot.querySelector = (sel: string) => {
    if (sel === '.donate-nudge-chip') return chip;
    if (sel === '.donate-nudge-popover') return popover;
    return fakeEl();
  };
  vi.stubGlobal('document', { getElementById: () => slot, addEventListener: () => {} });
  return { clickChip: () => onClick?.() };
}

describe('donate nudge analytics', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _resetDonateNudgeForTests();
    mockedAck.mockResolvedValue(undefined);
  });

  it('counts opening the popover once per painted chip, not per toggle', async () => {
    // "How often did a pilot open the ask" is the funnel step between shown and
    // answered. A pilot who opens, closes and reopens has shown interest once;
    // counting each toggle would inflate the ratio this exists to measure.
    const { clickChip } = stubInteractiveSlot();
    mockedFetch.mockResolvedValue(nudgeShowing());
    await initDonateNudge('GREEN');

    clickChip(); // open
    clickChip(); // close
    clickChip(); // reopen

    const opens = mockedTrack.mock.calls.filter(
      (c) => c[0] === EVENTS.DONATE_NUDGE_OPENED,
    );
    expect(opens).toHaveLength(1);
    expect(opens[0][1]).toMatchObject({ kind: 'evergreen', rung: 1 });
  });

  it('records the impression without an open — shown and opened are distinct', async () => {
    stubInteractiveSlot();
    mockedFetch.mockResolvedValue(nudgeShowing());
    await initDonateNudge('GREEN');

    const names = mockedTrack.mock.calls.map((c) => c[0]);
    expect(names).toContain(EVENTS.DONATE_NUDGE_SHOWN);
    expect(names).not.toContain(EVENTS.DONATE_NUDGE_OPENED);
  });
});

describe('shouldSuppressNudge', () => {
  it.each(['RED', 'red', 'Red'])('suppresses beside %s', (a) => {
    expect(shouldSuppressNudge(a)).toBe(true);
  });

  it.each(['GREEN', 'AMBER', 'UNAVAILABLE', '', null, undefined])(
    'does not suppress beside %s',
    (a) => {
      expect(shouldSuppressNudge(a)).toBe(false);
    },
  );
});

describe('initDonateNudge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _resetDonateNudgeForTests();
    mockedAck.mockResolvedValue(undefined);
    stubSlot();
  });

  it('never asks the server for a chip it could not render', async () => {
    await initDonateNudge('RED');
    expect(mockedFetch).not.toHaveBeenCalled();
    expect(mockedAck).not.toHaveBeenCalled();
  });

  it('a first render on RED does not cost the chip for the session', async () => {
    // The pilot can switch away from a RED pack via the history dropdown. If
    // the RED short-circuit also consumed the one-shot fetch, the chip would
    // never appear again for the rest of the page load.
    mockedFetch.mockResolvedValue(nudgeShowing());
    await initDonateNudge('RED');
    expect(mockedFetch).not.toHaveBeenCalled();

    await initDonateNudge('GREEN');
    expect(mockedFetch).toHaveBeenCalledOnce();
  });

  it('acks the impression once, however many times it re-renders', async () => {
    mockedFetch.mockResolvedValue(nudgeShowing());
    await initDonateNudge('GREEN');
    expect(mockedAck).toHaveBeenCalledExactlyOnceWith('shown');

    await initDonateNudge('AMBER');
    await initDonateNudge('GREEN');
    expect(mockedAck).toHaveBeenCalledOnce();
    expect(mockedFetch).toHaveBeenCalledOnce();
  });

  it('hides a painted chip when a RED pack replaces the one it painted on', async () => {
    // The history dropdown swaps packs in place. Without a re-check the chip
    // would sit there beside a serious-hazard assessment — the one thing this
    // feature must never do.
    mockedFetch.mockResolvedValue(nudgeShowing());
    await initDonateNudge('GREEN');
    expect(slot.style.display).toBe('');

    await initDonateNudge('RED');
    expect(slot.style.display).toBe('none');

    // ...and comes back when they switch away again, without a second ack.
    await initDonateNudge('AMBER');
    expect(slot.style.display).toBe('');
    expect(mockedAck).toHaveBeenCalledOnce();
  });

  it('does not paint onto a pack that went RED while the request was in flight', async () => {
    // The narrow race the visibility re-check exists to close: the fetch starts
    // on a GREEN pack, the pilot switches to a RED one before it resolves, and
    // no further render follows to correct it.
    let resolve!: (v: ReturnType<typeof nudgeShowing>) => void;
    mockedFetch.mockReturnValue(new Promise((r) => { resolve = r; }));

    const inFlight = initDonateNudge('GREEN');
    await initDonateNudge('RED'); // pack switch lands first
    resolve(nudgeShowing());
    await inFlight;

    expect(slot.style.display).toBe('none');
    // ...and no impression was spent on a chip nobody saw.
    expect(mockedAck).not.toHaveBeenCalled();

    // It counts once when the pilot switches back to a pack that allows it.
    await initDonateNudge('GREEN');
    expect(slot.style.display).toBe('');
    expect(mockedAck).toHaveBeenCalledExactlyOnceWith('shown');
  });

  it('opens the ask at most once across re-renders', async () => {
    // The store subscriber calls this on every assessment change; only the
    // first non-suppressed call may open an ask or count an impression.
    mockedFetch.mockResolvedValue({
      show: false, kind: '', rung: 0, reason: 'asked_recently',
      summary: { briefing_count: 0, first_briefing_at: null,
                 pilots_last_year: 0, briefings_last_year: 0 },
    });
    await initDonateNudge('GREEN');
    await initDonateNudge('AMBER');
    await initDonateNudge('GREEN');
    expect(mockedFetch).toHaveBeenCalledOnce();
  });

  it('acks nothing when the server withholds the ask', async () => {
    mockedFetch.mockResolvedValue({
      show: false, kind: '', rung: 0, reason: 'no_rung_crossed',
      summary: { briefing_count: 0, first_briefing_at: null,
                 pilots_last_year: 0, briefings_last_year: 0 },
    });
    await initDonateNudge('GREEN');
    expect(mockedFetch).toHaveBeenCalledOnce();
    expect(mockedAck).not.toHaveBeenCalled();
  });

  it('stays silent when the request fails', async () => {
    mockedFetch.mockRejectedValue(new Error('401'));
    await expect(initDonateNudge('GREEN')).resolves.toBeUndefined();
    expect(mockedAck).not.toHaveBeenCalled();
  });
});
