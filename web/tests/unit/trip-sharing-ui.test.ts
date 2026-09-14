/** The trip page in viewer mode — what a share-link recipient may see.
 *
 * The server refuses every owner action on someone else's trip, so nothing here
 * is a security boundary. What it pins is that the *page* does not offer them:
 * a Refresh button that 404s, an Unlink that cannot unlink, or a Delete on a
 * trip the viewer does not own are all worse than absent. Refresh is the one
 * that matters most — it spends the owner's money against the owner's queue.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../ts/i18n/i18n', () => ({
  t: (key: string, vars?: Record<string, unknown>) =>
    vars ? `${key}:${JSON.stringify(vars)}` : key,
  getDateLocale: () => 'en-GB',
}));

import {
  renderControls,
  renderLegs,
  renderSharedBy,
  renderViewerControls,
} from '../../ts/managers/trip-ui';
import type { TripLeg, TripResponse, TripSummary } from '../../ts/store/types';

/** Stub `document` with one element per id the renderers ask for. */
function stubDom(): Record<string, { innerHTML: string; style: { display: string } }> {
  const slots: Record<string, { innerHTML: string; style: { display: string };
    querySelector: () => null; querySelectorAll: () => never[];
    addEventListener: () => void; textContent: string }> = {};
  const make = () => ({
    innerHTML: '', textContent: '', style: { display: '' },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
  });
  vi.stubGlobal('document', {
    getElementById: (id: string) => (slots[id] ??= make()),
  });
  return slots as never;
}

const LEG: TripLeg = {
  flight_id: 'egtf-lsgs',
  label: 'EGTF → LSGS',
  departure_time: '2026-09-18T09:00:00Z',
  duration_hours: 2,
  state: 'remaining',
  grade_kind: 'assessment',
  assessment: 'GREEN',
  days_out: 4,
} as TripLeg;

const SUMMARY: TripSummary = {
  trip_id: 'abc123',
  name: 'Sion weekend',
  legs: [LEG],
  total_legs: 1,
  remaining_legs: 1,
  chain_label: 'EGTF → LSGS',
  continuity_warnings: [],
  beyond_horizon_leg_ids: [],
  pending_coverage_leg_ids: [],
  needs_briefing_leg_ids: [],
  unavailable_leg_ids: [],
  headline: 'EGTF → LSGS decides this trip.',
  is_round_trip: false,
} as unknown as TripSummary;

function trip(over: Partial<TripResponse> = {}): TripResponse {
  return {
    id: 'abc123', user_id: 'alice', name: 'Sion weekend', notes: null,
    auto_refresh: false, auto_refresh_hour: null, notify_override: 'default',
    created_at: '2026-09-14T00:00:00Z', flight_ids: [LEG.flight_id],
    summary: SUMMARY, ai_summary: null, ai_summary_at: null,
    ai_summary_stale: false, refresh: null,
    role: 'owner', owner_display_name: null, share_code: 'AbCd1234',
    is_subscribed: false, is_shareable: true,
    ...over,
  } as TripResponse;
}

const noopLegHandlers = { onRemoveLeg: () => {}, onRefreshLeg: () => {} };

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('leg rows', () => {
  it('offers the owner refresh, edit and unlink', () => {
    const slots = stubDom();
    renderLegs(SUMMARY, noopLegHandlers, {}, false, {}, true);
    const html = slots['trip-legs'].innerHTML;
    expect(html).toContain('btn-refresh-leg');
    expect(html).toContain('btn-remove-leg');
    expect(html).toContain('/flight.html?id=');
  });

  it('offers a viewer the briefing and nothing else', () => {
    const slots = stubDom();
    renderLegs(SUMMARY, noopLegHandlers, {}, false, {}, false);
    const html = slots['trip-legs'].innerHTML;
    // The whole point of the share: the recipient can read each leg.
    expect(html).toContain('/briefing.html?flight=egtf-lsgs');
    expect(html).not.toContain('btn-refresh-leg');
    expect(html).not.toContain('btn-remove-leg');
    expect(html).not.toContain('/flight.html?id=');
  });
});

describe('trip controls', () => {
  it('gives the owner the full bar plus a share button', () => {
    const slots = stubDom();
    renderControls(trip(), {
      onRefresh: () => {}, onRename: () => {}, onToggleAutoRefresh: () => {},
      onDelete: () => {}, onShare: () => {},
    });
    const html = slots['trip-controls'].innerHTML;
    expect(html).toContain('btn-trip-refresh');
    expect(html).toContain('btn-trip-share');
    expect(html).toContain('btn-trip-delete');
    expect(html).not.toContain('disabled');
  });

  it('disables share and says why when a leg is private', () => {
    // All-or-nothing: the link would 404 for the recipient, and only the owner
    // can fix it, so the button explains rather than handing out a dead link.
    const slots = stubDom();
    renderControls(trip({ is_shareable: false }), {
      onRefresh: () => {}, onRename: () => {}, onToggleAutoRefresh: () => {},
      onDelete: () => {}, onShare: () => {},
    });
    const html = slots['trip-controls'].innerHTML;
    expect(html).toContain('btn-trip-share');
    expect(html).toContain('disabled');
    expect(html).toContain('trips.shareBlocked');
  });

  it('gives a viewer one action and no owner controls', () => {
    const slots = stubDom();
    renderViewerControls(
      trip({ role: 'viewer' }), { onFollow: () => {}, onUnfollow: () => {} },
    );
    const html = slots['trip-controls'].innerHTML;
    expect(html).toContain('btn-trip-follow');
    expect(html).toContain('trips.viewerNote');
    for (const owned of [
      'btn-trip-refresh', 'btn-trip-rename', 'btn-trip-delete',
      'trip-auto-refresh-toggle', 'btn-trip-share',
    ]) {
      expect(html).not.toContain(owned);
    }
  });

  it("flips the viewer's action to un-follow once every leg is theirs", () => {
    const slots = stubDom();
    renderViewerControls(
      trip({ role: 'viewer', is_subscribed: true }),
      { onFollow: () => {}, onUnfollow: () => {} },
    );
    expect(slots['trip-controls'].innerHTML).toContain('trips.btnUnfollow');
  });
});

describe('shared-by line', () => {
  it('names the owner to a viewer', () => {
    const slots = stubDom();
    renderSharedBy(trip({ role: 'viewer', owner_display_name: 'Alice Pilot' }));
    expect(slots['trip-shared-by'].innerHTML).toContain('Alice Pilot');
    expect(slots['trip-shared-by'].style.display).toBe('');
  });

  it('falls back when the owner has no display name', () => {
    // Never the email — that would leak it to every recipient.
    const slots = stubDom();
    renderSharedBy(trip({ role: 'viewer', owner_display_name: null }));
    expect(slots['trip-shared-by'].innerHTML).toContain('trips.sharedByUnknown');
  });

  it('stays hidden for the owner', () => {
    const slots = stubDom();
    renderSharedBy(trip());
    expect(slots['trip-shared-by'].innerHTML).toBe('');
    expect(slots['trip-shared-by'].style.display).toBe('none');
  });
});
