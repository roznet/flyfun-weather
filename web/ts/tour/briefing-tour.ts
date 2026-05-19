/**
 * Briefing-page guided tour.
 *
 * A prototype demonstrating the tour primitives we need:
 *   - plain popover (no anchor)
 *   - highlight + scroll-into-view (driver.js does this automatically)
 *   - highlight while leaving the underlying control interactive
 *     (so the user can actually toggle layers mid-tour)
 *   - programmatic driving — opening the Skew-T by setting
 *     `selectedPointIndex` on the store rather than waiting for a click
 *
 * Extend by adding steps to BRIEFING_TOUR_STEPS. For other pages,
 * create a sibling file (e.g. flights-tour.ts) with its own step list.
 */

import { driver, type DriveStep, type Driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import { briefingStore } from '../store/briefing-store';

/** Wait until a selector resolves to an element, or give up after `timeoutMs`. */
function waitForElement(selector: string, timeoutMs = 3000): Promise<HTMLElement | null> {
  const found = document.querySelector<HTMLElement>(selector);
  if (found) return Promise.resolve(found);
  return new Promise((resolve) => {
    const start = Date.now();
    const interval = window.setInterval(() => {
      const el = document.querySelector<HTMLElement>(selector);
      if (el || Date.now() - start > timeoutMs) {
        window.clearInterval(interval);
        resolve(el);
      }
    }, 100);
  });
}

/** Expand a collapsed `<details>`-style section so the highlight can find it. */
function ensureSectionExpanded(sectionAttr: string): void {
  const wrapper = document.querySelector<HTMLElement>(`[data-section="${sectionAttr}"]`);
  if (wrapper && wrapper.classList.contains('collapsed')) {
    // Trigger the existing collapse toggle if present
    const header = wrapper.querySelector<HTMLElement>('h3');
    header?.click();
  }
}

/**
 * Step list. Each step is a driver.js DriveStep. We use `onHighlightStarted`
 * for "prep work" that has to happen before the step's element exists or is
 * visible (e.g. expanding a section, opening the Skew-T).
 */
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
    onHighlightStarted: async () => {
      // Drive the Skew-T open programmatically so the user sees it populated.
      // Pick a mid-route point if none is selected yet.
      const state = briefingStore.getState();
      if (state.selectedPointIndex == null && state.routeAnalyses) {
        const n = state.routeAnalyses.analyses?.length ?? 0;
        if (n > 0) state.setSelectedPoint(Math.floor(n / 2));
      }
      // Make sure the section is expanded and rendered before we highlight it.
      ensureSectionExpanded('skewt');
      await waitForElement('#skewt-canvas-container canvas', 2000);
    },
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

/** Start the briefing tour. Safe to call multiple times — re-entrant. */
export function startBriefingTour(): void {
  if (activeDriver) {
    activeDriver.destroy();
    activeDriver = null;
  }
  activeDriver = driver({
    showProgress: true,
    allowClose: true,
    // Let users interact with the highlighted element (toggle layers, click ⓘ).
    // The dimmed backdrop still blocks clicks outside the highlight.
    disableActiveInteraction: false,
    steps: BRIEFING_TOUR_STEPS,
    onDestroyed: () => {
      activeDriver = null;
    },
  });
  activeDriver.drive();
}

/**
 * Auto-start when `?tour=1` is in the URL. Call this after the briefing
 * has finished its initial render so the target elements exist.
 */
export function maybeAutoStartBriefingTour(): void {
  const params = new URLSearchParams(window.location.search);
  if (params.get('tour') === '1') {
    // One frame after render ensures canvas containers etc. are laid out.
    requestAnimationFrame(() => startBriefingTour());
  }
}
