/** Tests for the route-map legend grid (#731): pickers in the legend rows,
 *  stop labels aligned to swatches, and a Width row that draws thickness. */

import { describe, it, expect } from 'vitest';
import { mapLegendHtml, widthBarPx } from '../../ts/visualization/route-map/legend';
import { getMapMetricById, MAP_METRICS, MAP_METRIC_NONE } from '../../ts/visualization/route-map/metrics';
import { t } from '../../ts/i18n/i18n';

function metric(id: string) {
  const m = getMapMetricById(id);
  if (!m) throw new Error(`map metric ${id} not found`);
  return m;
}

function legend(colorId: string, widthId: string) {
  return mapLegendHtml({
    colorMetricId: colorId,
    widthMetricId: widthId,
    colorMetric: getMapMetricById(colorId) ?? null,
    widthMetric: widthId === MAP_METRIC_NONE ? null : (getMapMetricById(widthId) ?? null),
  });
}

/** The inner HTML of the scale cell for one channel. */
function scale(html: string, channel: 'color' | 'width'): string {
  const m = html.match(new RegExp(`<div class="map-legend-scale map-legend-scale-${channel}">(.*?)</div>(?=<label|</div>$)`));
  if (!m) throw new Error(`no ${channel} scale in ${html}`);
  return m[1];
}

const count = (s: string, re: RegExp) => (s.match(re) ?? []).length;

describe('mapLegendHtml', () => {
  it('renders both pickers with the stored selection, in the legend', () => {
    const html = legend('icing-risk-at-level', 'convective-risk');
    expect(html).toContain('id="map-color-metric"');
    expect(html).toContain('id="map-width-metric"');
    expect(html).toMatch(/id="map-color-metric"[^]*?<option value="icing-risk-at-level" selected>/);
    expect(html).toMatch(/id="map-width-metric"[^]*?<option value="convective-risk" selected>/);
  });

  it('names each metric only in its picker — no "Color: X" text label', () => {
    const html = legend('icing-risk-at-level', 'convective-risk');
    const outsidePickers = html.replace(/<select[^]*?<\/select>/g, '');
    for (const id of ['icing-risk-at-level', 'convective-risk']) {
      expect(outsidePickers).not.toContain(metric(id).label);
      expect(outsidePickers).not.toContain(t(`map.${id}`));
    }
  });

  it.each(MAP_METRICS.map((m) => m.id))('%s: one label per swatch, on one column template', (id) => {
    const stops = metric(id).legendStops.length;
    const html = legend(id, id);
    for (const channel of ['color', 'width'] as const) {
      const s = scale(html, channel);
      const swatchClass = channel === 'color' ? 'map-legend-stop' : 'map-legend-width-cell';
      expect(count(s, new RegExp(`class="${swatchClass}"`, 'g'))).toBe(stops);
      expect(count(s, /<span>/g)).toBe(stops);
      expect(count(s, new RegExp(`grid-template-columns:repeat\\(${stops},1fr\\)`, 'g'))).toBe(2);
    }
  });

  it('width row draws thickness from getWidth, not colour', () => {
    const m = metric('convective-risk');
    const s = scale(legend('icing-risk-at-level', 'convective-risk'), 'width');
    expect(s).not.toContain('background:');
    const heights = [...s.matchAll(/height:([\d.]+)px/g)].map((x) => Number(x[1]));
    expect(heights).toEqual(m.legendStops.map((st) => widthBarPx(m.getWidth(st.value))));
    // Convective width grows with risk: thin → thick.
    for (let i = 1; i < heights.length; i++) expect(heights[i]).toBeGreaterThan(heights[i - 1]);
  });

  it('inverted-width metrics read thick-first (low ceiling = thick)', () => {
    const s = scale(legend('icing-risk-at-level', 'nwp-ceiling'), 'width');
    const heights = [...s.matchAll(/height:([\d.]+)px/g)].map((x) => Number(x[1]));
    expect(heights[0]).toBeGreaterThan(heights[heights.length - 1]);
  });

  it('escapes stop labels (the ceiling legend carries "<")', () => {
    const s = scale(legend('nwp-ceiling', MAP_METRIC_NONE), 'color');
    expect(s).toContain('<span>LIFR &lt;500</span>');
    expect(s).not.toContain('LIFR <500');
  });

  it('width "none" keeps the picker but draws no scale', () => {
    const html = legend('cape', MAP_METRIC_NONE);
    expect(html).toMatch(/<option value="none" selected>/);
    expect(scale(html, 'width')).toBe('');
  });

  it('width bars fit the 16px row for every metric', () => {
    for (const m of MAP_METRICS) {
      for (const st of m.legendStops) {
        const h = widthBarPx(m.getWidth(st.value));
        expect(h).toBeGreaterThanOrEqual(2);
        expect(h).toBeLessThanOrEqual(16);
      }
    }
  });
});
