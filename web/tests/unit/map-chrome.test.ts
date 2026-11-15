/** Briefing route-map chrome (#998): what the overlay shows in each state. */

import { describe, it, expect } from 'vitest';
import {
  mapChromeHtml, formatFlightLevel, stepAltitude, layersSummary, type MapChromeState,
} from '../../ts/visualization/route-map/map-chrome';
import { getMapMetricById } from '../../ts/visualization/route-map/metrics';
import { t } from '../../ts/i18n/i18n';

function state(over: Partial<MapChromeState> = {}): MapChromeState {
  return {
    hidden: false,
    metric: getMapMetricById('icing-risk-at-level')!,
    altitudeFt: 8000,
    ceilingFt: 12000,
    models: ['ecmwf', 'gfs'],
    selectedModel: 'ecmwf',
    airports: null,
    observed: null,
    fronts: null,
    ...over,
  };
}

describe('altitude helpers', () => {
  it('formats flight levels', () => {
    expect(formatFlightLevel(8000)).toBe('FL080');
    expect(formatFlightLevel(50)).toBe('SFC');
    expect(formatFlightLevel(10500)).toBe('FL105');
  });

  it('steps 500 ft and clamps to 0 … ceiling', () => {
    expect(stepAltitude(8000, 1, 12000)).toBe(8500);
    expect(stepAltitude(8000, -1, 12000)).toBe(7500);
    expect(stepAltitude(0, -1, 12000)).toBe(0);
    expect(stepAltitude(12000, 1, 12000)).toBe(12000);
    // An off-grid cruise snaps first.
    expect(stepAltitude(8240, 1, 12000)).toBe(8500);
  });
});

describe('mapChromeHtml', () => {
  it('shows the stepper only for a metric that uses altitude', () => {
    expect(mapChromeHtml(state())).toContain('id="rm-alt-reset"');
    expect(mapChromeHtml(state())).toContain('FL080');
    const surface = mapChromeHtml(state({ metric: getMapMetricById('cloud-cover-total')! }));
    expect(surface).not.toContain('rm-alt-reset');
  });

  it('disables the arrows at the ends', () => {
    expect(mapChromeHtml(state({ altitudeFt: 0 }))).toMatch(/id="rm-alt-down"[^>]*disabled/);
    expect(mapChromeHtml(state({ altitudeFt: 12000 }))).toMatch(/id="rm-alt-up"[^>]*disabled/);
  });

  it('groups SHOWING into At altitude and Along route, with no Width', () => {
    const html = mapChromeHtml(state());
    expect(html).toContain('<optgroup label="At altitude">');
    expect(html).toContain('<optgroup label="Along route">');
    expect(html).toContain('value="icing-risk-at-level" selected');
    expect(html).not.toContain('map-width-metric');
  });

  it('hidden leaves only the pill, with the level when it matters', () => {
    const html = mapChromeHtml(state({ hidden: true }));
    expect(html).toContain('id="rm-show"');
    expect(html).toContain(`FL080 · ${t('map.ctl.show')}`);
    expect(html).not.toContain('rm-panel');
    const surface = mapChromeHtml(state({ hidden: true, metric: getMapMetricById('cape')! }));
    expect(surface).toContain(`>${t('map.ctl.show')}<`);
  });

  it('offers LAYERS only for what the briefing has', () => {
    expect(mapChromeHtml(state())).not.toContain('id="rm-layers"');
    const html = mapChromeHtml(state({
      fronts: false,
      observed: { options: [{ id: '', label: 'None' }, { id: 'opera_dbzh', label: 'Radar reflectivity' }],
        selected: 'opera_dbzh', satellite: true, lightning: null, cells: false, opacity: 0.75 },
    }));
    expect(html).toContain('id="rm-layers"');
    expect(html).toContain('id="rm-observed-source"');
    expect(html).toContain('id="rm-fronts"');
    // No lightning in this briefing: no toggle for it.
    expect(html).not.toContain('id="rm-lightning"');
    expect(html).not.toContain('id="rm-airports"');
  });

  it('draws the airport legend line and valid time while the overlay is on', () => {
    const airports = {
      visible: true, metric: 'flight_category', modelSupported: true, model: 'ecmwf',
      timeLabel: 'Wed 12Z', fullMapUrl: 'maps.html?fc.day=1',
      legend: { title: 'Flight category', items: [{ color: '#22c55e', label: 'VFR' }] },
    };
    const html = mapChromeHtml(state({ airports }));
    expect(html).toContain('rm-legend-airports');
    expect(html).toContain('id="map-forecast-time">Wed 12Z<');
    expect(html).toContain('id="rm-open-map"');
    expect(html).toContain(`${t('viz.airports.openMap')}</span>`);
    const off = mapChromeHtml(state({ airports: { ...airports, visible: false } }));
    expect(off).not.toContain('rm-legend-airports');
  });

  it('says the model has no airport data instead of an empty overlay', () => {
    const html = mapChromeHtml(state({ airports: {
      visible: true, metric: 'flight_category', modelSupported: false, model: 'gfs', legend: null,
    } }));
    expect(html).toContain('rm-note');
    expect(html).not.toContain('id="rm-airport-metric"');
  });

  it('hides MODEL with a single model', () => {
    expect(mapChromeHtml(state({ models: ['ecmwf'] }))).not.toContain('id="rm-model"');
  });
});

describe('layersSummary', () => {
  it('names what is on, or None', () => {
    expect(layersSummary(state())).toBe(t('map.ctl.layersNone'));
    expect(layersSummary(state({ fronts: true }))).toBe(t('viz.fronts'));
  });
});
