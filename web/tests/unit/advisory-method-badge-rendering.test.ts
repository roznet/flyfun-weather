import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../ts/components/info-popup', () => ({
  initInfoPopup: vi.fn(),
  showMetricInfo: vi.fn(),
  showPopupContent: vi.fn(),
}));

import { renderAdvisories } from '../../ts/managers/advisories-ui';
import type { AdvisoryDataState, AdvisoryStatus } from '../../ts/types/advisories';
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

function renderWithRepresentative(
  dataState: AdvisoryDataState | null | undefined | 'future-state',
  status: AdvisoryStatus,
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
  const representative = manifest.advisories[0].per_model.find(
    model => model.model === manifest.advisories[0].representative_model,
  )!;
  representative.data_state = dataState as AdvisoryDataState;
  representative.status = status;

  renderAdvisories(manifest);
  return section.innerHTML;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('representative advisory method badge', () => {
  it.each([
    ['legacy', undefined, 'amber'],
    ['unknown', 'future-state', 'amber'],
    ['unavailable', 'unavailable', 'amber'],
    ['partial unavailable', 'partial', 'unavailable'],
  ] as const)('does not render for %s model results', (_label, dataState, status) => {
    expect(renderWithRepresentative(dataState, status))
      .not.toContain('advisory-method-badge');
  });

  it.each(['complete', 'partial'] as const)(
    'renders for an assessed %s hazard',
    (dataState) => {
      const html = renderWithRepresentative(dataState, 'amber');
      expect(html).toContain('advisory-method-badge');
      expect(html).toContain('>NWP</span>');
    },
  );
});
