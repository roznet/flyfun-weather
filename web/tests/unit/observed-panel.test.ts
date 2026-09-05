import { afterEach, expect, it, vi } from 'vitest';
// Unrelated map library initializes browser layout at import; the real panel
// renderer does not call Leaflet. Keep all panel and formatting code real.
vi.mock('leaflet', () => ({}));

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it('refreshes source ages without rewriting saved summary text or using computed_at as observation time', async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-08-26T14:05:00Z'));
  // Minimal DOM boundary: retain text-node identities between updates so a
  // minute tick that replaces controls/links is observable, without mocking
  // the panel or its clock/formatters.
  let markup = '';
  let replacements = 0;
  let nodes: Array<{ dataset: Record<string, string>; textContent: string }> = [];
  const section = {
    isConnected: true,
    get innerHTML() { return markup; },
    set innerHTML(value: string) {
      markup = value;
      replacements++;
      nodes = [...value.matchAll(/<span[^>]*data-observed-time="([^"]*)"[^>]*>([^<]*)<\/span>/g)]
        .map(m => ({ dataset: { observedTime: m[1] }, textContent: m[2] }));
    },
    querySelectorAll: () => nodes,
  };
  const wrapper = { style: { display: '' } };
  const document = Object.assign(new EventTarget(), {
    visibilityState: 'visible',
    getElementById: (id: string) => id === 'observed-section' ? section : id === 'observed-wrapper' ? wrapper : null,
  });
  vi.stubGlobal('document', document);
  vi.stubGlobal('window', new EventTarget());
  const { renderObservedConditions } = await import('../../ts/managers/briefing-ui');
  const field = { source: 'opera_dbzh', valid_time: '2026-08-25T14:05:00Z', age_minutes: 0, window_minutes: 10,
    attribution: { text: 'OPERA', producer: null, license: null, url: null }, stations: [] };
  const snapshot = { observed_conditions: { has_any_field: true, computed_at: '2026-08-25T14:10:00Z',
    reflectivity: field, rain_rate: null, cloud_tops: null, lightning: null, sources: [], corridor_nm: 20,
    summary_entries: [{ kind: 'reflectivity', text: 'Peak echo (observed 0 min ago).', metric_id: '' }],
  } } as never;
  renderObservedConditions(snapshot);
  expect(section.innerHTML).toContain('1440 min old');
  expect(section.innerHTML).toContain('2026-08-25 14:05Z');
  expect(section.innerHTML).toContain('Saved summary');
  expect(section.innerHTML).toContain('Peak echo (observed 0 min ago).');
  const originalNodes = [...nodes];
  vi.advanceTimersByTime(60000);
  expect(nodes.some(node => node.textContent.includes('1441 min old'))).toBe(true);
  expect(nodes[0]).toBe(originalNodes[0]);
  expect(replacements).toBe(1);
  renderObservedConditions(null);
  expect(vi.getTimerCount()).toBe(0);
});
