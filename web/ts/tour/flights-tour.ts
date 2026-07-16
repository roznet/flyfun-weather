/** Guided tour for the flight-creation page (#400).
 *
 * A sibling of `briefing-tour.ts`, sharing the same driver.js machinery and the
 * generalized per-tour storage. Walks a pilot through the New Flight form:
 * aircraft + profile, route entry, the Interpret preview, the schedule row, and
 * the flexibility scan.
 */

import { driver, type DriveStep, type Driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import { t } from '../i18n/i18n';
import { markOffered } from './tour-storage';
import { track, EVENTS } from '../analytics/track';

// Resolve the .form-row / .form-group a control lives in. driver.js types
// `element` as `() => Element` but tolerates a null return at runtime (it falls
// back to a centered dummy element), so the cast is safe when the DOM differs.
function rowOf(selector: string): () => Element {
  return (() => document.querySelector(selector)?.closest('.form-row')) as () => Element;
}

function groupOf(selector: string): () => Element {
  return (() => document.querySelector(selector)?.closest('.form-group')) as () => Element;
}

function buildSteps(): DriveStep[] {
  return [
    {
      popover: {
        title: t('tour.flights.welcome.title'),
        description: t('tour.flights.welcome.desc'),
      },
    },
    {
      // Aircraft + profile share one form-row; highlight both together.
      element: rowOf('#input-aircraft'),
      popover: {
        title: t('tour.flights.config.title'),
        description: t('tour.flights.config.desc'),
        side: 'bottom',
        align: 'start',
      },
    },
    {
      element: rowOf('#input-waypoints'),
      popover: {
        title: t('tour.flights.route.title'),
        description: t('tour.flights.route.desc'),
        side: 'bottom',
        align: 'start',
      },
    },
    {
      element: '#btn-preview-route',
      popover: {
        title: t('tour.flights.interpret.title'),
        description: t('tour.flights.interpret.desc'),
        side: 'left',
        align: 'start',
      },
    },
    {
      // Date / time / altitude / ceiling / duration share the bottom form-row.
      element: rowOf('#input-date'),
      popover: {
        title: t('tour.flights.schedule.title'),
        description: t('tour.flights.schedule.desc'),
        side: 'top',
        align: 'start',
      },
    },
    {
      element: groupOf('#input-flexibility'),
      popover: {
        title: t('tour.flights.flexibility.title'),
        description: t('tour.flights.flexibility.desc'),
        side: 'top',
        align: 'end',
      },
    },
    {
      popover: {
        title: t('tour.flights.done.title'),
        description: t('tour.flights.done.desc'),
      },
    },
  ];
}

let activeDriver: Driver | null = null;

export function startFlightsTour(): void {
  markOffered('flights');
  if (activeDriver) {
    activeDriver.destroy();
    activeDriver = null;
  }
  track(EVENTS.TOUR_STARTED, { tour: 'flights' });
  activeDriver = driver({
    showProgress: true,
    allowClose: true,
    steps: buildSteps(),
    // Overriding onDestroyStarted means driver.js no longer auto-closes, so we
    // must call destroy() ourselves. Reaching the last step (Done, or closing
    // while on it) counts as completed; closing earlier does not.
    onDestroyStarted: () => {
      if (activeDriver?.isLastStep()) {
        track(EVENTS.TOUR_COMPLETED, { tour: 'flights' });
      }
      activeDriver?.destroy();
    },
    onDestroyed: () => {
      activeDriver = null;
    },
  });
  activeDriver.drive();
}

export function maybeAutoStartFlightsTour(): void {
  const params = new URLSearchParams(window.location.search);
  if (params.get('tour') === '1') {
    requestAnimationFrame(() => startFlightsTour());
  }
}
