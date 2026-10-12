/** What the route-SIGMET section shows for a pack's `route_sigmets` block.
 *
 * Kept out of briefing-ui.ts so unit tests can import it without the DOM.
 *
 * - `unavailable`: the fetch failed when the briefing was built (#734). The
 *   empty list means "not checked", so the section says so instead of hiding
 *   like a quiet route. A live tick or ↻ only ever delivers a good fetch,
 *   which replaces the block and clears the notice.
 * - `hidden`: no block (not D-0) or a good fetch that matched nothing. Older
 *   packs have no `fetch_ok` and read as a good fetch.
 * - `table`: at least one SIGMET.
 *
 * SYNC: iOS `RouteSigmets.unavailable` / `showsSection`
 * (app/flyfun-weather/flyfun-weather/Models/API/SnapshotResponse.swift).
 */

import type { RouteSigmets } from '../store/types';

export type SigmetSectionState = 'unavailable' | 'hidden' | 'table';

export function sigmetSectionState(sig: RouteSigmets | null | undefined): SigmetSectionState {
  if (!sig) return 'hidden';
  if (sig.fetch_ok === false) return 'unavailable';
  return sig.sigmets.length > 0 ? 'table' : 'hidden';
}
