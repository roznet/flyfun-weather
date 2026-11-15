/** Route-map legend (#998): the route metric's scale, one label centred under
 *  each swatch, plus the compact airport line. */

import { describe, it, expect } from 'vitest';
import { routeLegendHtml, airportLegendHtml } from '../../ts/visualization/route-map/legend';
import { getMapMetricById, MAP_METRICS } from '../../ts/visualization/route-map/metrics';

describe('routeLegendHtml', () => {
  it('is empty without a metric', () => {
    expect(routeLegendHtml(null)).toBe('');
  });

  for (const m of MAP_METRICS) {
    it(`${m.id}: one label per swatch, on one column template`, () => {
      const html = routeLegendHtml(m);
      const n = m.legendStops.length;
      expect(n).toBeGreaterThan(1);
      expect((html.match(/class="map-legend-stop"/g) ?? []).length).toBe(n);
      const labels = html.match(/<div class="map-legend-labels"[^>]*>(.*?)<\/div>/)?.[1] ?? '';
      expect((labels.match(/<span>/g) ?? []).length).toBe(n);
      expect((html.match(new RegExp(`repeat\\(${n},1fr\\)`, 'g')) ?? []).length).toBe(2);
    });
  }

  it('colours each swatch through the metric scale', () => {
    const m = getMapMetricById('nwp-ceiling')!;
    for (const stop of m.legendStops) {
      expect(stop.color).toBe(m.getColor(stop.value));
      expect(routeLegendHtml(m)).toContain(`background:${stop.color}`);
    }
  });

  it('escapes stop labels (the ceiling legend carries "<")', () => {
    const html = routeLegendHtml(getMapMetricById('nwp-ceiling')!);
    expect(html).not.toMatch(/<span><\d/);
    expect(html).toContain('&lt;');
  });

  it('every colour the ceiling metric draws is in its legend (#735)', () => {
    const m = getMapMetricById('nwp-ceiling')!;
    const legend = new Set(m.legendStops.map((s) => s.color));
    for (let ft = 0; ft <= 10000; ft += 50) expect(legend).toContain(m.getColor(ft));
  });
});

describe('airportLegendHtml', () => {
  it('is empty without items', () => {
    expect(airportLegendHtml(null)).toBe('');
    expect(airportLegendHtml({ title: 'X', items: [] })).toBe('');
  });

  it('draws a title and one dot per item, escaped', () => {
    const html = airportLegendHtml({ title: 'Cat <x>', items: [{ color: '#111', label: 'A' }, { color: '#222', label: 'B' }] });
    expect(html).toContain('Cat &lt;x&gt;');
    expect((html.match(/rm-legend-dot/g) ?? []).length).toBe(2);
  });
});
