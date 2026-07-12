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
  overrides: Partial<AirportModelCondition> = {},
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
    ...overrides,
  };
}

function renderAirportConditions(
  arrivalCondition: AirportModelCondition = condition('ecmwf', undefined),
): string {
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
      conditions: [arrivalCondition],
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

  it('renders an evidence-free condition with a muted category and valid wind', () => {
    const html = renderAirportConditions(condition('ecmwf', false, {
      visibility_sm: null,
      wind_speed_kt: 35,
      wind_direction_deg: 270,
    }));
    const row = html.match(
      /<tr class="airport-condition-row">\s*<td class="airport-model">ECMWF<\/td>[\s\S]*?<\/tr>/,
    )?.[0];

    expect(row).toContain('badge-muted">N/A</span>');
    expect(row).not.toContain('flight-cat-vfr');
    expect(row).toContain('<td>vis N/A</td>');
    expect(row).toContain('<td>ceil N/A</td>');
    expect(row).toContain('270@35');
  });
});
