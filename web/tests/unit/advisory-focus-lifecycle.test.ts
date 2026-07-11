import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../ts/components/info-popup', () => ({
  initInfoPopup: vi.fn(),
  showMetricInfo: vi.fn(),
  showPopupContent: vi.fn(),
}));

import * as api from '../../ts/adapters/api-adapter';
import { showPopupContent } from '../../ts/components/info-popup';
import { renderAdvisories } from '../../ts/managers/advisories-ui';
import { briefingStore } from '../../ts/store/briefing-store';
import type { ResolvedView } from '../../ts/visualization/cross-section/advisory-presets';
import {
  effectiveEmphasis,
  reconcileAdvisoryFocus,
  replaceAdvisoryFocus,
  resolveAdvisoryFocus,
} from '../../ts/visualization/advisory-focus';
import {
  activeFocus,
  manifestWithoutFocusedModel,
  manifestWithTwoModels,
  refreshedManifest,
  routeData,
} from './fixtures/advisory-focus';

let storageValues: Map<string, string>;

interface FakeListenerHost {
  style: Record<string, string>;
  innerHTML: string;
  listeners: Map<string, EventListener[]>;
  addEventListener: (
    type: string,
    listener: EventListenerOrEventListenerObject,
    options?: AddEventListenerOptions | boolean,
  ) => void;
  querySelectorAll: () => never[];
}

function fakeListenerHost(): FakeListenerHost {
  const listeners = new Map<string, EventListener[]>();
  return {
    style: {},
    innerHTML: '',
    listeners,
    addEventListener: (type, listener) => {
      const callback = typeof listener === 'function'
        ? listener
        : (event: Event) => listener.handleEvent(event);
      listeners.set(type, [...(listeners.get(type) ?? []), callback]);
    },
    querySelectorAll: () => [],
  };
}

function dispatchFakeClick(
  host: FakeListenerHost,
  target: { closest: (selector: string) => unknown },
): void {
  for (const listener of host.listeners.get('click') ?? []) {
    listener({ target } as unknown as Event);
  }
}

function badgeTarget(advisoryId: string, model: string): {
  closest: (selector: string) => unknown;
} {
  const badge = { dataset: { advisoryId, model } };
  return {
    closest: (selector: string) => (
      selector === '.adv-model-badge--tappable' ? badge : null
    ),
  };
}

beforeEach(() => {
  storageValues = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: vi.fn((key: string) => storageValues.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => storageValues.set(key, value)),
    removeItem: vi.fn((key: string) => storageValues.delete(key)),
    clear: vi.fn(() => storageValues.clear()),
  });
  vi.stubGlobal('window', {
    dispatchEvent: vi.fn(),
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
  });
  vi.mocked(showPopupContent).mockClear();
  briefingStore.setState(briefingStore.getInitialState(), true);
});

afterEach(() => {
  briefingStore.setState(briefingStore.getInitialState(), true);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('focus lifecycle helpers', () => {
  it('replaces cloud-top focus with the exact turbulence focus', () => {
    const current = activeFocus('gfs', 'cloud_top');
    const next = activeFocus('gfs', 'turbulence');

    expect(replaceAdvisoryFocus(current, next)).toBe(next);
  });

  it('retains a focus whose exact advisory and model survive refresh', () => {
    const focus = activeFocus('gfs');

    expect(reconcileAdvisoryFocus(focus, refreshedManifest())).toBe(focus);
  });

  it('clears a focus whose exact model is absent after refresh', () => {
    expect(reconcileAdvisoryFocus(
      activeFocus('gfs'),
      manifestWithoutFocusedModel(),
    )).toBeNull();
  });

  it('drops emphasis after a manual layer edit without changing focus identity', () => {
    const focus = activeFocus('gfs', 'cloud_top');

    expect(effectiveEmphasis(focus, null)).toBeNull();
    expect(focus.advisoryId).toBe('cloud_top');
  });

  it('treats missing-representative aggregate focus as legacy without geometry', () => {
    const focus = {
      ...activeFocus('gfs', 'cloud_top'),
      modelAttributionKnown: false,
    };

    const resolved = resolveAdvisoryFocus(
      focus,
      manifestWithTwoModels(),
      routeData(),
    );

    expect(resolved?.modelResult.model).toBe('gfs');
    expect(resolved?.locationState).toBe('legacy');
    expect(resolved?.regions).toEqual([]);
  });
});

describe('briefing store advisory focus lifecycle', () => {
  const view: ResolvedView = {
    enabledLayers: {
      'square-nwp-cloud-bands': true,
      'icing-bands': false,
    },
    highlightSurfaces: ['cross-section', 'route-map'],
    emphasizeLayers: ['square-nwp-cloud-bands', 'terrain', 'cruise-altitude'],
    routeGraph: { left: 'cloud-cover', right: 'ceiling-nwp' },
    map: { metric: 'cloud-at-level', altitudeFt: null },
    skewtOverlays: { 'clouds-nwp': true, 'icing-nwp': false },
    skewtSidePanel: 'relative_humidity',
  };

  function focusCloudTop(): ReturnType<typeof activeFocus> {
    const focus = {
      ...activeFocus('gfs', 'cloud_top'),
      modelAttributionKnown: false,
    };
    briefingStore.getState().focusAdvisory(focus, 'clouds', view);
    return focus;
  }

  it('applies model, resolved view, preset, and focus in one atomic update', () => {
    storageValues.set('wb_vizSettings', JSON.stringify({ sentinel: 'unchanged' }));
    briefingStore.setState({
      selectedModel: 'ecmwf',
      vizSettings: {
        ...briefingStore.getState().vizSettings,
        layout: 'map',
        mapAltitudeFt: 12000,
      },
    });
    const notifications: Array<ReturnType<typeof briefingStore.getState>> = [];
    const unsubscribe = briefingStore.subscribe((state) => notifications.push(state));

    const focus = focusCloudTop();
    unsubscribe();

    expect(notifications).toHaveLength(1);
    const state = notifications[0];
    expect(state.selectedModel).toBe('gfs');
    expect(state.activeAdvisoryFocus).toBe(focus);
    expect(state.activeAdvisoryFocus?.modelAttributionKnown).toBe(false);
    expect(state.vizSettings).toMatchObject({
      layout: 'map',
      activePreset: 'clouds',
      routeGraphLeftMetric: 'cloud-cover',
      routeGraphRightMetric: 'ceiling-nwp',
      mapColorMetric: 'cloud-at-level',
      mapAltitudeFt: null,
      skewtOverlays: { 'clouds-nwp': true, 'icing-nwp': false },
      skewtPrimaryVar: 'relative_humidity',
    });
    expect(state.vizSettings.enabledLayers).toMatchObject(view.enabledLayers!);
    expect(storageValues.get('wb_selectedModel')).toBe('gfs');

    const persistedViz = JSON.parse(storageValues.get('wb_vizSettings')!);
    expect(persistedViz).toEqual({ sentinel: 'unchanged' });
    expect(persistedViz).not.toHaveProperty('activeAdvisoryFocus');
    expect(persistedViz).not.toHaveProperty('highlightSurfaces');
    expect(persistedViz).not.toHaveProperty('emphasizeLayers');
    expect(vi.mocked(localStorage.setItem).mock.calls.some(
      ([key]) => key === 'wb_vizSettings',
    )).toBe(false);
    expect(window.dispatchEvent).not.toHaveBeenCalled();
  });

  it('manual model and generic preset actions clear focus', () => {
    focusCloudTop();
    briefingStore.getState().setSelectedModel('ecmwf');
    expect(briefingStore.getState().activeAdvisoryFocus).toBeNull();

    focusCloudTop();
    briefingStore.getState().applyAdvisoryPreset('clouds', view);
    expect(briefingStore.getState().activeAdvisoryFocus).toBeNull();

    focusCloudTop();
    briefingStore.getState().setVizPreset(null);
    expect(briefingStore.getState().activeAdvisoryFocus).toBeNull();
  });

  it('manual layer edits retain focus identity while clearing active preset', () => {
    const focus = focusCloudTop();

    briefingStore.getState().toggleVizLayer('square-nwp-cloud-bands');

    const state = briefingStore.getState();
    expect(state.activeAdvisoryFocus).toBe(focus);
    expect(state.vizSettings.activePreset).toBeNull();
    expect(effectiveEmphasis(
      state.activeAdvisoryFocus,
      state.vizSettings.activePreset ?? null,
    )).toBeNull();
  });

  it('custom view edits retain focus while programmatic batches retain its preset', () => {
    let focus = focusCloudTop();
    briefingStore.getState().setCloudStyle('natural');
    expect(briefingStore.getState().activeAdvisoryFocus).toBe(focus);
    expect(briefingStore.getState().vizSettings.activePreset).toBeNull();

    focus = focusCloudTop();
    briefingStore.getState().markVizCustom();
    expect(briefingStore.getState().activeAdvisoryFocus).toBe(focus);
    expect(briefingStore.getState().vizSettings.activePreset).toBeNull();

    focus = focusCloudTop();
    briefingStore.getState().setLayersBatch({ 'current-conditions': false });
    expect(briefingStore.getState().activeAdvisoryFocus).toBe(focus);
    expect(briefingStore.getState().vizSettings.activePreset).toBe('clouds');
  });

  it('switching packs clears focus', async () => {
    vi.spyOn(api, 'fetchPack').mockResolvedValue({
      fetch_timestamp: '2026-07-11T10:00:00Z',
      has_digest: false,
      has_advisories: false,
      has_alt_advisories: false,
    } as never);
    vi.spyOn(api, 'fetchSnapshot').mockRejectedValue(new Error('not available'));
    vi.spyOn(api, 'fetchRouteAnalyses').mockRejectedValue(new Error('not available'));
    vi.spyOn(api, 'fetchElevationProfile').mockRejectedValue(new Error('not available'));
    vi.spyOn(api, 'fetchRouteFronts').mockRejectedValue(new Error('not available'));
    briefingStore.setState({ flight: { id: 'flight-1' } as never });
    focusCloudTop();

    await briefingStore.getState().selectPack('2026-07-11T10:00:00Z');

    expect(briefingStore.getState().activeAdvisoryFocus).toBeNull();
  });

  it('atomically reconciles focus with a recalculated manifest', async () => {
    vi.spyOn(api, 'recalculateAdvisories').mockResolvedValue({
      manifest: manifestWithoutFocusedModel(),
      wind_overlay: null,
    });
    briefingStore.setState({
      flight: { id: 'flight-1' } as never,
      currentPack: { fetch_timestamp: '2026-07-11T10:00:00Z' } as never,
    });
    focusCloudTop();
    const notifications: Array<ReturnType<typeof briefingStore.getState>> = [];
    const unsubscribe = briefingStore.subscribe((state) => notifications.push(state));

    await briefingStore.getState().recalculateAdvisories();
    unsubscribe();

    expect(notifications).toHaveLength(1);
    expect(notifications[0].routeAdvisories).toEqual(manifestWithoutFocusedModel());
    expect(notifications[0].activeAdvisoryFocus).toBeNull();
  });
});

describe('per-model advisory popup focus action', () => {
  function renderPopupHarness(
    onAdvisoryAction: (advisoryId: string, model?: string) => void,
    popup: FakeListenerHost = fakeListenerHost(),
  ): {
    section: FakeListenerHost;
    popup: FakeListenerHost;
    transientButton: { onclick: null | (() => void) };
  } {
    const section = fakeListenerHost();
    const wrapper = fakeListenerHost();
    const transientButton: { onclick: null | (() => void) } = { onclick: null };
    vi.stubGlobal('document', {
      getElementById: vi.fn((id: string) => {
        if (id === 'advisories-section') return section;
        if (id === 'advisories-wrapper') return wrapper;
        if (id === 'metric-info-popup') return popup;
        return null;
      }),
      querySelector: vi.fn((selector: string) => (
        selector === '#metric-info-popup .advisory-model-focus-btn'
          ? transientButton
          : null
      )),
    });

    renderAdvisories(
      manifestWithTwoModels(),
      undefined,
      'full',
      undefined,
      undefined,
      undefined,
      undefined,
      onAdvisoryAction,
    );
    dispatchFakeClick(section, badgeTarget('cloud_top', 'gfs'));
    return { section, popup, transientButton };
  }

  it('renders the exact native semantic focus button markup', () => {
    renderPopupHarness(vi.fn());

    const html = vi.mocked(showPopupContent).mock.lastCall?.[0] ?? '';
    expect(html).toContain(
      'class="btn btn-secondary btn-sm advisory-model-focus-btn"',
    );
    expect(html).toContain('data-advisory-id="cloud_top"');
    expect(html).toContain('data-model="gfs"');
    expect(html).toContain('aria-label="Show on chart: GFS"');
    expect(html).toContain('>Show on chart</button>');
  });

  it('uses one persistent popup delegate and the latest exact-model callback', () => {
    const popup = fakeListenerHost();
    const firstCallback = vi.fn();
    const first = renderPopupHarness(firstCallback, popup);
    const secondCallback = vi.fn();
    const second = renderPopupHarness(secondCallback, popup);

    expect(popup.listeners.get('click')).toHaveLength(1);
    expect(first.transientButton.onclick).toBeNull();
    expect(second.transientButton.onclick).toBeNull();

    const button = {
      dataset: { advisoryId: 'cloud_top', model: 'ecmwf' },
    };
    dispatchFakeClick(popup, {
      closest: (selector) => (
        selector === '.advisory-model-focus-btn' ? button : null
      ),
    });

    expect(firstCallback).not.toHaveBeenCalled();
    expect(secondCallback).toHaveBeenCalledOnce();
    expect(secondCallback).toHaveBeenCalledWith('cloud_top', 'ecmwf');
  });
});

describe('briefing subscription point-section effects', () => {
  it('loads point sections once for one atomic focus notification', async () => {
    vi.doMock('leaflet', () => ({}));
    vi.doMock('../../ts/tour/briefing-tour', () => ({
      startBriefingTour: vi.fn(),
      maybeAutoStartBriefingTour: vi.fn(),
    }));
    vi.doMock('../../ts/tour/tour-offer', () => ({
      maybeOfferTour: vi.fn(),
    }));
    vi.stubGlobal('window', {
      dispatchEvent: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      matchMedia: vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
      location: { search: '', hostname: 'localhost' },
      setTimeout: globalThis.setTimeout.bind(globalThis),
      clearTimeout: globalThis.clearTimeout.bind(globalThis),
    });
    vi.stubGlobal('document', {
      readyState: 'loading',
      addEventListener: vi.fn(),
      documentElement: { dataset: {}, style: {} },
      querySelector: vi.fn(() => null),
      querySelectorAll: vi.fn(() => []),
      getElementById: vi.fn(() => null),
    });
    vi.stubGlobal('location', { search: '' });
    vi.stubGlobal('navigator', { languages: ['en'], language: 'en' });

    const main = await import('../../ts/briefing-main') as Partial<{
      createPointSectionsRenderOnce: (effect: () => void) => () => void;
    }>;
    // Before the production coalescer exists, this faithfully models the
    // current direct branch calls and exposes the duplicate behavior (2 loads).
    const createRenderOnce = main.createPointSectionsRenderOnce
      ?? ((effect: () => void) => effect);
    let soundingLoads = 0;
    briefingStore.setState({ selectedModel: 'ecmwf' });
    const unsubscribe = briefingStore.subscribe((state, previous) => {
      const renderPointSectionsOnce = createRenderOnce(() => {
        soundingLoads += 1;
      });
      if (state.selectedModel !== previous.selectedModel) {
        renderPointSectionsOnce();
      }
      if (state.vizSettings !== previous.vizSettings) {
        renderPointSectionsOnce();
      }
    });

    briefingStore.getState().focusAdvisory(
      activeFocus('gfs', 'cloud_top'),
      'clouds',
      { enabledLayers: { 'square-nwp-cloud-bands': true } },
    );
    unsubscribe();

    expect(soundingLoads).toBe(1);
  });
});
