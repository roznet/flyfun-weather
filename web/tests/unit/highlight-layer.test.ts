/** Rendering rules for the advisory highlight scrim (#375 follow-up).
 *
 * The scrim lays a translucent wash over the plot area and punches the flagged
 * regions out of it with `destination-out`. That operator computes
 * `outAlpha = destAlpha * (1 - srcAlpha)`, so the punch source MUST be fully
 * opaque: punching with the wash's own translucent colour erases only part of
 * it, leaving the "spotlight" still dimmed and making overlapping cutouts
 * brighter than isolated ones. iOS punches with `.color(.black)` — these tests
 * pin the web renderer to the same rule so the two platforms can't drift.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import type { CoordTransform, VizRouteData } from '../../ts/visualization/types';
import type { AdvisoryHighlights } from '../../ts/types/advisories';

interface FillRectCall {
  gco: string;
  fillStyle: string;
  rect: [number, number, number, number];
}

/** An outline drawn with `strokeRect` (a bounded region's frame). */
interface StrokeRectCall {
  strokeStyle: string;
  dash: number[];
  rect: [number, number, number, number];
}

/** A stroked path (the depth-unknown stub) — its points and dash pattern. */
interface StrokePathCall {
  strokeStyle: string;
  dash: number[];
  points: [number, number][];
}

interface StrokeCalls {
  rects: StrokeRectCall[];
  paths: StrokePathCall[];
}

/** Minimal 2D-context stub recording the fills and strokes we care about. */
function makeCtx(calls: FillRectCall[], strokes: StrokeCalls = { rects: [], paths: [] }) {
  return {
    canvas: { width: 800, height: 400 },
    globalCompositeOperation: 'source-over',
    fillStyle: '' as string,
    strokeStyle: '' as string,
    lineWidth: 1,
    _dash: [] as number[],
    _path: [] as [number, number][],
    fillRect(x: number, y: number, w: number, h: number) {
      calls.push({
        gco: this.globalCompositeOperation,
        fillStyle: String(this.fillStyle),
        rect: [x, y, w, h],
      });
    },
    setLineDash(d: number[]) { this._dash = d; },
    strokeRect(x: number, y: number, w: number, h: number) {
      strokes.rects.push({
        strokeStyle: String(this.strokeStyle),
        dash: [...this._dash],
        rect: [x, y, w, h],
      });
    },
    moveTo(x: number, y: number) { this._path.push([x, y]); },
    lineTo(x: number, y: number) { this._path.push([x, y]); },
    stroke() {
      strokes.paths.push({
        strokeStyle: String(this.strokeStyle),
        dash: [...this._dash],
        points: [...this._path],
      });
    },
    scale() {}, drawImage() {}, save() {}, restore() {},
    beginPath() { this._path = []; }, rect() {}, clip() {},
  };
}

/** Alpha of an `rgba(...)` / `#rgb` / `#rrggbb` fill style. Opaque → 1. */
function alphaOf(style: string): number {
  const m = /rgba\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*,\s*([\d.]+)\s*\)/.exec(style);
  return m ? parseFloat(m[1]) : 1;
}

const PLOT = { left: 50, top: 20, width: 700, height: 300, bottom: 320, right: 750 };

const transform: CoordTransform = {
  distanceToX: (nm) => PLOT.left + nm,               // 1 nm = 1 px over a 700 nm route
  altitudeToY: (ft) => PLOT.top + PLOT.height - ft / 100,  // 100 ft = 1 px
  xToDistance: (x) => x - PLOT.left,
  yToAltitude: (y) => (PLOT.top + PLOT.height - y) * 100,
  plotArea: PLOT as CoordTransform['plotArea'],
};

/** Fills recorded on the OFFSCREEN context (where the wash + punches happen). */
let offCalls: FillRectCall[];
/** Fills recorded on the MAIN context (where the ribbon happens). */
let mainCalls: FillRectCall[];
/** Strokes recorded on the MAIN context: cutout outlines + depth-unknown stubs. */
let mainStrokes: StrokeCalls;

// Module under test imports `cssVar`/`isDarkTheme`, which touch the DOM; the
// vitest env is `node`, so stub just enough of it (mirrors url-state.test.ts).
beforeEach(() => {
  offCalls = [];
  mainCalls = [];
  mainStrokes = { rects: [], paths: [] };
  const g = globalThis as unknown as Record<string, unknown>;
  g.document = {
    documentElement: { dataset: {} as Record<string, string> },
    createElement: () => ({ width: 0, height: 0, getContext: () => makeCtx(offCalls) }),
  };
  g.getComputedStyle = () => ({ getPropertyValue: () => '' });
  g.window = { devicePixelRatio: 2 };
});

function render(highlights: AdvisoryHighlights | null) {
  // Imported lazily so the DOM stubs above are in place first.
  return import('../../ts/visualization/cross-section/layers/highlight-layer').then(
    ({ highlightLayer }) => {
      const ctx = makeCtx(mainCalls, mainStrokes) as unknown as CanvasRenderingContext2D;
      const data = { advisoryHighlights: highlights } as unknown as VizRouteData;
      highlightLayer.render(ctx, transform, data);
    },
  );
}

const region = (
  from: number, to: number, base: number | null, top: number | null,
  kind = 'icing_band',
) => ({
  dist_from_nm: from, dist_to_nm: to, base_ft: base, top_ft: top,
  kind, severity: 'red' as const,
});

describe('highlight scrim compositing', () => {
  it('punches cutouts with a fully opaque source, not the translucent wash', async () => {
    await render({ ribbon: [], regions: [region(100, 200, 3000, 9000)] });

    const punches = offCalls.filter((c) => c.gco === 'destination-out');
    expect(punches).toHaveLength(1);
    // The whole point: a translucent punch (alpha 0.34) would only remove ~a
    // third of the wash and leave the highlighted region dimmed.
    expect(alphaOf(punches[0].fillStyle)).toBe(1);
  });

  it('lays the wash translucent before punching', async () => {
    await render({ ribbon: [], regions: [region(100, 200, 3000, 9000)] });

    const wash = offCalls.find((c) => c.gco === 'source-over');
    expect(wash).toBeDefined();
    expect(alphaOf(wash!.fillStyle)).toBeGreaterThan(0);
    expect(alphaOf(wash!.fillStyle)).toBeLessThan(1);
    // Wash covers exactly the plot area.
    expect(wash!.rect).toEqual([PLOT.left, PLOT.top, PLOT.width, PLOT.height]);
  });

  it('keeps overlapping cutouts as bright as isolated ones (opaque punch is idempotent)', async () => {
    await render({
      ribbon: [],
      regions: [region(100, 300, 3000, 9000), region(200, 400, 3000, 9000)],
    });

    const punches = offCalls.filter((c) => c.gco === 'destination-out');
    expect(punches).toHaveLength(2);
    // Both punches opaque ⇒ the overlap is erased to the same alpha (0) as the
    // non-overlapping parts, rather than being double-reduced and standing out.
    for (const p of punches) expect(alphaOf(p.fillStyle)).toBe(1);
  });

  it('never dims a clean chart (no regions ⇒ no wash at all)', async () => {
    await render({
      ribbon: [{ dist_from_nm: 0, dist_to_nm: 700, severity: 'green' }],
      regions: [],
    });

    expect(offCalls).toHaveLength(0);
    // ...but the all-green verdict ribbon still renders on the main context.
    expect(mainCalls.length).toBeGreaterThan(0);
  });

  it('no-ops entirely when the pack carries no highlight data', async () => {
    await render(null);
    expect(offCalls).toHaveLength(0);
    expect(mainCalls).toHaveLength(0);
  });
});

/** #592 — a `tower_unresolved` region means "a cell is here, depth unknown",
 *  which is NOT the same as "the hazard fills the column". It used to render as
 *  a terrain-to-top rectangle: the strongest possible claim about vertical
 *  extent, drawn exactly where the least is known, and it read as a rendering
 *  bug (tall empty boxes over clear sky). These pin the replacement, and the
 *  iOS renderer mirrors them. */
describe('depth-unknown regions (#592)', () => {
  it('punches no cutout and draws no box for a depth-unknown region', async () => {
    await render({ ribbon: [], regions: [region(100, 200, null, null, 'tower_unresolved')] });

    expect(offCalls.filter((c) => c.gco === 'destination-out')).toHaveLength(0);
    expect(mainStrokes.rects).toHaveLength(0);
  });

  it('does not dim the chart when depth-unknown regions are all there is', async () => {
    await render({ ribbon: [], regions: [region(100, 200, null, null, 'tower_unresolved')] });

    // Dimming the whole chart to spotlight nothing is worse than not dimming it.
    expect(offCalls).toHaveLength(0);
  });

  it('marks the position with a short dashed stub on the plot floor', async () => {
    await render({ ribbon: [], regions: [region(100, 200, null, null, 'tower_unresolved')] });

    expect(mainStrokes.paths).toHaveLength(1);
    const stub = mainStrokes.paths[0];
    expect(stub.dash.length).toBeGreaterThan(0);            // dashed = "unknown"
    const [[x0, y0], [x1, y1]] = stub.points;
    expect(x0).toBe(x1);                                    // vertical
    expect(x0).toBe(transform.distanceToX(150));            // at the region's mid-x
    expect(y0).toBe(PLOT.top + PLOT.height);                // rooted on the floor
    expect(y0 - y1).toBeGreaterThan(0);                     // rising
    expect(y0 - y1).toBeLessThan(PLOT.height / 4);          // a stub, not a column
  });

  it('still spotlights the bounded regions beside a depth-unknown one', async () => {
    await render({
      ribbon: [],
      regions: [
        region(100, 200, 3000, 9000),
        region(300, 400, null, null, 'tower_unresolved'),
      ],
    });

    // Exactly one punch + one outline (the bounded region), one stub (the ghost).
    expect(offCalls.filter((c) => c.gco === 'destination-out')).toHaveLength(1);
    expect(mainStrokes.rects).toHaveLength(1);
    expect(mainStrokes.paths).toHaveLength(1);
  });

  it('keeps drawing genuinely full-column kinds terrain-to-top', async () => {
    // `precip_column` / `freezing_precip_column` are full-column BY DEFINITION
    // (rain reaches the ground) — the depth-unknown rule must not swallow them.
    await render({ ribbon: [], regions: [region(100, 200, null, null, 'precip_column')] });

    expect(offCalls.filter((c) => c.gco === 'destination-out')).toHaveLength(1);
    expect(mainStrokes.rects).toHaveLength(1);
    expect(mainStrokes.rects[0].rect[1]).toBe(PLOT.top);
    expect(mainStrokes.rects[0].rect[3]).toBe(PLOT.height);
  });
});

describe('estimated-depth regions (#592)', () => {
  it('outlines a borrowed-bounds tower dashed, a model-own tower solid', async () => {
    await render({
      ribbon: [],
      regions: [
        region(100, 200, 3000, 9000, 'tower'),
        region(300, 400, 3000, 32000, 'tower_estimated'),
      ],
    });

    expect(mainStrokes.rects).toHaveLength(2);
    expect(mainStrokes.rects[0].dash).toEqual([]);           // the model's own claim
    expect(mainStrokes.rects[1].dash.length).toBeGreaterThan(0);  // an estimate
  });

  it('spotlights an estimated tower like any other bounded region', async () => {
    await render({ ribbon: [], regions: [region(300, 400, 3000, 32000, 'tower_estimated')] });

    const punches = offCalls.filter((c) => c.gco === 'destination-out');
    expect(punches).toHaveLength(1);
    expect(alphaOf(punches[0].fillStyle)).toBe(1);
  });
});
