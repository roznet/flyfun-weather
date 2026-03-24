/** Generic zone accessor for compare band layers.
 *
 * Maps a ComparableLayer id to the corresponding zone array on a VizPoint,
 * normalizing all zone types to a common { baseFt, topFt, severity } shape.
 */

import type { VizPoint } from '../types';

export interface ZoneSlice {
  baseFt: number;
  topFt: number;
  severity: string;
}

/** Return the zone array for a given band layer id from a VizPoint. */
export function getZonesForLayer(layerId: string, point: VizPoint): ZoneSlice[] {
  switch (layerId) {
    case 'icing-bands':
      return point.icingZones
        .filter((z) => z.risk !== 'none')
        .map((z) => ({ baseFt: z.baseFt, topFt: z.topFt, severity: z.risk }));

    case 'icing-ogimet-nwp-bands':
      return point.icingOgimetNwpZones
        .filter((z) => z.risk !== 'none')
        .map((z) => ({ baseFt: z.baseFt, topFt: z.topFt, severity: z.risk }));

    case 'sfip-bands':
      return point.sfipZones
        .filter((z) => z.risk !== 'none')
        .map((z) => ({ baseFt: z.baseFt, topFt: z.topFt, severity: z.risk }));

    case 'cloud-bands':
      return point.cloudLayers
        .map((cl) => ({ baseFt: cl.baseFt, topFt: cl.topFt, severity: cl.coverage }));

    case 'nwp-cloud-bands':
      return point.nwpCloudLayers
        .map((cl) => ({ baseFt: cl.baseFt, topFt: cl.topFt, severity: cl.coverage }));

    case 'cat-bands':
      return point.catLayers
        .filter((z) => z.risk !== 'none')
        .map((z) => ({ baseFt: z.baseFt, topFt: z.topFt, severity: z.risk }));

    case 'inversion-bands':
      return point.inversions
        .map((inv) => ({ baseFt: inv.baseFt, topFt: inv.topFt, severity: 'inversion' }));

    case 'thermo-convective-bg':
      if (point.convectiveRisk !== 'none' &&
          point.convectiveBaseFt !== null && point.convectiveTopFt !== null) {
        return [{ baseFt: point.convectiveBaseFt, topFt: point.convectiveTopFt, severity: point.convectiveRisk }];
      }
      return [];

    case 'nwp-convective-bg':
      if (point.nwpConvectiveRisk !== 'none' &&
          point.nwpConvectiveBaseFt !== null && point.nwpConvectiveTopFt !== null) {
        return [{ baseFt: point.nwpConvectiveBaseFt, topFt: point.nwpConvectiveTopFt, severity: point.nwpConvectiveRisk }];
      }
      return [];

    default:
      return [];
  }
}
