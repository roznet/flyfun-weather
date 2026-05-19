/** Per-dataset scales, popup builders, and "Color by" sub-metric lists.
 *
 * Adding a 5th dataset means: define a ``DatasetConfig`` here, fetch its
 * envelope in ``climatology-tab.ts``, and add it to the dropdown. No other
 * touch points.
 */

import { COLORS, type Scale, type PopupBuilder, type MapAirport } from './climatology-map';
import type {
  CategoryAirport,
  ClimatologyAirport,
} from '../adapters/climatology-adapter';

// --- View #1: Category scales (per-metric thresholds) ----------------------

const CATEGORY_SCALES: Record<'vfr' | 'mvfr' | 'ifr' | 'lifr', Scale> = {
  vfr: {
    title: 'VFR %', unit: '%',
    stops: [
      { bound: 95, color: COLORS.green },
      { bound: 90, color: COLORS.lime },
      { bound: 85, color: COLORS.yellow },
      { bound: 80, color: COLORS.orange },
      { bound: 70, color: COLORS.red },
      { bound: 0,  color: COLORS.darkred },
    ],
  },
  mvfr: {
    title: 'MVFR %', unit: '%',
    stops: [
      { bound: 25, color: COLORS.darkred },
      { bound: 15, color: COLORS.red },
      { bound: 10, color: COLORS.orange },
      { bound: 5,  color: COLORS.yellow },
      { bound: 2,  color: COLORS.lime },
      { bound: 0,  color: COLORS.green },
    ],
  },
  ifr: {
    title: 'IFR %', unit: '%',
    stops: [
      { bound: 10,  color: COLORS.darkred },
      { bound: 5,   color: COLORS.red },
      { bound: 3,   color: COLORS.orange },
      { bound: 1.5, color: COLORS.yellow },
      { bound: 0.5, color: COLORS.lime },
      { bound: 0,   color: COLORS.green },
    ],
  },
  lifr: {
    title: 'LIFR %', unit: '%',
    // LIFR is binary-ish — the 0.1/0.3/0.7 bands all caught the same set
    // of airports in April. Widened to give meaningful separation.
    stops: [
      { bound: 5,   color: COLORS.darkred },
      { bound: 3,   color: COLORS.red },
      { bound: 2,   color: COLORS.orange },
      { bound: 1,   color: COLORS.yellow },
      { bound: 0.3, color: COLORS.lime },
      { bound: 0,   color: COLORS.green },
    ],
  },
};

export type CategoryKey = keyof typeof CATEGORY_SCALES;

// Map CategoryAirport → MapAirport using the selected sub-metric.
export function categoryAirportToMapAirport(
  a: CategoryAirport, sub: CategoryKey,
): MapAirport {
  const value = a[`pct_${sub}` as 'pct_vfr'] ?? null;
  return { ...a, value: value ?? null };
}

export const CategoryDataset = {
  subOptions: [
    { value: 'vfr',  label: 'VFR %' },
    { value: 'mvfr', label: 'MVFR %' },
    { value: 'ifr',  label: 'IFR %' },
    { value: 'lifr', label: 'LIFR %' },
  ] as { value: CategoryKey; label: string }[],
  scaleFor: (sub: CategoryKey): Scale => CATEGORY_SCALES[sub],
  /** Same popup regardless of which sub-metric is selected. */
  popup: (a: MapAirport): string => {
    const ca = a as unknown as CategoryAirport;
    const rows = (['vfr', 'mvfr', 'ifr', 'lifr'] as const).map((k) => {
      const pct = ca[`pct_${k}` as 'pct_vfr'];
      const cell = pct === undefined ? '—' : `${pct.toFixed(1)}%`;
      return `<tr><td>${k.toUpperCase()}</td><td style="text-align:right">${cell}</td></tr>`;
    }).join('');
    return `
      <div class="clim-popup">
        <div class="clim-popup-title"><b>${ca.icao}</b></div>
        <table class="clim-popup-table">${rows}</table>
        <div class="clim-popup-foot">${ca.n_obs.toLocaleString()} obs</div>
      </div>
    `;
  },
};

// --- View #2: Phenomena (TS / fog) -----------------------------------------

const TS_SCALE: Scale = {
  title: 'TS %', unit: '%',
  stops: [
    { bound: 5,   color: COLORS.darkred },
    { bound: 3,   color: COLORS.red },
    { bound: 2,   color: COLORS.orange },
    { bound: 1,   color: COLORS.yellow },
    { bound: 0.3, color: COLORS.lime },
    { bound: 0,   color: COLORS.green },
  ],
};

const FOG_SCALE: Scale = {
  title: 'Fog %', unit: '%',
  stops: [
    { bound: 10,  color: COLORS.darkred },
    { bound: 5,   color: COLORS.red },
    { bound: 3,   color: COLORS.orange },
    { bound: 1.5, color: COLORS.yellow },
    { bound: 0.5, color: COLORS.lime },
    { bound: 0,   color: COLORS.green },
  ],
};

export type PhenomenaKey = 'ts' | 'fog';

export const PhenomenaDataset = {
  subOptions: [
    { value: 'ts',  label: 'TS' },
    { value: 'fog', label: 'Fog' },
  ] as { value: PhenomenaKey; label: string }[],
  scaleFor: (sub: PhenomenaKey): Scale => sub === 'ts' ? TS_SCALE : FOG_SCALE,
  popup: (a: MapAirport): string => {
    const pa = a as unknown as ClimatologyAirport & {
      n_obs: number; n_ts: number; n_fg: number;
    };
    const tsPct = pa.n_obs > 0 ? (100 * pa.n_ts / pa.n_obs).toFixed(2) : '—';
    const fgPct = pa.n_obs > 0 ? (100 * pa.n_fg / pa.n_obs).toFixed(2) : '—';
    return `
      <div class="clim-popup">
        <div class="clim-popup-title"><b>${pa.icao}</b></div>
        <table class="clim-popup-table">
          <tr><td>TS</td><td style="text-align:right">${pa.n_ts} (${tsPct}%)</td></tr>
          <tr><td>Fog</td><td style="text-align:right">${pa.n_fg} (${fgPct}%)</td></tr>
        </table>
        <div class="clim-popup-foot">${pa.n_obs.toLocaleString()} obs</div>
      </div>
    `;
  },
};

// --- View #3: Wind ---------------------------------------------------------

const WIND_OVER25_SCALE: Scale = {
  title: '% hrs > 25 kt', unit: '%',
  // April max was 13% — the ≥15 darkred caught nobody. Compressed.
  stops: [
    { bound: 10, color: COLORS.darkred },
    { bound: 7,  color: COLORS.red },
    { bound: 5,  color: COLORS.orange },
    { bound: 3,  color: COLORS.yellow },
    { bound: 1,  color: COLORS.lime },
    { bound: 0,  color: COLORS.green },
  ],
};

const WIND_P95_SCALE: Scale = {
  title: 'Wind p95', unit: ' kt',
  // April max was 31 kt — the original ≥35 / ≥30 bounds left half the map
  // in the "green" bucket. Recentred so p10..p90 spreads across the palette.
  stops: [
    { bound: 25, color: COLORS.darkred },
    { bound: 20, color: COLORS.red },
    { bound: 17, color: COLORS.orange },
    { bound: 14, color: COLORS.yellow },
    { bound: 11, color: COLORS.lime },
    { bound: 0,  color: COLORS.green },
  ],
};

const WIND_GUST_SCALE: Scale = {
  title: 'Peak gust', unit: ' kt',
  // 40 kt is already a meaningful warning level for GA; 50/60 kt peaks
  // are rare enough that giving them their own buckets wastes the palette.
  stops: [
    { bound: 40, color: COLORS.darkred },
    { bound: 35, color: COLORS.red },
    { bound: 30, color: COLORS.orange },
    { bound: 25, color: COLORS.yellow },
    { bound: 20, color: COLORS.lime },
    { bound: 0,  color: COLORS.green },
  ],
};

export type WindKey = 'over25' | 'p95' | 'gust';

export const WindDataset = {
  subOptions: [
    { value: 'over25', label: 'Hrs > 25 kt', mtd: false },
    { value: 'p95',    label: 'Wind p95',    mtd: false },
    { value: 'gust',   label: 'Peak gust',   mtd: true },
  ] as { value: WindKey; label: string; mtd: boolean }[],
  scaleFor: (sub: WindKey): Scale => ({
    over25: WIND_OVER25_SCALE, p95: WIND_P95_SCALE, gust: WIND_GUST_SCALE,
  }[sub]),
  popup: (a: MapAirport): string => {
    const v = a.value;
    const sub = a._sub as WindKey | undefined;
    const unit = sub === 'over25' ? '%' : 'kt';
    const valStr = v === null ? '—' : `${v}${unit === '%' ? '%' : ' kt'}`;
    const label = sub === 'over25' ? 'Hrs > 25 kt'
                : sub === 'p95'    ? 'Wind p95'
                : 'Peak gust';
    return `
      <div class="clim-popup">
        <div class="clim-popup-title"><b>${a.icao}</b></div>
        <table class="clim-popup-table">
          <tr><td>${label}</td><td style="text-align:right">${valStr}</td></tr>
        </table>
      </div>
    `;
  },
};

// --- View #4: Volatility ---------------------------------------------------

const VOLATILITY_SCALE: Scale = {
  title: 'Changes / obs', unit: '',
  // April real range was 0..0.333 — original ≥0.5 / ≥0.8 stops never
  // activated. Recentred so the coastal/maritime cluster (EGPL, BIKF, EKVG,
  // LPLA at 0.30+) lands in darkred where they belong.
  stops: [
    { bound: 0.30, color: COLORS.darkred },
    { bound: 0.20, color: COLORS.red },
    { bound: 0.15, color: COLORS.orange },
    { bound: 0.10, color: COLORS.yellow },
    { bound: 0.05, color: COLORS.lime },
    { bound: 0,    color: COLORS.green },
  ],
};

export const VolatilityDataset = {
  // No sub-metric — single value (changes/n_obs).
  subOptions: [] as { value: string; label: string }[],
  scaleFor: (): Scale => VOLATILITY_SCALE,
  popup: (a: MapAirport): string => {
    const va = a as MapAirport & { n_obs: number; n_changes: number };
    const cell = a.value === null ? '—' : a.value.toFixed(3);
    return `
      <div class="clim-popup">
        <div class="clim-popup-title"><b>${va.icao}</b></div>
        <table class="clim-popup-table">
          <tr><td>Changes / obs</td><td style="text-align:right">${cell}</td></tr>
          <tr><td>Changes</td><td style="text-align:right">${va.n_changes}</td></tr>
        </table>
        <div class="clim-popup-foot">${va.n_obs.toLocaleString()} obs</div>
      </div>
    `;
  },
};

// --- Dataset registry ------------------------------------------------------

export type DatasetKey = 'category' | 'phenomena' | 'wind' | 'volatility';

export const DATASET_LABEL: Record<DatasetKey, string> = {
  category: 'Flight Category',
  phenomena: 'TS / Fog',
  wind: 'Wind',
  volatility: 'Volatility',
};
