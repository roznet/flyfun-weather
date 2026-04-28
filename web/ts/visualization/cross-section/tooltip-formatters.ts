/** Per-layer tooltip formatters for cross-section hover.
 *
 * Each layer is described as a small declarative entry: the layer id,
 * any aliases that should also enable it (e.g. soft-* variants), an
 * optional section header, a function to pull relevant zones from a
 * point, and a per-zone line formatter. `interaction.ts` iterates this
 * registry instead of carrying one if-block per layer.
 */

import type { VizPoint, VizCloudLayer, VizIcingZone, VizSfipZone, VizSldZone, VizCATLayer, VizInversionLayer } from '../types';
import { fmtFL } from '../interaction-utils';

export interface LayerTooltipDef {
  /** Primary layer id (the toggle that owns the data). */
  id: string;
  /** Alternate ids that should also enable this row (e.g. soft variants). */
  enabledBy?: string[];
  /** Optional section header rendered above the lines. */
  header?: string;
  /** Pull the relevant zones from a point. Empty list → no row. */
  getZones: (p: VizPoint) => Array<{ baseFt: number; topFt: number }>;
  /** Format one zone matched at hoverAltFt. Return null to skip. */
  formatLine: (zone: any, hoverAltFt: number) => string | null;
}

// ── format helpers ──────────────────────────────────────────────────

const fmtT = (c?: number | null): string =>
  c != null ? `T ${c >= 0 ? '+' : ''}${c.toFixed(0)}°C` : '';

const fmtIdx = (n?: number | null): string =>
  n != null ? `idx ${Math.round(n)}` : '';

const fmtCC = (pct?: number | null): string =>
  pct != null ? `CC ${pct.toFixed(0)}%` : '';

const fmtDD = (dd?: number | null): string =>
  dd != null ? `DD ${dd.toFixed(1)}°C` : '';

const fmtRi = (ri?: number | null): string =>
  ri != null ? `Ri ${ri.toFixed(2)}` : '';

const sldTag = (sld?: boolean): string => sld ? ' +SLD' : '';

/** Wrap a list of "key value" parts in parentheses, dropping empties. */
function extras(parts: string[]): string {
  const filtered = parts.filter(Boolean);
  return filtered.length ? ` (${filtered.join(', ')})` : '';
}

// ── layer entries ───────────────────────────────────────────────────

const cloudDD: LayerTooltipDef = {
  id: 'cloud-bands',
  enabledBy: ['soft-cloud-bands'],
  getZones: (p) => p.cloudLayers,
  formatLine: (cl: VizCloudLayer) => {
    return `${fmtFL(cl.baseFt)}–${fmtFL(cl.topFt)} ${cl.coverage}`
      + extras([fmtDD(cl.meanDewpointDepressionC), fmtT(cl.meanTemperatureC)]);
  },
};

const cloudNWP: LayerTooltipDef = {
  id: 'nwp-cloud-bands',
  enabledBy: ['soft-nwp-cloud-bands'],
  getZones: (p) => p.nwpCloudLayers ?? [],
  formatLine: (cl: VizCloudLayer) => {
    const tag = cl.source === 'grib' ? ' [band]' : '';
    return `NWP: ${cl.coverage} ${fmtFL(cl.baseFt)}–${fmtFL(cl.topFt)}`
      + extras([fmtCC(cl.meanCloudCoverPct), fmtT(cl.meanTemperatureC)])
      + tag;
  },
};

const icingOgimetDD: LayerTooltipDef = {
  id: 'icing-bands',
  header: 'Icing (Ogimet-DD)',
  getZones: (p) => p.icingZones.filter(z => z.risk !== 'none'),
  formatLine: (z: VizIcingZone) => {
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} ${z.risk} ${z.type}`
      + extras([fmtIdx(z.meanIcingIndex), fmtT(z.meanTemperatureC)])
      + sldTag(z.sldRisk);
  },
};

const icingOgimetNWP: LayerTooltipDef = {
  id: 'icing-ogimet-nwp-bands',
  header: 'Icing (Ogimet-NWP)',
  getZones: (p) => p.icingOgimetNwpZones.filter(z => z.risk !== 'none'),
  formatLine: (z: VizIcingZone) => {
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} ${z.risk} ${z.type}`
      + extras([fmtIdx(z.meanIcingIndex), fmtT(z.meanTemperatureC)])
      + sldTag(z.sldRisk);
  },
};

const sfip: LayerTooltipDef = {
  id: 'sfip-bands',
  getZones: (p) => p.sfipZones.filter(z => z.risk !== 'none'),
  formatLine: (z: VizSfipZone) => {
    const score = z.meanSfip100 !== null ? `SFIP ${Math.round(z.meanSfip100)}/100` : 'SFIP';
    const proxy = z.variant !== 'full' ? ' [proxy]' : '';
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} ${score} ${z.type}`
      + extras([fmtT(z.meanTemperatureC)])
      + proxy;
  },
};

const ieng: LayerTooltipDef = {
  id: 'ieng-icing-bands',
  header: 'IENG',
  getZones: (p) => p.iengIcingZones.filter(z => z.risk !== 'none'),
  formatLine: (z: VizIcingZone) => {
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} ${z.risk} ${z.type}`
      + extras([fmtIdx(z.meanIcingIndex), fmtT(z.meanTemperatureC)])
      + sldTag(z.sldRisk);
  },
};

const sld: LayerTooltipDef = {
  id: 'sld-bands',
  header: 'SLD',
  getZones: (p) => p.sldZones.filter(z => z.risk !== 'none'),
  formatLine: (z: VizSldZone) => {
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} ${z.risk} ${z.mechanism}`;
  },
};

const cat: LayerTooltipDef = {
  id: 'cat-bands',
  getZones: (p) => p.catLayers.filter(z => z.risk !== 'none'),
  formatLine: (z: VizCATLayer) => {
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} CAT ${z.risk}`
      + extras([fmtRi(z.richardsonNumber)]);
  },
};

const eShear: LayerTooltipDef = {
  id: 'e-shear-bands',
  getZones: (p) => p.eShearLayers.filter(z => z.risk !== 'none'),
  formatLine: (z: VizCATLayer) => {
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} E-Shear ${z.risk}`
      + extras([fmtRi(z.richardsonNumber)]);
  },
};

/** Convective layers are per-point, not per-zone; we synthesize a single
 *  pseudo-zone from the point's convective fields when risk != none. */
interface ThermoConvZone {
  baseFt: number;
  topFt: number;
  risk: string;
  capeJkg: number;
  cinJkg: number;
  lclFt: number | null;
  elFt: number | null;
}

const thermoConv: LayerTooltipDef = {
  id: 'thermo-convective-bg',
  getZones: (p): ThermoConvZone[] => {
    if (p.convectiveRisk === 'none') return [];
    const baseFt = p.convectiveBaseFt;
    const topFt = p.convectiveTopFt;
    if (baseFt === null || topFt === null) return [];
    return [{
      baseFt, topFt,
      risk: p.convectiveRisk,
      capeJkg: p.capeSurfaceJkg,
      cinJkg: p.cinSurfaceJkg,
      lclFt: p.altitudeLines.lclAltitudeFt,
      elFt: p.altitudeLines.elAltitudeFt,
    }];
  },
  formatLine: (z: ThermoConvZone) => {
    const cape = z.capeJkg > 0 ? `CAPE ${Math.round(z.capeJkg)}` : '';
    const cin = z.cinJkg < 0 ? `CIN ${Math.round(z.cinJkg)}` : '';
    let line = `Thermo Conv: ${z.risk}` + extras([cape, cin]);
    if (z.lclFt !== null && z.elFt !== null) {
      line += `<br>LCL→EL: ${fmtFL(z.lclFt)}–${fmtFL(z.elFt)}`;
    } else {
      line += `<br>Tower: ${fmtFL(z.baseFt)}–${fmtFL(z.topFt)}`;
    }
    return line;
  },
};

interface NwpConvZone {
  baseFt: number;
  topFt: number;
  risk: string;
  coverPct: number | null;
  method: string | null;
}

const nwpConv: LayerTooltipDef = {
  id: 'nwp-convective-bg',
  getZones: (p): NwpConvZone[] => {
    if (p.nwpConvectiveRisk === 'none') return [];
    const baseFt = p.nwpConvectiveBaseFt;
    const topFt = p.nwpConvectiveTopFt;
    if (baseFt === null || topFt === null) return [];
    return [{
      baseFt, topFt,
      risk: p.nwpConvectiveRisk,
      coverPct: p.nwpConvectiveCoverPct,
      method: p.nwpConvectiveMethod,
    }];
  },
  formatLine: (z: NwpConvZone) => {
    const cover = z.coverPct !== null ? `${Math.round(z.coverPct)}% cover` : '';
    const tag = z.method ? ` [${z.method}]` : '';
    let line = `NWP Conv: ${z.risk}` + extras([cover]);
    line += `<br>Tower: ${fmtFL(z.baseFt)}–${fmtFL(z.topFt)}${tag}`;
    return line;
  },
};

const inversion: LayerTooltipDef = {
  id: 'inversion-bands',
  header: 'Inversion',
  getZones: (p) => p.inversions,
  formatLine: (inv: VizInversionLayer) => {
    const sfc = inv.surfaceBased ? ' (sfc)' : '';
    return `${fmtFL(inv.baseFt)}–${fmtFL(inv.topFt)} +${inv.strengthC.toFixed(1)}°C${sfc}`;
  },
};

/** All band/zone-style tooltip definitions, in display order. */
export const LAYER_TOOLTIPS: LayerTooltipDef[] = [
  cloudDD,
  cloudNWP,
  icingOgimetDD,
  icingOgimetNWP,
  sfip,
  ieng,
  sld,
  cat,
  eShear,
  thermoConv,
  nwpConv,
  inversion,
];
