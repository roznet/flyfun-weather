/** The departure / arrival airport cards (#881). Rendered from
 *  tests/fixtures/airport_cards.json, which the server's own models wrote
 *  (scripts/export_airport_card_fixtures.py): the card only lays out what the
 *  server sent, so every assertion is "this field shows up, in this place". */

import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import en from '../../ts/i18n/locales/en.json';

vi.mock('../../ts/i18n/i18n', () => ({
  t: (key: string, params?: Record<string, string | number>) => {
    let msg = (en as Record<string, string>)[key] ?? key;
    for (const [k, v] of Object.entries(params ?? {})) msg = msg.split(`{${k}}`).join(String(v));
    return msg;
  },
}));

import type { LiveGlance, LiveGlanceDeparture, LiveGlanceDestination } from '../../ts/store/types';
import {
  airportCardHtml, airportCardsHtml, cardFocus, tafStripHtml,
} from '../../ts/visualization/observed/airport-card-view';
import { nutshellHtml, offlineBannerHtml } from '../../ts/visualization/observed/nutshell-view';

interface Moment { departure: LiveGlanceDeparture; destination: LiveGlanceDestination }
const FIXTURE = JSON.parse(readFileSync(resolve(__dirname, '../fixtures/airport_cards.json'), 'utf8')) as
  { t0: string; before: Moment; after: Moment };
const NOW = Date.parse(FIXTURE.t0);
const { departure: dep, destination: dest } = FIXTURE.before;

const plain = (html: string): string =>
  html.replace(/&amp;/g, '&').replace(/&#39;/g, "'").replace(/&quot;/g, '"');

describe('airport card', () => {
  it('heads with role, field, METAR now → TAF at ETA and the worse TEMPO dashed', () => {
    const html = airportCardHtml(dest, 'ARR', NOW);
    expect(html).toContain('>Arrival<');
    expect(html).toContain('>EGLF<');
    expect(html).toContain('>Farnborough<');
    expect(html).toMatch(/ac-cat-vfr"><b>VFR<\/b><i>now<\/i>/);
    expect(html).toMatch(/ac-cat-mvfr"><b>MVFR<\/b><i>at ETA<\/i>/);
    expect(html).toMatch(/ac-cat-ifr ac-catbox-dashed"><b>IFR<\/b><i>TEMPO<\/i>/);
    expect(airportCardHtml(dep, 'DEP', NOW)).toContain('<i>at ETD</i>');
  });

  it('draws the server\'s meter positions, sky and visibility words', () => {
    const html = airportCardHtml(dep, 'DEP', NOW);
    expect(html).toContain('BKN 2,500 ft');
    expect(html).toContain('10 km+');
    expect(html).toContain(`left:${(dep.airport!.metar_ceiling_meter! * 100).toFixed(1)}%`);
    expect(html).toContain('left:100.0%');
    // Not CAVOK: the cloud icon, not the sun.
    expect(html).not.toContain('M12 2v2M12 20v2');
    const cavok = { ...dep, airport: { ...dep.airport!, metar_cavok: true, metar_sky_text: 'CAVOK' } };
    expect(airportCardHtml(cavok, 'DEP', NOW)).toContain('M12 2v2M12 20v2');
  });

  it('leaves the dot out when the server sent no position', () => {
    const html = airportCardHtml({ ...dep, airport: { ...dep.airport!, metar_ceiling_meter: null } }, 'DEP', NOW);
    expect(html.match(/ac-meter-dot/g)).toHaveLength(1);
  });

  it('lists the weather in words, with the wind, runway and toned crosswind', () => {
    const html = plain(airportCardHtml(dep, 'DEP', NOW));
    expect(html).toContain('Light rain');
    expect(html).toContain('290/15G25');
    expect(html).toContain('RWY 24');
    // Amber advisory → toned; "right" → ▶.
    expect(html).toMatch(/ac-xw ac-tone-amber"[^>]*>▶ 12G20 kt X/);
    // Green stays quiet.
    expect(airportCardHtml(dest, 'ARR', NOW)).toMatch(/class="ac-xw" [^>]*>▶ 6 kt X/);
  });

  it('says where the METAR is from, how old, SPECI, the trend and the ETA', () => {
    const d = plain(airportCardHtml(dep, 'DEP', NOW));
    expect(d).toContain('METAR 09:30Z');
    expect(d).toContain('data-live-age-short="2026-10-23T09:30:00Z"');
    expect(d).toContain('↘ was VFR 09:00Z');
    expect(d).toContain('ETD 10:00Z');
    expect(d).not.toContain('SPECI');
    const a = plain(airportCardHtml(dest, 'ARR', NOW));
    expect(a).toContain('>SPECI<');
    expect(a).toContain('ETA 11:00Z');
    expect(a).not.toContain('was VFR');
    // Fresh: not tinted; past 30 min it is.
    expect(d).not.toContain('ac-metar-time ac-stale');
    expect(airportCardHtml(dep, 'DEP', NOW + 45 * 60_000)).toContain('ac-metar-time ac-stale');
  });

  it('shows the server\'s "no TAF" reason when there is no category at the ETA', () => {
    const noTaf = {
      ...dest, taf_text: 'no TAF for ETA',
      airport: { ...dest.airport!, taf_prevailing_category_at_eta: null, taf_temporary_category_at_eta: null, taf_hours: [] },
    };
    const html = airportCardHtml(noTaf, 'ARR', NOW);
    expect(html).toContain('no TAF for ETA');
    expect(html).not.toContain('at ETA</i>');
    expect(html).not.toContain('card-taf-strip');
  });

  it('keeps the cells and lightning in the server\'s words, each tappable', () => {
    const html = plain(airportCardHtml(dest, 'ARR', NOW));
    expect(html).toContain(dest.cells_text);
    expect(html).toContain(dest.lightning_text!);
    expect(html).toContain('data-card-focus="arr"');
    expect(html).toContain('data-card-cell="arr:0"');
    expect(cardFocus(dep, dest, 'arr:0')).toEqual(dest.cells![0].focus);
    expect(cardFocus(dep, dest, 'dep')).toEqual(dep.focus);
    expect(cardFocus(dep, dest, 'dep:3')).toBeNull();
  });

  it('tones the cells / lightning row only when the server flags it', () => {
    const html = airportCardHtml(dest, 'ARR', NOW);
    expect(html).toMatch(/class="ac-row ac-near ac-tone-amber"[^>]*data-testid="card-cells"/);
    expect(html).toMatch(/class="ac-row ac-near ac-tone-amber"[^>]*data-testid="card-lightning"/);
    const quiet = airportCardHtml({ ...dest, cells_alert: false, lightning_alert: undefined }, 'ARR', NOW);
    expect(quiet).not.toContain('ac-near ac-tone-amber');
    expect(airportCardHtml(dep, 'DEP', NOW)).not.toContain('ac-near ac-tone-amber');
  });

  it('draws the dial at a fixed size, and none without runway data', () => {
    const html = airportCardHtml(dest, 'ARR', NOW);
    expect(html).toContain('<div class="ac-dial">');
    expect(html).toContain('rw-svg rw-compact');
    // Solid arrow = METAR, outline ghost = TAF at ETA.
    expect(html).toContain('rw-arrow rw-ghost');
    const bare = { ...dest, airport: { ...dest.airport!, runway_wind: null } };
    expect(airportCardHtml(bare, 'ARR', NOW)).not.toContain('ac-dial');
  });

  it('falls back to the block\'s lines without an airport row (older server)', () => {
    const html = plain(airportCardHtml({ ...dest, airport: null }, 'ARR', NOW));
    expect(html).toContain(dest.metar_text);
    expect(html).toContain(dest.wind_text!);
    expect(html).toMatch(/ac-cat-vfr"><b>VFR<\/b><i>now/);
    expect(html).not.toMatch(/undefined|NaN|\[object/);
  });

  it('never prints an unresolved value', () => {
    for (const html of [airportCardHtml(dep, 'DEP', NOW), airportCardHtml(dest, 'ARR', NOW)]) {
      expect(html).not.toMatch(/undefined|NaN|\[object|null/);
    }
  });
});

describe('TAF strip', () => {
  it('one cell per hour: prevailing fill, worse group hatched, the ETA outlined', () => {
    const html = tafStripHtml(dest.airport!.taf_hours!, 'ARR');
    expect(html.match(/ac-strip-h/g)).toHaveLength(dest.airport!.taf_hours!.length);
    expect(html.match(/ac-strip-eta/g)).toHaveLength(1);
    expect(html).toContain('>11Z<');
    expect(html).toContain('ac-strip-temp ac-cat-ifr');
    expect(html).toContain('outlined hour = ETA');
    expect(tafStripHtml([], 'ARR')).toBe('');
  });
});

describe('both cards', () => {
  it('departure then arrival while the departure is open', () => {
    const html = airportCardsHtml(dep, dest, { now: NOW });
    expect(html.indexOf('airport-card-dep')).toBeLessThan(html.indexOf('airport-card-arr'));
    expect(html).not.toContain('airport-card-folded');
  });

  it('after the fold: the arrival first, then the one-line departure', () => {
    const { departure, destination } = FIXTURE.after;
    const html = plain(airportCardsHtml(departure, destination, { now: NOW }));
    expect(html.indexOf('airport-card-arr')).toBeLessThan(html.indexOf('airport-card-folded'));
    expect(html).toContain('EGTF 290/15G25 · RWY 24 · departed 10:04Z');
    expect(html).toMatch(/<details class="ac-folded" data-card-fold data-testid="airport-card-folded">/);
    // Remembered open.
    expect(airportCardsHtml(departure, destination, { now: NOW, departureOpen: true }))
      .toContain('data-testid="airport-card-folded" open');
  });

  it('is empty without either block (an older server)', () => {
    expect(airportCardsHtml(null, undefined)).toBe('');
  });
});

describe('nutshell with cards', () => {
  const glance: LiveGlance = {
    as_of: FIXTURE.t0, headline: 'Observed 09:40Z · worse at the departure', comparison: 'worse',
    lines: [
      { phase: 'departure', icao: 'EGTF', alert: false, passed: false, text: 'EGTF MVFR line', unavailable: [], sources: [], focus: null },
      { phase: 'enroute', icao: null, alert: false, passed: false, text: 'Nothing on the route', unavailable: [], sources: [], focus: null },
      { phase: 'arrival', icao: 'EGLF', alert: false, passed: false, text: 'EGLF VFR line', unavailable: [], sources: [], focus: null },
    ],
    departure: dep, destination: dest,
  } as unknown as LiveGlance;

  it('the cards stand in for the terminal lines; the rest folds under Details', () => {
    const html = nutshellHtml(glance, true);
    expect(html).not.toContain('EGTF MVFR line');
    expect(html).not.toContain('EGLF VFR line');
    const details = html.indexOf('data-testid="glance-details"');
    expect(html.indexOf('data-testid="airport-cards"')).toBeLessThan(details);
    expect(html.indexOf('Nothing on the route')).toBeGreaterThan(details);
    expect(html.indexOf('data-glance-showmap')).toBeGreaterThan(details);
    expect(nutshellHtml(glance, true, { detailsOpen: true })).toContain('data-testid="glance-details" open');
  });

  it('leads with the offline banner when the last read failed', () => {
    const html = nutshellHtml(glance, true, { offlineSince: FIXTURE.t0 });
    expect(html.indexOf('observed-offline')).toBeLessThan(html.indexOf('airport-cards'));
    expect(offlineBannerHtml(FIXTURE.t0)).toContain('Offline · showing data from 09:40Z, ');
    expect(offlineBannerHtml(FIXTURE.t0)).toContain(`data-live-age="${FIXTURE.t0}"`);
    expect(nutshellHtml(glance, true)).not.toContain('observed-offline');
  });
});
