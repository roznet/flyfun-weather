/** Lightning strikes as small bolt glyphs on one canvas (#665).
 *
 * Shared by the briefing route map and the maps page's "Now" tab. One canvas
 * for every flash rather than a marker each: a storm in the corridor is
 * hundreds of flashes an hour, and the "Now" tab's whole-Europe view can be
 * thousands, which DOM markers (a `divIcon` per flash) would not survive.
 *
 * The canvas covers the viewport and is redrawn when the map settles
 * (`moveend` / `zoomend` / `resize`); during a zoom animation it is hidden
 * rather than scaled, so a bolt never draws stretched or in the wrong place.
 * Non-interactive. Which flashes to draw and how faded is
 * `drawableFlashes` (pure, tested); this layer only paints them.
 */

import * as L from 'leaflet';
import {
  flashGlyphPoints,
  type DrawableFlash,
} from './route-map/observed-overlay-geometry';

export const FLASH_COLOR = '#7c3aed';
/** A pale rim keeps a bolt readable over radar colours and dark imagery. */
const FLASH_RIM = 'rgba(255, 255, 255, 0.85)';

export class FlashGlyphLayer extends L.Layer {
  private flashes: DrawableFlash[];
  private canvas: HTMLCanvasElement | null = null;

  constructor(flashes: DrawableFlash[] = [], options?: L.LayerOptions) {
    super(options);
    this.flashes = flashes;
  }

  setFlashes(flashes: DrawableFlash[]): this {
    this.flashes = flashes;
    this.redraw();
    return this;
  }

  onAdd(map: L.Map): this {
    const canvas = L.DomUtil.create('canvas', 'wb-flash-glyphs') as HTMLCanvasElement;
    canvas.style.pointerEvents = 'none';
    canvas.style.position = 'absolute';
    map.getPane(this.options.pane ?? 'overlayPane')?.appendChild(canvas);
    this.canvas = canvas;
    map.on('moveend zoomend resize viewreset', this.redraw, this);
    map.on('zoomstart', this.hide, this);
    this.redraw();
    return this;
  }

  onRemove(map: L.Map): this {
    map.off('moveend zoomend resize viewreset', this.redraw, this);
    map.off('zoomstart', this.hide, this);
    this.canvas?.remove();
    this.canvas = null;
    return this;
  }

  private hide(): void {
    if (this.canvas) this.canvas.style.visibility = 'hidden';
  }

  redraw(): void {
    const map = this._map as L.Map | undefined;
    const canvas = this.canvas;
    if (!map || !canvas) return;
    const size = map.getSize();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(size.x * ratio);
    canvas.height = Math.round(size.y * ratio);
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;
    // The pane moves with the map; pin the canvas to the viewport's top-left.
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    canvas.style.visibility = '';

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);
    ctx.fillStyle = FLASH_COLOR;
    ctx.strokeStyle = FLASH_RIM;
    ctx.lineWidth = 1;
    ctx.lineJoin = 'round';
    const margin = 12;
    for (const flash of this.flashes) {
      const p = map.latLngToContainerPoint([flash.lat, flash.lon]);
      if (p.x < -margin || p.y < -margin || p.x > size.x + margin || p.y > size.y + margin) continue;
      const pts = flashGlyphPoints(p.x, p.y);
      ctx.globalAlpha = flash.opacity;
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.closePath();
      ctx.stroke();
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}
