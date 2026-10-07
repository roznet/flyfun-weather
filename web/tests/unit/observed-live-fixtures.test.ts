/** The Observed nutshell + ribbon against real server output (#690).
 *
 * These are the `/live` response bodies `scripts/export_live_scenario_ios.py`
 * writes by replaying a real flight morning through the production live layer.
 * `tests/test_live_scenarios.py::test_ios_live_fixtures_match_the_server`
 * fails when they drift from what the server would produce, so a server-side
 * change to `glance` / `ribbon` / `storms` lands here as a failing web test
 * rather than as a silently empty section in the browser.
 *
 * The iOS UI test feeds the SAME files to the app (`FLYFUN_MOCK_LIVE_JSON`).
 * One fixture, three consumers — that is the whole point.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import type { LiveLayer } from '../../ts/store/types';
import { nutshellHtml } from '../../ts/visualization/observed/nutshell-view';
import { ribbonLegendHtml, ribbonSvg, stormDetailHtml } from '../../ts/visualization/observed/ribbon-view';
import { weatherAvailable } from '../../ts/visualization/observed/ribbon-core';

const DIR = resolve(__dirname, '../../../app/flyfun-weather/flyfun-weatherUITests/LiveScenarios');

function ticks(): { name: string; layer: LiveLayer }[] {
  return readdirSync(DIR)
    .filter((f) => f.endsWith('.json'))
    .sort()
    .map((name) => ({ name, layer: JSON.parse(readFileSync(resolve(DIR, name), 'utf8')) as LiveLayer }));
}

describe('observed live fixtures', () => {
  const all = ticks();

  it('finds the exported ticks (a missing export dir would silently pass)', () => {
    expect(all.length).toBeGreaterThan(0);
  });

  it.each(all)('$name carries a glance and a ribbon', ({ layer }) => {
    expect(layer.glance).toBeTruthy();
    expect(layer.glance!.headline).toBeTruthy();
    expect(layer.glance!.lines.length).toBeGreaterThan(0);
    expect(layer.ribbon).toBeTruthy();
    expect(layer.ribbon!.route_nm).toBeGreaterThan(0);
  });

  it.each(all)('$name renders the nutshell with the server\'s own words', ({ layer }) => {
    const html = nutshellHtml(layer.glance!, true);
    // The text is the server's: not re-worded, not re-ordered, not dropped.
    for (const line of layer.glance!.lines) {
      // Compare on a decoration-free form: the markup escapes `&` and `<`.
      const plain = html.replace(/&amp;/g, '&').replace(/&#39;/g, "'").replace(/&quot;/g, '"');
      expect(plain).toContain(line.text);
    }
    expect(html).not.toMatch(/undefined|NaN|\[object/);
  });

  it.each(all)('$name renders a ribbon with no unresolved numbers', ({ layer }) => {
    const storms = layer.storms?.status === 'available' ? layer.storms.storms : [];
    const svg = ribbonSvg(layer.ribbon!, storms, layer.storms?.corridor_nm ?? 30, 520);
    expect(svg).toMatch(/^<svg /);
    // A NaN or undefined in an SVG attribute drops the whole element silently.
    expect(svg).not.toMatch(/undefined|NaN|\[object/);
    expect(svg).toContain('</svg>');
  });

  it.each(all)('$name draws every waypoint and station it was given', ({ layer }) => {
    const ribbon = layer.ribbon!;
    const svg = ribbonSvg(ribbon, [], 30, 520);
    for (const wp of ribbon.waypoints ?? []) {
      if (wp.along_nm == null) continue;
      expect(svg).toContain(`>${wp.icao}<`);
    }
    // One <g class="ribbon-station"> per station that has a position.
    const positioned = (ribbon.stations ?? []).filter(
      (s) => s.role === 'departure' || s.role === 'destination' || s.along_nm != null,
    );
    const drawn = svg.match(/class="ribbon-station/g) ?? [];
    expect(drawn.length).toBe(positioned.length);
  });

  it.each(all)('$name keys the ribbon to the lane it actually drew', ({ layer }) => {
    const ribbon = layer.ribbon!;
    const key = ribbonLegendHtml(weatherAvailable(ribbon));
    // The cells feed is dark in these fixtures, so the key must offer the
    // radar strip and NOT promise rain/cell bands that are not drawn.
    if (weatherAvailable(ribbon)) {
      expect(key).toContain('cells 35/41/50 dBZ');
    } else {
      expect(key).toContain('radar');
      expect(key).not.toContain('cells 35/41/50 dBZ');
    }
    expect(key).toContain('Left of course above the line, right below');
  });

  it('renders a cell detail without promoting the estimate out of its block', () => {
    const withStorm = all.find((t) => (t.layer.storms?.storms ?? []).length > 0);
    // The archived morning has a dark cells feed; a synthetic cell then covers
    // the detail panel, which is the only place the estimate may appear.
    const storm = withStorm?.layer.storms!.storms[0] ?? {
      id: 'c1', cell_ids: ['c1'], lat: 41.5, lon: 2.1, peak_dbz: 47,
      intensity: 'heavy', along_nm: 60, offtrack_nm: 25, cross_nm: -25,
      side: 'left' as const, relative_motion: 'closing' as const, closing_kt: 8,
      estimate: { cpa_nm: 12, cpa_time: '2026-10-02T09:05:00Z', horizon_min: 35 },
    };
    const html = stormDetailHtml(storm);
    expect(html).toContain('Estimate at current motion');
    expect(html).toContain('A projection, not an observation');
    expect(html).not.toMatch(/undefined|NaN|\[object/);
  });

  it.each(all)('$name never reports absent data as clear', ({ layer }) => {
    const html = nutshellHtml(layer.glance!, true);
    // #689: a source that could not be read says "unavailable". If the server
    // marked a phase's source missing, the rendered line must not read as a
    // clear sky at that phase.
    for (const line of layer.glance!.lines) {
      if ((line.unavailable ?? []).length === 0) continue;
      expect(html).toContain('unavailable');
    }
  });
});
