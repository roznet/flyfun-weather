import { driver, type DriveStep, type Driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import { briefingStore } from '../store/briefing-store';

function ensureSectionExpanded(sectionAttr: string): void {
  const wrapper = document.querySelector<HTMLElement>(`[data-section="${sectionAttr}"]`);
  if (wrapper && wrapper.classList.contains('collapsed')) {
    const header = wrapper.querySelector<HTMLElement>('h3');
    header?.click();
  }
}

const BRIEFING_TOUR_STEPS: DriveStep[] = [
  {
    popover: {
      title: 'Welcome to your briefing',
      description:
        'This quick tour walks through the main parts of a flight briefing. ' +
        'You can hit Skip anytime — nothing here changes your flight.',
    },
  },
  {
    element: '#assessment-banner',
    popover: {
      title: 'Overall assessment',
      description:
        'The headline read on your flight: a colour-coded summary from the ' +
        'AI digest, combining advisories, observations, and forecast confidence.',
      side: 'bottom',
      align: 'start',
    },
  },
  {
    element: '#advisories-wrapper',
    popover: {
      title: 'Route advisories',
      description:
        'Deterministic hazard evaluators — icing, turbulence, convective, ' +
        'cloud, and model-agreement. Click any advisory for the underlying ' +
        'numbers and the rule that triggered it.',
      side: 'bottom',
      align: 'start',
    },
    onHighlightStarted: () => ensureSectionExpanded('advisories'),
  },
  {
    element: '#viz-section',
    popover: {
      title: 'Cross-section',
      description:
        'A vertical slice along your route. Clouds, icing, turbulence, ' +
        'convective columns, and terrain — all on the same time/altitude grid.',
      side: 'top',
      align: 'center',
    },
    onHighlightStarted: () => ensureSectionExpanded('cross-section'),
  },
  {
    element: '.viz-layer-toggles',
    popover: {
      title: 'Toggle layers',
      description:
        'Try it: click a checkbox to turn a layer on or off. The tour stays ' +
        'open — interact freely, then hit Next when you\'re ready.',
      side: 'bottom',
      align: 'start',
    },
    // The default driver.js overlay blocks clicks; we override per-step
    // via `disableActiveInteraction: false` (set at driver-instance level below).
  },
  {
    element: '.viz-layer-info-btn',
    popover: {
      title: 'Info on any metric',
      description:
        'Every layer and metric has a ⓘ button. Click it for the formula, ' +
        'units, source model, and a colour-scale legend.',
      side: 'left',
      align: 'start',
    },
  },
  {
    element: '#route-graph-controls',
    popover: {
      title: 'Route graph metrics',
      description:
        'Below the cross-section is a scalar graph along the route — wind, ' +
        'CAPE, ceiling, and more. Use these selectors to pick what to plot ' +
        'on the left and right axes.',
      side: 'top',
      align: 'start',
    },
  },
  {
    element: '[data-section="skewt"]',
    popover: {
      title: 'Skew-T at any point',
      description:
        'Click any point on the cross-section to inspect its full sounding ' +
        'here — temperature, dewpoint, parcel path, CAPE/CIN, and overlay ' +
        'bands showing where clouds and icing live.',
      side: 'top',
      align: 'center',
    },
    onHighlightStarted: () => ensureSectionExpanded('skewt'),
  },
  {
    popover: {
      title: 'That\'s the tour',
      description:
        'Synopsis, GRAMET, Sounding Analysis, and Model Comparison are below ' +
        'if you want more depth. Have a safe flight.',
    },
  },
];

let activeDriver: Driver | null = null;

// Preload the Skew-T at tour start. driver.js types onHighlightStarted as
// returning void and never awaits the returned promise, so per-step async
// prep can't actually block the highlight. By the time the user has clicked
// through the earlier steps, the canvas is rendered and ready.
function preloadSkewT(): void {
  const state = briefingStore.getState();
  if (state.selectedPointIndex == null && state.routeAnalyses) {
    const n = state.routeAnalyses.analyses?.length ?? 0;
    if (n > 0) state.setSelectedPoint(Math.floor(n / 2));
  }
}

export function startBriefingTour(): void {
  if (activeDriver) {
    activeDriver.destroy();
    activeDriver = null;
  }
  preloadSkewT();
  activeDriver = driver({
    showProgress: true,
    allowClose: true,
    // Let users interact with the highlighted element (toggle layers, click ⓘ).
    disableActiveInteraction: false,
    steps: BRIEFING_TOUR_STEPS,
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
