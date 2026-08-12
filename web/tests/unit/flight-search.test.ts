/** Flights-list route filter (#542).
 *
 *  The matcher cases mirror `tests/test_flight_search.py`: the same rule runs
 *  server-side for the paginated past section and here for future + recent, so
 *  the two implementations must agree. Keep the two case lists in step.
 */

import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { parseQuery, matchesQuery, matchedWaypointIndices } from '../../ts/helpers/flight-search';
import { flightRouteCompact } from '../../ts/utils';

describe('filter row visibility', () => {
  it('overrides display for the hidden attribute', () => {
    // The row is shown/hidden via the `hidden` attribute, but `.list-filter-row`
    // sets `display: flex` — an author rule, which beats the user-agent's
    // `[hidden] { display: none }`. Without an explicit override the attribute
    // is set and silently ignored, and the filter appears for short lists.
    // Nothing else in the suite can see this, hence the pin.
    const css = readFileSync(new URL('../../css/style.css', import.meta.url), 'utf8');
    expect(css).toMatch(/\.list-filter-row\[hidden\]\s*\{[^}]*display:\s*none/);
  });
});

describe('parseQuery', () => {
  it('treats blank input as no filter', () => {
    expect(parseQuery(null)).toEqual([]);
    expect(parseQuery(undefined)).toEqual([]);
    expect(parseQuery('')).toEqual([]);
    expect(parseQuery('   ')).toEqual([]);
  });

  it('uppercases and splits', () => {
    expect(parseQuery('lfmd egtf')).toEqual(['LFMD', 'EGTF']);
  });

  it('collapses runs of whitespace', () => {
    expect(parseQuery('  LFMD \t  EGTF \n')).toEqual(['LFMD', 'EGTF']);
  });
});

describe('matchesQuery', () => {
  const WPS = ['LFMD', 'MTL', 'POGOL', 'SITET', 'EGTF'];
  const m = (q: string, wps = WPS, name = '') => matchesQuery(wps, name, parseQuery(q));

  it('matches everything with no tokens', () => {
    expect(matchesQuery(WPS, '', [])).toBe(true);
  });

  it('matches endpoints', () => {
    expect(m('LFMD')).toBe(true);
    expect(m('EGTF')).toBe(true);
  });

  it('matches an intermediate waypoint', () => {
    expect(m('POGOL')).toBe(true);
  });

  it('is case insensitive', () => {
    expect(m('lfmd')).toBe(true);
  });

  it('prefix-matches, giving a country filter', () => {
    expect(m('LF')).toBe(true);
    expect(m('EG')).toBe(true);
    expect(m('ED')).toBe(false);
  });

  it('ANDs tokens together', () => {
    expect(m('LFMD EGTF')).toBe(true);
    expect(m('LFMD LFAT')).toBe(false);
  });

  it('ignores token order', () => {
    expect(m('EGTF LFMD')).toBe(true);
  });

  it('does not match on a suffix', () => {
    // Prefix-only, otherwise "LF" would stop meaning "France".
    expect(m('GTF')).toBe(false);
  });

  it('matches words in the route name', () => {
    expect(m('alps', [], 'Alps trip')).toBe(true);
    expect(m('trip', [], 'Alps trip')).toBe(true);
    expect(m('pyrenees', [], 'Alps trip')).toBe(false);
  });

  it('never matches with an empty haystack', () => {
    expect(m('LFMD', [], '')).toBe(false);
    expect(matchesQuery(undefined, undefined, parseQuery('LFMD'))).toBe(false);
  });

  it('ignores blank waypoints', () => {
    expect(m('LFMD', ['', 'LFMD'])).toBe(true);
  });
});

describe('matchedWaypointIndices', () => {
  it('is empty without tokens', () => {
    expect(matchedWaypointIndices(['LFMD'], []).size).toBe(0);
  });

  it('reports every matching position', () => {
    const hits = matchedWaypointIndices(['LFMD', 'MTL', 'LFQA', 'EGTF'], parseQuery('LF'));
    expect([...hits].sort()).toEqual([0, 2]);
  });
});

describe('flightRouteCompact with filter tokens', () => {
  const LONG = ['LFMD', 'MTL', 'LFQA', 'SITET', 'EGTF'];

  it('elides the middle when unfiltered', () => {
    const r = flightRouteCompact(LONG);
    expect(r.html).toBe('LFMD → MTL → … → SITET → EGTF');
    expect(r.isTruncated).toBe(true);
  });

  it('pulls a matched middle waypoint out of the ellipsis and bolds it', () => {
    // Without this the row surfaced by "LFQA" would render as
    // `LFMD → MTL → … → SITET → EGTF` and look unrelated to the query.
    const r = flightRouteCompact(LONG, 4, parseQuery('LFQA'));
    expect(r.html).toContain('<strong class="route-match">LFQA</strong>');
    expect(r.html).not.toContain('MTL → … → SITET');
  });

  it('keeps the full route text for the tooltip', () => {
    const r = flightRouteCompact(LONG, 4, parseQuery('LFQA'));
    expect(r.fullText).toBe('LFMD → MTL → LFQA → SITET → EGTF');
  });

  it('bolds matches on a short, untruncated route', () => {
    const r = flightRouteCompact(['LFMD', 'EGTF'], 4, parseQuery('LFMD'));
    expect(r.html).toBe('<strong class="route-match">LFMD</strong> → EGTF');
    expect(r.isTruncated).toBe(false);
  });

  it('drops the ellipsis when matches fill the gap', () => {
    const r = flightRouteCompact(LONG, 4, parseQuery('LFQA SITET'));
    expect(r.html).not.toContain('…');
    expect(r.isTruncated).toBe(false);
  });

  it('escapes waypoint text', () => {
    const r = flightRouteCompact(['<b>', 'EGTF'], 4, parseQuery('EGTF'));
    expect(r.html).toContain('&lt;b&gt;');
  });
});
