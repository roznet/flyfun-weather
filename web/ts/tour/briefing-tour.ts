import { driver, type DriveStep, type Driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import { briefingStore } from '../store/briefing-store';
import { t } from '../i18n/i18n';
import { markOffered } from './tour-storage';
import { track, EVENTS } from '../analytics/track';
import { VIZ_LAYER_BAR_RERENDER } from '../visualization/controls/panel';

function ensureSectionExpanded(sectionAttr: string): void {
  const wrapper = document.querySelector<HTMLElement>(`[data-section="${sectionAttr}"]`);
  if (wrapper && wrapper.classList.contains('collapsed')) {
    const header = wrapper.querySelector<HTMLElement>('h3');
    header?.click();
  }
}

// The viz layout (`Compare`/`Split`/`Map`) is persisted to localStorage, so a
// user who previously picked a non-cross-section layout would arrive at the
// later viz steps with a different panel rendered — no `.viz-layer-toggles`,
// `.viz-toolbar-top`, or per-layer ⓘ button. Force the layout back to
// `cross-section` so those steps highlight real elements.
function ensureCrossSectionLayout(): void {
  const state = briefingStore.getState();
  if (state.vizSettings.layout !== 'cross-section') {
    state.setLayout('cross-section');
  }
}

// driver.js leaves the highlighted element interactive, so the user can flip the
// layout toggle to Compare on any of the viz steps (it lives inside the
// highlighted `#viz-section` / `.viz-toolbar-top`). That swaps in a different
// panel whose `.viz-layer-toggles` / `.viz-toolbar-top` / per-layer ⓘ button
// don't exist, breaking every subsequent layout-dependent step.
function ensureCrossSectionView(): void {
  ensureSectionExpanded('cross-section');
  ensureCrossSectionLayout();
}

// Resolve a cross-section control for a step's `element`. driver.js resolves the
// element (via this function) *before* it runs `onHighlightStarted`, so the
// reset has to happen here, not in the hook: ensureCrossSectionView() restores
// the cross-section panel (the store re-renders synchronously) and only then do
// we query the now-present node. Returning null lets driver fall back to its
// dummy element, but after the reset the element exists.
// driver.js types `element` as `() => Element` but handles a null/undefined
// return at runtime (it falls back to a dummy element), so the cast is safe.
function vizElement(selector: string): () => Element {
  return (() => {
    ensureCrossSectionView();
    return document.querySelector(selector);
  }) as () => Element;
}

// Stable reference so watchLayerToggleStep can recognise the layers step.
const layerTogglesElement = vizElement('.viz-layer-toggles');

// The per-layer ⓘ buttons are gone (#591): help is one labelled "About …"
// button per family, and it lives in the detail row.
//
// Two things have to be true before that button exists, and neither holds by
// default. Compact is the default display mode, and it deliberately has no
// detail row at all — it makes the method choice for you, so there is nothing
// to expand. And even in full mode the row starts closed.
//
// So switch to full and open a family, the same way ensureCrossSectionLayout
// already forces the layout: driver.js resolves `element` BEFORE running
// onHighlightStarted, so the setup belongs in the resolver, not the hook.
function ensureFullDisplayMode(): void {
  const state = briefingStore.getState();
  if (state.displayMode !== 'full') state.setDisplayMode('full');
}

function aboutButtonElement(): () => Element {
  return (() => {
    ensureCrossSectionView();
    ensureFullDisplayMode();
    if (!document.querySelector('.viz-about-btn')) {
      document.querySelector<HTMLElement>('.viz-family[data-family]')?.click();
    }
    return document.querySelector('.viz-about-btn');
  }) as () => Element;
}

function buildSteps(): DriveStep[] {
  return [
    {
      popover: {
        title: t('tour.welcome.title'),
        description: t('tour.welcome.desc'),
      },
    },
    {
      element: '#display-mode-toggle',
      popover: {
        title: t('tour.displayMode.title'),
        description: t('tour.displayMode.desc'),
        side: 'bottom',
        align: 'end',
      },
    },
    {
      element: '#assessment-banner',
      popover: {
        title: t('tour.assessment.title'),
        description: t('tour.assessment.desc'),
        side: 'bottom',
        align: 'start',
      },
    },
    {
      element: '#advisories-wrapper',
      popover: {
        title: t('tour.advisories.title'),
        description: t('tour.advisories.desc'),
        side: 'bottom',
        align: 'start',
      },
      onHighlightStarted: () => ensureSectionExpanded('advisories'),
    },
    {
      element: '#viz-section',
      popover: {
        title: t('tour.crossSection.title'),
        description: t('tour.crossSection.desc'),
        side: 'top',
        align: 'center',
      },
      onHighlightStarted: () => ensureCrossSectionView(),
    },
    {
      // element fns reset the layout to cross-section before resolving, so these
      // steps work even if the user just switched to Compare (see vizElement).
      element: layerTogglesElement,
      popover: {
        title: t('tour.layers.title'),
        description: t('tour.layers.desc'),
        side: 'bottom',
        align: 'start',
      },
      // Only step where the user is meant to click the underlying control.
      disableActiveInteraction: false,
    },
    {
      element: vizElement('.viz-toolbar-top'),
      popover: {
        title: t('tour.modelTheme.title'),
        description: t('tour.modelTheme.desc'),
        side: 'bottom',
        align: 'start',
      },
    },
    {
      element: aboutButtonElement(),
      popover: {
        title: t('tour.layerInfo.title'),
        description: t('tour.layerInfo.desc'),
        side: 'left',
        align: 'start',
      },
    },
    {
      element: '#route-graph-controls',
      popover: {
        title: t('tour.routeGraph.title'),
        description: t('tour.routeGraph.desc'),
        side: 'top',
        align: 'start',
      },
    },
    {
      element: '[data-section="skewt"]',
      popover: {
        title: t('tour.skewt.title'),
        description: t('tour.skewt.desc'),
        side: 'top',
        align: 'center',
      },
      onHighlightStarted: () => ensureSectionExpanded('skewt'),
    },
    {
      popover: {
        title: t('tour.done.title'),
        description: t('tour.done.desc'),
      },
    },
  ];
}

let activeDriver: Driver | null = null;
let layerToggleUnsub: (() => void) | null = null;
let layoutShiftFrame: number | null = null;
let lastHighlightRect: string | null = null;

// driver.js recomputes its cutout on window `resize` and `scroll` only. That
// misses most of the ways this page moves a highlighted control:
//
//  - the stale-pack "Updates available" banner arrives asynchronously and
//    inserts itself ABOVE the display-mode toggle, pushing it down;
//  - `.briefing-rail` — which holds that toggle — is `position: sticky` with
//    `overflow-y: auto` and a max-height, so it is its own scroll container.
//    Scroll events do not bubble, so scrolling the rail never reaches window;
//  - content growing inside that fixed-height rail (advisories filling in)
//    moves the toggle while resizing nothing at all: the rail's box is capped,
//    body's box is unchanged, and the element's own box is the same.
//
// The first attempt at this watched `document.body` with a ResizeObserver,
// which only catches the first case — and even then only because the document
// happens to grow. Rather than keep enumerating causes, watch the SYMPTOM: has
// the highlighted element moved? A per-frame rect comparison catches every one
// of them, costs a `getBoundingClientRect` per frame, and runs only while the
// tour is open.
function watchLayoutShifts(): void {
  stopWatchingLayoutShifts();
  lastHighlightRect = null;
  const tick = (): void => {
    if (!activeDriver) return;
    const el = document.querySelector('.driver-active-element');
    if (el) {
      const r = el.getBoundingClientRect();
      // Rounded so sub-pixel jitter during driver's own transition does not
      // trigger a refresh on every frame.
      const key = `${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.width)},${Math.round(r.height)}`;
      if (lastHighlightRect !== null && key !== lastHighlightRect) activeDriver.refresh();
      lastHighlightRect = key;
    } else {
      lastHighlightRect = null;
    }
    layoutShiftFrame = requestAnimationFrame(tick);
  };
  layoutShiftFrame = requestAnimationFrame(tick);
}

function stopWatchingLayoutShifts(): void {
  if (layoutShiftFrame !== null) cancelAnimationFrame(layoutShiftFrame);
  layoutShiftFrame = null;
  lastHighlightRect = null;
}

// Toggling a layer checkbox rebuilds the controls panel, which detaches the
// `.viz-layer-toggles` node driver.js highlighted and shifts the freshly
// rendered checkboxes outside the now-stale cutout — so only the first click
// "works", the rest land on the dimmed overlay. While the tour sits on the
// layers step, re-highlight it after every viz change so the cutout re-resolves
// to the new toggles and stays interactive.
//
// The re-highlight is deferred to the next frame on purpose: the store notifies
// listeners synchronously and in registration order, so re-resolving the
// toggles inline can race the app's own render listener and latch onto the old
// (about-to-be-detached) node. By rAF time the panel has been rebuilt, so
// `moveTo` (which re-runs the step's element resolver) finds the fresh node.
function onLayersStep(): boolean {
  return !!activeDriver && activeDriver.getActiveStep()?.element === layerTogglesElement;
}

function reHighlightLayersStep(): void {
  if (!onLayersStep()) return;
  const idx = activeDriver!.getActiveIndex();
  if (idx == null) return;
  requestAnimationFrame(() => {
    if (onLayersStep()) activeDriver!.moveTo(idx);
  });
}

function watchLayerToggleStep(): void {
  layerToggleUnsub?.();
  const onStoreChange = briefingStore.subscribe((state, prev) => {
    if (state.vizSettings === prev.vizSettings) return;
    reHighlightLayersStep();
  });
  // Opening or closing a family swaps the bar's subtree without touching the
  // store, so the subscription above never fires for it. Without this the
  // cutout latches onto a detached node and every click after the first lands
  // on the dimmed overlay.
  const onBarRerender = (): void => reHighlightLayersStep();
  window.addEventListener(VIZ_LAYER_BAR_RERENDER, onBarRerender);

  layerToggleUnsub = () => {
    onStoreChange();
    window.removeEventListener(VIZ_LAYER_BAR_RERENDER, onBarRerender);
  };
}

function stopWatchingLayerToggleStep(): void {
  layerToggleUnsub?.();
  layerToggleUnsub = null;
}

// driver.js types onHighlightStarted as returning void and never awaits the
// returned promise, so per-step async prep can't block the highlight. Open
// the mid-route sounding here so it's loaded by the time the user arrives.
function preloadSkewT(): void {
  const state = briefingStore.getState();
  if (state.selectedPointIndex == null && state.routeAnalyses) {
    const n = state.routeAnalyses.analyses?.length ?? 0;
    if (n > 0) state.setSelectedPoint(Math.floor(n / 2));
  }
}

export function startBriefingTour(): void {
  markOffered('briefing');
  if (activeDriver) {
    activeDriver.destroy();
    activeDriver = null;
  }
  stopWatchingLayerToggleStep();
  stopWatchingLayoutShifts();
  preloadSkewT();
  track(EVENTS.TOUR_STARTED, { tour: 'briefing' });
  activeDriver = driver({
    showProgress: true,
    allowClose: true,
    steps: buildSteps(),
    // Overriding onDestroyStarted means driver.js no longer auto-closes, so
    // we must call destroy() ourselves. Reaching the last step (Done, or
    // closing while on it) counts as completed; closing earlier does not.
    onDestroyStarted: () => {
      if (activeDriver?.isLastStep()) {
        track(EVENTS.TOUR_COMPLETED, { tour: 'briefing' });
      }
      activeDriver?.destroy();
    },
    onDestroyed: () => {
      activeDriver = null;
      stopWatchingLayerToggleStep();
      stopWatchingLayoutShifts();
    },
  });
  watchLayerToggleStep();
  watchLayoutShifts();
  activeDriver.drive();
}

export function maybeAutoStartBriefingTour(): void {
  const params = new URLSearchParams(window.location.search);
  if (params.get('tour') === '1') {
    requestAnimationFrame(() => startBriefingTour());
  }
}
