/** Cell overlay (#656) — the pure half: types, colours, stamp matching, words.
 *
 * The home node's cell analysis pushes one display file per radar frame
 * (`/api/observed/cells/{stamp}.json`). Two maps draw it — the forecast map's
 * "Now" tab and the briefing route map — through one renderer
 * (`cells-overlay.ts`); everything here carries no Leaflet import so the rules
 * can be unit-tested in node.
 *
 * Look: the standalone prototype (`python -m weatherbrief.observed.cells map`):
 * outlines core35 white, core41 black, rain20 grey (off by default); markers
 * coloured by **trend** (evolution, not safety — no red/green "go"); a magenta
 * arrow to where the cell would be in 30 min, only when motion is
 * `available`.
 *
 * Product voice: it directs attention, it never gives a verdict. It is
 * labelled experimental everywhere it appears.
 */

import { escapeHtml } from '../utils';

export type TrendState = 'developing' | 'decaying' | 'steady' | 'mixed' | 'new';

export const TREND_COLOURS: Record<TrendState, string> = {
  developing: '#d7263d',
  decaying: '#1b6ca8',
  steady: '#7a7a7a',
  mixed: '#f18f01',
  new: '#ffffff',
};
const UNKNOWN_TREND_COLOUR = '#cccccc';

export const ARROW_COLOUR = '#b000b5';

export type CellTier = 'rain20' | 'core35' | 'core41';

export const OUTLINE_STYLE: Record<CellTier, { color: string; weight: number }> = {
  rain20: { color: '#8a8a8a', weight: 1 },
  core35: { color: '#ffffff', weight: 1.5 },
  core41: { color: '#000000', weight: 1.5 },
};

export const TIER_LABEL: Record<CellTier, string> = {
  rain20: 'Rain area ≥20 dBZ',
  core35: 'Core ≥35 dBZ',
  core41: 'Core ≥41 dBZ',
};

export interface CellTrend {
  state: TrendState | string;
  window_min: number | null;
  d_peak_db?: number | null;
  area_ratio?: number | null;
  d_flashes?: number | null;
}

export interface CellMotion {
  status: 'available' | 'withheld' | 'unsupported' | 'no_pair' | string;
  reason: string | null;
  speed_kt: number | null;
  toward_deg: number | null;
}

export interface DisplayCell {
  id: string;
  tier: CellTier | string;
  lat: number;
  lon: number;
  area_km2: number | null;
  peak_dbz: number | null;
  rate_peak_mm_h: number | null;
  flashes: number | null;
  top_fl: number | null;
  truncated: boolean;
  age_min: number | null;
  event: string;
  trend: CellTrend;
  motion: CellMotion;
  /** `[lat, lon]` after `arrow_minutes` at the current motion, or null. */
  arrow: [number, number] | null;
}

/** One display file (`DISPLAY_SCHEMA = observed-cells-display/1`). */
export interface CellDisplay {
  schema: string;
  policy_version: string;
  code_revision: string | null;
  valid_time: string;
  window_minutes: number | null;
  times: { radar: string | null; rate: string | null; lightning: string | null; cloud_top: string | null };
  /** How much older the rain rate is than the radar (#666: newest on disk). */
  rate_age_min?: number | null;
  /** 0 when first published; 1 once its lightning landed (#666). */
  revision?: number;
  unavailable: Array<{ what: string; reason: string }>;
  rain_min_area_km2: number;
  arrow_minutes: number;
  outlines: Partial<Record<CellTier, Array<Array<[number, number]>>>>;
  cells: DisplayCell[];
}

export interface CellFrame {
  stamp: string;
  /** `<stamp>.r<n>`, the newest revision (#666); absent on an older server. */
  key?: string;
  revision?: number;
  valid_time: string;
  received_at: string;
  age_minutes: number;
}

/** What goes into `url_template`'s `{stamp}`: the frame's newest revision
 *  (immutable, so a cache by URL can never hold an older one), or the bare
 *  stamp on a server from before revisions. */
export function frameKey(frame: CellFrame): string {
  return frame.key ?? frame.stamp;
}

/** The rain rate's own time when it is not the radar's (#666: the newest
 *  RATE on disk is used rather than waiting for the frame's slot), else null. */
export function rateAsOf(display: CellDisplay | null | undefined): string | null {
  const rate = display?.times?.rate;
  const radar = display?.times?.radar;
  if (!rate || !radar) return null;
  return new Date(rate).getTime() === new Date(radar).getTime() ? null : rate;
}

/** `/api/observed/cells/frames`. */
export interface CellFramesInfo {
  enabled: boolean;
  frames: CellFrame[];
  newest: CellFrame | null;
  stale: boolean;
  stale_after_minutes: number;
  unavailable_since: string | null;
  url_template: string;
}

export function trendColour(state: string | null | undefined): string {
  return (state && (TREND_COLOURS as Record<string, string>)[state]) || UNKNOWN_TREND_COLOUR;
}

/** Draw a motion arrow only for measured motion — never for withheld or
 *  unsupported motion, even if a stale arrow point were present. */
export function hasArrow(cell: DisplayCell): boolean {
  return cell.motion?.status === 'available' && Array.isArray(cell.arrow) && cell.arrow.length === 2;
}

export type CellMatch =
  | { state: 'ok'; frame: CellFrame }
  | { state: 'disabled' }
  | { state: 'unavailable'; since: string | null };

/**
 * Which overlay to draw beside a radar frame.
 *
 * The overlay for the **same stamp** as the drawn radar frame when there is
 * one, else the newest at or before it (`radarStamp` null — no radar drawn —
 * means the newest). Then the overlay's *own* age decides: past
 * `stale_after_minutes` nothing is drawn and the map says "unavailable since"
 * that overlay's time. Stamps are `YYYYMMDDTHHMM`, so string order is time
 * order.
 */
export function matchCellFrame(
  info: CellFramesInfo | null | undefined,
  radarStamp: string | null,
  now: Date = new Date(),
): CellMatch {
  if (!info) return { state: 'unavailable', since: null };
  if (!info.enabled) return { state: 'disabled' };
  const frames = info.frames ?? [];
  const pick = radarStamp
    ? frames.find((f) => f.stamp === radarStamp) ?? frames.find((f) => f.stamp <= radarStamp)
    : frames[0];
  if (!pick) {
    return { state: 'unavailable', since: frames[0]?.valid_time ?? info.unavailable_since ?? null };
  }
  const age = (now.getTime() - new Date(pick.valid_time).getTime()) / 60000;
  if (!Number.isFinite(age) || age > info.stale_after_minutes) {
    return { state: 'unavailable', since: pick.valid_time };
  }
  return { state: 'ok', frame: pick };
}

export function hhmmZ(iso: string | null | undefined): string {
  if (!iso) return '--:--Z';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '--:--Z';
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}Z`;
}

/** The overlay's badge line — its own time, never the radar's. */
export function cellsBadge(match: CellMatch, display: CellDisplay | null, now: Date = new Date()): string {
  if (match.state === 'disabled') return 'Cell analysis: not available on this server';
  if (match.state === 'unavailable') {
    return match.since
      ? `Cell analysis unavailable since ${hhmmZ(match.since)}`
      : 'Cell analysis unavailable (nothing received yet)';
  }
  const valid = match.frame.valid_time;
  const age = Math.max(0, Math.round((now.getTime() - new Date(valid).getTime()) / 60000));
  const count = display ? ` · ${display.cells.length} cells` : '';
  const missing = display?.unavailable?.length
    ? ` · no ${display.unavailable.map((u) => u.what).join(', ')}`
    : '';
  return `Cells ${hhmmZ(valid)} · ${age} min old${count}${missing} · experimental`;
}

const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];

export function compass(deg: number | null | undefined): string {
  if (deg == null || !Number.isFinite(deg)) return '–';
  return COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
}

/** "moving NE at 9 kt", or why there is no motion — in words, not codes. */
export function motionText(m: CellMotion | null | undefined): string {
  if (!m) return 'motion unknown';
  if (m.status === 'available') {
    if (m.toward_deg == null || (m.speed_kt ?? 0) < 1) return 'nearly stationary';
    return `moving ${compass(m.toward_deg)} at ${Math.round(m.speed_kt ?? 0)} kt`;
  }
  if (m.status === 'withheld') return 'motion withheld: split/merge this frame';
  if (m.status === 'unsupported') return 'motion withheld: too little of the cell in matched tiles';
  if (m.status === 'no_pair') return 'no motion yet: no earlier radar frame to compare';
  return `motion ${m.status}${m.reason ? `: ${m.reason}` : ''}`;
}

function signed(v: number): string {
  return `${v > 0 ? '+' : ''}${v}`;
}

export function trendText(t: CellTrend | null | undefined): string {
  if (!t) return 'trend unknown';
  if (t.state === 'new' || t.window_min == null) return 'new (less than 15 min of history)';
  const parts: string[] = [];
  if (t.d_peak_db != null) parts.push(`peak ${signed(t.d_peak_db)} dB`);
  if (t.area_ratio != null) parts.push(`area ×${t.area_ratio}`);
  if (t.d_flashes != null) parts.push(`flashes ${signed(t.d_flashes)}`);
  return `${t.state} over ${Math.round(t.window_min)} min${parts.length ? ` (${parts.join(', ')})` : ''}`;
}

function value(v: number | null | undefined, unit = ''): string {
  return v == null ? '–' : `${v}${unit}`;
}

/** Popup for one cell: measurements, age and lineage, trend with its numbers,
 *  motion. Descriptive only. */
export function cellPopupHtml(c: DisplayCell, rateTime: string | null = null): string {
  const tier = (TIER_LABEL as Record<string, string>)[c.tier] ?? c.tier;
  const asOf = rateTime && c.rate_peak_mm_h != null ? ` (as of ${hhmmZ(rateTime)})` : '';
  const lines = [
    `<b>${escapeHtml(tier)}</b> <span class="cells-exp">experimental</span>`,
    `peak <b>${value(c.peak_dbz, ' dBZ')}</b> · area ${value(c.area_km2, ' km²')}`,
    `rain rate peak ${value(c.rate_peak_mm_h, ' mm/h')}${asOf} · lightning ${value(c.flashes)}`
      + (c.top_fl != null ? ` · cloud top FL${c.top_fl}` : ''),
    `age ${value(c.age_min, ' min')} (${escapeHtml(c.event ?? '')}) · ${escapeHtml(trendText(c.trend))}`,
    escapeHtml(motionText(c.motion)),
  ];
  if (c.truncated) lines.push('<i>partly outside radar coverage</i>');
  return lines.join('<br>');
}

/** Legend: trend colours, outline tiers, what the arrow means, the caveat. */
export function cellsLegendHtml(): string {
  const swatch = (colour: string, round: boolean) =>
    `<span class="cells-sw" style="background:${colour};border-radius:${round ? '50%' : '1px'}"></span>`;
  const trends = (Object.keys(TREND_COLOURS) as TrendState[])
    .map((k) => `${swatch(TREND_COLOURS[k], true)}${k}`).join(' ');
  const tiers = (Object.keys(OUTLINE_STYLE) as CellTier[])
    .map((k) => `${swatch(OUTLINE_STYLE[k].color, false)}${escapeHtml(TIER_LABEL[k])}`).join(' ');
  return [
    '<b>Radar cells</b> <span class="cells-exp">experimental</span>',
    `Marker colour = trend over ~30 min: ${trends}`,
    `Outlines: ${tiers}`,
    `<span style="color:${ARROW_COLOUR}">━</span> arrow = position in 30 min if the recent motion continues`,
    '<span class="cells-muted">Provisional thresholds: cells often flicker in as "new", rain rate can show artefact peaks and lag the radar by ~10 min. Describes evolution, not safety.</span>',
  ].join('<br>');
}
