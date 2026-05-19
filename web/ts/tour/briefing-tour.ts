import { driver, type DriveStep, type Driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import { briefingStore } from '../store/briefing-store';
import { t } from '../i18n/i18n';
import { markOffered } from './tour-storage';

function ensureSectionExpanded(sectionAttr: string): void {
  const wrapper = document.querySelector<HTMLElement>(`[data-section="${sectionAttr}"]`);
  if (wrapper && wrapper.classList.contains('collapsed')) {
    const header = wrapper.querySelector<HTMLElement>('h3');
    header?.click();
  }
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
      onHighlightStarted: () => ensureSectionExpanded('cross-section'),
    },
    {
      element: '.viz-layer-toggles',
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
      element: '.viz-toolbar-top',
      popover: {
        title: t('tour.modelTheme.title'),
        description: t('tour.modelTheme.desc'),
        side: 'bottom',
        align: 'start',
      },
      onHighlightStarted: () => ensureSectionExpanded('cross-section'),
    },
    {
      // `[data-layer-info]` excludes the per-group ⓘ buttons (those also carry
      // `viz-group-info-btn`); we want the first per-layer info button.
      element: '[data-layer-info]',
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
  markOffered();
  if (activeDriver) {
    activeDriver.destroy();
    activeDriver = null;
  }
  preloadSkewT();
  activeDriver = driver({
    showProgress: true,
    allowClose: true,
    steps: buildSteps(),
    onDestroyed: () => {
      activeDriver = null;
    },
  });
  activeDriver.drive();
}

export function maybeAutoStartBriefingTour(): void {
  const params = new URLSearchParams(window.location.search);
  if (params.get('tour') === '1') {
    requestAnimationFrame(() => startBriefingTour());
  }
}
