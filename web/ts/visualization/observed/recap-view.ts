/** Debrief recap and single-tick replay (#769) — the drawing.
 *
 * Rules live in `helpers/debrief-replay.ts`; the ribbon itself is drawn by
 * `ribbon-view.mountRibbon` from the recap turned into a `LiveRibbon`. This
 * file adds what only the recap has, on the ribbon's own x scale
 * (`xForNm`, `INSET`) so the lanes line up under it:
 *
 * - **Seen at**: the radar frame each stretch was read from, or why not;
 * - **Forecast**: the selected pack's worst-of route-weather row;
 * - **Alerts**: when the app raised, worsened, improved or cleared something,
 *   at the aircraft's position then.
 *
 * Plus the tick-chip row (during the window too), the mode bar, and the
 * distance × time grid (experiment, owner decides keep or drop).
 *
 * Web only for now: iOS display of the recap is decided after this works.
 */

import { escapeHtml } from '../../utils';
import { formatHhmmZ, isoToMs, sameInstant } from '../../helpers/live-layer';
import {
  departureGapText,
  gridRows,
  recapEmptyTitle,
  recapRibbon,
  segmentSeenAt,
  segmentStatusText,
  timeBaseCaption,
  type ForecastBand,
  type GridRow,
} from '../../helpers/debrief-replay';
import type { LiveStorm, LiveTickEntry, RecapAlert, RecapForecast, RecapResponse } from '../../store/types';
import { INSET, radarFill, xForNm } from './ribbon-core';
import { mountRibbon } from './ribbon-view';

const MIN_WIDTH = 240;

export interface RecapHandlers {
  /** Replay this recorded tick. */
  onTick: (tickAt: string) => void;
  /** A point on the route was clicked: replay the tick the aircraft was there. */
  onRoutePoint: (alongNm: number) => void;
  /** Open a cell's detail. */
  onStorm: (storm: LiveStorm) => void;
}

// --- Tick chips -------------------------------------------------------------

/** The row of recorded tick times. `selected` marks the tick on screen;
 *  `extra` is appended inside the row (the selected route point's chip). */
export function tickChipsHtml(ticks: LiveTickEntry[], selected: string | null, extra = ''): string {
  const chips = ticks.filter((t) => t.tick_at).map((t) => {
    const active = selected != null && sameInstant(t.tick_at, selected);
    const alerts = (t.alert_count ?? 0) > 0
      ? ` <span class="tick-chip-alerts" aria-label="${t.alert_count} alerts">●</span>` : '';
    return `<button type="button" class="tick-chip${active ? ' tick-chip-active' : ''}"`
      + ` data-tick="${escapeHtml(t.tick_at as string)}" aria-pressed="${active}">`
      + `${escapeHtml(formatHhmmZ(t.tick_at))}${alerts}</button>`;
  }).join('');
  return `<div class="tick-chips" data-testid="tick-chips" role="group" aria-label="Recorded ticks">`
    + `<span class="tick-chips-label">Recorded</span>${chips}${extra}</div>`;
}

// --- Mode bar ---------------------------------------------------------------

/** "Viewing 17:23Z · route map imagery stays live · Back to live". */
export function modeBarHtml(text: string, backLabel: string): string {
  return `<span class="mode-bar-text" data-testid="mode-bar-text">${escapeHtml(text)}</span>`
    + '<span class="mode-bar-note">Route map radar &amp; satellite stay live (past frames are not kept)</span>'
    + `<button type="button" class="btn-secondary mode-bar-back" data-testid="mode-bar-back">`
    + `${escapeHtml(backLabel)}</button>`;
}

// --- Lanes ---------------------------------------------------------------------

const LANE_LABEL_H = 12;
const LANE_ROW_H = 14;
const LANE_GAP = 4;
const LANE_H = LANE_LABEL_H + LANE_ROW_H + LANE_GAP;

const FORECAST_FILL: Record<string, string> = {
  green: 'var(--green)',
  amber: 'var(--amber)',
  red: 'var(--red)',
};

function laneLabel(y: number, text: string): string {
  return `<text x="${INSET}" y="${y + LANE_LABEL_H - 3}" class="recap-lane-label">${escapeHtml(text)}</text>`;
}

/** Alerts that made the page say something, raised (worse / better) or cleared. */
function alertGlyph(a: RecapAlert, x: number, y: number): string {
  const worse = a.event === 'raised' && a.direction !== 'better';
  const cls = a.event === 'cleared' ? 'recap-alert-cleared' : worse ? 'recap-alert-worse' : 'recap-alert-better';
  const word = a.event === 'cleared' ? 'cleared' : worse ? (a.direction === 'worse' ? 'worsened' : 'raised') : 'improved';
  const label = `${formatHhmmZ(a.at)} ${word}: ${a.message ?? a.kind ?? ''}`;
  const shape = worse
    ? `<path d="M${x.toFixed(1)} ${y + 1} l5 10 h-10 z"/>`
    : `<circle cx="${x.toFixed(1)}" cy="${y + 7}" r="4.5"/>`;
  return `<g class="recap-alert ${cls}${a.tier === 'alert' ? ' recap-alert-tier' : ''}"`
    + ` data-alert-at="${escapeHtml(a.at ?? '')}" tabindex="0" role="button"`
    + ` aria-label="${escapeHtml(label)}"><title>${escapeHtml(label)}</title>${shape}</g>`;
}

/** The three lanes under the recap ribbon, at a known width. Pure. */
export function recapLanesSvg(
  recap: RecapResponse,
  bands: ForecastBand[],
  forecast: RecapForecast | null,
  width: number,
): string {
  const routeNm = Math.max(recap.route_nm ?? 1, 1);
  const x = (nm: number) => xForNm(nm, routeNm, width);
  let y = 0;
  let inner = '';

  // Seen at: one cell per stretch, its radar frame time.
  inner += laneLabel(y, 'Seen at (radar frame when the aircraft was there)');
  const rowY = y + LANE_LABEL_H;
  inner += `<rect class="recap-route-hit" x="${INSET}" y="${rowY}" width="${Math.max(width - 2 * INSET, 1)}"`
    + ` height="${LANE_ROW_H}" fill="transparent"/>`;
  inner += recap.segments.map((s, i) => {
    const x0 = x(s.from_nm);
    const x1 = x(s.to_nm);
    const w = Math.max(x1 - x0, 1);
    const seen = segmentSeenAt(s);
    const text = seen || segmentStatusText(s.status);
    const observed = s.status === 'observed';
    const label = observed
      ? `${Math.round(s.from_nm)}–${Math.round(s.to_nm)} NM: radar frame ${seen}, aircraft ${formatHhmmZ(s.at)}`
      : `${Math.round(s.from_nm)}–${Math.round(s.to_nm)} NM: ${text}`;
    return `<g class="recap-seen${observed ? ' recap-seen-observed' : ' recap-seen-missing'}"`
      + (observed && s.tick_at ? ` data-seg-tick="${escapeHtml(s.tick_at)}" tabindex="0" role="button"` : '')
      + ` data-recap-seg="${i}" aria-label="${escapeHtml(label)}"><title>${escapeHtml(label)}</title>`
      + `<rect x="${x0.toFixed(1)}" y="${rowY}" width="${w.toFixed(1)}" height="${LANE_ROW_H}"/>`
      + (w >= 30 && text
        ? `<text x="${(x0 + w / 2).toFixed(1)}" y="${rowY + LANE_ROW_H - 3}" text-anchor="middle">${escapeHtml(text)}</text>`
        : '')
      + '</g>';
  }).join('');
  y += LANE_H;

  // Forecast: the worst-of route-weather row of the selected pack.
  // Named by what it grades: the observed lane above sees radar and
  // lightning only, so an icing or cloud grade has nothing to match there.
  const what = 'Forecast, worst of icing/cloud/precip/turb/convective';
  const fcLabel = forecast
    ? `${what} (pack ${formatHhmmZ(forecast.pack_timestamp)}${forecast.chosen_by === 'default' ? ', newest before departure' : ''})`
    : what;
  inner += laneLabel(y, fcLabel);
  const fcY = y + LANE_LABEL_H;
  if (bands.length === 0) {
    inner += `<text x="${INSET}" y="${fcY + LANE_ROW_H - 3}" class="recap-lane-empty" data-testid="forecast-lane-empty">`
      + 'No forecast row for this pack</text>';
  } else {
    inner += `<g data-testid="forecast-lane">${bands.map((b) => {
      const x0 = x(b.from_nm);
      const w = Math.max(x(b.to_nm) - x0, 1);
      const label = `${Math.round(b.from_nm)}–${Math.round(b.to_nm)} NM: ${b.severity}`
        + (b.advisories.length ? ` (${b.advisories.join(', ')})` : '');
      return `<rect class="recap-fc recap-fc-${escapeHtml(b.severity)}" data-severity="${escapeHtml(b.severity)}"`
        + ` x="${x0.toFixed(1)}" y="${fcY}" width="${w.toFixed(1)}" height="${LANE_ROW_H}"`
        + ` fill="${FORECAST_FILL[b.severity] ?? 'var(--text-muted)'}"><title>${escapeHtml(label)}</title></rect>`;
    }).join('')}</g>`;
  }
  y += LANE_H;

  // Alerts: on the aircraft's position at the tick they were raised.
  const placed = recap.alerts.filter((a) => a.aircraft_nm != null);
  inner += laneLabel(y, placed.length ? 'Alerts (at the aircraft position then)' : 'Alerts: none raised');
  const alY = y + LANE_LABEL_H;
  inner += placed.map((a) => alertGlyph(a, x(a.aircraft_nm as number), alY)).join('');
  y += LANE_H;

  return `<svg class="recap-lanes-svg" width="${width.toFixed(0)}" height="${y}" viewBox="0 0 ${width.toFixed(0)} ${y}"`
    + ' role="group" aria-label="Recap lanes">'
    + inner + '</svg>';
}

// --- Distance × time grid -------------------------------------------------------------

const GRID_GUTTER = 48;

/** The ribbon stacked once per tick (rows = ticks, x = along route), the
 *  stretches the recap took outlined: the diagonal. Pure. */
export function gridSvg(rows: GridRow[], routeNm: number, alerts: RecapAlert[], width: number): string {
  if (rows.length === 0) return '';
  const rowH = Math.max(6, Math.min(14, Math.floor(360 / rows.length)));
  const plotW = Math.max(width - GRID_GUTTER - INSET, 1);
  const x = (nm: number) => GRID_GUTTER + Math.min(Math.max(nm / Math.max(routeNm, 1), 0), 1) * plotW;
  const labelEvery = Math.max(1, Math.ceil(12 / rowH));
  const alertMs = new Set(alerts.map((a) => isoToMs(a.at)).filter((ms) => !isNaN(ms)));
  let inner = '';
  rows.forEach((r, i) => {
    const y = i * rowH;
    const label = formatHhmmZ(r.tick_at);
    const hasAlert = alertMs.has(isoToMs(r.tick_at));
    inner += `<g class="grid-row" data-grid-tick="${escapeHtml(r.tick_at)}" tabindex="0" role="button"`
      + ` aria-label="Replay ${escapeHtml(label)}"><title>Replay ${escapeHtml(label)}</title>`
      + `<rect class="grid-row-hit" x="0" y="${y}" width="${width}" height="${rowH}" fill="transparent"/>`;
    if (i % labelEvery === 0) {
      inner += `<text x="${GRID_GUTTER - 6}" y="${y + rowH - 1}" text-anchor="end" class="grid-time">${escapeHtml(label)}</text>`;
    }
    for (const c of r.segments) {
      const x0 = x(c.from_nm);
      const w = Math.max(x(c.to_nm) - x0, 1);
      inner += `<rect x="${x0.toFixed(1)}" y="${y + 0.5}" width="${w.toFixed(1)}" height="${rowH - 1}"`
        + ` fill="${radarFill(c.seg)}"${c.diagonal ? ' class="grid-diagonal"' : ' class="grid-cell"'}/>`;
    }
    if (r.flown_nm != null) {
      inner += `<circle cx="${x(r.flown_nm).toFixed(1)}" cy="${y + rowH / 2}" r="${Math.min(3, rowH / 3)}" class="grid-aircraft"/>`;
    }
    if (hasAlert) {
      inner += `<path d="M${width - INSET + 4} ${y + rowH / 2} l6 -4 v8 z" class="grid-alert"/>`;
    }
    inner += '</g>';
  });
  const h = rows.length * rowH;
  return `<svg class="grid-svg" width="${width.toFixed(0)}" height="${h}" viewBox="0 0 ${width.toFixed(0)} ${h}"`
    + ' role="group" aria-label="Distance by time: one row per recorded tick">' + inner + '</svg>';
}

// --- Mount --------------------------------------------------------------------------

export interface RecapModel {
  recap: RecapResponse;
  forecast: RecapForecast | null;
  bands: ForecastBand[];
  ticks: LiveTickEntry[];
  /** Grid shown (toggle state; wide screens default it on). */
  showGrid: boolean;
}

/** Wide enough to show the grid without a toggle. */
export const GRID_WIDE_PX = 900;

/** A mounted recap: `setForecast` swaps the forecast row (pack picker)
 *  without remounting; kept on the model, so a section collapsed at the time
 *  draws the new row when it is next laid out. */
export interface RecapHandle {
  setForecast: (forecast: RecapForecast | null, bands: ForecastBand[]) => void;
  teardown: () => void;
}

/** The recap block: caption, ribbon, lanes, grid toggle and grid.
 *  `onGridToggle` reports the toggle so the caller keeps it across
 *  re-renders. */
export function mountRecap(
  el: HTMLElement,
  initial: RecapModel,
  handlers: RecapHandlers,
  onGridToggle: (on: boolean) => void,
): RecapHandle {
  const model = { ...initial };
  const { recap } = model;
  if (recap.status !== 'ok') {
    el.innerHTML = `<div class="recap-empty" data-testid="recap-empty">`
      + `<strong>${escapeHtml(recapEmptyTitle(recap.status))}</strong>`
      + (recap.message ? `<p class="muted">${escapeHtml(recap.message)}</p>` : '')
      + '</div>';
    return { setForecast: () => {}, teardown: () => { el.innerHTML = ''; } };
  }

  const caption = recapCaption(recap, model.forecast);
  el.innerHTML = `<div class="recap" data-testid="recap">`
    + `<div class="recap-caption" data-testid="recap-caption">${escapeHtml(caption)}</div>`
    + (recap.capped ? `<p class="recap-capped" data-testid="recap-capped">${escapeHtml(
      'Recording stopped at the size cap: later stretches were not recorded (not "not reached").')}</p>` : '')
    + '<div class="recap-ribbon"></div>'
    + '<div class="recap-lanes"></div>'
    + '<label class="recap-grid-toggle"><input type="checkbox" data-grid-toggle'
    + `${model.showGrid ? ' checked' : ''}> Show over time (experiment)</label>`
    + `<div class="recap-grid"${model.showGrid ? '' : ' hidden'}></div>`
    + '</div>';

  const ribbonEl = el.querySelector<HTMLElement>('.recap-ribbon')!;
  const lanesEl = el.querySelector<HTMLElement>('.recap-lanes')!;
  const gridEl = el.querySelector<HTMLElement>('.recap-grid')!;
  const { ribbon, storms } = recapRibbon(recap);
  const ribbonTeardown = mountRibbon(ribbonEl, ribbon, storms, {
    onFocus: () => { /* recap stretches carry no map focus */ },
    onStorm: handlers.onStorm,
    onSegment: (i) => {
      const seg = recap.segments[i];
      if (seg?.status === 'observed' && seg.tick_at) handlers.onTick(seg.tick_at);
      else if (seg) handlers.onRoutePoint((seg.from_nm + seg.to_nm) / 2);
    },
  });

  const rows = gridRows(model.ticks, recap);
  const draw = () => {
    const width = lanesEl.clientWidth;
    if (width < MIN_WIDTH) return;
    lanesEl.innerHTML = recapLanesSvg(recap, model.bands, model.forecast, width);
    if (!gridEl.hidden) {
      gridEl.innerHTML = rows.length
        ? gridSvg(rows, recap.route_nm ?? 1, recap.alerts, width)
        : '<p class="muted">No per-tick lanes loaded.</p>';
    }
  };

  const onClick = (ev: MouseEvent) => {
    const target = ev.target as Element | null;
    if (!target) return;
    const seg = target.closest<SVGElement>('[data-seg-tick]');
    if (seg) { handlers.onTick(seg.getAttribute('data-seg-tick') as string); return; }
    const alert = target.closest<SVGElement>('[data-alert-at]');
    if (alert && alert.getAttribute('data-alert-at')) {
      handlers.onTick(alert.getAttribute('data-alert-at') as string);
      return;
    }
    const row = target.closest<SVGElement>('[data-grid-tick]');
    if (row) { handlers.onTick(row.getAttribute('data-grid-tick') as string); return; }
    // Anywhere else on the "seen at" lane: that point of the route.
    const lane = target.closest('.recap-seen, .recap-route-hit');
    const svg = target.closest('svg.recap-lanes-svg');
    if (lane && svg) {
      const rect = svg.getBoundingClientRect();
      const f = (ev.clientX - rect.left - INSET) / Math.max(rect.width - 2 * INSET, 1);
      handlers.onRoutePoint(Math.min(Math.max(f, 0), 1) * Math.max(recap.route_nm ?? 1, 1));
    }
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const hit = (ev.target as Element | null)?.closest('[data-seg-tick],[data-alert-at],[data-grid-tick]');
    if (!hit) return;
    ev.preventDefault();
    hit.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  };
  const toggle = el.querySelector<HTMLInputElement>('[data-grid-toggle]');
  const onToggle = () => {
    gridEl.hidden = !toggle?.checked;
    onGridToggle(!!toggle?.checked);
    draw();
  };
  lanesEl.addEventListener('click', onClick);
  gridEl.addEventListener('click', onClick);
  lanesEl.addEventListener('keydown', onKey);
  gridEl.addEventListener('keydown', onKey);
  toggle?.addEventListener('change', onToggle);
  draw();

  let observer: ResizeObserver | null = null;
  if (typeof ResizeObserver !== 'undefined') {
    let last = lanesEl.clientWidth;
    observer = new ResizeObserver(() => {
      if (Math.abs(lanesEl.clientWidth - last) < 8 && lanesEl.querySelector('svg')) return;
      last = lanesEl.clientWidth;
      draw();
    });
    observer.observe(lanesEl);
  }

  return {
    setForecast: (forecast, bands) => {
      model.forecast = forecast;
      model.bands = bands;
      const cap = el.querySelector<HTMLElement>('[data-testid="recap-caption"]');
      if (cap) cap.textContent = recapCaption(recap, forecast);
      // Draws now when laid out; else the ResizeObserver draws it on expand.
      draw();
    },
    teardown: () => {
      observer?.disconnect();
      ribbonTeardown();
      lanesEl.removeEventListener('click', onClick);
      gridEl.removeEventListener('click', onClick);
      lanesEl.removeEventListener('keydown', onKey);
      gridEl.removeEventListener('keydown', onKey);
      toggle?.removeEventListener('change', onToggle);
    },
  };
}

/** "Recap · actual times · forecast for 16:45Z departure, flown 17:40Z". */
export function recapCaption(recap: RecapResponse, forecast: RecapForecast | null): string {
  return [
    'Recap',
    timeBaseCaption(recap),
    departureGapText(forecast?.planned_departure, recap.departed_at, recap.cancelled ? 'planned' : recap.time_base),
  ].filter(Boolean).join(' · ');
}
