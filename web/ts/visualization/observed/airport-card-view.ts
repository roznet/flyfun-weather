/** Departure / arrival airport cards (#881, part of #737) — the drawing.
 *
 * Agreed design: the "Airport Card Lab" mockup (Now → at ETA, category meter
 * on, TAF strip on, dial large, departure folded after Start). Every value is
 * the server's (#880: `glance.departure` / `glance.destination` and their
 * `airport` row, `models/observations.py`): the meter positions, the sky and
 * visibility words, the weather words and their icon kind, the short wind,
 * the crosswind reading, the trend, the TAF hours and the fold. This file only
 * lays them out; a missing field is left out, never derived here.
 *
 * SYNC — iOS (#882) and Android (#883) draw the same card from the same
 * fields. Registered in `/sync-ios-web` as a hand-copied surface (drawing
 * only: there are no rules to mirror).
 */

import { escapeHtml } from '../../utils';
import { t } from '../../i18n/i18n';
import { formatHhmmZ, minutesAgo, OBSERVED_STALE_MIN } from '../../helpers/live-layer';
import type {
  AirportObservation, LiveFocus, LiveGlanceDeparture, LiveGlanceDestination, TafHour,
} from '../../store/types';
import { runwayWindDial } from '../runway-wind/runway-wind-core';
import { runwayWindSvg } from '../runway-wind/runway-wind-view';

type Role = 'DEP' | 'ARR';
type Block = LiveGlanceDeparture | LiveGlanceDestination;

// 24×24 stroke icons, `currentColor` through `.ac-ic` (the mockup's set).
const CLOUD = '<path d="M7 18h10a4 4 0 0 0 .4-8 6 6 0 0 0-11.5 1.5A3.3 3.3 0 0 0 7 18z"/>';
const RAIN_CLOUD = '<path d="M7 14h10a4 4 0 0 0 .4-8 6 6 0 0 0-11.5 1.5A3.3 3.3 0 0 0 7 14z"/>';
const ICONS: Record<string, string> = {
  cloud: CLOUD,
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  eye: '<path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.6"/>',
  wind: '<path d="M3 9h11a3 3 0 1 0-3-3M3 15h15a3 3 0 1 1-3 3M3 12h8"/>',
  rain: `${RAIN_CLOUD}<path d="M9 17l-1 3M13 17l-1 3M17 17l-1 3"/>`,
  drizzle: `${RAIN_CLOUD}<path d="M9 18v.5M13 18v.5M17 18v.5"/>`,
  snow: `${RAIN_CLOUD}<path d="M9 18l0 2M8 19h2M14 18l0 2M13 19h2"/>`,
  hail: `${RAIN_CLOUD}<circle cx="9" cy="19" r="1"/><circle cx="14" cy="19" r="1"/>`,
  fog: '<path d="M4 9h16M6 13h12M4 17h16"/>',
  thunder: '<path d="M13 2 5 13h6l-1 9 8-11h-6z"/>',
  cell: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
};

/** The server's weather-word icon kinds onto the drawn glyphs. */
const WORD_ICON: Record<string, string> = {
  thunder: 'thunder', hail: 'hail', snow: 'snow', freezing: 'snow',
  showers: 'rain', rain: 'rain', drizzle: 'drizzle',
  fog: 'fog', mist: 'fog', haze: 'fog', smoke: 'fog', dust: 'fog',
  squall: 'wind', funnel: 'wind', other: 'cloud',
};

function icon(name: string): string {
  return `<svg class="ac-ic" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] ?? ICONS.cloud}</svg>`;
}

const catClass = (cat: string) => `ac-cat-${escapeHtml(cat.toLowerCase())}`;

/** A category box: filled = prevailing, dashed = a TEMPO/PROB group. */
export function catBoxHtml(cat: string, opts: { sub?: string; dashed?: boolean; small?: boolean } = {}): string {
  return `<span class="ac-catbox ${catClass(cat)}${opts.dashed ? ' ac-catbox-dashed' : ''}`
    + `${opts.small ? ' ac-catbox-small' : ''}"><b>${escapeHtml(cat)}</b>`
    + `${opts.sub ? `<i>${escapeHtml(opts.sub)}</i>` : ''}</span>`;
}

/** "PROB30 TEMPO" → "PROB30": the box has room for one word. */
function shortType(type: string | null | undefined): string {
  return (type ?? 'TEMPO').split(' ')[0];
}

function headHtml(block: Block, role: Role, a: AirportObservation | null): string {
  const now = a?.metar_flight_category ?? block.metar_category ?? null;
  const at = a?.taf_prevailing_category_at_eta ?? null;
  const temp = a?.taf_temporary_category_at_eta ?? null;
  const boxes = [
    now ? catBoxHtml(now, { sub: t('observed.card.now') }) : '',
    now && at ? '<span class="ac-cats-arrow" aria-hidden="true">→</span>' : '',
    at ? catBoxHtml(at, { sub: t(role === 'DEP' ? 'observed.card.atEtd' : 'observed.card.atEta') }) : '',
    at && temp ? catBoxHtml(temp, { dashed: true, sub: shortType(a?.taf_temporary_type) }) : '',
  ].join('');
  return '<div class="ac-head">'
    + `<span class="ac-role">${escapeHtml(t(role === 'DEP' ? 'observed.card.departure' : 'observed.card.arrival'))}</span>`
    + `<span class="ac-icao">${escapeHtml(block.icao)}</span>`
    + (block.name ? `<span class="ac-name">${escapeHtml(block.name)}</span>` : '')
    + (boxes ? `<span class="ac-cats">${boxes}</span>` : '')
    + '</div>';
}

/** A row with the four equal bands and a dot at the server's 0..1 position. */
function meterHtml(label: string, iconName: string, text: string | null | undefined, at: number | null | undefined): string {
  const dot = at != null && Number.isFinite(at)
    ? `<span class="ac-meter-dot" style="left:${(Math.min(1, Math.max(0, at)) * 100).toFixed(1)}%"></span>`
    : '';
  return `<div class="ac-meter">${icon(iconName)}<span class="ac-meter-val">${escapeHtml(text ?? '—')}</span>`
    + `<span class="ac-meter-track" role="img" aria-label="${escapeHtml(`${label}: ${text ?? '—'}`)}">`
    + '<span class="ac-m-lifr"></span><span class="ac-m-ifr"></span><span class="ac-m-mvfr"></span><span class="ac-m-vfr"></span>'
    + `${dot}</span></div>`;
}

function windRowHtml(a: AirportObservation): string {
  if (!a.metar_wind_short) return '';
  const x = a.metar_crosswind ?? null;
  // Toned only by the server's own METAR wind advisory; green stays quiet.
  const adv = a.metar_wind_advisory;
  const tone = adv === 'amber' || adv === 'red' ? ` ac-tone-${adv}` : '';
  const glyph = x?.side === 'left' ? '◀ ' : x?.side === 'right' ? '▶ ' : '';
  const from = x?.side === 'left' || x?.side === 'right'
    ? ` title="${escapeHtml(t(`observed.card.crosswindFrom.${x.side}`))}"` : '';
  return `<div class="ac-row ac-wind">${icon('wind')}<span class="ac-wtxt">${escapeHtml(a.metar_wind_short)}</span>`
    + (x ? `<span class="ac-rwy">RWY ${escapeHtml(x.runway)}</span>`
      + `<span class="ac-xw${tone}"${from} data-testid="card-crosswind">${glyph}${escapeHtml(x.text)} X</span>` : '')
    + '</div>';
}

/** Cells and lightning near the field, in the server's words. The row opens
 *  the map on the field; a listed cell opens on that cell. */
function nearHtml(block: Block, key: string): string {
  // Amber only when the server says an alert-tier change stands behind it.
  const row = (iconName: string, text: string, alert: boolean | undefined, id: string) => {
    const cls = `ac-row ac-near${alert ? ' ac-tone-amber' : ''}`;
    const body = `${icon(iconName)}<span>${escapeHtml(text)}</span>`;
    return block.focus
      ? `<button type="button" class="${cls}" data-card-focus="${key}" data-testid="card-${id}">${body}`
        + '<span class="glance-chevron" aria-hidden="true">▸</span></button>'
      : `<div class="${cls}" data-testid="card-${id}">${body}</div>`;
  };
  const cells = (block.cells ?? []).map((c, i) => (c.focus
    ? `<button type="button" class="ac-cell" data-card-cell="${key}:${i}">${escapeHtml(c.text)}`
      + '<span class="glance-chevron" aria-hidden="true">▸</span></button>'
    : `<div class="ac-cell">${escapeHtml(c.text)}</div>`)).join('');
  return row('cell', block.cells_text, block.cells_alert, 'cells')
    + (cells ? `<div class="ac-cells">${cells}</div>` : '')
    + (block.lightning_text ? row('thunder', block.lightning_text, block.lightning_alert, 'lightning') : '');
}

/** "METAR 09:30Z · 8 min" (amber past the stale age) · SPECI · trend · ETA. */
function provenanceHtml(block: Block, role: Role, a: AirportObservation | null, now: number): string {
  const parts: string[] = [];
  const metarAt = a?.metar_time ?? block.metar_time ?? null;
  if (metarAt) {
    const min = minutesAgo(metarAt, now);
    const stale = min != null && min > OBSERVED_STALE_MIN;
    parts.push(`<span class="ac-metar-time${stale ? ' ac-stale' : ''}" data-live-stale="${escapeHtml(metarAt)}">`
      + `METAR ${escapeHtml(formatHhmmZ(metarAt))} · <span data-live-age-short="${escapeHtml(metarAt)}"></span></span>`);
  }
  if (a?.metar_report_type === 'SPECI') parts.push('<span class="ac-tag">SPECI</span>');
  if (a?.metar_trend && a.metar_previous_flight_category) {
    const was = t('observed.card.was', {
      cat: a.metar_previous_flight_category, time: formatHhmmZ(a.metar_previous_time ?? null),
    }).trim();
    parts.push(`<span class="ac-trend-${escapeHtml(a.metar_trend)}">${a.metar_trend === 'worse' ? '↘' : '↗'} ${escapeHtml(was)}</span>`);
  }
  // No TAF category at the ETA: the server's reason ("no TAF for ETA").
  if (!a?.taf_prevailing_category_at_eta && block.taf_text) parts.push(`<span>${escapeHtml(block.taf_text)}</span>`);
  const time = role === 'DEP' ? (block as LiveGlanceDeparture).etd : (block as LiveGlanceDestination).eta;
  if (time) {
    parts.push(`<span class="ac-muted">${escapeHtml(t(role === 'DEP' ? 'observed.card.etd' : 'observed.card.eta',
      { time: formatHhmmZ(time) }))}</span>`);
  }
  return `<div class="ac-prov">${icon('clock')}${parts.join('<span class="ac-dot">·</span>')}</div>`;
}

function hourLabel(iso: string): string {
  const hhmm = formatHhmmZ(iso);
  return hhmm ? `${hhmm.slice(0, 2)}Z` : '';
}

/** One cell per TAF hour: prevailing fill, TEMPO/PROB hatch, ETA outlined. */
export function tafStripHtml(hours: TafHour[], role: Role): string {
  if (!hours.length) return '';
  const cells = hours.map((h) => {
    const cat = h.prevailing_category ? catClass(h.prevailing_category) : 'ac-cat-none';
    const temp = h.temporary_category
      ? `<span class="ac-strip-temp ${catClass(h.temporary_category)}"></span>` : '';
    const title = [hourLabel(h.time), h.prevailing_category ?? '—',
      h.temporary_category ? `${shortType(h.temporary_type)} ${h.temporary_category}` : ''].filter(Boolean).join(' ');
    return `<span class="ac-strip-h${h.is_eta ? ' ac-strip-eta' : ''}" title="${escapeHtml(title)}">`
      + `<span class="ac-strip-bar ${cat}">${temp}</span>`
      + `<span class="ac-strip-lbl">${escapeHtml(hourLabel(h.time))}</span></span>`;
  }).join('');
  const mark = t(role === 'DEP' ? 'observed.card.etdShort' : 'observed.card.etaShort');
  return `<div class="ac-strip" data-testid="card-taf-strip" title="${escapeHtml(t('observed.card.stripTitle', { mark }))}">${cells}</div>`;
}

/** The full card for one terminal. `key` ("dep" / "arr") names its taps. */
export function airportCardHtml(block: Block, role: Role, now: number = Date.now()): string {
  const a = block.airport ?? null;
  const key = role === 'DEP' ? 'dep' : 'arr';
  const rows: string[] = [];
  if (a) {
    rows.push(meterHtml(t('observed.card.ceiling'), a.metar_cavok ? 'sun' : 'cloud', a.metar_sky_text, a.metar_ceiling_meter));
    rows.push(meterHtml(t('observed.card.visibility'), 'eye', a.metar_visibility_text, a.metar_visibility_meter));
    const words = (a.metar_weather_words ?? [])
      .map((w) => `<span class="ac-pair" title="${escapeHtml(w.code)}">${icon(WORD_ICON[w.icon] ?? 'cloud')}${escapeHtml(w.text)}</span>`)
      .join('');
    if (words) rows.push(`<div class="ac-row">${words}</div>`);
    rows.push(windRowHtml(a));
  } else {
    // No airport row (an older server, or no corridor row): the block's
    // own lines, as the destination block showed them.
    rows.push(`<div class="ac-row">${escapeHtml(block.metar_text)}</div>`);
    if (block.wind_text) rows.push(`<div class="ac-row">${escapeHtml(block.wind_text)}</div>`);
  }
  rows.push(nearHtml(block, key));
  rows.push(provenanceHtml(block, role, a, now));
  if (block.proxy_caption) rows.push(`<div class="glance-proxy">${escapeHtml(block.proxy_caption)}</div>`);

  const dial = a?.runway_wind ? runwayWindSvg(runwayWindDial(role, block.icao, a, true), 'compact') : '';
  return `<article class="ac-card${block.alert ? ' ac-card-alert' : ''}" data-testid="airport-card-${key}" data-icao="${escapeHtml(block.icao)}">`
    + headHtml(block, role, a)
    + '<div class="ac-body">'
    + `<div class="ac-rows">${rows.join('')}</div>`
    + (dial ? `<div class="ac-dial">${dial}</div>` : '')
    + '</div>'
    + tafStripHtml(a?.taf_hours ?? [], role)
    + '</article>';
}

/** The departure after it folded: one line that opens on the full card. */
export function foldedDepartureHtml(dep: LiveGlanceDeparture, open: boolean, now: number = Date.now()): string {
  const cat = dep.metar_category ?? dep.airport?.metar_flight_category ?? null;
  return `<details class="ac-folded" data-card-fold data-testid="airport-card-folded"${open ? ' open' : ''}>`
    + '<summary>'
    + (cat ? catBoxHtml(cat, { small: true }) : '')
    + `<span class="ac-folded-text">${escapeHtml(dep.folded_text ?? dep.icao)}</span>`
    + '<span class="ac-chev" aria-hidden="true">›</span>'
    + '</summary>'
    + airportCardHtml(dep, 'DEP', now)
    + '</details>';
}

export interface AirportCardsOptions {
  /** The folded departure is open (remembered per flight). */
  departureOpen?: boolean;
  now?: number;
}

/** Both cards: departure then arrival, or the arrival first and the folded
 *  departure line after it once the server says the departure is behind. */
export function airportCardsHtml(
  departure: LiveGlanceDeparture | null | undefined,
  destination: LiveGlanceDestination | null | undefined,
  opts: AirportCardsOptions = {},
): string {
  const now = opts.now ?? Date.now();
  const folded = !!departure?.folded;
  const dep = departure
    ? (folded ? foldedDepartureHtml(departure, !!opts.departureOpen, now) : airportCardHtml(departure, 'DEP', now))
    : '';
  const arr = destination ? airportCardHtml(destination, 'ARR', now) : '';
  if (!dep && !arr) return '';
  return `<div class="ac-cards${folded ? ' ac-cards-folded' : ''}" data-testid="airport-cards">`
    + (folded ? arr + dep : dep + arr)
    + '</div>';
}

/** The focus a card tap names: `dep` / `arr` for the field, `dep:2` for a cell. */
export function cardFocus(
  departure: LiveGlanceDeparture | null | undefined,
  destination: LiveGlanceDestination | null | undefined,
  ref: string,
): LiveFocus | null {
  const [key, cell] = ref.split(':');
  const block = key === 'dep' ? departure : key === 'arr' ? destination : null;
  if (!block) return null;
  if (cell == null) return block.focus ?? null;
  return (block.cells ?? [])[Number(cell)]?.focus ?? null;
}
