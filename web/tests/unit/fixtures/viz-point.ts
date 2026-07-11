/** Minimal VizPoint builders for unit tests.
 *
 * Tests should override only the fields they care about. The default
 * VizPoint represents a clear, calm point at sea level.
 */

import type {
  VizPoint, VizCloudLayer, VizIcingZone, VizSfipZone, VizSldZone,
  VizCATLayer, VizInversionLayer, AltitudeLines, VizCloudDiag,
} from '../../../ts/visualization/types';

export function makeAltitudeLines(overrides: Partial<AltitudeLines> = {}): AltitudeLines {
  return {
    freezingLevelFt: null,
    minus10cLevelFt: null,
    minus20cLevelFt: null,
    lclAltitudeFt: null,
    lfcAltitudeFt: null,
    elAltitudeFt: null,
    ...overrides,
  };
}

export function makeVizPoint(overrides: Partial<VizPoint> = {}): VizPoint {
  return {
    pointIndex: 0,
    distanceNm: 0,
    lat: 0,
    lon: 0,
    time: '2026-04-29T12:00:00Z',
    altitudeLines: makeAltitudeLines(),
    cloudLayers: [],
    icingZones: [],
    icingOgimetNwpZones: [],
    sfipZones: [],
    iengIcingZones: [],
    sldZones: [],
    catLayers: [],
    eShearLayers: [],
    inversions: [],
    convectiveRisk: 'none',
    convectiveBaseFt: null,
    convectiveTopFt: null,
    cinSurfaceJkg: 0,
    nwpConvectiveRisk: 'none',
    nwpConvectiveBaseFt: null,
    nwpConvectiveTopFt: null,
    nwpConvectiveCoverPct: null,
    nwpConvectivePrecipMmH: null,
    nwpConvectiveMethod: null,
    hasNwpConvective: false,
    cloudCoverTotalPct: 0,
    cloudCoverLowPct: 0,
    cloudCoverMidPct: 0,
    headwindKt: 0,
    crosswindKt: 0,
    capeSurfaceJkg: 0,
    worstModelAgreement: 'good',
    nwpCloudLayers: null,
    nwpCloudDiag: null,
    soundingCeilingFt: null,
    terrainElevationFt: 0,
    temperatureC: null,
    temperatureCruiseC: null,
    isaDevC: null,
    precipitationMm: null,
    surfaceObscuration: null,
    ...overrides,
  };
}

export function makeCloudLayer(overrides: Partial<VizCloudLayer> = {}): VizCloudLayer {
  return {
    baseFt: 3000,
    topFt: 6000,
    coverage: 'bkn',
    ...overrides,
  };
}

export function makeIcingZone(overrides: Partial<VizIcingZone> = {}): VizIcingZone {
  return {
    baseFt: 4000,
    topFt: 8000,
    risk: 'light',
    type: 'mixed',
    ...overrides,
  };
}

export function makeSfipZone(overrides: Partial<VizSfipZone> = {}): VizSfipZone {
  return {
    baseFt: 4000,
    topFt: 8000,
    risk: 'light',
    type: 'rime',
    meanSfip100: null,
    variant: 'full',
    ...overrides,
  };
}

export function makeSldZone(overrides: Partial<VizSldZone> = {}): VizSldZone {
  return {
    baseFt: 2000,
    topFt: 5000,
    risk: 'light',
    mechanism: 'warm-nose',
    ...overrides,
  };
}

export function makeCatLayer(overrides: Partial<VizCATLayer> = {}): VizCATLayer {
  return {
    baseFt: 15000,
    topFt: 20000,
    risk: 'light',
    ...overrides,
  };
}

export function makeInversion(overrides: Partial<VizInversionLayer> = {}): VizInversionLayer {
  return {
    baseFt: 1500,
    topFt: 3000,
    strengthC: 2.5,
    ...overrides,
  };
}

export function makeNwpCloudDiag(overrides: Partial<VizCloudDiag> = {}): VizCloudDiag {
  return {
    low: { coverPct: null, baseFt: null, topFt: null },
    mid: { coverPct: null, baseFt: null, topFt: null },
    high: { coverPct: null, baseFt: null, topFt: null },
    ceilingFt: null,
    ...overrides,
  };
}
