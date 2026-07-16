import { driver, type DriveStep, type Driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import { briefingStore } from '../store/briefing-store';
import { t } from '../i18n/i18n';
import { markOffered } from './tour-storage';
import { track, EVENTS } from '../analytics/track';

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
      // `[data-layer-info]` excludes the per-group ⓘ buttons (those also carry
      // `viz-group-info-btn`); we want the first per-layer info button.
      element: vizElement('[data-layer-info]'),
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

function watchLayerToggleStep(): void {
  layerToggleUnsub?.();
  layerToggleUnsub = briefingStore.subscribe((state, prev) => {
    if (state.vizSettings === prev.vizSettings || !onLayersStep()) return;
    const idx = activeDriver!.getActiveIndex();
    if (idx == null) return;
    requestAnimationFrame(() => {
      if (onLayersStep()) activeDriver!.moveTo(idx);
    });
  });
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
  preloadSkewT();
  track(EVENTS.TOUR_STARTED);
  activeDriver = driver({
    showProgress: true,
    allowClose: true,
    steps: buildSteps(),
    // Overriding onDestroyStarted means driver.js no longer auto-closes, so
    // we must call destroy() ourselves. Reaching the last step (Done, or
    // closing while on it) counts as completed; closing earlier does not.
    onDestroyStarted: () => {
      if (activeDriver?.isLastStep()) {
        track(EVENTS.TOUR_COMPLETED);
      }
      activeDriver?.destroy();
    },
    onDestroyed: () => {
      activeDriver = null;
      stopWatchingLayerToggleStep();
    },
  });
  watchLayerToggleStep();
  activeDriver.drive();
}

export function maybeAutoStartBriefingTour(): void {
  const params = new URLSearchParams(window.location.search);
  if (params.get('tour') === '1') {
    requestAnimationFrame(() => startBriefingTour());
  }
}
