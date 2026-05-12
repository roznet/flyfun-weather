/** Tests for compare-mode zone accessor.
 *
 * The accessor normalizes per-layer zone shapes (icing/cloud/CAT/inversion/
 * convective) into a uniform { baseFt, topFt, severity } slice for the
 * cross-model overlay/consensus renderers. A regression here would make
 * compare-mode silently render an empty band for the affected layer.
 */

import { describe, it, expect } from 'vitest';
import { getZonesForLayer } from '../../ts/visualization/cross-section/compare-zone-access';
import {
  makeVizPoint, makeIcingZone, makeCloudLayer, makeCatLayer, makeInversion,
  makeSfipZone,
} from './fixtures/viz-point';

describe('getZonesForLayer', () => {
  it('returns empty for unknown layer id', () => {
    expect(getZonesForLayer('does-not-exist', makeVizPoint())).toEqual([]);
  });

  it('icing-bands filters out risk=none and exposes severity', () => {
    const p = makeVizPoint({
      icingZones: [
        makeIcingZone({ baseFt: 1000, topFt: 2000, risk: 'none' }),
        makeIcingZone({ baseFt: 4000, topFt: 8000, risk: 'moderate' }),
      ],
    });
    const slices = getZonesForLayer('icing-bands', p);
    expect(slices).toEqual([{ baseFt: 4000, topFt: 8000, severity: 'moderate' }]);
  });

  it('icing-ogimet-nwp-bands reads icingOgimetNwpZones', () => {
    const p = makeVizPoint({
      icingOgimetNwpZones: [makeIcingZone({ baseFt: 5000, topFt: 9000, risk: 'light' })],
    });
    expect(getZonesForLayer('icing-ogimet-nwp-bands', p)).toEqual([
      { baseFt: 5000, topFt: 9000, severity: 'light' },
    ]);
  });

  it('sfip-bands filters none and exposes risk as severity', () => {
    const p = makeVizPoint({
      sfipZones: [
        makeSfipZone({ risk: 'none' }),
        makeSfipZone({ baseFt: 4000, topFt: 8000, risk: 'severe' }),
      ],
    });
    expect(getZonesForLayer('sfip-bands', p)).toEqual([
      { baseFt: 4000, topFt: 8000, severity: 'severe' },
    ]);
  });

  it('square-cloud-bands does NOT filter (covers low-coverage layers too) and uses coverage as severity', () => {
    const p = makeVizPoint({
      cloudLayers: [
        makeCloudLayer({ baseFt: 1000, topFt: 3000, coverage: 'sct' }),
        makeCloudLayer({ baseFt: 5000, topFt: 9000, coverage: 'ovc' }),
      ],
    });
    const slices = getZonesForLayer('square-cloud-bands', p);
    expect(slices).toEqual([
      { baseFt: 1000, topFt: 3000, severity: 'sct' },
      { baseFt: 5000, topFt: 9000, severity: 'ovc' },
    ]);
  });

  it('square-nwp-cloud-bands handles null nwpCloudLayers', () => {
    const p = makeVizPoint({ nwpCloudLayers: null });
    expect(getZonesForLayer('square-nwp-cloud-bands', p)).toEqual([]);
  });

  it('square-nwp-cloud-bands maps coverage to severity', () => {
    const p = makeVizPoint({
      nwpCloudLayers: [makeCloudLayer({ baseFt: 5000, topFt: 9000, coverage: 'bkn' })],
    });
    expect(getZonesForLayer('square-nwp-cloud-bands', p)).toEqual([
      { baseFt: 5000, topFt: 9000, severity: 'bkn' },
    ]);
  });

  it('cat-bands filters none and exposes risk', () => {
    const p = makeVizPoint({
      catLayers: [
        makeCatLayer({ risk: 'none' }),
        makeCatLayer({ baseFt: 18000, topFt: 22000, risk: 'moderate' }),
      ],
    });
    expect(getZonesForLayer('cat-bands', p)).toEqual([
      { baseFt: 18000, topFt: 22000, severity: 'moderate' },
    ]);
  });

  it('inversion-bands always uses severity="inversion" (no filtering)', () => {
    const p = makeVizPoint({
      inversions: [
        makeInversion({ baseFt: 0, topFt: 1500, strengthC: 0.5 }),
        makeInversion({ baseFt: 4000, topFt: 6000, strengthC: 5.0 }),
      ],
    });
    expect(getZonesForLayer('inversion-bands', p)).toEqual([
      { baseFt: 0, topFt: 1500, severity: 'inversion' },
      { baseFt: 4000, topFt: 6000, severity: 'inversion' },
    ]);
  });

  it('thermo-convective-bg synthesizes a single zone from per-point fields', () => {
    const p = makeVizPoint({
      convectiveRisk: 'high',
      convectiveBaseFt: 4000,
      convectiveTopFt: 35000,
    });
    expect(getZonesForLayer('thermo-convective-bg', p)).toEqual([
      { baseFt: 4000, topFt: 35000, severity: 'high' },
    ]);
  });

  it('thermo-convective-bg returns empty when risk=none', () => {
    const p = makeVizPoint({ convectiveRisk: 'none', convectiveBaseFt: 4000, convectiveTopFt: 30000 });
    expect(getZonesForLayer('thermo-convective-bg', p)).toEqual([]);
  });

  it('thermo-convective-bg returns empty when base/top missing', () => {
    const p = makeVizPoint({ convectiveRisk: 'high', convectiveBaseFt: null, convectiveTopFt: null });
    expect(getZonesForLayer('thermo-convective-bg', p)).toEqual([]);
  });

  it('nwp-convective-bg synthesizes from nwp* fields', () => {
    const p = makeVizPoint({
      nwpConvectiveRisk: 'moderate',
      nwpConvectiveBaseFt: 3000,
      nwpConvectiveTopFt: 25000,
    });
    expect(getZonesForLayer('nwp-convective-bg', p)).toEqual([
      { baseFt: 3000, topFt: 25000, severity: 'moderate' },
    ]);
  });

  it('nwp-convective-bg returns empty when risk=none', () => {
    expect(getZonesForLayer('nwp-convective-bg', makeVizPoint())).toEqual([]);
  });
});
