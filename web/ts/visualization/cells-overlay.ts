/** Cell overlay (#656) — the one Leaflet renderer both maps use.
 *
 * `CellsLayer` owns a layer group, fetches the frame listing and the display
 * file it needs, and draws outlines, trend-coloured markers with popups, and
 * 30-minute motion arrows. The forecast map's "Now" tab and the briefing
 * route map each hold one; the route map passes its corridor box so it never
 * downloads all of Europe.
 *
 * Pure rules (colours, stamp matching, words) live in `cells-overlay-core.ts`.
 */

import * as L from 'leaflet';
import type { SummaryChip } from './route-map/observed-overlay-geometry';
import {
  ARROW_COLOUR,
  OUTLINE_STYLE,
  cellPopupHtml,
  frameKey,
  cellsBadge,
  cellsChip,
  hasArrow,
  matchCellFrame,
  trendColour,
  type CellDisplay,
  type CellFramesInfo,
  type CellMatch,
  type CellTier,
} from './cells-overlay-core';

export interface CellsBox {
  south: number;
  west: number;
  north: number;
  east: number;
}

const FRAMES_TTL_MS = 60_000;
const CELLS_PANE = 'cellsPane';
const DISPLAY_CACHE_SIZE = 6;

let framesCache: { at: number; info: CellFramesInfo | null } | null = null;
const displayCache = new Map<string, CellDisplay>();

/** The overlay listing, cached for a minute (a new frame lands every 5). */
export async function fetchCellFrames(now: number = Date.now()): Promise<CellFramesInfo | null> {
  if (framesCache && now - framesCache.at < FRAMES_TTL_MS) return framesCache.info;
  let info: CellFramesInfo | null = null;
  try {
    const response = await fetch('/api/observed/cells/frames');
    if (response.ok) info = (await response.json()) as CellFramesInfo;
  } catch {
    info = null;
  }
  framesCache = { at: now, info };
  return info;
}

/** `key` is a frame's `frameKey` (newest revision), so a new revision is a new URL. */
export function cellDisplayUrl(info: CellFramesInfo, key: string, box: CellsBox | null): string {
  const base = info.url_template.replace('{stamp}', encodeURIComponent(key));
  if (!box) return base;
  const q = new URLSearchParams({
    south: box.south.toFixed(2), west: box.west.toFixed(2),
    north: box.north.toFixed(2), east: box.east.toFixed(2),
  });
  return `${base}?${q.toString()}`;
}

/** One display file. Immutable per URL (one revision), so cached by URL. */
export async function fetchCellDisplay(url: string): Promise<CellDisplay | null> {
  const hit = displayCache.get(url);
  if (hit) return hit;
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    const display = (await response.json()) as CellDisplay;
    displayCache.set(url, display);
    while (displayCache.size > DISPLAY_CACHE_SIZE) displayCache.delete(displayCache.keys().next().value!);
    return display;
  } catch {
    return null;
  }
}

/** Draw one display file into `group` (cleared first). */
export function drawCells(
  group: L.LayerGroup,
  display: CellDisplay,
  opts: { showRain: boolean; pane?: string },
): void {
  // Leaflet does not pass a group's pane to its children: set it per layer.
  const pane = opts.pane ?? 'overlayPane';
  group.clearLayers();
  // Outlines first (rain20 under the cores), markers and arrows on top.
  for (const tier of ['rain20', 'core35', 'core41'] as CellTier[]) {
    if (tier === 'rain20' && !opts.showRain) continue;
    const lines = display.outlines?.[tier];
    if (!lines?.length) continue;
    const style = OUTLINE_STYLE[tier];
    L.polyline(lines, { color: style.color, weight: style.weight, opacity: 0.95, interactive: false, pane }).addTo(group);
  }
  for (const c of display.cells ?? []) {
    if (c.tier === 'rain20' && !opts.showRain) continue;
    const radius = c.tier === 'core41' ? 7 : c.tier === 'core35' ? 5 : 6;
    L.circleMarker([c.lat, c.lon], {
      radius,
      pane,
      color: '#222',
      weight: 1,
      fillOpacity: 0.9,
      fillColor: trendColour(c.trend?.state),
      dashArray: c.tier === 'rain20' ? '3' : undefined,
    }).bindPopup(cellPopupHtml(c)).addTo(group);
    // Arrows for cores only (as the prototype): a frontal band's centroid
    // motion is not a useful "where will it be".
    if (c.tier !== 'rain20' && hasArrow(c)) {
      L.polyline([[c.lat, c.lon], c.arrow!], { color: ARROW_COLOUR, weight: 2.5, interactive: false, pane }).addTo(group);
      L.circleMarker(c.arrow!, { radius: 2.5, color: ARROW_COLOUR, fillOpacity: 1, interactive: false, pane }).addTo(group);
    }
  }
}

/** One map's cell overlay: state, fetching, drawing and the badge line. */
export class CellsLayer {
  readonly group: L.LayerGroup;
  private enabled: boolean;
  private showRain = false;
  private token = 0;
  private lastBadge = '';
  private lastChip: SummaryChip | null = null;
  // What is drawn now, so a re-render with the same overlay (the route map
  // re-renders on every altitude drag) keeps the layers and any open popup.
  private drawnKey: string | null = null;

  constructor(map: L.Map, enabled = true) {
    // Its own pane, above the radar tiles and the route but under popups.
    if (!map.getPane(CELLS_PANE)) {
      const pane = map.createPane(CELLS_PANE);
      pane.style.zIndex = '450';
    }
    this.group = L.layerGroup().addTo(map);
    this.enabled = enabled;
  }

  setEnabled(on: boolean): void {
    this.enabled = on;
    if (!on) {
      this.token++;
      this.clear();
      this.lastBadge = '';
      this.lastChip = null;
    }
  }

  private clear(): void {
    this.group.clearLayers();
    this.drawnKey = null;
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  setShowRain(on: boolean): void {
    this.showRain = on;
  }

  /** The badge line from the last refresh ('' when disabled). */
  badge(): string {
    return this.enabled ? this.lastBadge : '';
  }

  /** The summary chip from the last refresh (null when disabled or not yet loaded). */
  chip(): SummaryChip | null {
    return this.enabled ? this.lastChip : null;
  }

  /**
   * Fetch and draw the overlay that pairs with `radarStamp` (the radar frame
   * on screen, or null), optionally clipped to `box`. Resolves to the badge
   * line. Draws nothing — never a stale picture — unless the match is `ok`.
   */
  async refresh(radarStamp: string | null, box: CellsBox | null, now: Date = new Date()): Promise<string> {
    if (!this.enabled) return '';
    const token = ++this.token;
    const info = await fetchCellFrames(now.getTime());
    if (token !== this.token) return this.lastBadge;
    const match: CellMatch = matchCellFrame(info, radarStamp, now);
    if (match.state !== 'ok' || !info) {
      this.clear();
      this.lastBadge = cellsBadge(match, null, now);
      this.lastChip = cellsChip(match, now);
      return this.lastBadge;
    }
    const url = cellDisplayUrl(info, frameKey(match.frame), box);
    const display = await fetchCellDisplay(url);
    if (token !== this.token || !this.enabled) return this.lastBadge;
    if (!display) {
      this.clear();
      this.lastBadge = cellsBadge({ state: 'unavailable', since: match.frame.valid_time }, null, now);
      this.lastChip = cellsChip({ state: 'unavailable', since: match.frame.valid_time }, now);
      return this.lastBadge;
    }
    const key = `${url}|${this.showRain}`;
    if (key !== this.drawnKey) {
      drawCells(this.group, display, { showRain: this.showRain, pane: CELLS_PANE });
      this.drawnKey = key;
    }
    this.lastBadge = cellsBadge(match, display, now);
    this.lastChip = cellsChip(match, now);
    return this.lastBadge;
  }
}
