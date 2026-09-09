/**
 * The order and visual weight of the flights-list selection bar (#602).
 *
 * Two groups, never interleaved:
 *
 * 1. **Selection** — what is picked: Select all, Select past, Clear.
 * 2. **Actions** — what happens to the picks: Delete, Remove from trip, and
 *    the constructive trip action.
 *
 * The trip buttons used to sit *between* Select past and Clear, which put the
 * most-used action inside the selection group where nobody would look for it.
 *
 * Within the action group the sequence is deliberate: destructive first, the
 * take-apart action next, and the single most likely constructive action last.
 * Last is the primary slot in a left-to-right bar, so "Group as trip" / "Add
 * to …" lands where the eye finishes — and it is the only button styled
 * `primary`, because position alone does not make something look primary when
 * every neighbour is an outline pill.
 *
 * Extracted as a pure rule rather than inlined in the markup so the sequence
 * can be asserted directly, and so a future button cannot quietly land in the
 * wrong group.
 */

export type SelectionBarButtonKey =
  | 'select-all'
  | 'select-past'
  | 'clear'
  | 'delete'
  | 'remove-trip'
  | 'group-trip'
  | 'add-trip';

export interface SelectionBarButton {
  key: SelectionBarButtonKey;
  group: 'selection' | 'action';
  variant: 'outline' | 'danger' | 'primary';
}

export interface SelectionBarOptions {
  hasSelectAll: boolean;
  hasSelectPast: boolean;
  canGroupAsTrip: boolean;
  canAddToTrip: boolean;
  canRemoveFromTrip: boolean;
}

export function selectionBarButtons(opts: SelectionBarOptions): SelectionBarButton[] {
  const buttons: SelectionBarButton[] = [];

  // --- Selection group ---
  if (opts.hasSelectAll) {
    buttons.push({ key: 'select-all', group: 'selection', variant: 'outline' });
  }
  if (opts.hasSelectPast) {
    buttons.push({ key: 'select-past', group: 'selection', variant: 'outline' });
  }
  // Clear always shows: the bar only exists while something is selected.
  buttons.push({ key: 'clear', group: 'selection', variant: 'outline' });

  // --- Action group ---
  buttons.push({ key: 'delete', group: 'action', variant: 'danger' });
  if (opts.canRemoveFromTrip) {
    // An unlink, and a lesser action than grouping — it never takes the
    // primary slot, even when it is the only trip button present.
    buttons.push({ key: 'remove-trip', group: 'action', variant: 'outline' });
  }
  // Mutually exclusive upstream (see buildTripSelection: zero trips → group,
  // exactly one → add). Handled as a chain anyway so a caller that sets both
  // gets the more specific action rather than two competing primaries.
  if (opts.canGroupAsTrip) {
    buttons.push({ key: 'group-trip', group: 'action', variant: 'primary' });
  } else if (opts.canAddToTrip) {
    buttons.push({ key: 'add-trip', group: 'action', variant: 'primary' });
  }

  return buttons;
}

/** Index at which the action group starts, or -1 when there is no boundary. */
export function actionGroupStart(buttons: SelectionBarButton[]): number {
  return buttons.findIndex(b => b.group === 'action');
}
