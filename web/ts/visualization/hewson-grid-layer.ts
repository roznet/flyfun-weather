/** HewsonGridLayer — paints one colored rect per (lat, lon) cell of a
 * Hewson snapshot onto a canvas overlay.
 *
 * Templated on the FogOfWarLayer pattern in pirep-map.ts: a custom Leaflet
 * pane between tiles and markers, sized to the map's container, redrawn on
 * move/zoom/resize. Per § 7.5 of the design doc, the rendering is
 * intentionally pixelated at low zoom — it shows the actual 0.25° grid
 * resolution rather than pretending to interpolated precision.
 */

import * as L from 'leaflet';
import type { HewsonSlice } from '../adapters/hewson-map-adapter';
import { COLORMAPS, colorFor, type HewsonMetric } from './hewson-colormaps';

export interface RenderState {
  slice: HewsonSlice;
  vmin: number;
  vmax: number;
}

export class HewsonGridLayer extends L.Layer {
  private canvas: HTMLCanvasElement | null = null;
  private pane: HTMLElement | null = null;
  private state: RenderState | null = null;
  private opacity = 0.5;
  private map: L.Map | null = null;

  /** Replace the slice and trigger a redraw. */
  setSlice(slice: HewsonSlice, vmin?: number, vmax?: number): void {
    const spec = COLORMAPS[slice.metric as HewsonMetric] ?? COLORMAPS.gradient;
    this.state = {
      slice,
      vmin: vmin ?? spec.defaultVmin,
      vmax: vmax ?? spec.defaultVmax,
    };
    this.redraw();
  }

  /** Override the (vmin, vmax) without changing the slice. */
  setVRange(vmin: number, vmax: number): void {
    if (!this.state) return;
    this.state = { ...this.state, vmin, vmax };
    this.redraw();
  }

  /** 0…1 — applied uniformly to the whole grid via canvas globalAlpha. */
  setOpacity(o: number): void {
    this.opacity = Math.max(0, Math.min(1, o));
    if (this.canvas) this.canvas.style.opacity = String(this.opacity);
  }

  clear(): void {
    this.state = null;
    this.redraw();
  }

  onAdd(map: L.Map): this {
    this.map = map;
    if (!map.getPane('hewsonGridPane')) {
      this.pane = map.createPane('hewsonGridPane');
      this.pane.style.zIndex = '350';
      this.pane.style.pointerEvents = 'none';
    } else {
      this.pane = map.getPane('hewsonGridPane')!;
    }

    this.canvas = L.DomUtil.create('canvas', '', this.pane) as HTMLCanvasElement;
    this.canvas.style.position = 'absolute';
    this.canvas.style.top = '0';
    this.canvas.style.left = '0';
    this.canvas.style.opacity = String(this.opacity);

    map.on('move zoom viewreset resize', this.redraw, this);
    this.redraw();
    return this;
  }

  onRemove(map: L.Map): this {
    map.off('move zoom viewreset resize', this.redraw, this);
    if (this.canvas?.parentNode) this.canvas.parentNode.removeChild(this.canvas);
    this.canvas = null;
    this.map = null;
    return this;
  }

  private redraw = (): void => {
    const map = this.map;
    if (!map || !this.canvas) return;

    const size = map.getSize();
    const dpr = window.devicePixelRatio || 1;
    const newW = size.x * dpr;
    const newH = size.y * dpr;
    // Setting width/height clears the canvas — even when assigned the same
    // value. Leaflet fires `move` continuously during pan, so reallocating
    // the GPU texture every frame is wasted work. Only resize when the
    // viewport actually changes; otherwise just clear in place.
    if (this.canvas.width !== newW || this.canvas.height !== newH) {
      this.canvas.width = newW;
      this.canvas.height = newH;
      this.canvas.style.width = size.x + 'px';
      this.canvas.style.height = size.y + 'px';
    }

    // Re-align canvas to the map container origin (so panning works).
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(this.canvas, topLeft);

    const ctx = this.canvas.getContext('2d')!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);

    if (!this.state) return;

    const { slice, vmin, vmax } = this.state;
    const lat = slice.lat;
    const lon = slice.lon;
    const values = slice.values;
    const metric = slice.metric as HewsonMetric;
    if (lat.length < 2 || lon.length < 2) return;

    const dLat = Math.abs(lat[1] - lat[0]);
    const dLon = Math.abs(lon[1] - lon[0]);
    const halfLat = dLat / 2;
    const halfLon = dLon / 2;

    // Quick viewport cull — skip cells that are completely off-screen.
    const bounds = map.getBounds();
    const viewW = bounds.getWest() - dLon;
    const viewE = bounds.getEast() + dLon;
    const viewS = bounds.getSouth() - dLat;
    const viewN = bounds.getNorth() + dLat;

    for (let i = 0; i < lat.length; i++) {
      const la = lat[i];
      if (la < viewS || la > viewN) continue;

      // Compute container Y for the cell's north/south edges once per row.
      const ptN = map.latLngToContainerPoint([la + halfLat, lon[0]]);
      const ptS = map.latLngToContainerPoint([la - halfLat, lon[0]]);
      const yTop = Math.min(ptN.y, ptS.y);
      const yBot = Math.max(ptN.y, ptS.y);
      // +1 px to swallow sub-pixel seams between adjacent rows.
      const h = yBot - yTop + 1;

      const row = values[i];
      for (let j = 0; j < lon.length; j++) {
        const lo = lon[j];
        if (lo < viewW || lo > viewE) continue;
        const v = row[j];
        if (v === null || !Number.isFinite(v)) continue;

        const ptW = map.latLngToContainerPoint([la, lo - halfLon]);
        const ptE = map.latLngToContainerPoint([la, lo + halfLon]);
        const xLeft = Math.min(ptW.x, ptE.x);
        const xRight = Math.max(ptW.x, ptE.x);
        const w = xRight - xLeft + 1;

        ctx.fillStyle = colorFor(metric, v, vmin, vmax);
        ctx.fillRect(xLeft, yTop, w, h);
      }
    }
  };
}
