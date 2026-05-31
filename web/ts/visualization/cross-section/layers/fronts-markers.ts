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
import { frontColor, frontKindLabel, FRONT_INTENSITY_DASH, FRONT_INTENSITY_WEIGHT } from '../../front-style';

const KM_PER_NM = 1.852;

export const frontsMarkersLayer: CrossSectionLayer = {
  id: 'fronts-markers',
  name: 'Fronts (experimental)',
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
    for (const c of fronts.crossings) {
      const x = transform.distanceToX(c.distance_km / KM_PER_NM);
      const color = frontColor(c.kind);

      ctx.strokeStyle = color;
      ctx.lineWidth = FRONT_INTENSITY_WEIGHT[c.intensity] ?? 2;
      ctx.setLineDash(FRONT_INTENSITY_DASH[c.intensity] ?? []);
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
    }
    ctx.restore();
  },
};
