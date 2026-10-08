/** Route ribbon (#690) — the pure half: geometry, colour ladders and words.
 *
 * ===========================================================================
 * SYNC — paired with
 * app/flyfun-weather/flyfun-weather/Views/Briefing/RouteRibbonRules.swift.
 *
 * The ribbon is a **symbolic map of the route**: the middle line is the route,
 * straight and to scale in distance, departure and destination as circles at
 * its ends and the planned position on it. Facing the direction of flight,
 * left of course is above the line and right of course below: an airport row
 * each side (disc = METAR now, ring = TAF at its ETA), and between the rows
 * and the line the rain areas and convective cores the cells feed outlines
 * within the corridor, at their distance off track, coloured by strength,
 * with an arrow for their motion relative to the course. SIGMETs run as a
 * thin band across the top. Without the cells feed, the radar strip hugs the
 * line instead.
 *
 * Everything here is pure so the rules — the cross-track mapping, which bands
 * get an arrow, what each mark is called — are unit-testable in node and can
 * be compared against the Swift file symbol by symbol. The renderer
 * (`ribbon-view.ts`) owns the SVG and nothing else.
 *
 * Deliberate divergences from the Swift side, documented so `/sync-ios-web`
 * does not re-flag them:
 *  - **Category colours.** Web MVFR is amber (`--amber`), iOS MVFR is blue.
 *    The web page already badges MVFR amber in every table, so matching iOS
 *    here would paint one airport two colours on one page. Thresholds and
 *    colour *roles* are shared; the palette is each platform's own.
 *  - **dBZ hexes** come from the web's own VIP ramp (`layer-legends.ts`) so
 *    the ribbon matches the radar legend beside it. The *boundaries*
 *    (35 / 41 / 50) are shared with iOS exactly — those are the cell tiers.
 * ===========================================================================
 *
 * Observations only: no verdict, no estimate. Nothing here re-grades the
 * briefing, and the colours describe strength and category — never "go".
 */

import type {
  LiveFocus,
  LiveRibbon,
  LiveRibbonSegment,
  LiveStorm,
  RibbonSigmet,
  RibbonStation,
  RibbonWeather,
} from '../../store/types';

// --- Geometry (px from the top of the drawing; mirrors RouteRibbonRules) ----

export const RIBBON_HEIGHT = 214;
export const SIGMET_Y = 7;
export const LEFT_ROW_Y = 26;
export const ZONE_TOP = 40;
export const TRACK_Y = 100;
export const ZONE_BOTTOM = 160;
export const RIGHT_ROW_Y = 174;
export const AXIS_Y = 197;
/** Horizontal padding: the route line runs from `INSET` to `width − INSET`. */
export const INSET = 20;

/** Along-route distance → x. Clamped, so a storm past the route's end sits on
 *  the end rather than off the drawing. */
export function xForNm(nm: number, routeNm: number, width: number): number {
  const span = Math.max(width - 2 * INSET, 1);
  const f = Math.min(Math.max(nm / Math.max(routeNm, 1), 0), 1);
  return INSET + f * span;
}

/** Off-track distance → y: left (−) above the line, right (+) below. */
export function yForCross(cross: number, corridor: number): number {
  const f = Math.max(-1, Math.min(1, cross / Math.max(corridor, 1)));
  return f < 0 ? TRACK_Y + f * (TRACK_Y - ZONE_TOP) : TRACK_Y + f * (ZONE_BOTTOM - TRACK_Y);
}

/** The route's length, never 0 (a divisor). */
export function routeNmOf(ribbon: LiveRibbon): number {
  return Math.max(ribbon.route_nm ?? 1, 1);
}

/** Half-width of the off-track scale: the corridor the bands were cut to. */
export function corridorOf(ribbon: LiveRibbon, fallbackNm: number): number {
  return Math.max(ribbon.weather_corridor_nm ?? fallbackNm, 1);
}

/** Are the cells feed's rain/core bands drawable? Otherwise the radar strip. */
export function weatherAvailable(ribbon: LiveRibbon): boolean {
  return ribbon.weather_status === 'available' && !!ribbon.weather;
}

// --- Colour ladders --------------------------------------------------------

/** METAR / TAF category. CSS custom properties, so dark mode follows the
 *  page. See the divergence note above: MVFR is amber on web. */
export function categoryColour(category: string | null | undefined): string {
  switch ((category ?? '').toLowerCase()) {
    case 'vfr': return 'var(--green)';
    case 'mvfr': return 'var(--amber)';
    case 'ifr': return 'var(--red)';
    case 'lifr': return 'var(--lifr)';
    default: return 'var(--text-muted, #888)';
  }
}

/** Reflectivity → the VIP ramp's colour at the cell tiers (35 / 41 / 50). */
export function dbzColour(dbz: number | null | undefined): string {
  if (dbz == null) return 'rgba(60, 190, 90, 0.4)';
  if (dbz >= 50) return '#e13c3c';
  if (dbz >= 41) return '#f08c28';
  if (dbz >= 35) return '#f0d23c';
  return '#3cbe5a';
}

/** Rain areas pale (their strength is in the cores drawn on top), cores by
 *  their peak. */
export function bandFill(band: RibbonWeather): string {
  return band.tier === 'core' ? dbzColour(band.peak_dbz) : 'rgba(60, 190, 90, 0.28)';
}

/** Opacity for a core band, so a pale rain area beneath stays visible. */
export const CORE_BAND_OPACITY = 0.85;

/** The radar strip's fill for one stretch, when there are no cell bands.
 *  "no_coverage" is grey — the radar could not see the stretch, which must
 *  never read as "no weather" (#574 invariant). */
export function radarFill(seg: LiveRibbonSegment): string {
  switch (seg.radar_status) {
    case 'no_coverage': return 'rgba(136, 136, 136, 0.35)';
    case 'measured': {
      const dbz = seg.radar_max_dbz;
      if (dbz == null) return 'rgba(60, 190, 90, 0.08)';
      if (dbz >= 20 && dbz < 35) return 'rgba(60, 190, 90, 0.6)';
      return dbz < 20 ? 'rgba(60, 190, 90, 0.25)' : dbzColour(dbz);
    }
    default: return 'transparent';
  }
}

/** A storm's motion arrow: toward the track when closing, away when moving
 *  away. `null` when the motion was not measured — never a guess. */
export function motionArrowDir(
  storm: LiveStorm,
  cross: number,
): 'up' | 'down' | null {
  const below = cross >= 0;
  switch (storm.relative_motion) {
    case 'closing': return below ? 'up' : 'down';
    case 'moving_away': return below ? 'down' : 'up';
    default: return null;
  }
}

// --- Words (labels + the accessible name of each mark) ---------------------

/** Round the way Swift's `Double.rounded()` does — half **away from zero**.
 *
 * SYNC-critical: JS `Math.round` breaks ties toward +∞, so `Math.round(-2.5)`
 * is -2 where Swift's `(-2.5).rounded()` is -3. Every number below ends up in
 * a label string the two clients are supposed to produce identically, and
 * `ribbon-core.test.ts` / `RouteRibbonRulesTests.swift` assert them verbatim.
 * Rather than reason about which inputs can go negative (reflectivity can),
 * every mirrored label rounds through this.
 */
export function roundHalfAway(v: number): number {
  // `|| 0` normalises the -0 that `Math.sign(-0.2) * 0` would otherwise give,
  // since `${-0}` prints "-0" in a few engines.
  return (v < 0 ? -1 : 1) * Math.round(Math.abs(v)) || 0;
}

export function sigmetText(s: RibbonSigmet): string {
  const hazard = [s.qualifier, s.hazard].filter(Boolean).join(' ');
  const fir = s.label ? s.label.split(':')[0] : null;
  return [fir, hazard || 'SIGMET'].filter(Boolean).join(' ');
}

export function segmentLabel(seg: LiveRibbonSegment): string {
  const span = `${roundHalfAway(seg.from_nm ?? 0)}–${roundHalfAway(seg.to_nm ?? 0)} NM`;
  switch (seg.radar_status) {
    case 'measured':
      return seg.radar_max_dbz != null
        ? `${span}: radar peak ${roundHalfAway(seg.radar_max_dbz)} dBZ`
        : `${span}: no radar echo`;
    case 'no_coverage': return `${span}: radar coverage insufficient`;
    default: return `${span}: no radar sample`;
  }
}

export function stationLabel(st: RibbonStation): string {
  const parts: string[] = [st.icao, st.metar_category ?? 'METAR unavailable'];
  parts.push(...(st.convective ?? []));
  if (st.taf_category_at_eta) parts.push(`TAF at ETA ${st.taf_category_at_eta}`);
  if (st.taf_temporary_type && st.taf_temporary_category) {
    parts.push(`${st.taf_temporary_type} ${st.taf_temporary_category}`);
  }
  if (st.cross_nm != null && (st.role === 'route' || st.role === 'alternate')) {
    parts.push(`${roundHalfAway(Math.abs(st.cross_nm))} NM ${st.cross_nm < 0 ? 'left' : 'right'} of course`);
  }
  return parts.join(' ');
}

export function stormLabel(storm: LiveStorm): string {
  const parts = [`Cell ${roundHalfAway(storm.peak_dbz ?? 0)} dBZ`];
  parts.push(stormPositionText(storm));
  parts.push(stormMotionText(storm));
  if (storm.flashes != null && storm.flashes > 0) {
    parts.push(storm.flashes === 1 ? '1 flash' : `${storm.flashes} flashes`);
  }
  return parts.join(', ');
}

/** The weather zones in a sentence, for the drawing's accessible name. */
export function weatherSummary(bands: RibbonWeather[]): string {
  if (bands.length === 0) return 'No rain or cells within the corridor';
  const cores = bands.filter((b) => b.tier === 'core');
  const peaks = cores.map((c) => c.peak_dbz).filter((v): v is number => v != null);
  let s = `${bands.length - cores.length} rain areas, ${cores.length} cells along the route`;
  if (peaks.length > 0) s += `, strongest ${roundHalfAway(Math.max(...peaks))} dBZ`;
  return s;
}

/** Motion relative to the course in words. */
export function relativeMotionText(deg: number): string {
  const a = Math.abs(deg);
  if (a <= 30) return 'moving along the course';
  if (a >= 150) return 'moving against the course';
  return deg > 0 ? 'drifting toward the right of course' : 'drifting toward the left of course';
}

/** "8 NM right of track at 85 NM", or from the airport past the route's ends
 *  — the server's `storm_position_text`. */
export function stormPositionText(storm: LiveStorm): string {
  const off = `${roundHalfAway(storm.offtrack_nm ?? 0)} NM`;
  if (storm.end != null) {
    const place = storm.end_icao ?? storm.end ?? '';
    return [off, storm.end_bearing, 'of', place].filter(Boolean).join(' ');
  }
  const along = roundHalfAway(storm.along_nm ?? 0);
  if (!storm.side) return `on track at ${along} NM`;
  return `${off} ${storm.side} of track at ${along} NM`;
}

/** Observed motion against the track — the server's wording. */
export function stormMotionText(storm: LiveStorm): string {
  switch (storm.relative_motion) {
    case 'closing':
      return storm.closing_kt != null ? `closing ${roundHalfAway(storm.closing_kt)} kt` : 'closing';
    case 'moving_away':
      return storm.closing_kt != null
        ? `moving away ${roundHalfAway(-storm.closing_kt)} kt` : 'moving away';
    case 'parallel': return 'moving along the track';
    case 'stationary': return 'nearly stationary';
    default: return 'motion not yet measured';
  }
}

/** The ribbon's caption: the scale it is drawn to and the frame's time. */
export function ribbonCaption(
  ribbon: LiveRibbon,
  stormCorridorNm: number | null | undefined,
  zulu: (iso: string) => string | null,
): string {
  const parts: string[] = [];
  if (weatherAvailable(ribbon)) {
    const corridor = ribbon.weather_corridor_nm ?? stormCorridorNm;
    if (corridor != null) parts.push(`±${roundHalfAway(corridor)} NM of course`);
  } else if (ribbon.radar_radius_nm != null) {
    parts.push(`radar ≤${roundHalfAway(ribbon.radar_radius_nm)} NM`);
  }
  if (ribbon.radar_time) {
    const t = zulu(ribbon.radar_time);
    if (t) parts.push(t);
  }
  return parts.join(' · ');
}

// --- Derived geometry (the parts worth testing) ----------------------------

export interface BandRect {
  x: number; y: number; width: number; height: number; fill: string; opacity: number;
}

/** One rect per profile bin: the off-track range the outline covers over that
 *  stretch of route. Rain first so cores paint on top. */
export function bandRects(ribbon: LiveRibbon, width: number): BandRect[] {
  const routeNm = routeNmOf(ribbon);
  const corridor = corridorOf(ribbon, 30);
  const half = (ribbon.weather_bin_nm ?? 5) / 2;
  const bands = [...(ribbon.weather ?? [])].sort(
    (a, b) => (a.tier === 'core' ? 1 : 0) - (b.tier === 'core' ? 1 : 0),
  );
  const out: BandRect[] = [];
  for (const band of bands) {
    const fill = bandFill(band);
    const opacity = band.tier === 'core' ? CORE_BAND_OPACITY : 1;
    for (const bin of band.profile ?? []) {
      if (bin.length !== 3) continue;
      const x0 = xForNm(bin[0] - half, routeNm, width);
      const x1 = xForNm(bin[0] + half, routeNm, width);
      const y0 = yForCross(bin[1], corridor);
      const y1 = yForCross(bin[2], corridor);
      out.push({
        x: x0,
        y: Math.min(y0, y1),
        width: Math.max(x1 - x0, 1.5),
        height: Math.max(Math.abs(y1 - y0), 3),
        fill,
        opacity,
      });
    }
  }
  return out;
}

/** The point a band's arrow and tap target sit on: its widest bin. */
export function bandAnchor(
  band: RibbonWeather,
  ribbon: LiveRibbon,
  width: number,
): { x: number; y: number } | null {
  let best: [number, number, number] | null = null;
  for (const bin of band.profile ?? []) {
    if (bin.length !== 3) continue;
    if (!best || bin[2] - bin[1] > best[2] - best[1]) best = bin;
  }
  if (!best) return null;
  return {
    x: xForNm(best[0], routeNmOf(ribbon), width),
    y: yForCross((best[1] + best[2]) / 2, corridorOf(ribbon, 30)),
  };
}

export interface RibbonArrow { id: string; x: number; y: number; deg: number; core: boolean }

/** Minimum gap between two arrows; a closer one is dropped rather than drawn
 *  overlapping. */
export const ARROW_MIN_GAP = 16;
/** A rain area gets an arrow only once it is this long along the route — a
 *  short one is a shower, and its arrow would be noise. */
export const ARROW_MIN_RAIN_NM = 15;

/** One arrow per moving band, strongest first, skipping one that would sit on
 *  top of another. */
export function ribbonArrows(ribbon: LiveRibbon, width: number): RibbonArrow[] {
  const moving = (ribbon.weather ?? [])
    .filter((b) => b.motion_rel_deg != null
      && (b.tier === 'core' || (b.to_nm ?? 0) - (b.from_nm ?? 0) >= ARROW_MIN_RAIN_NM))
    .sort((a, b) => {
      const ac = a.tier === 'core' ? 1 : 0;
      const bc = b.tier === 'core' ? 1 : 0;
      if (ac !== bc) return bc - ac;
      return (b.peak_dbz ?? 0) - (a.peak_dbz ?? 0);
    });
  const out: RibbonArrow[] = [];
  for (const band of moving) {
    const at = bandAnchor(band, ribbon, width);
    if (!at || band.motion_rel_deg == null) continue;
    if (out.some((o) => Math.hypot(o.x - at.x, o.y - at.y) < ARROW_MIN_GAP)) continue;
    out.push({ id: band.id, x: at.x, y: at.y, deg: band.motion_rel_deg, core: band.tier === 'core' });
  }
  return out;
}

export interface StormTarget { storm: LiveStorm; x: number; y: number }

/** The tap target for each storm that has a band on the ribbon — one per
 *  storm, on its first band. */
export function stormTargets(
  ribbon: LiveRibbon,
  storms: LiveStorm[],
  width: number,
): StormTarget[] {
  const byId = new Map(storms.map((s) => [s.id, s]));
  const seen = new Set<string>();
  const out: StormTarget[] = [];
  for (const band of ribbon.weather ?? []) {
    const sid = band.storm_id;
    if (!sid || seen.has(sid)) continue;
    const storm = byId.get(sid);
    if (!storm) continue;
    const at = bandAnchor(band, ribbon, width);
    if (!at) continue;
    seen.add(sid);
    out.push({ storm, x: at.x, y: at.y });
  }
  return out;
}

/** Departure / destination sit on the line's ends; the others in the row on
 *  their side of the course. `null` without a position. */
export function stationPoint(
  st: RibbonStation,
  ribbon: LiveRibbon,
  width: number,
): { x: number; y: number } | null {
  if (st.role === 'departure') return { x: INSET, y: TRACK_Y };
  if (st.role === 'destination') return { x: width - INSET, y: TRACK_Y };
  if (st.along_nm == null) return null;
  return {
    x: xForNm(st.along_nm, routeNmOf(ribbon), width),
    y: (st.cross_nm ?? 0) < 0 ? LEFT_ROW_Y : RIGHT_ROW_Y,
  };
}

/** Is this an end-of-route airport (drawn larger, on the line)? */
export function isEndStation(st: RibbonStation): boolean {
  return st.role === 'departure' || st.role === 'destination';
}

/** An airport's disc: the route's ends sit on the line and are drawn larger
 *  than the en-route airports in their rows. */
export function stationMarkSize(st: RibbonStation): number {
  return isEndStation(st) ? 16 : 10;
}

/** Minimum tap/click target radius for a mark. A 10 px disc is well under the
 *  44 px guidance on its own, so every mark carries an invisible circle of at
 *  least this radius. */
export const STORM_HIT_RADIUS = 14;
export const STATION_HIT_RADIUS = 12;

/** Tapping the weather zones frames the map on that stretch of route. */
export function segmentFocusAt(
  ribbon: LiveRibbon,
  px: number,
  width: number,
): LiveFocus | null {
  const nm = ((px - INSET) / Math.max(width - 2 * INSET, 1)) * routeNmOf(ribbon);
  const segs = ribbon.segments ?? [];
  const hit = segs.find((s) => (s.from_nm ?? 0) <= nm && nm < (s.to_nm ?? 0));
  return hit?.focus ?? segs[segs.length - 1]?.focus ?? null;
}

/** Storm marker diameter by peak reflectivity (no cell bands: storms as
 *  points). */
export function stormMarkSize(peakDbz: number | null | undefined): number {
  const dbz = peakDbz ?? 0;
  if (dbz >= 50) return 18;
  if (dbz >= 41) return 14;
  return 10;
}

/** The nutshell's phase label (iOS `LiveGlanceLine.phaseLabel`). Title case,
 *  not shouted: `.glance-phase` uppercases it in CSS, so a screen reader
 *  reads "En route" rather than spelling out capitals. */
export function phaseLabel(phase: string | null | undefined): string {
  switch (phase) {
    case 'departure': return 'Departure';
    case 'enroute': return 'En route';
    case 'arrival': return 'Arrival';
    default: {
      const p = phase ?? '';
      return p ? p.charAt(0).toUpperCase() + p.slice(1) : '';
    }
  }
}
