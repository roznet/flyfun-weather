/** Route ribbon hover tooltips (#742): content against the exported `/live`
 *  ticks (airports, raw METAR / TAF, SIGMETs), and the band hit test against
 *  the same rects `bandRects` draws. */

import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import type { LiveLayer, LiveRibbon, LiveStorm, RibbonWeather } from '../../ts/store/types';
import { bandRects } from '../../ts/visualization/observed/ribbon-core';
import {
  airportFor,
  bandAt,
  bandTooltipHtml,
  sigmetTooltipHtml,
  stationTooltipHtml,
  stormTooltipHtml,
} from '../../ts/visualization/observed/ribbon-tooltip';

const DIR = resolve(__dirname, '../../../app/flyfun-weather/flyfun-weatherUITests/LiveScenarios');
const WIDTH = 520;

function ticks(): { name: string; layer: LiveLayer }[] {
  return readdirSync(DIR)
    .filter((f) => f.endsWith('.json'))
    .sort()
    .map((name) => ({ name, layer: JSON.parse(readFileSync(resolve(DIR, name), 'utf8')) as LiveLayer }));
}

function band(over: Partial<RibbonWeather> = {}): RibbonWeather {
  return {
    id: 'b1', tier: 'rain', from_nm: 10, to_nm: 30, side: 'left',
    near_nm: 5, far_nm: 20, peak_dbz: 30, profile: [[15, -20, -5], [20, -18, -6], [25, -15, -5]], ...over,
  };
}

function ribbon(over: Partial<LiveRibbon> = {}): LiveRibbon {
  return {
    route_nm: 100, segment_nm: 25, radar_radius_nm: 10,
    weather_status: 'available', weather_corridor_nm: 30, weather_bin_nm: 5, ...over,
  };
}

const noLeak = /undefined|NaN|null|\[object/;

describe('ribbon tooltips: airports', () => {
  const all = ticks();

  it('finds the exported ticks', () => {
    expect(all.length).toBeGreaterThan(0);
  });

  it.each(all)('$name: every station names its airport and quotes its raw METAR / TAF', ({ layer }) => {
    const airports = layer.route_observations?.airports ?? [];
    for (const st of layer.ribbon?.stations ?? []) {
      const apt = airportFor(st, airports);
      const html = stationTooltipHtml(st, apt);
      expect(html).toContain(st.icao);
      expect(html).not.toMatch(noLeak);
      if (apt?.name) expect(html).toContain(apt.name.replace(/&/g, '&amp;').replace(/'/g, '&#39;'));
      if (apt?.metar_raw) expect(html).toContain('<code>');
      if (apt?.taf_raw) expect(html).toContain('TAF</span><code>');
    }
  });

  it('without a matching airport: the ribbon fields alone, no raw text', () => {
    const html = stationTooltipHtml({ icao: 'ZZZZ', role: 'route', metar_category: null, cross_nm: -4.6, along_nm: 40 }, null);
    expect(html).toContain('ZZZZ');
    expect(html).toContain('METAR now');
    expect(html).toContain('unavailable');
    expect(html).toContain('5 NM left of course at 40 NM');
    // Not joined is not "no report": the raw text is unknown, so nothing.
    expect(html).not.toContain('no report');
    expect(html).not.toContain('none issued');
    expect(html).not.toContain('<code>');
  });

  it('escapes the raw report', () => {
    const html = stationTooltipHtml(
      { icao: 'ZZAA', role: 'destination', metar_category: 'VFR' },
      { icao: 'ZZAA', name: 'Test <Field>', metar_raw: 'ZZAA 091200Z <script>', taf_raw: null } as never,
    );
    expect(html).toContain('Test &lt;Field&gt;');
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('none issued');
  });

  it.each(all)('$name: SIGMETs say what and when', ({ layer }) => {
    for (const s of layer.ribbon?.sigmets ?? []) {
      const html = sigmetTooltipHtml(s);
      expect(html).toContain('SIGMET');
      expect(html).not.toMatch(noLeak);
    }
  });
});

describe('ribbon tooltips: cells and bands', () => {
  it('a cell: strength, position, motion, lightning, top', () => {
    const storm: LiveStorm = {
      id: 'c1', cell_ids: ['c1'], lat: 0, lon: 0, peak_dbz: 47.4, intensity: 'very heavy',
      along_nm: 60, offtrack_nm: 8, cross_nm: -8, side: 'left',
      relative_motion: 'closing', closing_kt: 12, flashes: 3, top_fl: 310, trend: 'growing',
    };
    const html = stormTooltipHtml(storm);
    expect(html).toContain('Very heavy cell');
    expect(html).toContain('47 dBZ');
    expect(html).toContain('8 NM left of track at 60 NM');
    expect(html).toContain('closing 12 kt');
    expect(html).toContain('3 flashes');
    expect(html).toContain('FL310');
    expect(html).not.toMatch(noLeak);
  });

  it('a rain band: span, side, distance, motion', () => {
    const html = bandTooltipHtml(band({ motion_rel_deg: 80, speed_kt: 14 }));
    expect(html).toContain('Rain area');
    expect(html).toContain('10–30 NM');
    expect(html).toContain('5–20 NM left of course');
    expect(html).toContain('drifting toward the right of course, 14 kt');
    expect(html).not.toMatch(noLeak);
    expect(bandTooltipHtml(band({ side: 'both', tier: 'core' }))).toContain('across the track');
  });

  it('the hit test finds the band at the centre of every rect it draws', () => {
    const r = ribbon({ weather: [band()] });
    for (const rect of bandRects(r, WIDTH)) {
      expect(bandAt(r, rect.x + rect.width / 2, rect.y + rect.height / 2, WIDTH)?.id).toBe('b1');
    }
    // The other side of the line, and past its end: nothing.
    const [first] = bandRects(r, WIDTH);
    expect(bandAt(r, first.x + 1, 150, WIDTH)).toBeNull();
    expect(bandAt(r, WIDTH - 21, first.y + 1, WIDTH)).toBeNull();
  });

  it('a core inside a rain area wins: it is drawn on top', () => {
    const r = ribbon({
      weather: [
        band({ id: 'rain' }),
        band({ id: 'core', tier: 'core', profile: [[20, -14, -10]] }),
      ],
    });
    const core = bandRects(ribbon({ weather: [band({ id: 'core', tier: 'core', profile: [[20, -14, -10]] })] }), WIDTH)[0];
    expect(bandAt(r, core.x + core.width / 2, core.y + core.height / 2, WIDTH)?.id).toBe('core');
  });
});
