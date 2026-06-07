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

/** Projects WGS84 (lat, lon) to native chart-pixel (x, y). When set, the grid
 * is painted into a chart's polar-stereographic pixel space (DWD / Met Office
 * basemap) instead of Web Mercator. */
export type ChartProjector = (lat: number, lon: number) => { x: number; y: number };

export class HewsonGridLayer extends L.Layer {
  private canvas: HTMLCanvasElement | null = null;
  private pane: HTMLElement | null = null;
  private state: RenderState | null = null;
  private opacity = 0.5;
  private map: L.Map | null = null;
  private projector: ChartProjector | null = null;

  /** Switch between Web-Mercator (null) and chart-pixel (projector) rendering.
   * In chart mode the map is expected to use `L.CRS.Simple` with chart pixels
   * fed as `L.latLng(y, x)` (matching the chart image overlay's bounds). */
  setProjector(projector: ChartProjector | null): void {
    this.projector = projector;
    this.redraw();
  }

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

    // Chart-basemap mode: cells become polar-stereographic quadrilaterals.
    if (this.projector) {
      this.drawChartCells(ctx, map, size, halfLat, halfLon);
      return;
    }

    // Quick viewport cull — skip cells that are completely off-screen.
    const bounds = map.getBounds();
    const viewW = bounds.getWest() - dLon;
    const viewE = bounds.getEast() + dLon;
    const viewS = bounds.getSouth() - dLat;
    const viewN = bounds.getNorth() + dLat;

    // Precompute column edge X coordinates once per redraw. In Web Mercator
    // the X-projection depends only on longitude, so the same xLeft/xRight
    // applies to every row. At the default zoom (~20k cells visible) this
    // collapses ~40k projection calls per pan frame down to ~400.
    const colCount = lon.length;
    const xLeft = new Float32Array(colCount);
    const xRight = new Float32Array(colCount);
    const colVisible = new Uint8Array(colCount);
    for (let j = 0; j < colCount; j++) {
      const lo = lon[j];
      colVisible[j] = lo >= viewW && lo <= viewE ? 1 : 0;
      if (!colVisible[j]) continue;
      // lat[0] is fine here — Web Mercator X is independent of latitude.
      const ptW = map.latLngToContainerPoint([lat[0], lo - halfLon]);
      const ptE = map.latLngToContainerPoint([lat[0], lo + halfLon]);
      const xl = Math.min(ptW.x, ptE.x);
      const xr = Math.max(ptW.x, ptE.x);
      xLeft[j] = xl;
      xRight[j] = xr;
    }

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
      for (let j = 0; j < colCount; j++) {
        if (!colVisible[j]) continue;
        const v = row[j];
        if (v === null || !Number.isFinite(v)) continue;

        const w = xRight[j] - xLeft[j] + 1;
        ctx.fillStyle = colorFor(metric, v, vmin, vmax);
        ctx.fillRect(xLeft[j], yTop, w, h);
      }
    }
  };

  /** Chart-pixel render path: each cell is a 4-corner quad projected through
   * the active chart projection. Polar-stereo X depends on latitude, so the
   * per-column precompute used in Web-Mercator mode doesn't apply; we project
   * all four corners per cell. ~80k projections per full redraw, but in
   * `L.CRS.Simple` Leaflet translates the pane on pan so this only fires on
   * zoom/viewreset/resize/setSlice, not every pan frame. */
  private drawChartCells(
    ctx: CanvasRenderingContext2D,
    map: L.Map,
    size: L.Point,
    halfLat: number,
    halfLon: number,
  ): void {
    const project = this.projector!;
    const { slice, vmin, vmax } = this.state!;
    const { lat, lon, values } = slice;
    const metric = slice.metric as HewsonMetric;

    // Container point for a WGS84 (lat, lon): project to chart pixels, then let
    // the CRS.Simple map place them (lat=y, lng=x — matches the image overlay).
    const toPoint = (la: number, lo: number): L.Point => {
      const p = project(la, lo);
      return map.latLngToContainerPoint(L.latLng(p.y, p.x));
    };

    // Cull generously in container space: skip cells whose centre is well
    // outside the viewport. A whole grid cell projects to at most a few px.
    const margin = 32;
    const minX = -margin;
    const maxX = size.x + margin;
    const minY = -margin;
    const maxY = size.y + margin;

    for (let i = 0; i < lat.length; i++) {
      const la = lat[i];
      const row = values[i];
      for (let j = 0; j < lon.length; j++) {
        const v = row[j];
        if (v === null || !Number.isFinite(v)) continue;
        const lo = lon[j];

        const c = toPoint(la, lo);
        if (c.x < minX || c.x > maxX || c.y < minY || c.y > maxY) continue;

        const nw = toPoint(la + halfLat, lo - halfLon);
        const ne = toPoint(la + halfLat, lo + halfLon);
        const se = toPoint(la - halfLat, lo + halfLon);
        const sw = toPoint(la - halfLat, lo - halfLon);

        ctx.fillStyle = colorFor(metric, v, vmin, vmax);
        ctx.beginPath();
        ctx.moveTo(nw.x, nw.y);
        ctx.lineTo(ne.x, ne.y);
        ctx.lineTo(se.x, se.y);
        ctx.lineTo(sw.x, sw.y);
        ctx.closePath();
        ctx.fill();
      }
    }
  }
}
