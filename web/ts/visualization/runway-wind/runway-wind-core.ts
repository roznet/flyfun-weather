/** Runway + wind widget (#758) — the pure half: geometry and words.
 *
 * ===========================================================================
 * SYNC — paired with
 * app/flyfun-weather/flyfun-weather/Views/Shared/RunwayWindRules.swift.
 * Same constants, same functions, same strings; the cases in
 * web/tests/unit/runway-wind-core.test.ts and
 * flyfun-weatherTests/RunwayWindRulesTests.swift are one for one.
 *
 * The widget draws one airport: its runways north-up at their TRUE heading,
 * through the centre (schematic, not a chart: parallels side by side, length
 * roughly proportional), and the wind as an arrow from the upwind rim toward
 * the centre — the arrow points the way the wind BLOWS, drawn on the side it
 * comes FROM. Runway idents are the painted (magnetic) numbers; headings and
 * winds are both true, so no magnetic variation is applied. Do not "fix" that.
 *
 * Geometry is in a unit square (0..1, y down, north up), never pixels, so the
 * renderers (`runway-wind-view.ts`, SwiftUI `RunwayWindView`) scale it to any
 * box. Rounding of knots is `roundHalfAway` (Swift's `.rounded()`), never
 * `Math.round`, which rounds -2.5 to -2.
 *
 * Observations only: nothing here grades anything. The crosswind figure takes
 * the colour of the existing wind advisory the server computed (the METAR/TAF
 * table's); there is no new threshold here.
 * ===========================================================================
 */

import { GUST_DISPLAY_MIN_EXCESS_KT, formatHeading, formatWind } from '../../units';
import { roundHalfAway } from '../observed/ribbon-core';
import { isoToMs } from '../../helpers/live-layer';
import type {
  AirportObservation,
  EndComponents,
  RunwayInfo,
  RunwayWindPicture,
  WindAtAirport,
  WindSample,
} from '../../store/types';

export type RunwayWindSize = 'compact' | 'regular' | 'inline';

// --- Geometry constants (unit square; mirrors RunwayWindRules) ---------------

export const CENTER = 0.5;
/** Dial rim: the wind arrow starts here, on the upwind side. */
export const RIM_R = 0.46;
/** Half-length of the longest runway. */
export const BAR_MAX_HALF = 0.3;
/** Shortest bar as a fraction of the longest, so short strips stay visible. */
export const BAR_MIN_RATIO = 0.4;
/** Distance between adjacent parallel runway centre lines. */
export const PARALLEL_GAP = 0.08;
/** Two runways within this many degrees (mod 180) are drawn as parallels. */
export const PARALLEL_TOLERANCE_DEG = 10;
/** Ident labels sit this far beyond the bar end, on the approach side. */
export const LABEL_GAP = 0.07;
/** Parallel runways' labels are pushed this many times further apart than
 *  their bars, so "05L" and "05R" don't overprint. */
export const LABEL_SPREAD = 2.5;
/** Arrow length at and above `ARROW_FULL_SCALE_KT`. */
export const ARROW_MAX_LEN = 0.3;
/** Arrow length floor, so a 2 kt wind still has a shaft behind its head. */
export const ARROW_MIN_LEN = 0.12;
export const ARROW_FULL_SCALE_KT = 35;
/** Dashed circle drawn for a VRB wind. */
export const VRB_CIRCLE_R = 0.38;

export interface Pt {
  x: number;
  y: number;
}

/** Unit vector for a true bearing, north up, y down. */
export function bearingVector(deg: number): Pt {
  const r = (deg * Math.PI) / 180;
  return { x: Math.sin(r), y: -Math.cos(r) };
}

/** The point at `bearing` and distance `r` from `origin` (default the centre). */
export function pointAt(bearing: number, r: number, origin: Pt = { x: CENTER, y: CENTER }): Pt {
  const v = bearingVector(bearing);
  return { x: origin.x + v.x * r, y: origin.y + v.y * r };
}

// --- Runways ------------------------------------------------------------------

export interface RunwayLabel {
  ident: string;
  at: Pt;
}

export interface RunwayBar {
  id: string;
  /** Threshold of the first listed end (bearing heading + 180 from the centre). */
  a: Pt;
  /** The opposite end. */
  b: Pt;
  hard: boolean | null;
  labels: RunwayLabel[];
}

/** L / C / R from an ident ("05L" → "L"), or null. */
export function designator(ident: string): 'L' | 'C' | 'R' | null {
  const c = ident.trim().slice(-1).toUpperCase();
  return c === 'L' || c === 'C' || c === 'R' ? c : null;
}

function axisOf(r: RunwayInfo): number {
  return r.ends[0].heading_true;
}

/** Angle between two runway axes, 0..90 (a runway is the same both ways). */
export function axisDifference(a: number, b: number): number {
  const d = (((a - b) % 180) + 180) % 180;
  return Math.min(d, 180 - d);
}

/** Bar half-length for each runway: proportional to length, floored. Unknown
 *  lengths draw full length — nothing says the strip is short. */
export function barHalfLengths(runways: RunwayInfo[]): number[] {
  const known = runways.map((r) => r.length_ft).filter((l): l is number => l != null && l > 0);
  const longest = known.length ? Math.max(...known) : 0;
  return runways.map((r) => {
    if (r.length_ft == null || r.length_ft <= 0 || longest <= 0) return BAR_MAX_HALF;
    return BAR_MAX_HALF * Math.max(BAR_MIN_RATIO, r.length_ft / longest);
  });
}

/** Perpendicular offset of each runway's centre line from the dial centre.
 *  Near-parallel runways (within `PARALLEL_TOLERANCE_DEG`) are spread side by
 *  side: by their L/C/R designator when every one has one (L to the left of
 *  its own end's heading), else in listed order. */
export function parallelOffsets(runways: RunwayInfo[]): Pt[] {
  const offsets: Pt[] = runways.map(() => ({ x: 0, y: 0 }));
  const assigned = runways.map(() => false);
  for (let i = 0; i < runways.length; i++) {
    if (assigned[i]) continue;
    const group = [i];
    for (let j = i + 1; j < runways.length; j++) {
      if (!assigned[j] && axisDifference(axisOf(runways[i]), axisOf(runways[j])) <= PARALLEL_TOLERANCE_DEG) {
        group.push(j);
      }
    }
    group.forEach((k) => { assigned[k] = true; });
    if (group.length < 2) continue;
    const designators = group.map((k) => designator(runways[k].ends[0].ident));
    if (designators.every((d) => d != null)) {
      const hasCentre = designators.includes('C');
      group.forEach((k, n) => {
        const d = designators[n];
        const slot = d === 'C' ? 0 : (d === 'L' ? -1 : 1) * (hasCentre ? 1 : 0.5);
        // Right of the end's own heading is bearing + 90; L is the negative slot.
        const v = bearingVector(axisOf(runways[k]) + 90);
        offsets[k] = { x: v.x * slot * PARALLEL_GAP, y: v.y * slot * PARALLEL_GAP };
      });
    } else {
      const v = bearingVector(axisOf(runways[i]) + 90);
      group.forEach((k, n) => {
        const slot = n - (group.length - 1) / 2;
        offsets[k] = { x: v.x * slot * PARALLEL_GAP, y: v.y * slot * PARALLEL_GAP };
      });
    }
  }
  return offsets;
}

/** Every runway as a bar through the centre at its true heading, with an
 *  ident label beyond each end on its approach side. A runway with no end
 *  (the server drops those) is skipped. */
export function runwayBars(runways: RunwayInfo[]): RunwayBar[] {
  const drawable = runways.filter((r) => r.ends.length > 0);
  const halves = barHalfLengths(drawable);
  const offsets = parallelOffsets(drawable);
  return drawable.map((r, i) => {
    const origin = { x: CENTER + offsets[i].x, y: CENTER + offsets[i].y };
    const labelOrigin = { x: CENTER + offsets[i].x * LABEL_SPREAD, y: CENTER + offsets[i].y * LABEL_SPREAD };
    const axis = axisOf(r);
    return {
      id: r.id,
      a: pointAt(axis + 180, halves[i], origin),
      b: pointAt(axis, halves[i], origin),
      hard: r.hard,
      // Landing on an end you fly its heading, so you arrive from heading + 180.
      labels: r.ends.map((e) => ({ ident: e.ident, at: pointAt(e.heading_true + 180, halves[i] + LABEL_GAP, labelOrigin) })),
    };
  });
}

/** Which labels a size shows: all (regular), the best end only (compact,
 *  falling back to all when there is no best end), none (inline). */
export function visibleLabels(bars: RunwayBar[], size: RunwayWindSize, bestEnd: string | null): RunwayLabel[] {
  const all = bars.flatMap((b) => b.labels);
  if (size === 'inline') return [];
  if (size === 'compact' && bestEnd != null) return all.filter((l) => l.ident === bestEnd);
  return all;
}

// --- Wind -----------------------------------------------------------------------

export interface WindArrow {
  /** On the rim, upwind. */
  from: Pt;
  /** Arrow head, toward the centre; length on a fixed kt scale. */
  to: Pt;
  /** Where a shown gust extends the arrow to, or null. */
  gustTo: Pt | null;
}

export interface VariableArc {
  from: Pt;
  to: Pt;
  /** Clockwise sweep from `variable_from` to `variable_to`, degrees. */
  sweepDeg: number;
  r: number;
}

export type WindMark =
  | { kind: 'arrow'; arrow: WindArrow; arc: VariableArc | null }
  | { kind: 'vrb'; r: number; label: string }
  | { kind: 'calm'; label: string }
  | { kind: 'missing'; label: string };

export function arrowLength(kt: number): number {
  const len = (kt / ARROW_FULL_SCALE_KT) * ARROW_MAX_LEN;
  return Math.min(ARROW_MAX_LEN, Math.max(ARROW_MIN_LEN, len));
}

/** Whether a gust is shown beside `base` (the shared suppression rule). */
export function gustShown(base: number, gust: number | null): boolean {
  return gust != null && roundHalfAway(gust) - roundHalfAway(base) >= GUST_DISPLAY_MIN_EXCESS_KT;
}

/** The arrow for a directional wind; null for calm, VRB or no wind. */
export function windArrow(w: WindSample): WindArrow | null {
  if (w.calm || w.variable || w.direction_true == null || w.speed_kt == null) return null;
  const dir = w.direction_true;
  return {
    from: pointAt(dir, RIM_R),
    to: pointAt(dir, RIM_R - arrowLength(w.speed_kt)),
    gustTo: gustShown(w.speed_kt, w.gust_kt) ? pointAt(dir, RIM_R - arrowLength(w.gust_kt!)) : null,
  };
}

/** The rim arc of a `dddVddd` range, or null. */
export function variableArc(w: WindSample): VariableArc | null {
  if (w.variable_from == null || w.variable_to == null) return null;
  const sweep = (((w.variable_to - w.variable_from) % 360) + 360) % 360;
  return { from: pointAt(w.variable_from, RIM_R), to: pointAt(w.variable_to, RIM_R), sweepDeg: sweep, r: RIM_R };
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/** The wind as reported: "270@12G22", "270@12 240V300", "VRB 04", "VRB 04G15", "Calm". */
export function windLabel(w: WindSample): string {
  if (w.calm) return 'Calm';
  if (w.speed_kt == null) return '';
  if (w.variable || w.direction_true == null) {
    const gust = gustShown(w.speed_kt, w.gust_kt) ? `G${roundHalfAway(w.gust_kt!)}` : '';
    return `VRB ${pad2(roundHalfAway(w.speed_kt))}${gust}`;
  }
  const base = formatWind(w.speed_kt, w.direction_true, w.gust_kt);
  if (w.variable_from != null && w.variable_to != null) {
    return `${base} ${formatHeading(w.variable_from)}V${formatHeading(w.variable_to)}`;
  }
  return base;
}

/** What the dial draws for the primary wind; `missing` says why there is none. */
export function windMark(w: WindAtAirport | null, missing: string): WindMark {
  if (w == null) return { kind: 'missing', label: missing };
  const s = w.wind;
  if (s.calm) return { kind: 'calm', label: 'Calm' };
  if (s.variable || s.direction_true == null) return { kind: 'vrb', r: VRB_CIRCLE_R, label: windLabel(s) };
  const arrow = windArrow(s);
  if (arrow == null) return { kind: 'missing', label: missing };
  return { kind: 'arrow', arrow, arc: variableArc(s) };
}

// --- Words ---------------------------------------------------------------------------

function headPart(e: EndComponents): string {
  const hw = roundHalfAway(e.headwind_kt);
  return hw < 0 ? `${-hw} kt tail` : `${hw} kt head`;
}

function crossPart(e: EndComponents, s: WindSample): string {
  const xw = roundHalfAway(Math.abs(e.crosswind_kt));
  const side = xw !== 0 && e.side ? ` from ${e.side}` : '';
  let out = `${xw} kt X-wind${side}`;
  const gx = e.gust_crosswind_kt != null ? roundHalfAway(Math.abs(e.gust_crosswind_kt)) : null;
  const gustOn = gx != null && gx - xw >= GUST_DISPLAY_MIN_EXCESS_KT;
  if (gustOn) out += ` (G ${gx})`;
  // The variable range's worst case, when it says more than the figures above.
  const mx = roundHalfAway(e.max_crosswind_kt);
  if (s.variable_from != null && mx > Math.max(xw, gustOn ? gx! : 0)) out += ` · up to ${mx} kt`;
  return out;
}

/** One end's components: "27 · 6 kt head · 11 kt X-wind from left (G 17)". */
export function endLine(e: EndComponents, s: WindSample): string {
  return `${e.ident} · ${headPart(e)} · ${crossPart(e, s)}`;
}

/** The dial's one line for a wind: its best end, or what stands in for it. */
export function componentsLine(w: WindAtAirport | null, missing: string): string {
  if (w == null) return missing;
  const s = w.wind;
  if (s.calm) return 'Calm';
  if (s.variable || s.direction_true == null) {
    if (s.speed_kt == null) return missing;
    const worst = w.ends.length
      ? Math.max(...w.ends.map((e) => roundHalfAway(e.max_crosswind_kt)))
      : roundHalfAway(Math.max(s.speed_kt, s.gust_kt ?? 0));
    return `${windLabel(s)} · up to ${worst} kt X-wind`;
  }
  const best = w.best_end != null ? w.ends.find((e) => e.ident === w.best_end) : undefined;
  if (best == null) return 'No runway data';
  return endLine(best, s);
}

/** "14Z" for a whole hour, else "1437Z"; '' without a time. A time without a
 *  zone is UTC (the server's convention). */
export function sampleTimeLabel(iso: string | null): string {
  const ms = isoToMs(iso);
  if (Number.isNaN(ms)) return '';
  const d = new Date(ms);
  const hh = pad2(d.getUTCHours());
  const mm = d.getUTCMinutes();
  return mm === 0 ? `${hh}Z` : `${hh}${pad2(mm)}Z`;
}

/** The ghost (TAF at ETA) line: "TAF 14Z 250@18G28 · 27 · 14 kt X-wind from left". */
export function ghostLine(w: WindAtAirport): string {
  const source = w.wind.source.toUpperCase();
  const time = sampleTimeLabel(w.wind.time);
  const head = [source, time, windLabel(w.wind)].filter(Boolean).join(' ');
  const s = w.wind;
  if (s.calm || s.variable || s.direction_true == null) return head;
  const best = w.best_end != null ? w.ends.find((e) => e.ident === w.best_end) : undefined;
  return best ? `${head} · ${best.ident} · ${crossPart(best, s)}` : head;
}

/** The crosswind part of `componentsLine` / `ghostLine` ("11 kt X-wind from
 *  left (G 17)"), the only part the advisory tone colours; null when the line
 *  has none (calm, VRB, no runway). */
export function crosswindText(w: WindAtAirport | null): string | null {
  if (w == null) return null;
  const s = w.wind;
  if (s.calm || s.variable || s.direction_true == null) return null;
  const best = w.best_end != null ? w.ends.find((e) => e.ident === w.best_end) : undefined;
  return best ? crossPart(best, s) : null;
}

/** The crosswind figure's tone: the server's advisory tier; green is quiet. */
export function crosswindTone(advisory: string | null): 'amber' | 'red' | null {
  return advisory === 'amber' || advisory === 'red' ? advisory : null;
}

// --- Placement helpers -------------------------------------------------------------

export function windOf(p: RunwayWindPicture | null | undefined, source: WindSample['source']): WindAtAirport | null {
  return p?.winds.find((w) => w.wind.source === source) ?? null;
}

export interface RunwayWindDial {
  role: 'DEP' | 'ARR';
  icao: string;
  /** Null when the airport has no picture (no runway data, or none fetched). */
  picture: RunwayWindPicture | null;
  primary: WindAtAirport | null;
  /** The TAF at ETA for the destination; never for the departure ("now"). */
  ghost: WindAtAirport | null;
}

/** The departure + destination dials, or [] when neither airport has a
 *  picture (an older server: the section renders as before). */
export function runwayWindPair(
  airports: AirportObservation[] | null | undefined,
  departure: string | null | undefined,
  destination: string | null | undefined,
): RunwayWindDial[] {
  const find = (icao: string) => (airports ?? []).find((a) => a.icao.toUpperCase() === icao.toUpperCase());
  const dials: RunwayWindDial[] = [];
  const add = (role: 'DEP' | 'ARR', icao: string | null | undefined) => {
    if (!icao) return;
    const picture = find(icao)?.runway_wind ?? null;
    dials.push({
      role,
      icao: icao.toUpperCase(),
      picture,
      primary: windOf(picture, 'metar'),
      ghost: role === 'ARR' ? windOf(picture, 'taf') : null,
    });
  };
  add('DEP', departure);
  if (destination && destination.toUpperCase() !== (departure ?? '').toUpperCase()) add('ARR', destination);
  return dials.some((d) => d.picture != null) ? dials : [];
}

/** What stands in for a missing primary wind. */
export function missingWindLabel(d: RunwayWindDial): string {
  return d.picture == null ? 'No runway data' : 'No METAR';
}
