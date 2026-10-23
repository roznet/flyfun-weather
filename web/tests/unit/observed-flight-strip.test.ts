/** The flight strip and the ribbon's position caption (#816). Every word is
 *  the server's. ("Destination at your ETA" became the arrival airport card,
 *  #881: tests/unit/airport-card.test.ts.) */

import { describe, it, expect, vi } from 'vitest';
import en from '../../ts/i18n/locales/en.json';

vi.mock('../../ts/i18n/i18n', () => ({
  t: (key: string, params?: Record<string, string | number>) => {
    let msg = (en as Record<string, string>)[key] ?? key;
    for (const [k, v] of Object.entries(params ?? {})) msg = msg.split(`{${k}}`).join(String(v));
    return msg;
  },
}));

import type { LiveGlanceFlight, LiveRibbon } from '../../ts/store/types';
import { flightStripHtml } from '../../ts/visualization/observed/nutshell-view';
import { ribbonSvg } from '../../ts/visualization/observed/ribbon-view';

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

describe('ribbon position caption', () => {
  const ribbon = {
    route_nm: 154, flown_nm: 60, segment_nm: 10, waypoints: [], segments: [], stations: [], sigmets: [],
  } as unknown as LiveRibbon;

  it('labels the marker from the strip, else as before', () => {
    expect(ribbonSvg(ribbon, [], 30, 520, false, 'You · 4 min ago')).toContain('aria-label="You · 4 min ago"');
    expect(ribbonSvg(ribbon, [], 30, 520)).toContain('aria-label="Planned position now"');
  });
});
