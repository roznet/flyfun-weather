import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderAdvisories } from '../../ts/managers/advisories-ui';
import type { AirportModelCondition } from '../../ts/types/advisories';
import { manifestWithTwoModels } from './fixtures/advisory-focus';

interface FakeElement {
  style: Record<string, string>;
  innerHTML: string;
  addEventListener: () => void;
  querySelectorAll: () => never[];
}

function fakeElement(): FakeElement {
  return {
    style: {},
    innerHTML: '',
    addEventListener: () => undefined,
    querySelectorAll: () => [],
  };
}

function condition(
  model: string,
  ceilingEvaluated: boolean | undefined,
): AirportModelCondition {
  return {
    model,
    flight_category: 'VFR',
    ceiling_ft: null,
    ceiling_evaluated: ceilingEvaluated,
    visibility_m: null,
    visibility_sm: 10,
    wind_speed_kt: null,
    wind_direction_deg: null,
    wind_gust_kt: null,
    best_runway: null,
    all_runways: [],
  };
}

function renderAirportConditions(): string {
  const section = fakeElement();
  const wrapper = fakeElement();
  vi.stubGlobal('document', {
    getElementById: vi.fn((id: string) => {
      if (id === 'advisories-section') return section;
      if (id === 'advisories-wrapper') return wrapper;
      return null;
    }),
    querySelector: vi.fn(() => null),
  });

  const manifest = manifestWithTwoModels();
  manifest.airport_conditions = {
    departure: {
      icao: 'EGTK',
      name: 'Oxford',
      runway_ends: [],
      conditions: [condition('gfs', true)],
    },
    arrival: {
      icao: 'LSGS',
      name: 'Sion',
      runway_ends: [],
      conditions: [condition('ecmwf', undefined)],
    },
  };

  renderAdvisories(manifest);
  return section.innerHTML;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('airport condition ceiling rendering', () => {
  it('distinguishes assessed clear from an unassessed null ceiling', () => {
    const html = renderAirportConditions();

    expect(html).toContain('airport-summary-detail">CLR · vis');
    expect(html).toContain('airport-summary-detail">N/A · vis');
    expect(html).toContain('<td>ceil CLR</td>');
    expect(html).toContain('<td>ceil N/A</td>');
  });
});
