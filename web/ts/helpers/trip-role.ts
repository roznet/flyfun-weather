/** Who a trip payload belongs to, as one testable rule (#602 sharing).
 *
 * Pulled out of `trip-main.ts` so it can be unit-tested and so there is a
 * single place the rule lives — the sibling of iOS's `TripResponse.isOwned`.
 * The two must agree: a control offered on one client and withheld on the
 * other is the inconsistency this feature has already produced once.
 */

/** Whether the owner's controls may be offered for this role.
 *
 * Narrows the **positive** case. `role !== 'viewer'` looks equivalent and is
 * not: the declared `'owner' | 'viewer'` union is what today's server sends,
 * not a runtime guarantee, so a role added later falls through the negative
 * test and lands on owner — offering refresh, rename and delete on a trip the
 * caller does not own. The server refuses those, so it is not a data hole; it
 * is a confusing UI and the wrong default. A missing control is recoverable.
 */
export function isTripOwner(role: string | undefined | null): boolean {
  return role === 'owner';
}
