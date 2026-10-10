/** Route ribbon hover tooltips (#742); the iOS tap card mirrors them (#747).
 *
 * Hovering a mark of the "Along the route" drawing with a mouse says what the
 * mark stands for: an airport's name, categories and raw METAR / TAF, a
 * cell's strength and motion, the rain band under the pointer, a SIGMET.
 * Clicks are unchanged (map / cell detail).
 *
 * SYNC — paired with `app/flyfun-weather/flyfun-weather/Views/Briefing/
 * RouteRibbonInspectorRules.swift` (#747): iOS shows the same rows in a card
 * under the ribbon when a mark is tapped. Same wording, same `bandAt`; the
 * Swift header carries the symbol map, and `ribbon-tooltip.test.ts` ↔
 * `RouteRibbonInspectorRulesTests.swift` assert the same strings.
 *
 * Every word comes from fields the page already holds — the ribbon, the
 * storms and `route_observations.airports` of the same `/live` response,
 * joined by ICAO. Pure, so the content and the band hit test are unit-tested
 * in node; `ribbon-view.ts` owns the element and the pointer.
 */

import { escapeHtml } from '../../utils';
import { formatHhmmZ } from '../../helpers/live-layer';
import type {
  AirportObservation,
  LiveRibbon,
  LiveRibbonSegment,
  LiveStorm,
  RibbonSigmet,
  RibbonStation,
  RibbonWeather,
} from '../../store/types';
import {
  corridorOf,
  relativeMotionText,
  roundHalfAway,
  routeNmOf,
  segmentLabel,
  sigmetText,
  stormMotionText,
  stormPositionText,
  xForNm,
  yForCross,
} from './ribbon-core';

const ROLE_LABEL: Record<RibbonStation['role'], string> = {
  departure: 'Departure',
  destination: 'Destination',
  alternate: 'Alternate',
  route: 'En route',
};

function row(label: string, value: string): string {
  return `<div class="ribbon-tip-row"><span class="ribbon-tip-label">${escapeHtml(label)}</span>`
    + `<span class="ribbon-tip-value">${escapeHtml(value)}</span></div>`;
}

function head(title: string, sub: string | null = null): string {
  return `<div class="ribbon-tip-head">${escapeHtml(title)}`
    + (sub ? ` <span class="ribbon-tip-sub">${escapeHtml(sub)}</span>` : '')
    + '</div>';
}

function raw(label: string, text: string): string {
  return `<div class="ribbon-tip-raw"><span class="ribbon-tip-label">${escapeHtml(label)}</span>`
    + `<code>${escapeHtml(text)}</code></div>`;
}

/** The airport observation for a ribbon station, by ICAO. */
export function airportFor(
  st: RibbonStation,
  airports: AirportObservation[] | null | undefined,
): AirportObservation | null {
  return (airports ?? []).find((a) => a.icao === st.icao) ?? null;
}

/** An airport disc: who it is, its categories now and at ETA, the raw text. */
export function stationTooltipHtml(st: RibbonStation, airport: AirportObservation | null): string {
  let html = head(st.icao, airport?.name ?? null);
  html += row('Role', ROLE_LABEL[st.role] ?? st.role);
  const metarAt = formatHhmmZ(st.metar_time ?? airport?.metar_time ?? null);
  html += row('METAR now', st.metar_category
    ? `${st.metar_category}${metarAt ? ` at ${metarAt}` : ''}` : 'unavailable');
  if ((st.convective ?? []).length > 0) html += row('Observed', (st.convective ?? []).join(' '));
  const eta = formatHhmmZ(st.eta ?? null);
  if (st.taf_category_at_eta) {
    let taf = st.taf_category_at_eta;
    if (st.taf_temporary_type && st.taf_temporary_category) {
      taf += `, ${st.taf_temporary_type} ${st.taf_temporary_category}`;
    }
    html += row(eta ? `TAF at ${eta}` : 'TAF at ETA', taf);
  } else if (eta) {
    html += row('ETA', eta);
  }
  if ((st.taf_weather ?? []).length > 0) html += row('TAF weather', (st.taf_weather ?? []).join(' '));
  if (st.cross_nm != null && (st.role === 'route' || st.role === 'alternate')) {
    const off = roundHalfAway(Math.abs(st.cross_nm));
    html += row('Position', `${off} NM ${st.cross_nm < 0 ? 'left' : 'right'} of course`
      + (st.along_nm != null ? ` at ${roundHalfAway(st.along_nm)} NM` : ''));
  }
  // Without a joined airport the raw text is unknown, not absent: say
  // nothing rather than "no report" beside the station's own METAR category.
  if (airport) {
    if (airport.metar_raw) html += raw(airport.metar_report_type === 'SPECI' ? 'SPECI' : 'METAR', airport.metar_raw);
    else html += row('METAR', 'no report');
    if (airport.taf_raw) html += raw('TAF', airport.taf_raw);
    else html += row('TAF', 'none issued');
  }
  return html;
}

/** A cell: strength, where it is, how it moves. Click opens the full detail. */
export function stormTooltipHtml(storm: LiveStorm): string {
  const title = storm.intensity
    ? `${storm.intensity.charAt(0).toUpperCase()}${storm.intensity.slice(1)} cell`
    : 'Cell';
  let html = head(title, storm.peak_dbz != null ? `${roundHalfAway(storm.peak_dbz)} dBZ` : null);
  html += row('Position', stormPositionText(storm));
  if (storm.abeam_eta && storm.end == null) html += row('Abeam at plan', formatHhmmZ(storm.abeam_eta));
  html += row('Motion', stormMotionText(storm));
  if (storm.flashes != null) {
    html += row('Lightning', storm.flashes === 0 ? 'none'
      : storm.flashes === 1 ? '1 flash' : `${storm.flashes} flashes`);
  } else if (storm.flashes_pending) {
    html += row('Lightning', 'pending');
  }
  if (storm.top_fl != null) html += row('Cloud top', `FL${storm.top_fl}`);
  if (storm.trend) html += row('Trend (30 min)', storm.trend);
  html += '<div class="ribbon-tip-hint">Click for detail</div>';
  return html;
}

/** A rain area or convective core band. */
export function bandTooltipHtml(band: RibbonWeather): string {
  const title = band.tier === 'core' ? 'Convective core' : 'Rain area';
  let html = head(title, band.peak_dbz != null ? `${roundHalfAway(band.peak_dbz)} dBZ` : null);
  html += row('Along route', `${roundHalfAway(band.from_nm)}–${roundHalfAway(band.to_nm)} NM`);
  if (band.side === 'both') {
    html += row('Off track', 'across the track');
  } else {
    const near = roundHalfAway(band.near_nm);
    const far = roundHalfAway(band.far_nm);
    html += row('Off track', `${near === far ? `${near}` : `${near}–${far}`} NM ${band.side} of course`);
  }
  if (band.intensity) html += row('Intensity', band.intensity);
  if (band.flashes != null && band.flashes > 0) {
    html += row('Lightning', band.flashes === 1 ? '1 flash' : `${band.flashes} flashes`);
  }
  if (band.motion_rel_deg != null) {
    html += row('Motion', relativeMotionText(band.motion_rel_deg)
      + (band.speed_kt != null ? `, ${roundHalfAway(band.speed_kt)} kt` : ''));
  }
  return html;
}

/** A SIGMET band across the top. */
export function sigmetTooltipHtml(s: RibbonSigmet): string {
  let html = head(sigmetText(s), s.new ? 'new' : null);
  if (s.label) html += row('SIGMET', s.label);
  if (s.from_nm != null && s.to_nm != null) {
    html += row('Along route', `${roundHalfAway(s.from_nm)}–${roundHalfAway(s.to_nm)} NM`);
  }
  const from = formatHhmmZ(s.valid_from ?? null);
  const to = formatHhmmZ(s.valid_to ?? null);
  if (from || to) html += row('Valid', `${from || '?'}–${to || '?'}`);
  if (s.pending) html += row('Status', 'issued, not yet valid');
  return html;
}

/** A radar-strip stretch (no cells feed). */
export function segmentTooltipHtml(seg: LiveRibbonSegment): string {
  return head(segmentLabel(seg));
}

/** The band drawn under a point of the drawing (px in the SVG's own
 *  coordinates), cores before rain since they paint on top. Uses the same
 *  per-bin rects as `bandRects`, so the hover matches what is seen.
 *  SYNC: `RouteRibbonInspectorRules.bandAt` (iOS) ports this, same order. */
export function bandAt(
  ribbon: LiveRibbon,
  px: number,
  py: number,
  width: number,
): RibbonWeather | null {
  const routeNm = routeNmOf(ribbon);
  const corridor = corridorOf(ribbon, 30);
  const half = (ribbon.weather_bin_nm ?? 5) / 2;
  const bands = [...(ribbon.weather ?? [])].sort(
    (a, b) => (a.tier === 'core' ? 0 : 1) - (b.tier === 'core' ? 0 : 1),
  );
  for (const band of bands) {
    for (const bin of band.profile ?? []) {
      if (bin.length !== 3) continue;
      const x0 = xForNm(bin[0] - half, routeNm, width);
      const x1 = Math.max(xForNm(bin[0] + half, routeNm, width), x0 + 1.5);
      const ya = yForCross(bin[1], corridor);
      const yb = yForCross(bin[2], corridor);
      const top = Math.min(ya, yb);
      const bottom = Math.max(Math.max(ya, yb), top + 3);
      if (px >= x0 && px <= x1 && py >= top && py <= bottom) return band;
    }
  }
  return null;
}
