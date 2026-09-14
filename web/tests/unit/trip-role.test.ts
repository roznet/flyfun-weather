/** Who the trip page treats as owner (#602 sharing).
 *
 * One rule, two clients: this must agree with iOS's `TripResponse.isOwned`
 * (`role == .owner`, pinned by `TripTests.swift`'s unknown/absent-role tests).
 * They diverged once already — iOS was fixed to narrow the positive case and
 * web was left narrowing the negative — so the unknown-role case is pinned on
 * both sides rather than trusted to stay in step.
 */

import { describe, it, expect } from 'vitest';
import { isTripOwner } from '../../ts/helpers/trip-role';

describe('isTripOwner', () => {
  it('is true only for the owner', () => {
    expect(isTripOwner('owner')).toBe(true);
  });

  it('is false for a viewer', () => {
    expect(isTripOwner('viewer')).toBe(false);
  });

  it('treats a role this build does not know as a viewer', () => {
    // The declared 'owner' | 'viewer' union is what today's server sends, not
    // a runtime guarantee. A `!== 'viewer'` test would land these on owner and
    // offer refresh, rename and delete on someone else's trip.
    for (const role of ['co_owner', 'editor', 'OWNER', 'admin', '']) {
      expect(isTripOwner(role), role || '(empty)').toBe(false);
    }
  });

  it('treats a missing role as a viewer', () => {
    expect(isTripOwner(undefined)).toBe(false);
    expect(isTripOwner(null)).toBe(false);
  });
});
