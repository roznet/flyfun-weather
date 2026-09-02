/** Persisted-settings migration for the Lens split (#591).
 *
 * This runs once per user, on load, and is invisible when it goes wrong: the
 * chart still renders GRAMET-style while both selectors claim otherwise. So it
 * gets a test even though it is four lines.
 */

import { describe, it, expect } from 'vitest';
import { migrateVizSettings } from '../../ts/store/briefing-store';
import type { VizSettings } from '../../ts/visualization/types';

function settings(over: Partial<VizSettings> = {}): VizSettings {
  return { enabledLayers: {}, ...over } as VizSettings;
}

describe('migrateVizSettings', () => {
  it('moves a stored tool emulation out of the lens field', () => {
    const out = migrateVizSettings(settings({ activePreset: 'gramet' }));
    expect(out.activeEmulation).toBe('gramet');
    expect(out.activePreset).toBeNull();
  });

  it('leaves an advisory lens where it is', () => {
    // 'icing' is a focus lens, not an emulation — moving it would silently
    // swap what the user was looking at.
    const out = migrateVizSettings(settings({ activePreset: 'icing' }));
    expect(out.activePreset).toBe('icing');
    expect(out.activeEmulation).toBeUndefined();
  });

  it('is a no-op for settings that never had a preset', () => {
    const input = settings({ activePreset: null });
    expect(migrateVizSettings(input)).toBe(input);
  });

  it('does not clobber an emulation that is already split out', () => {
    const out = migrateVizSettings(settings({ activePreset: null, activeEmulation: 'windy' }));
    expect(out.activeEmulation).toBe('windy');
    expect(out.activePreset).toBeNull();
  });
});
