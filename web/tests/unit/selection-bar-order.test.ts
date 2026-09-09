/**
 * The selection bar's button order (#602).
 *
 * Pinned because the sequence is a deliberate UX decision, not an accident of
 * the order someone happened to append markup in — which is exactly how the
 * trip buttons originally ended up *inside* the selection group, between
 * "Select past" and "Clear".
 */

import { describe, expect, it } from 'vitest';
import {
  actionGroupStart,
  selectionBarButtons,
  type SelectionBarOptions,
} from '../../ts/helpers/selection-bar-order';

const NONE: SelectionBarOptions = {
  hasSelectAll: false,
  hasSelectPast: false,
  canGroupAsTrip: false,
  canAddToTrip: false,
  canRemoveFromTrip: false,
};

const keys = (o: Partial<SelectionBarOptions>) =>
  selectionBarButtons({ ...NONE, ...o }).map(b => b.key);

describe('selectionBarButtons', () => {
  it('puts selection first and actions last, in the agreed order', () => {
    expect(keys({ hasSelectAll: true, hasSelectPast: true, canGroupAsTrip: true }))
      .toEqual(['select-all', 'select-past', 'clear', 'delete', 'group-trip']);
  });

  it('never places a trip action inside the selection group', () => {
    // The original bug: trip buttons sat between "Select past" and "Clear".
    const buttons = selectionBarButtons({
      ...NONE,
      hasSelectAll: true,
      hasSelectPast: true,
      canGroupAsTrip: true,
      canRemoveFromTrip: true,
    });
    const selectionKeys = buttons.filter(b => b.group === 'selection').map(b => b.key);
    expect(selectionKeys).toEqual(['select-all', 'select-past', 'clear']);
  });

  it('groups are contiguous — no action button before the last selection one', () => {
    const buttons = selectionBarButtons({
      ...NONE, hasSelectAll: true, hasSelectPast: true,
      canAddToTrip: true, canRemoveFromTrip: true,
    });
    const lastSelection = buttons.map(b => b.group).lastIndexOf('selection');
    const firstAction = buttons.map(b => b.group).indexOf('action');
    expect(firstAction).toBeGreaterThan(lastSelection);
  });

  it('ends on the constructive trip action, which is the only primary', () => {
    const buttons = selectionBarButtons({
      ...NONE, hasSelectAll: true, canGroupAsTrip: true, canRemoveFromTrip: true,
    });
    expect(buttons[buttons.length - 1].key).toBe('group-trip');
    expect(buttons.filter(b => b.variant === 'primary').map(b => b.key))
      .toEqual(['group-trip']);
  });

  it('"Add to …" takes the primary slot when that is the trip action', () => {
    const buttons = selectionBarButtons({ ...NONE, hasSelectAll: true, canAddToTrip: true });
    expect(buttons[buttons.length - 1]).toEqual(
      { key: 'add-trip', group: 'action', variant: 'primary' },
    );
  });

  it('keeps "Remove from trip" out of the primary slot when it stands alone', () => {
    // It is an unlink and a lesser action; being the only trip button present
    // must not promote it to where the eye finishes.
    const buttons = selectionBarButtons({ ...NONE, canRemoveFromTrip: true });
    expect(buttons.map(b => b.key)).toEqual(['clear', 'delete', 'remove-trip']);
    expect(buttons.some(b => b.variant === 'primary')).toBe(false);
  });

  it('orders delete before remove-from-trip before the constructive action', () => {
    expect(keys({ canRemoveFromTrip: true, canAddToTrip: true }))
      .toEqual(['clear', 'delete', 'remove-trip', 'add-trip']);
  });

  it('keeps Delete the only danger button', () => {
    const buttons = selectionBarButtons({
      ...NONE, hasSelectAll: true, canGroupAsTrip: true, canRemoveFromTrip: true,
    });
    expect(buttons.filter(b => b.variant === 'danger').map(b => b.key)).toEqual(['delete']);
  });

  it('always offers Clear — the bar only exists while something is selected', () => {
    expect(keys({})).toEqual(['clear', 'delete']);
  });

  it('prefers the more specific trip action if a caller sets both', () => {
    expect(keys({ canGroupAsTrip: true, canAddToTrip: true })).not.toContain('add-trip');
  });

  it('reports where the divider goes', () => {
    const buttons = selectionBarButtons({ ...NONE, hasSelectAll: true, canGroupAsTrip: true });
    // select-all, clear | delete, group-trip
    expect(actionGroupStart(buttons)).toBe(2);
    expect(buttons[2].key).toBe('delete');
  });
});
