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

/** Minimal 2D-context stub recording the fills we care about. */
function makeCtx(calls: FillRectCall[]) {
  return {
    canvas: { width: 800, height: 400 },
    globalCompositeOperation: 'source-over',
    fillStyle: '' as string,
    strokeStyle: '' as string,
    lineWidth: 1,
    fillRect(x: number, y: number, w: number, h: number) {
      calls.push({
        gco: this.globalCompositeOperation,
        fillStyle: String(this.fillStyle),
        rect: [x, y, w, h],
      });
    },
    scale() {}, drawImage() {}, save() {}, restore() {},
    beginPath() {}, rect() {}, clip() {}, strokeRect() {},
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

// Module under test imports `cssVar`/`isDarkTheme`, which touch the DOM; the
// vitest env is `node`, so stub just enough of it (mirrors url-state.test.ts).
beforeEach(() => {
  offCalls = [];
  mainCalls = [];
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
      const ctx = makeCtx(mainCalls) as unknown as CanvasRenderingContext2D;
      const data = { advisoryHighlights: highlights } as unknown as VizRouteData;
      highlightLayer.render(ctx, transform, data);
    },
  );
}

const region = (from: number, to: number, base: number | null, top: number | null) => ({
  dist_from_nm: from, dist_to_nm: to, base_ft: base, top_ft: top,
  kind: 'icing_band', severity: 'red' as const,
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
