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
import {
  initDonateNudge,
  shouldSuppressNudge,
  _resetDonateNudgeForTests,
} from '../../ts/managers/donate-nudge-ui';

const mockedFetch = vi.mocked(fetchDonateNudge);
const mockedAck = vi.mocked(ackDonateNudge);

/** Enough of a slot for the early-return paths; none of these tests reach the
 * render, which needs a real DOM the node test env does not provide. */
function stubSlot(): void {
  vi.stubGlobal('document', { getElementById: () => ({}) });
}

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
    stubSlot();
  });

  it('never asks the server for a chip it could not render', async () => {
    await initDonateNudge('RED');
    expect(mockedFetch).not.toHaveBeenCalled();
    expect(mockedAck).not.toHaveBeenCalled();
  });

  it('never acks an impression it suppressed', async () => {
    mockedFetch.mockResolvedValue({
      show: true, kind: 'evergreen', rung: 1, reason: 'show',
      summary: { briefing_count: 23, first_briefing_at: null,
                 pilots_last_year: 0, briefings_last_year: 0 },
    });
    await initDonateNudge('RED');
    expect(mockedAck).not.toHaveBeenCalled();
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
