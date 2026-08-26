/** Per-layer tooltip formatters for cross-section hover.
 *
 * Each layer is described as a small declarative entry: the layer id,
 * any aliases that should also enable it (e.g. soft-* variants), an
 * optional section header, a function to pull relevant zones from a
 * point, and a per-zone line formatter. `interaction.ts` iterates this
 * registry instead of carrying one if-block per layer.
 */

import type { VizPoint, VizObservedPoint, VizCloudLayer, VizIcingZone, VizSfipZone, VizSldZone, VizCATLayer, VizInversionLayer, VizSurfaceObscuration } from '../types';
import { fmtFL } from '../interaction-utils';
import { formatVisibility, formatHeading } from '../../units';
import { getActiveTheme } from './theme';
import { icingRiskColor, catRiskColor, cloudFillFromDD, nwpCloudFill, inversionOpacity } from '../scales';
import { coverageToPct } from './layers/cloud-bands-factory';
import { sldRiskColor } from './layers/sld-bands';

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
  /** Optional fill colour for a small square swatch keyed to the band's
   *  on-chart colour, so the row can be matched to the band on the canvas.
   *  Return null/undefined to omit the swatch. */
  swatch?: (zone: any) => string | null;
}

/** Inversion fill colour: theme base RGB at strength-scaled opacity. */
function inversionSwatchColor(strengthC: number): string {
  const [r, g, b] = getActiveTheme().inversion.baseRgb;
  return `rgba(${r}, ${g}, ${b}, ${inversionOpacity(strengthC).toFixed(2)})`;
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
  enabledBy: ['soft-cloud-bands', 'square-cloud-bands'],
  getZones: (p) => p.cloudLayers,
  formatLine: (cl: VizCloudLayer) => {
    return `${fmtFL(cl.baseFt)}–${fmtFL(cl.topFt)} ${cl.coverage}`
      + extras([fmtDD(cl.meanDewpointDepressionC), fmtT(cl.meanTemperatureC)]);
  },
  swatch: (cl: VizCloudLayer) => cloudFillFromDD(cl.meanDewpointDepressionC ?? undefined, cl.coverage),
};

const cloudNWP: LayerTooltipDef = {
  id: 'nwp-cloud-bands',
  enabledBy: ['soft-nwp-cloud-bands', 'square-nwp-cloud-bands'],
  getZones: (p) => p.nwpCloudLayers ?? [],
  formatLine: (cl: VizCloudLayer) => {
    const tag = cl.source === 'grib' ? ' [band]' : '';
    return `NWP: ${cl.coverage} ${fmtFL(cl.baseFt)}–${fmtFL(cl.topFt)}`
      + extras([fmtCC(cl.meanCloudCoverPct), fmtT(cl.meanTemperatureC)])
      + tag;
  },
  swatch: (cl: VizCloudLayer) => nwpCloudFill(cl.meanCloudCoverPct ?? coverageToPct(cl.coverage)),
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
  swatch: (z: VizIcingZone) => icingRiskColor(z.risk),
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
  swatch: (z: VizIcingZone) => icingRiskColor(z.risk),
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
  swatch: (z: VizSfipZone) => getActiveTheme().sfipIcing[z.risk] ?? 'transparent',
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
  swatch: (z: VizIcingZone) => icingRiskColor(z.risk),
};

const sld: LayerTooltipDef = {
  id: 'sld-bands',
  header: 'SLD',
  getZones: (p) => p.sldZones.filter(z => z.risk !== 'none'),
  formatLine: (z: VizSldZone) => {
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} ${z.risk} ${z.mechanism}`;
  },
  swatch: (z: VizSldZone) => sldRiskColor(z.risk),
};

const cat: LayerTooltipDef = {
  id: 'cat-bands',
  getZones: (p) => p.catLayers.filter(z => z.risk !== 'none'),
  formatLine: (z: VizCATLayer) => {
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} CAT ${z.risk}`
      + extras([fmtRi(z.richardsonNumber)]);
  },
  swatch: (z: VizCATLayer) => catRiskColor(z.risk),
};

const eShear: LayerTooltipDef = {
  id: 'e-shear-bands',
  getZones: (p) => p.eShearLayers.filter(z => z.risk !== 'none'),
  formatLine: (z: VizCATLayer) => {
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} E-Shear ${z.risk}`
      + extras([fmtRi(z.richardsonNumber)]);
  },
  swatch: (z: VizCATLayer) => catRiskColor(z.risk),
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
  swatch: (z: ThermoConvZone) => getActiveTheme().convective.towerFill[z.risk] ?? null,
};

interface NwpConvZone {
  baseFt: number;
  topFt: number;
  risk: string;
  coverPct: number | null;
  precipMmH: number | null;
  method: string | null;
  unresolved: boolean;
}

const nwpConv: LayerTooltipDef = {
  id: 'nwp-convective-bg',
  getZones: (p): NwpConvZone[] => {
    if (p.nwpConvectiveRisk === 'none' || p.nwpConvectiveRisk === 'marginal') return [];
    const baseFt = p.nwpConvectiveBaseFt;
    const topFt = p.nwpConvectiveTopFt;
    const common = {
      risk: p.nwpConvectiveRisk,
      coverPct: p.nwpConvectiveCoverPct,
      precipMmH: p.nwpConvectivePrecipMmH,
      method: p.nwpConvectiveMethod,
    };
    // Depth unresolved (no model base/top): match the full column so the row
    // shows wherever the cursor is in the ghost column.
    if (baseFt === null || topFt === null) {
      return [{ ...common, baseFt: 0, topFt: Number.MAX_SAFE_INTEGER, unresolved: true }];
    }
    return [{ ...common, baseFt, topFt, unresolved: false }];
  },
  formatLine: (z: NwpConvZone) => {
    const tag = z.method ? ` [${z.method}]` : '';
    if (z.unresolved) {
      // Evidence the scheme fired even though it gave no tower geometry.
      const ev = z.precipMmH !== null
        ? `cp ${z.precipMmH.toFixed(1)} mm/h`
        : (z.coverPct !== null ? `${Math.round(z.coverPct)}% cover` : '');
      let line = `NWP Conv: ${z.risk}`;
      line += `<br>Tower: depth unresolved${ev ? ` (${ev})` : ''}${tag}`;
      return line;
    }
    const cover = z.coverPct !== null ? `${Math.round(z.coverPct)}% cover` : '';
    let line = `NWP Conv: ${z.risk}` + extras([cover]);
    line += `<br>Tower: ${fmtFL(z.baseFt)}–${fmtFL(z.topFt)}${tag}`;
    return line;
  },
  swatch: (z: NwpConvZone) => {
    // Unresolved ghost columns paint only bgWash on canvas (no solid body), so
    // match that here rather than the saturated towerFill a resolved tower uses.
    const fills = getActiveTheme().convective;
    return (z.unresolved ? fills.bgWash[z.risk] : fills.towerFill[z.risk]) ?? null;
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
  swatch: (inv: VizInversionLayer) => inversionSwatchColor(inv.strengthC),
};

const surfaceObscuration: LayerTooltipDef = {
  id: 'surface-obscuration-bands',
  header: 'Surface obscuration',
  getZones: (p) => (p.surfaceObscuration ? [p.surfaceObscuration] : []),
  formatLine: (z: VizSurfaceObscuration) => {
    // BR (mist) for both IFR and MVFR — humidity-driven low vis is mist,
    // not haze (HZ is reserved for dry suspended particles per ICAO
    // Annex 3). MIFG (shallow fog) on the IFR row matches the typical
    // 1–3 km radiation-fog signature.
    const metar = z.severity === 'lifr' ? 'FG' : z.severity === 'ifr' ? 'BR/MIFG' : 'BR';
    const cat = z.severity.toUpperCase();
    const vis = z.visM !== null ? `vis ${formatVisibility(z.visM)}` : 'vis n/a';
    const t = z.surfaceTC !== null ? `${z.surfaceTC.toFixed(0)}°C` : '—';
    const td = z.surfaceTdC !== null ? `${z.surfaceTdC.toFixed(0)}°C` : '—';
    const rh = z.surfaceRhPct !== null ? `${Math.round(z.surfaceRhPct)}%` : '—';
    return `${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} ${cat} ${metar}`
      + extras([vis, `T/Td ${t}/${td}`, `RH ${rh}`])
      + ` [${z.reason}]`;
  },
  swatch: (z: VizSurfaceObscuration) => getActiveTheme().obscuration[z.severity],
};

/** Sun geometry at the hovered point (#227). Owned by the night-shading toggle.
 *  Not altitude-bound — synthesize a full-height pseudo-zone so the row shows at
 *  any cursor altitude. Shows azimuth + angle to track when the sun is up; depth
 *  below the horizon otherwise. */
interface SunZone {
  baseFt: number;
  topFt: number;
  elevationDeg: number;
  azimuthDeg: number;
  relativeBearingDeg: number;
}

const sunAtPoint: LayerTooltipDef = {
  id: 'night-shading',
  header: 'Sun',
  getZones: (p): SunZone[] =>
    p.sun ? [{ baseFt: -1e6, topFt: 1e6, ...p.sun }] : [],
  formatLine: (z: SunZone) => {
    const az = `az ${formatHeading(z.azimuthDeg)}°T`;
    if (z.elevationDeg <= 0) {
      return `${Math.abs(z.elevationDeg).toFixed(0)}° below horizon · ${az}`;
    }
    const mag = Math.abs(z.relativeBearingDeg);
    const side = z.relativeBearingDeg >= 0 ? 'right' : 'left';
    const aligned = mag <= 5 ? 'dead ahead'
      : mag >= 175 ? 'dead astern'
      : `${mag.toFixed(0)}° ${side} of track`;
    return `${z.elevationDeg.toFixed(0)}° up · ${az} · ${aligned}`;
  },
};

/** All band/zone-style tooltip definitions, in display order. */

/** Observed radar + lightning (#574).
 *
 *  Not altitude-bound, and that is a data property rather than a convenience:
 *  the OPERA composite is a column MAXIMUM with no height information at all,
 *  so its value applies to the whole column and the row must show wherever the
 *  cursor sits. Same synthesized full-height zone as the sun row.
 *
 *  Coverage is reported explicitly. "No echo" and "the radar does not look
 *  here" are different answers, and roughly half the OPERA grid is the latter. */
interface ObservedSurfaceZone {
  baseFt: number;
  topFt: number;
  point: VizObservedPoint;
}

const observedSurface: LayerTooltipDef = {
  id: 'observed-surface',
  header: 'Observed radar & lightning',
  getZones: (p): ObservedSurfaceZone[] => {
    const o = p.observed;
    if (!o) return [];
    const hasAnything =
      o.radarNoCoverage || o.dbz != null || o.rateMmH != null || o.flashCount > 0;
    return hasAnything ? [{ baseFt: -1e6, topFt: 1e6, point: o }] : [];
  },
  formatLine: (z: ObservedSurfaceZone) => {
    const p = z.point;
    const parts: string[] = [];
    if (p.radarNoCoverage) {
      parts.push('no radar coverage');
    } else if (p.dbz != null) {
      parts.push(`${p.dbz.toFixed(0)} dBZ`);
    } else {
      parts.push('no echo');
    }
    if (!p.rateNoCoverage && p.rateMmH != null) parts.push(`${p.rateMmH.toFixed(1)} mm/h`);
    if (p.flashCount > 0) {
      const rate = p.flashRate != null ? ` (${p.flashRate.toFixed(1)}/1000km²/min)` : '';
      parts.push(`${p.flashCount} flash${p.flashCount === 1 ? '' : 'es'}${rate}`);
    } else {
      parts.push('no flashes');
    }
    return parts.join(' · ');
  },
};

/** Observed cloud tops (#574).
 *
 *  Zones are the populated FL bands plus, when the highest top is off the top
 *  of the chart, a zone reaching upward so hovering the off-scale box answers
 *  with the number the box cannot draw. */
interface ObservedTopsZone {
  baseFt: number;
  topFt: number;
  point: VizObservedPoint;
  label: string;
}

const observedTops: LayerTooltipDef = {
  id: 'observed-tops',
  header: 'Observed cloud tops',
  getZones: (p): ObservedTopsZone[] => {
    const o = p.observed;
    if (!o) return [];
    if (o.topsNoCoverage) {
      return [{ baseFt: -1e6, topFt: 1e6, point: o, label: 'no retrieval here' }];
    }
    const zones: ObservedTopsZone[] = [];
    for (const bin of o.topsBins) {
      if (bin.count <= 0) continue;
      // Share AND count: 4% of 201 pixels and 4% of 3 are very different
      // evidence, and only one of them is worth acting on. The share is of
      // the SKY the retrieval could see, so it reads as coverage — "4% of the
      // area around here had its cloud top in FL180-190" — and the bands at
      // a point sum to how cloudy the disc was, not to 100%.
      const pct = bin.fraction * 100;
      const share = pct >= 1 ? `${Math.round(pct)}%` : '<1%';
      zones.push({
        baseFt: bin.loFt,
        topFt: bin.hiFt,
        point: o,
        label: `${bin.label} · ${share} of sky (${bin.count} px)`,
      });
    }
    if (o.topsHighestFt != null) {
      // Spans the whole column, because the highest top is a property of the
      // POINT, not of an altitude — the same reasoning that makes the radar
      // row altitude-independent.
      //
      // It also fixes a gap that only appeared when the top was off-scale: the
      // layer pins its box to the plot ceiling, but this zone used to start at
      // the true altitude, so a cursor over that box (at FL226 on a chart
      // topping out there, against a FL361 top) fell between the last drawn
      // band and the top and matched nothing. The one mark a pilot could see
      // was the one mark they could not query.
      zones.push({
        baseFt: -1e6,
        topFt: 1e6,
        point: o,
        label: `highest top ${formatFl(o.topsHighestFt)}`,
      });
    }
    return zones;
  },
  formatLine: (z: ObservedTopsZone) => {
    const p = z.point;
    const parts: string[] = [z.label];
    if (p.topsColdestC != null) parts.push(`coldest ${p.topsColdestC.toFixed(0)}°C`);
    if (p.topsHighestCloudiness != null) {
      // The number that separates "cannot get on top" from "wispy".
      const pct = Math.round(p.topsHighestCloudiness * 100);
      const how = pct >= 90 ? 'solid' : pct >= 50 ? 'broken' : 'thin';
      parts.push(`${pct}% opaque (${how})`);
    }
    if (p.topsHighestAviationFl != null) parts.push(`≈FL${Math.round(p.topsHighestAviationFl)} pressure`);
    if (p.topsMultiLayerFraction > 0.1) {
      parts.push(`${Math.round(p.topsMultiLayerFraction * 100)}% multi-layer suspect`);
    }
    return parts.join(' · ');
  },
};

/** "FL350" from feet. */
function formatFl(ft: number): string {
  return `FL${Math.round(ft / 100)}`;
}

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
  surfaceObscuration,
  sunAtPoint,
  observedSurface,
  observedTops,
];
