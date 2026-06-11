/** Front markers (experimental, #196) — first Phase D.3 cross-section layer.
 *
 *  Draws a vertical marker at each on-track Hewson front crossing, positioned
 *  by along-route distance, colored by kind (cold=blue, warm=red,
 *  quasi=purple) and weighted by intensity. Advisory-only, free-atmosphere
 *  boundaries — not drawn SIGWX, and blind to low IMC / fog (§10a.2).
 *
 *  Phase D.4 (GRIB era) will replace these single markers with continuous-
 *  altitude θe bands; this layer is the discrete-marker precursor. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import {
  frontColor, frontKindLabel, frontAlpha, frontDash, frontIsConvective,
  FRONT_INTENSITY_WEIGHT,
} from '../../front-style';

const KM_PER_NM = 1.852;

export const frontsMarkersLayer: CrossSectionLayer = {
  id: 'fronts-markers',
  name: 'Air-mass boundary (experimental)',
  group: 'fronts',
  defaultEnabled: false,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const fronts = data.fronts;
    if (!fronts || fronts.crossings.length === 0) return;
    // Front distances are great-circle km; the X axis is in nautical miles.
    if (data.timeAxisMode) return;  // single-airport time view has no route distance

    const { top, height } = transform.plotArea;
    const yTop = top;
    const yBottom = top + height;

    ctx.save();
    // Visual encoding (matches the advisory gate so the picture reads like the
    // grade): colour = kind, line weight = intensity, SOLID vs DASHED = wet/dry
    // co-location, OPACITY = persistence (faint = flickering/uncertain), and a
    // small triangle flags convective boundaries (towers above an overflown front).
    for (const c of fronts.crossings) {
      const x = transform.distanceToX(c.distance_km / KM_PER_NM);
      const color = frontColor(c.kind);
      const savedAlpha = ctx.globalAlpha;
      ctx.globalAlpha = frontAlpha(c);

      ctx.strokeStyle = color;
      ctx.lineWidth = FRONT_INTENSITY_WEIGHT[c.intensity] ?? 2;
      ctx.setLineDash(frontDash(c));
      ctx.beginPath();
      ctx.moveTo(x, yTop);
      ctx.lineTo(x, yBottom);
      ctx.stroke();
      ctx.setLineDash([]);

      // Label chip at the top of the marker (kind initial).
      const label = frontKindLabel(c.kind).charAt(0).toUpperCase();
      ctx.font = 'bold 11px system-ui, sans-serif';
      const tw = ctx.measureText(label).width;
      const pad = 3;
      const chipW = tw + pad * 2;
      const chipH = 15;
      ctx.fillStyle = color;
      ctx.fillRect(x - chipW / 2, yTop, chipW, chipH);
      ctx.fillStyle = '#ffffff';
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'center';
      ctx.fillText(label, x, yTop + chipH / 2);

      // Convective glyph: a small upward triangle below the chip = towers.
      if (frontIsConvective(c)) {
        const ty = yTop + chipH + 2;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(x, ty);
        ctx.lineTo(x - 5, ty + 8);
        ctx.lineTo(x + 5, ty + 8);
        ctx.closePath();
        ctx.fill();
      }
      ctx.globalAlpha = savedAlpha;
    }
    ctx.restore();
  },
};
