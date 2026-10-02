/** The two Lens selectors have to stay independent (#591).
 *
 * Focus (what am I looking for) and Emulate (whose conventions) write
 * different fields and compose. The failure this pins is silent: picking
 * "Custom" in one selector clearing the other's choice — and with it the
 * methods the lens resolves against — leaves the chart drawn one way and both
 * selectors claiming another.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// The store broadcasts a theme change on `window`. jsdom is not a dependency
// here and this is the only DOM touch on the path under test, so stub it
// rather than pull in an environment for one method.
(globalThis as unknown as { window: unknown }).window = {
  dispatchEvent: () => true,
};

vi.mock('../../ts/adapters/api-adapter', () => ({
  RefreshStreamError: class RefreshStreamError extends Error {},
}));
vi.mock('../../ts/visualization/cross-section/theme', () => ({
  setActiveTheme: vi.fn(),
  THEMES: {
    standard: { label: 'Standard' }, 'high-contrast': { label: 'High contrast' },
    light: { label: 'Light' }, gramet: { label: 'GRAMET' },
  },
  getActiveThemeId: () => 'light',
}));

import { briefingStore, effectiveThemeId } from '../../ts/store/briefing-store';
import { setActiveTheme } from '../../ts/visualization/cross-section/theme';

function viz() { return briefingStore.getState().vizSettings; }

describe('Lens: Focus and Emulate are independent', () => {
  beforeEach(() => {
    briefingStore.setState({
      vizSettings: { ...viz(), activePreset: null, activeEmulation: null },
    });
  });

  it('keeps the emulation when the focus lens goes back to Custom', () => {
    briefingStore.getState().setVizPreset('gramet');
    expect(viz().activeEmulation).toBe('gramet');

    briefingStore.setState({ vizSettings: { ...viz(), activePreset: 'icing' } });
    briefingStore.getState().markVizCustom();

    expect(viz().activePreset).toBeNull();
    // The whole point: GRAMET is still the method set the next lens resolves
    // against, so dropping it here would silently change what gets drawn.
    expect(viz().activeEmulation).toBe('gramet');
  });

  it('applies the caller-resolved methods when Emulate goes to FlyFun', () => {
    briefingStore.getState().setVizPreset('gramet');
    briefingStore.getState().setVizPreset(null, { 'sfip-bands': true, 'icing-ogimet-nwp-bands': false });

    expect(viz().activeEmulation).toBeNull();
    // FlyFun is not an absence — it applies our own conventions, which are the
    // methods the briefing graded with.
    expect(viz().enabledLayers['sfip-bands']).toBe(true);
    expect(viz().enabledLayers['icing-ogimet-nwp-bands']).toBe(false);
  });

  it('leaves layers alone when FlyFun is selected with nothing resolved', () => {
    // Defensive: a caller with no graded methods yet must not blank the chart.
    briefingStore.setState({
      vizSettings: { ...viz(), enabledLayers: { 'sfip-bands': true } },
    });
    briefingStore.getState().setVizPreset(null);
    expect(viz().enabledLayers['sfip-bands']).toBe(true);
  });

  it('does not clear the focus lens when an emulation is applied', () => {
    briefingStore.setState({ vizSettings: { ...viz(), activePreset: 'icing' } });
    briefingStore.getState().setVizPreset('windy');
    expect(viz().activeEmulation).toBe('windy');
    expect(viz().activePreset).toBe('icing');
  });
});

/** #597: the theme belongs to the user; an emulation only borrows the screen.
 *
 * Before, GRAMET overwrote the stored theme and FlyFun never restored one, so
 * GRAMET → FlyFun drew our methods in GRAMET's colours under a selector
 * claiming our conventions. */
describe('Emulation and the user\'s theme', () => {
  beforeEach(() => {
    briefingStore.setState({
      vizSettings: { ...viz(), vizTheme: 'light', activePreset: null, activeEmulation: null, enabledLayers: {} },
    });
    vi.mocked(setActiveTheme).mockClear();
  });

  it('draws the emulation\'s theme without overwriting the user\'s', () => {
    briefingStore.getState().setVizPreset('gramet');
    expect(viz().vizTheme).toBe('light');
    expect(effectiveThemeId(viz())).toBe('gramet');
    expect(setActiveTheme).toHaveBeenLastCalledWith('gramet');
  });

  it('restores the user\'s theme when Emulate goes back to FlyFun', () => {
    briefingStore.getState().setVizPreset('gramet');
    briefingStore.getState().setVizPreset(null);
    expect(effectiveThemeId(viz())).toBe('light');
    expect(setActiveTheme).toHaveBeenLastCalledWith('light');
  });

  it('a hand-picked theme drops the emulation and applies FlyFun\'s methods', () => {
    briefingStore.getState().setVizPreset('gramet');
    briefingStore.getState().setVizTheme('high-contrast', { 'sfip-bands': true, 'icing-ogimet-nwp-bands': false });
    expect(viz().activeEmulation).toBeNull();
    expect(viz().vizTheme).toBe('high-contrast');
    // Not GRAMET's methods under a FlyFun label.
    expect(viz().enabledLayers['sfip-bands']).toBe(true);
    expect(viz().enabledLayers['icing-ogimet-nwp-bands']).toBe(false);
    expect(setActiveTheme).toHaveBeenLastCalledWith('high-contrast');
  });

  it('a theme pick with no emulation active leaves the layers alone', () => {
    briefingStore.getState().setVizTheme('gramet', { 'sfip-bands': true });
    expect(viz().vizTheme).toBe('gramet');
    expect(viz().enabledLayers['sfip-bands']).toBeUndefined();
  });

  it('falls back to the standard theme when nothing is stored', () => {
    expect(effectiveThemeId({ ...viz(), vizTheme: undefined })).toBe('standard');
  });
});

/** #597: `None` is one user edit, so one store update — and still a lens edit. */
describe('setVizLayers', () => {
  beforeEach(() => {
    briefingStore.setState({
      vizSettings: { ...viz(), activePreset: 'icing', activeEmulation: 'gramet', enabledLayers: { a: true, b: true } },
    });
  });

  it('applies every override in a single update', () => {
    const listener = vi.fn();
    const unsub = briefingStore.subscribe(listener);
    briefingStore.getState().setVizLayers({ a: false, b: false });
    unsub();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(viz().enabledLayers).toMatchObject({ a: false, b: false });
  });

  it('dirties the focus lens but keeps the emulation, like a single toggle', () => {
    briefingStore.getState().setVizLayers({ a: false });
    expect(viz().activePreset).toBeNull();
    expect(viz().activeEmulation).toBe('gramet');
  });
});
