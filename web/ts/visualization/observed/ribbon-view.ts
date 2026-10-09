/** Route ribbon (#690) — the drawing. All rules live in `ribbon-core.ts`.
 *
 * SYNC — paired with
 * app/flyfun-weather/flyfun-weather/Views/Briefing/ObservedNutshellView.swift
 * (`RouteRibbonCard` / `RouteRibbonView` / `RouteRibbonLegend`). This file is
 * the SVG equivalent of that SwiftUI drawing and holds no rules of its own:
 * if a colour, a label or a position needs changing, it changes in
 * `ribbon-core.ts` and in `RouteRibbonRules.swift`, not here.
 *
 * The marks are sized in CSS pixels, so the drawing is measured and re-laid
 * out on resize rather than scaled — the SwiftUI side gets the same from
 * `GeometryReader`. Scaling the SVG instead would squash the airport discs and
 * the ETA labels on a phone.
 */

import { escapeHtml } from '../../utils';
import { formatHhmmZ } from '../../helpers/live-layer';
import type {
  AirportObservation,
  LiveFocus,
  LiveRibbon,
  LiveStorm,
  LiveStorms,
  RibbonSigmet,
  RibbonStation,
} from '../../store/types';
import {
  AXIS_Y,
  INSET,
  LEFT_ROW_Y,
  RIBBON_HEIGHT,
  RIGHT_ROW_Y,
  SIGMET_Y,
  TRACK_Y,
  ZONE_BOTTOM,
  ZONE_TOP,
  bandRects,
  categoryColour,
  corridorOf,
  dbzColour,
  motionArrowDir,
  radarFill,
  ribbonArrows,
  ribbonCaption,
  routeNmOf,
  segmentFocusAt,
  segmentLabel,
  sigmetText,
  STATION_HIT_RADIUS,
  STORM_HIT_RADIUS,
  stationLabel,
  stationMarkSize,
  stationPoint,
  stormLabel,
  stormMarkSize,
  stormMotionText,
  stormPositionText,
  stormTargets,
  weatherAvailable,
  weatherSummary,
  xForNm,
  yForCross,
} from './ribbon-core';
import {
  airportFor,
  bandAt,
  bandTooltipHtml,
  segmentTooltipHtml,
  sigmetTooltipHtml,
  stationTooltipHtml,
  stormTooltipHtml,
} from './ribbon-tooltip';

/** Minimum drawing width: below this the ribbon is unreadable and the caller
 *  is better off waiting for a real measurement. */
const MIN_WIDTH = 240;

export interface RibbonHandlers {
  /** Open the route map framed on this item with its layers on. */
  onFocus: (focus: LiveFocus) => void;
  /** Open a cell's detail. */
  onStorm: (storm: LiveStorm) => void;
}

/** A right-pointing arrow head, rotated by the band's motion. */
function arrowPath(x: number, y: number, deg: number, core: boolean): string {
  const colour = core ? 'var(--text)' : 'var(--text-muted)';
  // Decorative: the motion it shows is already in the zone's summary and in
  // each cell's accessible name, so it is not announced twice.
  return `<path d="M-5 0 H3 M0 -3.5 L4 0 L0 3.5" transform="translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${deg.toFixed(1)})"`
    + ` stroke="${colour}" stroke-width="2" stroke-linecap="round" fill="none" class="ribbon-arrow"`
    + ' aria-hidden="true"/>';
}

/** Filled with the METAR category now, ringed with the TAF category at the
 *  airport's ETA (dashed when a PROB/TEMPO group sets it). */
function airportCircle(st: RibbonStation, x: number, y: number, size: number): string {
  const taf = st.taf_temporary_category ?? st.taf_category_at_eta;
  const ring = taf
    ? `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(size / 2 + 3).toFixed(1)}" fill="none"`
      + ` stroke="${categoryColour(taf)}" stroke-width="2.5"`
      + `${st.taf_temporary_type ? ' stroke-dasharray="2.5 2"' : ''}/>`
    : '';
  // A thicker outline marks CB/TCU in the observed part of the METAR.
  const convective = (st.convective ?? []).length > 0;
  return ring
    + `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(size / 2).toFixed(1)}"`
    + ` fill="${categoryColour(st.metar_category)}" stroke="#212121"`
    + ` stroke-width="${convective ? 2 : 0.5}"/>`;
}

function sigmetMark(s: RibbonSigmet, ribbon: LiveRibbon, width: number, i: number): string {
  if (s.from_nm == null || s.to_nm == null) return '';
  const routeNm = routeNmOf(ribbon);
  const x0 = xForNm(s.from_nm, routeNm, width);
  const x1 = xForNm(s.to_nm, routeNm, width);
  const w = Math.max(x1 - x0, 4);
  const label = sigmetText(s);
  const focusable = s.focus ? ' ribbon-hit' : '';
  return `<g class="ribbon-sigmet${focusable}" data-ribbon-sigmet="${i}"`
    + `${s.focus ? ` data-ribbon-focus="sigmet:${i}" tabindex="0" role="button"` : ''}`
    + ` aria-label="${escapeHtml(s.label || s.id)}">`
    + `<rect x="${x0.toFixed(1)}" y="${SIGMET_Y - 5.5}" width="${w.toFixed(1)}" height="11" rx="2"`
    + ` fill="var(--amber)" opacity="${s.pending ? 0.25 : 0.5}"/>`
    + (w >= 26
      ? `<text x="${(x0 + w / 2).toFixed(1)}" y="${SIGMET_Y + 3}" class="ribbon-sigmet-text"`
        + ` text-anchor="middle">${escapeHtml(label)}</text>`
      : '')
    + '</g>';
}

/** Without the cells feed: the radar max per stretch, hugging the line. */
function radarStrip(ribbon: LiveRibbon, width: number): string {
  const routeNm = routeNmOf(ribbon);
  return (ribbon.segments ?? []).map((seg, i) => {
    const x0 = xForNm(seg.from_nm ?? 0, routeNm, width);
    const x1 = xForNm(seg.to_nm ?? 0, routeNm, width);
    const w = Math.max(x1 - x0, 1);
    const bolt = seg.lightning === true
      ? `<text x="${((x0 + x1) / 2).toFixed(1)}" y="${TRACK_Y - 11}" class="ribbon-bolt"`
        + ' text-anchor="middle">⚡</text>'
      : '';
    const hit = seg.focus ? ' ribbon-hit' : '';
    return `<g class="ribbon-radar${hit}" data-ribbon-seg="${i}"`
      + `${seg.focus ? ` data-ribbon-focus="segment:${i}" tabindex="0" role="button"` : ''}`
      + ` aria-label="${escapeHtml(segmentLabel(seg))}">`
      + `<rect x="${x0.toFixed(1)}" y="${TRACK_Y - 8}" width="${w.toFixed(1)}" height="16"`
      + ` fill="${radarFill(seg)}"/>${bolt}</g>`;
  }).join('');
}

/** Without weather bands (an older server, or a dark feed): storms as points. */
function stormMarks(ribbon: LiveRibbon, storms: LiveStorm[], corridorNm: number, width: number): string {
  const routeNm = routeNmOf(ribbon);
  return storms.map((storm) => {
    if (storm.along_nm == null) return '';
    const cross = storm.cross_nm ?? 0;
    const x = xForNm(storm.along_nm, routeNm, width);
    const y = yForCross(cross, Math.max(corridorNm, 1));
    const size = stormMarkSize(storm.peak_dbz);
    const dir = motionArrowDir(storm, cross);
    // Behind the aircraft reads dimmer: it is still observed, just passed.
    const opacity = storm.ahead === false ? 0.35 : 0.9;
    const arrow = dir
      ? `<path d="M0 ${dir === 'up' ? 4 : -4} V${dir === 'up' ? -4 : 4} M${-3} ${dir === 'up' ? -1 : 1} L0 ${dir === 'up' ? -4.5 : 4.5} L3 ${dir === 'up' ? -1 : 1}"`
        + ` transform="translate(${x.toFixed(1)} ${(y + (cross >= 0 ? size * 0.9 : -size * 0.9)).toFixed(1)})"`
        + ' stroke="var(--text)" stroke-width="1.6" fill="none" stroke-linecap="round"/>'
      : '';
    return `<g class="ribbon-storm ribbon-hit" data-ribbon-storm="${escapeHtml(storm.id)}" tabindex="0" role="button"`
      + ` aria-label="${escapeHtml(stormLabel(storm))}">`
      + `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(size / 2).toFixed(1)}"`
      + ` fill="${dbzColour(storm.peak_dbz)}" opacity="${opacity}"/>${arrow}`
      // An invisible 28px target, so a 10px dot is still tappable.
      + `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${STORM_HIT_RADIUS}" fill="transparent"/>`
      + '</g>';
  }).join('');
}

function axisLabels(ribbon: LiveRibbon, width: number): string {
  const routeNm = routeNmOf(ribbon);
  return (ribbon.waypoints ?? []).map((wp) => {
    if (wp.along_nm == null) return '';
    const x = xForNm(wp.along_nm, routeNm, width);
    const eta = wp.eta ? formatHhmmZ(wp.eta) : '';
    return `<text x="${x.toFixed(1)}" y="${AXIS_Y}" class="ribbon-axis-icao" text-anchor="middle">`
      + `${escapeHtml(wp.icao ?? '')}</text>`
      + (eta
        ? `<text x="${x.toFixed(1)}" y="${AXIS_Y + 11}" class="ribbon-axis-eta" text-anchor="middle">`
          + `${escapeHtml(eta)}</text>`
        : '');
  }).join('');
}

/** The ribbon drawing at a known width. Pure: the caller owns measurement. */
export function ribbonSvg(
  ribbon: LiveRibbon,
  storms: LiveStorm[],
  corridorNm: number,
  width: number,
): string {
  const bands = weatherAvailable(ribbon);
  const corridor = corridorOf(ribbon, corridorNm);
  const routeNm = routeNmOf(ribbon);
  let inner = '';

  if (bands) {
    // The weather picture as a whole: `role="img"`, so the summary is read as
    // the drawing's description. (An aria-label on a bare <rect> with no role
    // is skipped by most screen readers.) It also keeps the iOS behaviour of
    // a tap anywhere in the zone framing that stretch of route.
    inner += `<rect class="ribbon-zone-hit" x="${INSET}" y="${ZONE_TOP}"`
      + ` width="${Math.max(width - 2 * INSET, 1).toFixed(1)}" height="${ZONE_BOTTOM - ZONE_TOP}"`
      + ` fill="transparent" role="img"`
      + ` aria-label="${escapeHtml(weatherSummary(ribbon.weather ?? []))}"/>`;
    inner += bandRects(ribbon, width).map((r) => `<rect x="${r.x.toFixed(1)}" y="${r.y.toFixed(1)}"`
      + ` width="${r.width.toFixed(1)}" height="${r.height.toFixed(1)}" fill="${r.fill}"`
      + ` opacity="${r.opacity}" aria-hidden="true"/>`).join('');
    // Keyboard parity for the zone: one focusable button per stretch of
    // route, tiling the same area. Pointer users hit these or the zone
    // behind them — both resolve to the same segment focus; keyboard users
    // had no path to it at all before.
    inner += (ribbon.segments ?? []).map((seg, i) => {
      if (!seg.focus) return '';
      const x0 = xForNm(seg.from_nm ?? 0, routeNm, width);
      const x1 = xForNm(seg.to_nm ?? 0, routeNm, width);
      return `<rect class="ribbon-seg-hit ribbon-hit" data-ribbon-focus="segment:${i}"`
        + ` tabindex="0" role="button" aria-label="${escapeHtml(segmentLabel(seg))}"`
        + ` x="${x0.toFixed(1)}" y="${ZONE_TOP}" width="${Math.max(x1 - x0, 1).toFixed(1)}"`
        + ` height="${ZONE_BOTTOM - ZONE_TOP}" fill="transparent"/>`;
    }).join('');
  } else {
    inner += radarStrip(ribbon, width);
  }

  // The route itself, straight and to scale.
  inner += `<line x1="${INSET}" y1="${TRACK_Y}" x2="${(width - INSET).toFixed(1)}" y2="${TRACK_Y}"`
    + ' class="ribbon-track"/>';

  inner += (ribbon.sigmets ?? []).map((s, i) => sigmetMark(s, ribbon, width, i)).join('');

  if (bands) {
    inner += ribbonArrows(ribbon, width).map((a) => arrowPath(a.x, a.y, a.deg, a.core)).join('');
    inner += stormTargets(ribbon, storms, width).map((t) =>
      `<g class="ribbon-storm ribbon-hit" data-ribbon-storm="${escapeHtml(t.storm.id)}" tabindex="0"`
      + ` role="button" aria-label="${escapeHtml(stormLabel(t.storm))}">`
      + `<circle cx="${t.x.toFixed(1)}" cy="${t.y.toFixed(1)}" r="${STORM_HIT_RADIUS}" fill="transparent"/></g>`).join('');
  } else {
    inner += stormMarks(ribbon, storms, corridor, width);
  }

  inner += (ribbon.stations ?? []).map((st, i) => {
    const at = stationPoint(st, ribbon, width);
    if (!at) return '';
    const hit = st.focus ? ' ribbon-hit' : '';
    // Without a focus it is still a labelled mark, just not a control.
    const role = st.focus ? ` data-ribbon-focus="station:${i}" tabindex="0" role="button"` : ' role="img"';
    return `<g class="ribbon-station${hit}" data-ribbon-station="${i}"${role}`
      + ` aria-label="${escapeHtml(stationLabel(st))}">`
      + airportCircle(st, at.x, at.y, stationMarkSize(st))
      + `<circle cx="${at.x.toFixed(1)}" cy="${at.y.toFixed(1)}" r="${STATION_HIT_RADIUS}" fill="transparent"/></g>`;
  }).join('');

  // Planned position now, on the line.
  if (ribbon.flown_nm != null && ribbon.flown_nm > 0) {
    const x = xForNm(ribbon.flown_nm, routeNm, width);
    inner += `<g role="img" aria-label="Planned position now">`
      + `<circle cx="${x.toFixed(1)}" cy="${TRACK_Y}" r="9" fill="var(--surface)"/>`
      + `<circle cx="${x.toFixed(1)}" cy="${TRACK_Y}" r="4.5" fill="var(--primary)"/></g>`;
  }

  inner += axisLabels(ribbon, width);

  return `<svg class="ribbon-svg" width="${width.toFixed(0)}" height="${RIBBON_HEIGHT}"`
    + ` viewBox="0 0 ${width.toFixed(0)} ${RIBBON_HEIGHT}" role="img"`
    + ` aria-label="Route ribbon: ${escapeHtml(weatherSummary(ribbon.weather ?? []))}">${inner}</svg>`;
}

/** The ribbon's key: what the discs, rings, bands and arrows mean. */
export function ribbonLegendHtml(bands: boolean): string {
  const swatch = (fill: string, extra = '') =>
    `<span class="ribbon-key-swatch" style="background:${fill}${extra}"></span>`;
  const items: string[] = [
    '<span class="ribbon-key-item"><span class="ribbon-key-apt"></span>METAR now · ring TAF at ETA</span>',
    `<span class="ribbon-key-item">${swatch('var(--amber)', ';opacity:.5')}SIGMET</span>`,
  ];
  if (bands) {
    items.push(`<span class="ribbon-key-item">${swatch('rgba(60,190,90,.28)')}rain</span>`);
    items.push('<span class="ribbon-key-item"><span class="ribbon-key-dbz">'
      + '<i style="background:#f0d23c"></i><i style="background:#f08c28"></i><i style="background:#e13c3c"></i>'
      + '</span>cells 35/41/50 dBZ</span>');
    items.push('<span class="ribbon-key-item"><span class="ribbon-key-arrow">→</span>motion vs course</span>');
  } else {
    items.push(`<span class="ribbon-key-item">${swatch('rgba(60,190,90,.6)')}radar ≤10 NM</span>`);
  }
  return `<div class="ribbon-key">${items.join('')}`
    + '<span class="ribbon-key-note">Left of course above the line, right below</span></div>';
}

/** A cell's detail (iOS `StormDetailSheet`): what it is, where it is against
 *  the route, how it moved — and, set apart and labelled, the estimate. */
export function stormDetailHtml(storm: LiveStorm): string {
  const row = (label: string, value: string) =>
    `<div class="storm-row"><span class="storm-row-label">${escapeHtml(label)}</span>`
    + `<span class="storm-row-value">${escapeHtml(value)}</span></div>`;
  const title = storm.intensity
    ? `${storm.intensity.charAt(0).toUpperCase()}${storm.intensity.slice(1)} cell`
    : 'Cell';

  let html = `<div class="popup-header"><h3>${escapeHtml(title)}</h3></div>`;
  html += '<h4>Observed</h4>';
  html += row('Peak', `${Math.round(storm.peak_dbz ?? 0)} dBZ`
    + (storm.intensity ? ` (${storm.intensity})` : ''));
  html += row('Position', stormPositionText(storm));
  if (storm.abeam_eta && storm.end == null) {
    html += row('Abeam at plan', formatHhmmZ(storm.abeam_eta));
  }
  html += row('Motion', stormMotionText(storm));
  if (storm.flashes != null) {
    html += row('Lightning', storm.flashes === 0 ? 'none'
      : storm.flashes === 1 ? '1 flash' : `${storm.flashes} flashes`);
  } else if (storm.flashes_pending) {
    html += row('Lightning', 'pending');
  }
  html += row('Cloud top', storm.top_fl != null ? `FL${storm.top_fl}` : 'unavailable');
  if ((storm.backing ?? []).length > 0) {
    html += row('Stations', (storm.backing ?? []).join('; '));
  }

  html += '<h4>Trend (30 min)</h4>';
  html += row('Trend', storm.trend ?? 'unknown');
  if (storm.d_peak_db != null) {
    html += row('Peak change', `${storm.d_peak_db >= 0 ? '+' : ''}${Math.round(storm.d_peak_db)} dB`);
  }
  if (storm.area_ratio != null) html += row('Area', `×${storm.area_ratio.toFixed(1)}`);
  if (storm.d_flashes != null) {
    html += row('Flashes change', `${storm.d_flashes >= 0 ? '+' : ''}${storm.d_flashes}`);
  }

  const history = storm.history ?? [];
  if (history.length > 0) {
    html += '<h4>Off track, last 30 min</h4>';
    html += history.map((p) => row(formatHhmmZ(p.at), `${Math.round(p.offtrack_nm ?? 0)} NM`)).join('');
    if (storm.offtrack_nm != null) html += row('Now', `${Math.round(storm.offtrack_nm)} NM`);
  }

  if (storm.estimate) {
    const est = storm.estimate;
    html += '<div class="storm-estimate" data-testid="storm-estimate">';
    html += '<h4>Estimate at current motion</h4>';
    if (est.cpa_nm != null) {
      html += row('Closest to your track', `${Math.round(est.cpa_nm)} NM`
        + (est.cpa_time ? ` at ${formatHhmmZ(est.cpa_time)}` : ''));
    }
    if (est.at_eta_offtrack_nm != null) {
      html += row('Off track when you are abeam', `${Math.round(est.at_eta_offtrack_nm)} NM`);
    }
    html += '<p class="storm-estimate-note">A projection, not an observation: it assumes the'
      + ' cell keeps its current speed and heading, and that you fly the plan on time.</p>';
    html += '</div>';
  }

  if (storm.focus) {
    html += '<div class="storm-actions"><button type="button" class="btn-secondary"'
      + ` data-storm-map="${escapeHtml(storm.id)}">Show on map</button></div>`;
  }
  return `<div class="storm-detail">${html}</div>`;
}

/** The whole "Route ribbon" section: heading, caption, drawing, key.
 *
 *  Re-lays out on resize (the marks are px-sized, so the drawing is measured,
 *  never scaled). `airports` (the same `/live` response's
 *  `route_observations.airports`) gives the hover tooltips (#742) each
 *  airport's name and raw METAR / TAF. Returns a teardown that drops the
 *  observer and the tooltip. */
export function mountRibbon(
  el: HTMLElement,
  ribbon: LiveRibbon,
  storms: LiveStorms | null | undefined,
  handlers: RibbonHandlers,
  airports: AirportObservation[] | null = null,
): () => void {
  const list = storms?.status === 'available' ? storms.storms ?? [] : [];
  const corridorNm = storms?.corridor_nm ?? 30;
  const bands = weatherAvailable(ribbon);

  const draw = () => {
    // `clientWidth` is 0 while the section is collapsed or still hidden;
    // drawing then would bake a 0-width layout in, so wait for a real one.
    const width = Math.max(el.clientWidth, 0);
    if (width < MIN_WIDTH) return;
    // No title here: the page's own section heading already says "Along the
    // route". (The iOS card carries one because it has no section header.)
    el.innerHTML =
      `<div class="ribbon-head">`
      + `<span class="ribbon-caption">${escapeHtml(ribbonCaption(ribbon, corridorNm, formatHhmmZ))}</span></div>`
      + `<div class="ribbon-plot">${ribbonSvg(ribbon, list, corridorNm, width)}</div>`
      + (bands ? ''
        : '<p class="ribbon-fallback-note">Rain and cells unavailable: radar strip within '
          + `${Math.round(ribbon.radar_radius_nm ?? 10)} NM of the route</p>`)
      + ribbonLegendHtml(bands);
    // innerHTML just dropped it; fixed-positioned, so `.ribbon-plot`'s
    // overflow clip does not cut it.
    el.appendChild(tip);
  };

  const tip = document.createElement('div');
  tip.className = 'ribbon-tip';
  tip.setAttribute('role', 'tooltip');
  tip.dataset.testid = 'ribbon-tip';
  tip.hidden = true;
  let tipKey = '';

  /** What the pointer is over, as [cache key, tooltip markup]. */
  const tipFor = (target: Element, clientX: number, clientY: number): [string, string] | null => {
    const stormEl = target.closest<HTMLElement>('[data-ribbon-storm]');
    if (stormEl) {
      const storm = list.find((s) => s.id === stormEl.dataset.ribbonStorm);
      return storm ? [`storm:${storm.id}`, stormTooltipHtml(storm)] : null;
    }
    const stationEl = target.closest<HTMLElement>('[data-ribbon-station]');
    if (stationEl) {
      const i = Number(stationEl.dataset.ribbonStation);
      const st = (ribbon.stations ?? [])[i];
      return st ? [`station:${i}`, stationTooltipHtml(st, airportFor(st, airports))] : null;
    }
    const sigmetEl = target.closest<HTMLElement>('[data-ribbon-sigmet]');
    if (sigmetEl) {
      const i = Number(sigmetEl.dataset.ribbonSigmet);
      const s = (ribbon.sigmets ?? [])[i];
      return s ? [`sigmet:${i}`, sigmetTooltipHtml(s)] : null;
    }
    const segEl = target.closest<HTMLElement>('[data-ribbon-seg]');
    if (segEl) {
      const i = Number(segEl.dataset.ribbonSeg);
      const seg = (ribbon.segments ?? [])[i];
      return seg ? [`seg:${i}`, segmentTooltipHtml(seg)] : null;
    }
    if (!bands) return null;
    const svg = el.querySelector('.ribbon-svg');
    if (!svg || !svg.contains(target)) return null;
    const rect = svg.getBoundingClientRect();
    const band = bandAt(ribbon, clientX - rect.left, clientY - rect.top, rect.width);
    if (!band) return null;
    // A core is a cell: its own tooltip says more than the band's.
    const storm = band.storm_id ? list.find((s) => s.id === band.storm_id) : undefined;
    if (storm) return [`storm:${storm.id}`, stormTooltipHtml(storm)];
    return [`band:${band.id}`, bandTooltipHtml(band)];
  };

  const hideTip = () => {
    tip.hidden = true;
    tipKey = '';
  };

  /** Show the tooltip for whatever is under a window point, or hide it. */
  const showAt = (target: Element, clientX: number, clientY: number) => {
    const hit = tipFor(target, clientX, clientY);
    if (!hit) {
      hideTip();
      return;
    }
    if (hit[0] !== tipKey) {
      tip.innerHTML = hit[1];
      tipKey = hit[0];
    }
    tip.hidden = false;
    // Beside the pointer, flipped to stay inside the window.
    const gap = 14;
    const w = tip.offsetWidth;
    const h = tip.offsetHeight;
    let left = clientX + gap;
    if (left + w > window.innerWidth - 8) left = Math.max(8, clientX - gap - w);
    let top = clientY + gap;
    if (top + h > window.innerHeight - 8) top = Math.max(8, clientY - gap - h);
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  };

  let pointer: { x: number; y: number } | null = null;
  const onMove = (ev: PointerEvent) => {
    // Hover is a mouse affordance: a touch keeps its tap (map / detail).
    if (ev.pointerType !== 'mouse' || !(ev.target instanceof Element)) return;
    pointer = { x: ev.clientX, y: ev.clientY };
    showAt(ev.target, ev.clientX, ev.clientY);
  };
  const onLeave = () => {
    pointer = null;
    hideTip();
  };
  // The page scrolls under a still mouse: re-read what is under it now, so
  // the fixed tooltip neither floats off its mark nor vanishes from one.
  const onScroll = () => {
    if (!pointer || tip.hidden) return;
    const under = document.elementFromPoint(pointer.x, pointer.y);
    if (under && el.contains(under)) showAt(under, pointer.x, pointer.y);
    else hideTip();
  };

  const focusFor = (token: string): LiveFocus | null => {
    const [kind, idx] = token.split(':');
    const i = Number(idx);
    if (kind === 'sigmet') return (ribbon.sigmets ?? [])[i]?.focus ?? null;
    if (kind === 'station') return (ribbon.stations ?? [])[i]?.focus ?? null;
    if (kind === 'segment') return (ribbon.segments ?? [])[i]?.focus ?? null;
    return null;
  };

  const activate = (target: HTMLElement, clientX: number | null) => {
    const stormEl = target.closest<HTMLElement>('[data-ribbon-storm]');
    if (stormEl) {
      const storm = list.find((s) => s.id === stormEl.dataset.ribbonStorm);
      if (storm) handlers.onStorm(storm);
      return;
    }
    const focusEl = target.closest<HTMLElement>('[data-ribbon-focus]');
    if (focusEl) {
      const focus = focusFor(focusEl.dataset.ribbonFocus ?? '');
      if (focus) handlers.onFocus(focus);
      return;
    }
    // Anywhere else in the weather zones: the stretch of route under the tap.
    const zone = target.closest<HTMLElement>('.ribbon-zone-hit');
    if (zone && clientX != null) {
      const svg = el.querySelector('.ribbon-svg');
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const focus = segmentFocusAt(ribbon, clientX - rect.left, rect.width);
      if (focus) handlers.onFocus(focus);
    }
  };

  const onClick = (ev: MouseEvent) => {
    if (!(ev.target instanceof Element)) return;
    activate(ev.target as HTMLElement, ev.clientX);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    if (!(ev.target instanceof Element)) return;
    const hit = (ev.target as HTMLElement).closest<HTMLElement>('[data-ribbon-storm],[data-ribbon-focus]');
    if (!hit) return;
    ev.preventDefault();
    activate(hit, null);
  };

  el.addEventListener('click', onClick);
  el.addEventListener('keydown', onKey);
  el.addEventListener('pointermove', onMove);
  el.addEventListener('pointerleave', onLeave);
  window.addEventListener('scroll', onScroll, { passive: true });
  draw();

  let observer: ResizeObserver | null = null;
  if (typeof ResizeObserver !== 'undefined') {
    let last = el.clientWidth;
    observer = new ResizeObserver(() => {
      // Only a real width change: a repaint on every scroll-driven reflow
      // would rebuild the SVG constantly.
      if (Math.abs(el.clientWidth - last) < 8 && el.querySelector('.ribbon-svg')) return;
      last = el.clientWidth;
      draw();
    });
    observer.observe(el);
  }

  return () => {
    observer?.disconnect();
    el.removeEventListener('click', onClick);
    el.removeEventListener('keydown', onKey);
    el.removeEventListener('pointermove', onMove);
    el.removeEventListener('pointerleave', onLeave);
    window.removeEventListener('scroll', onScroll);
    tip.remove();
  };
}
