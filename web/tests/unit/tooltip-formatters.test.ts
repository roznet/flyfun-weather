/** Tests for cross-section per-layer tooltip formatters.
 *
 * Why these tests exist: a previous refactor renamed cross-section layer
 * IDs and silently dropped tooltip lines for the renamed layers because
 * the tooltip registry's `id` / `enabledBy` aliases lost their backing
 * layer. The "structural" tests below check that every band-style layer
 * registered in `layer-registry.ts` is still reachable from the tooltip
 * registry — either as a primary `id` or via an `enabledBy` alias.
 */

import { describe, it, expect } from 'vitest';
import { LAYER_TOOLTIPS } from '../../ts/visualization/cross-section/tooltip-formatters';
import { getAllLayers } from '../../ts/visualization/cross-section/layer-registry';
import {
  makeVizPoint, makeAltitudeLines, makeCloudLayer, makeIcingZone,
  makeSfipZone, makeSldZone, makeCatLayer, makeInversion,
} from './fixtures/viz-point';

function findDef(id: string) {
  const def = LAYER_TOOLTIPS.find((d) => d.id === id);
  if (!def) throw new Error(`No tooltip def for ${id}`);
  return def;
}

/** Run a layer's formatLine against the first matching zone at a given altitude. */
function format(id: string, point: Parameters<ReturnType<typeof findDef>['getZones']>[0], hoverAltFt: number): string | null {
  const def = findDef(id);
  const zones = def.getZones(point);
  for (const z of zones) {
    if (hoverAltFt >= z.baseFt && hoverAltFt <= z.topFt) {
      return def.formatLine(z, hoverAltFt);
    }
  }
  return null;
}

describe('LAYER_TOOLTIPS registry — structural integrity', () => {
  it('registers exactly the band-style layers that need tooltips', () => {
    // Every band-style layer (anything except the proximity-based lines,
    // terrain, and the route-global current-conditions overlay) MUST be
    // reachable from the tooltip registry, otherwise the hover tooltip will
    // silently drop that row. The exempt layers are handled inline in
    // interaction.ts (different shape — proximity- or X-span-based, not
    // per-VizPoint zones).
    const PROXIMITY_OR_TERRAIN_LAYERS = new Set([
      'terrain',
      'freezing-level', 'minus-10c', 'minus-20c',
      'lcl', 'lfc', 'el',
      'cruise-altitude',
      'current-conditions',
    ]);

    const layerIds = getAllLayers()
      .map((l) => l.id)
      .filter((id) => !PROXIMITY_OR_TERRAIN_LAYERS.has(id));

    const reachable = new Set<string>();
    for (const def of LAYER_TOOLTIPS) {
      reachable.add(def.id);
      for (const alias of def.enabledBy ?? []) reachable.add(alias);
    }

    for (const id of layerIds) {
      expect(reachable.has(id), `layer "${id}" has no tooltip entry or enabledBy alias`).toBe(true);
    }
  });

  it('aliases soft-cloud-bands → cloud-bands tooltip', () => {
    // Specific guard for the historical regression: enabling only
    // soft-cloud-bands must still produce a DD cloud tooltip line.
    const cloudDD = findDef('cloud-bands');
    expect(cloudDD.enabledBy).toContain('soft-cloud-bands');
  });

  it('aliases soft-nwp-cloud-bands → nwp-cloud-bands tooltip', () => {
    const cloudNWP = findDef('nwp-cloud-bands');
    expect(cloudNWP.enabledBy).toContain('soft-nwp-cloud-bands');
  });
});

describe('cloud-bands (DD)', () => {
  it('formats with DD and temperature when present', () => {
    const point = makeVizPoint({
      cloudLayers: [makeCloudLayer({
        baseFt: 3000, topFt: 6000, coverage: 'bkn',
        meanDewpointDepressionC: 1.2, meanTemperatureC: -5,
      })],
    });
    expect(format('cloud-bands', point, 4500)).toBe('3,000 ft–FL060 bkn (DD 1.2°C, T -5°C)');
  });

  it('drops empty extras when DD/temperature missing', () => {
    const point = makeVizPoint({
      cloudLayers: [makeCloudLayer({ baseFt: 3000, topFt: 6000, coverage: 'sct' })],
    });
    expect(format('cloud-bands', point, 4500)).toBe('3,000 ft–FL060 sct');
  });
});

describe('nwp-cloud-bands (NWP)', () => {
  it('shows [band] tag only when source is grib', () => {
    const grib = makeVizPoint({
      nwpCloudLayers: [makeCloudLayer({
        baseFt: 5000, topFt: 9000, coverage: 'ovc',
        meanCloudCoverPct: 90, meanTemperatureC: -2, source: 'grib',
      })],
    });
    expect(format('nwp-cloud-bands', grib, 7000)).toBe(
      'NWP: ovc FL050–FL090 (CC 90%, T -2°C) [band]',
    );

    const nwp3d = makeVizPoint({
      nwpCloudLayers: [makeCloudLayer({
        baseFt: 5000, topFt: 9000, coverage: 'bkn',
        meanCloudCoverPct: 65, meanTemperatureC: -2, source: 'nwp_3d',
      })],
    });
    expect(format('nwp-cloud-bands', nwp3d, 7000)).toBe(
      'NWP: bkn FL050–FL090 (CC 65%, T -2°C)',
    );
  });

  it('returns null when no nwpCloudLayers present', () => {
    const point = makeVizPoint({ nwpCloudLayers: null });
    expect(format('nwp-cloud-bands', point, 5000)).toBeNull();
  });
});

describe('icing layers', () => {
  it('icing-bands filters none and renders +SLD when sldRisk', () => {
    const point = makeVizPoint({
      icingZones: [
        makeIcingZone({ baseFt: 1000, topFt: 2000, risk: 'none', type: 'mixed' }),
        makeIcingZone({
          baseFt: 4000, topFt: 8000, risk: 'moderate', type: 'rime',
          meanIcingIndex: 4, meanTemperatureC: -3, sldRisk: true,
        }),
      ],
    });
    // The "none" zone is filtered out by getZones; only the moderate zone
    // is matched at 6000 ft.
    expect(format('icing-bands', point, 6000)).toBe('4,000 ft–FL080 moderate rime (idx 4, T -3°C) +SLD');
  });

  it('icing-ogimet-nwp-bands renders header in registry', () => {
    expect(findDef('icing-ogimet-nwp-bands').header).toBe('Icing (Ogimet-NWP)');
  });

  it('sfip-bands shows SFIP score and [proxy] only for non-full variants', () => {
    const fullVariant = makeVizPoint({
      sfipZones: [makeSfipZone({
        baseFt: 4000, topFt: 8000, risk: 'light', type: 'mixed',
        meanSfip100: 35, variant: 'full', meanTemperatureC: -4,
      })],
    });
    expect(format('sfip-bands', fullVariant, 6000)).toBe(
      '4,000 ft–FL080 SFIP 35/100 mixed (T -4°C)',
    );

    const proxyVariant = makeVizPoint({
      sfipZones: [makeSfipZone({
        baseFt: 4000, topFt: 8000, risk: 'light', type: 'mixed',
        meanSfip100: 28, variant: 'interp_no_vv', meanTemperatureC: -4,
      })],
    });
    expect(format('sfip-bands', proxyVariant, 6000)).toBe(
      '4,000 ft–FL080 SFIP 28/100 mixed (T -4°C) [proxy]',
    );
  });

  it('sld-bands shows mechanism', () => {
    const point = makeVizPoint({
      sldZones: [makeSldZone({ baseFt: 2000, topFt: 5000, risk: 'moderate', mechanism: 'warm-nose' })],
    });
    expect(format('sld-bands', point, 3500)).toBe('2,000 ft–FL050 moderate warm-nose');
  });
});

describe('turbulence layers', () => {
  it('cat-bands shows Ri when populated', () => {
    const point = makeVizPoint({
      catLayers: [makeCatLayer({
        baseFt: 15000, topFt: 20000, risk: 'moderate', richardsonNumber: 0.18,
      })],
    });
    expect(format('cat-bands', point, 17000)).toBe('FL150–FL200 CAT moderate (Ri 0.18)');
  });

  it('cat-bands omits Ri when not populated', () => {
    const point = makeVizPoint({
      catLayers: [makeCatLayer({ baseFt: 15000, topFt: 20000, risk: 'light' })],
    });
    expect(format('cat-bands', point, 17000)).toBe('FL150–FL200 CAT light');
  });

  it('e-shear-bands renders separately from cat-bands', () => {
    const point = makeVizPoint({
      eShearLayers: [makeCatLayer({
        baseFt: 12000, topFt: 16000, risk: 'light', richardsonNumber: 0.22,
      })],
    });
    expect(format('e-shear-bands', point, 14000)).toBe('FL120–FL160 E-Shear light (Ri 0.22)');
  });
});

describe('convective layers', () => {
  it('thermo-convective synthesizes from per-point fields, includes CAPE/CIN/LCL→EL', () => {
    const point = makeVizPoint({
      convectiveRisk: 'high',
      convectiveBaseFt: 4000,
      convectiveTopFt: 30000,
      capeSurfaceJkg: 1500,
      cinSurfaceJkg: -25,
      altitudeLines: makeAltitudeLines({ lclAltitudeFt: 4000, elAltitudeFt: 32000 }),
    });
    const out = format('thermo-convective-bg', point, 10000)!;
    expect(out).toContain('Thermo Conv: high');
    expect(out).toContain('CAPE 1500');
    expect(out).toContain('CIN -25');
    expect(out).toContain('LCL→EL: 4,000 ft–FL320');
  });

  it('thermo-convective falls back to Tower range when LCL/EL missing', () => {
    const point = makeVizPoint({
      convectiveRisk: 'low',
      convectiveBaseFt: 5000,
      convectiveTopFt: 20000,
      capeSurfaceJkg: 200,
    });
    const out = format('thermo-convective-bg', point, 10000)!;
    expect(out).toContain('Tower: FL050–FL200');
    expect(out).not.toContain('LCL→EL');
  });

  it('thermo-convective returns no zones when risk is none', () => {
    const point = makeVizPoint({
      convectiveRisk: 'none', convectiveBaseFt: 5000, convectiveTopFt: 20000,
    });
    expect(format('thermo-convective-bg', point, 10000)).toBeNull();
  });

  it('nwp-convective shows cover% and method tag', () => {
    const point = makeVizPoint({
      nwpConvectiveRisk: 'moderate',
      nwpConvectiveBaseFt: 3000,
      nwpConvectiveTopFt: 25000,
      nwpConvectiveCoverPct: 45,
      nwpConvectiveMethod: 'nwp_lcl_top',
    });
    const out = format('nwp-convective-bg', point, 10000)!;
    expect(out).toContain('NWP Conv: moderate');
    expect(out).toContain('45% cover');
    expect(out).toContain('Tower: 3,000 ft–FL250');
    expect(out).toContain('[nwp_lcl_top]');
  });
});

describe('inversion-bands', () => {
  it('shows surface-based suffix', () => {
    const point = makeVizPoint({
      inversions: [makeInversion({ baseFt: 0, topFt: 1500, strengthC: 3.2, surfaceBased: true })],
    });
    expect(format('inversion-bands', point, 800)).toBe('0 ft–1,500 ft +3.2°C (sfc)');
  });

  it('omits suffix for elevated inversions', () => {
    const point = makeVizPoint({
      inversions: [makeInversion({ baseFt: 4000, topFt: 6000, strengthC: 1.8 })],
    });
    expect(format('inversion-bands', point, 5000)).toBe('4,000 ft–FL060 +1.8°C');
  });
});
