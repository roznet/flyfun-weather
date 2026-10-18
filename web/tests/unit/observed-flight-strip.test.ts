/** The flight strip, "Destination at your ETA" and the ribbon's position
 *  caption (#816). Every word is the server's; the client adds the title,
 *  the wind advisory colour and the taps. */

import { describe, it, expect, vi } from 'vitest';
import en from '../../ts/i18n/locales/en.json';

vi.mock('../../ts/i18n/i18n', () => ({
  t: (key: string) => (en as Record<string, string>)[key] ?? key,
}));

import type { LiveGlanceDestination, LiveGlanceFlight, LiveRibbon } from '../../ts/store/types';
import { destinationHtml, flightStripHtml } from '../../ts/visualization/observed/nutshell-view';
import { ribbonSvg } from '../../ts/visualization/observed/ribbon-view';

const focus = { kind: 'storm', id: 'core41-zz', bbox: [0, 49, 1, 50], layers: ['cells'], time: null } as const;

const dest: LiveGlanceDestination = {
  icao: 'ZZDS', name: 'Zedville', eta: '2026-10-06T13:30:00Z',
  metar_text: 'ZZDS VFR', taf_text: 'TAF at ETA VFR, PROB30 MVFR TS',
  wind_text: 'Wind at ETA 240° 18G28 kt · RWY 27 crosswind 12 kt', wind_advisory: 'amber',
  cells_text: 'nearest cell 7 NM SW (52 dBZ, lightning), closing 6 kt now',
  cells: [
    { id: 'core41-zz', text: '7 NM SW · 52 dBZ, lightning · closing 6 kt', focus },
    { id: 'core35-zz', text: '18 NM N · 40 dBZ · nearly stationary', focus: null },
  ],
  lightning_text: 'lightning ≤20 NM (3 flashes, nearest 12 NM)',
  unavailable: [],
  focus: { kind: 'station', id: 'ZZDS', bbox: [3, 49, 5, 51], layers: ['route'], time: null },
};

describe('flight strip', () => {
  it('shows the server line with a phase class, escaped', () => {
    const flight: LiveGlanceFlight = {
      phase: 'enroute', source: 'app', marker_label: 'You · 4 min ago',
      text: '120 of 280 NM · ETA ZZDS 10:32Z (+12 min vs plan) · position 4 min ago <b>',
    };
    const html = flightStripHtml(flight);
    expect(html).toContain('glance-flight-enroute');
    expect(html).toContain('position 4 min ago &lt;b&gt;');
  });
});

describe('destination at ETA', () => {
  it('titles the block, keeps every clause and colours the wind by its advisory', () => {
    const html = destinationHtml(dest);
    expect(html).toContain('Destination at your ETA · ZZDS Zedville · 13:30Z');
    for (const text of ['ZZDS VFR', 'TAF at ETA VFR, PROB30 MVFR TS', 'nearly stationary', '3 flashes']) {
      expect(html).toContain(text);
    }
    expect(html).toContain('glance-dest-wind-amber');
    expect(html).not.toMatch(/undefined|NaN|\[object/);
  });

  it('makes a cell a button only when it has a focus', () => {
    const html = destinationHtml(dest);
    expect(html.match(/data-dest-cell="/g)).toHaveLength(1);
    expect(html).toContain('data-dest-cell="0"');
    expect(html).toContain('data-dest-focus');
  });

  it('names what could not be read', () => {
    const html = destinationHtml({ ...dest, wind_text: null, unavailable: ['taf', 'storms'] });
    expect(html).toContain('taf, storms unavailable');
    expect(html).not.toContain('>Wind<');
  });
});

describe('ribbon position caption', () => {
  const ribbon = {
    route_nm: 154, flown_nm: 60, segment_nm: 10, waypoints: [], segments: [], stations: [], sigmets: [],
  } as unknown as LiveRibbon;

  it('labels the marker from the strip, else as before', () => {
    expect(ribbonSvg(ribbon, [], 30, 520, false, 'You · 4 min ago')).toContain('aria-label="You · 4 min ago"');
    expect(ribbonSvg(ribbon, [], 30, 520)).toContain('aria-label="Planned position now"');
  });
});
