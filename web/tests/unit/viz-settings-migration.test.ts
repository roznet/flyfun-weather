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

  it('is a no-op for current settings that never had a preset', () => {
    const input = settings({ activePreset: null, settingsVersion: 1 });
    expect(migrateVizSettings(input)).toBe(input);
  });

  it('does not clobber an emulation that is already split out', () => {
    const out = migrateVizSettings(settings({ activePreset: null, activeEmulation: 'windy' }));
    expect(out.activeEmulation).toBe('windy');
    expect(out.activePreset).toBeNull();
  });

  // #597: an emulation used to overwrite `vizTheme`, so a saved emulation
  // carrying its own theme there is that write, not a choice — and keeping it
  // would leave FlyFun with nothing to go back to.
  it('clears a theme an emulation wrote over the user\'s own', () => {
    const out = migrateVizSettings(settings({ activeEmulation: 'gramet', vizTheme: 'gramet' }));
    expect(out.activeEmulation).toBe('gramet');
    expect(out.vizTheme).toBeUndefined();
  });

  it('keeps a theme that differs from the active emulation\'s', () => {
    const out = migrateVizSettings(settings({ activeEmulation: 'gramet', vizTheme: 'light' }));
    expect(out.vizTheme).toBe('light');
  });

  it('runs the theme cleanup once, not on every load', () => {
    // After #597 "Light theme + Windy (light)" is a real choice; a state that
    // has already been migrated must keep it across reloads.
    const once = migrateVizSettings(settings({ activeEmulation: 'gramet', vizTheme: 'gramet' }));
    expect(once.settingsVersion).toBe(1);
    const reload = migrateVizSettings({ ...once, activeEmulation: 'windy', vizTheme: 'light' });
    expect(reload.vizTheme).toBe('light');
  });

  it('keeps a theme when no emulation is active', () => {
    // Indistinguishable from a deliberate pick, so it stays.
    const out = migrateVizSettings(settings({ activeEmulation: null, vizTheme: 'gramet' }));
    expect(out.vizTheme).toBe('gramet');
  });
});
