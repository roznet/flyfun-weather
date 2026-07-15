/** Airport summary card — the default view of the airport panel on the
 *  forecast map (/maps.html).
 *
 *  Pure client-side render from the `ForecastAirport` that the map already
 *  holds (`forecastData.airports`), so it appears instantly on click with
 *  no network round-trip. It surfaces every metric the color dropdown can
 *  show, for every model at once, plus the consensus — the one view the
 *  map itself can't give you (the map colors a single metric at a time).
 *
 *  Structure, top-to-bottom by GA go/no-go priority:
 *    1. Verdict: consensus flight-category badge + model-agreement chip
 *    2. Alternate required (FAA/EASA)
 *    3. Metric × model matrix (category, ceiling, vis, wind, xwind,
 *       headwind, convective, CAPE, cloud, temp), cells colored via the
 *       shared scales so they read identically to the markers. Rows where
 *       the models diverge are flagged.
 *
 *  Colors/formatting come from `weather-map-format.ts` (shared with the
 *  marker layer) — no duplicated thresholds.
 */

import type { ForecastAirport, ModelForecast } from '../adapters/maps-adapter';
import { buildWindyUrl } from '../utils';
import { type ConsensusMode } from './weather-map-consensus';
import {
  type ForecastMetric, METRIC_LABEL, CAT_COLORS, AGREEMENT_COLORS,
  getForecastColor, getConsensus, getAgreementForMetric, formatMetricValue,
  altLabel, aggAltRequired,
} from './weather-map-format';

/** Model columns, fixed order. Only models present on the airport render. */
const MODELS = ['gfs', 'icon', 'ecmwf'] as const;

/** Matrix rows, top-to-bottom by decision priority. Flight category leads
 *  (per-model category split is the single most useful comparison); the
 *  rest follow what actually drives the category, then wind, then hazards.
 *  `unit` is shown once in the row label so the cells stay narrow enough
 *  for the side panel (units repeated per cell overflowed a 4-model row). */
interface MatrixRow { metric: ForecastMetric; unit?: string; }
const MATRIX_ROWS: MatrixRow[] = [
  { metric: 'flight_category' },
  { metric: 'ceiling_ft', unit: 'ft' },
  { metric: 'visibility_m' },
  { metric: 'wind_speed_kt', unit: 'kt' },
  { metric: 'crosswind_kt', unit: 'kt' },
  { metric: 'headwind_kt', unit: 'kt' },
  { metric: 'convective_risk' },
  { metric: 'cape_jkg', unit: 'J/kg' },
  { metric: 'cloud_cover_pct' },
];

/** Compact per-cell value for the narrow matrix: units live in the row
 *  label (see MATRIX_ROWS) and the best-runway id in the row label too, so
 *  cells carry only the number (+ gust in parens). Falls back to the shared
 *  formatter for region-aware fields (visibility). */
function compactCell(data: { [k: string]: any }, metric: ForecastMetric): string {
  switch (metric) {
    case 'flight_category':
      return data.flight_category ?? '—';
    case 'ceiling_ft': {
      const v = data.ceiling_ft;
      if (v == null) return '—';
      return v >= 10000 ? 'CAVOK' : String(Math.round(v));
    }
    case 'visibility_m':
      return formatMetricValue(data, 'visibility_m');
    case 'wind_speed_kt': {
      const s = data.wind_speed_kt;
      if (s == null) return '—';
      const dir = data.wind_dir_deg != null ? `${Math.round(data.wind_dir_deg)}@` : '';
      const g = data.wind_gust_kt ? `G${Math.round(data.wind_gust_kt)}` : '';
      return `${dir}${Math.round(s)}${g}`;
    }
    case 'crosswind_kt':
    case 'headwind_kt': {
      const v = data[metric];
      if (v == null) return '—';
      const gust = metric === 'crosswind_kt' ? data.gust_crosswind_kt : data.gust_headwind_kt;
      const g = gust != null ? ` (${Math.round(gust)})` : '';
      return `${Math.round(v)}${g}`;
    }
    case 'cape_jkg':
      return data.cape_jkg != null ? String(Math.round(data.cape_jkg)) : '—';
    case 'convective_risk':
      return data.convective_risk || 'none';
    case 'cloud_cover_pct':
      return data.cloud_cover_pct != null ? `${Math.round(data.cloud_cover_pct)}%` : '—';
    default:
      return formatMetricValue(data, metric);
  }
}

export interface SummaryCardOptions {
  airport: ForecastAirport;
  /** Which consensus column to show. Mirrors the map's mode when it's a
   *  consensus mode; falls back to 'worst' when an individual model is
   *  selected on the map. */
  consensusMode: ConsensusMode;
  /** Valid time of the forecast sample (from the map's day/hour). */
  validTime: Date;
  /** Per-model init times, if known, for the freshness footnote. */
  modelInitTimes?: Record<string, string>;
  /** The panel's selected model — used only for the Windy link (Windy has a
   *  dedicated view for GFS/ICON; anything else lands on its ECMWF default). */
  model?: string;
  /** Fired when the user clicks a metric row — the host switches the map's
   *  color dropdown to that metric so the card and map reinforce each
   *  other. */
  onMetricSelect?: (metric: ForecastMetric) => void;
}

function esc(s: string): string {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

/** Pick black or white text for readability over an arbitrary cell color.
 *  Accepts #rgb / #rrggbb / rgb(r,g,b). Falls back to dark text. */
function textOn(bg: string): string {
  let r = 0, g = 0, b = 0;
  const hex = bg.trim();
  if (hex.startsWith('#')) {
    const h = hex.slice(1);
    if (h.length === 3) {
      r = parseInt(h[0] + h[0], 16); g = parseInt(h[1] + h[1], 16); b = parseInt(h[2] + h[2], 16);
    } else if (h.length === 6) {
      r = parseInt(h.slice(0, 2), 16); g = parseInt(h.slice(2, 4), 16); b = parseInt(h.slice(4, 6), 16);
    }
  } else {
    const m = hex.match(/rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
    if (m) { r = +m[1]; g = +m[2]; b = +m[3]; }
  }
  // Perceived luminance (sRGB weights). Bright cells → dark text.
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.6 ? '#111' : '#fff';
}

/** "Mon 14:00Z" from a UTC instant. */
function formatValidTime(d: Date): string {
  const day = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getUTCDay()];
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${day} ${hh}:${mm}Z`;
}

/** "GFS 06Z · ICON 03Z · ECMWF 00Z" from init-time ISO strings. */
function formatInitTimes(init: Record<string, string> | undefined, present: string[]): string {
  if (!init) return '';
  const parts: string[] = [];
  for (const m of present) {
    const iso = init[m];
    if (!iso) continue;
    const hh = String(new Date(iso).getUTCHours()).padStart(2, '0');
    parts.push(`${m.toUpperCase()} ${hh}Z`);
  }
  return parts.length ? `Init: ${parts.join(' · ')}` : '';
}

/** Map a per-metric agreement bucket to a short label + dot color. */
function agreementChip(bucket: string | null): { label: string; color: string } {
  switch (bucket) {
    case 'divergent': return { label: 'Models split', color: AGREEMENT_COLORS.divergent };
    case 'mixed': return { label: 'Some disagreement', color: AGREEMENT_COLORS.mixed };
    case 'consistent': return { label: 'Models agree', color: AGREEMENT_COLORS.consistent };
    default: return { label: '', color: '#888' };
  }
}

export function renderAirportSummaryCard(host: HTMLElement, opts: SummaryCardOptions): void {
  const { airport, consensusMode, validTime, modelInitTimes, model, onMetricSelect } = opts;
  const present = MODELS.filter((m) => airport.models[m]);
  const consensus = getConsensus(airport, consensusMode);
  const modeLabel = consensusMode === 'worst' ? 'Worst' : 'Majority';

  // --- Verdict header ---
  const catColor = CAT_COLORS[consensus.flight_category] || '#888';
  const overallAgr = agreementChip(getAgreementForMetric(consensus, 'flight_category'));
  const alt = aggAltRequired(airport, consensusMode);

  let html = '<div class="ap-card">';

  html += '<div class="ap-card-verdict">';
  html += `<span class="ap-card-cat" style="background:${catColor};color:${textOn(catColor)}">`
    + `${esc(consensus.flight_category)}</span>`;
  html += '<div class="ap-card-verdict-meta">';
  html += `<div class="ap-card-icao">${esc(airport.icao)}</div>`;
  html += `<div class="ap-card-validtime">${esc(formatValidTime(validTime))}</div>`;
  html += '</div>';
  if (overallAgr.label) {
    html += `<span class="ap-card-agr" title="Model agreement on flight category">`
      + `<span class="ap-card-agr-dot" style="background:${overallAgr.color}"></span>`
      + `${esc(overallAgr.label)}</span>`;
  }
  html += '</div>';

  // --- Alternate required ---
  html += '<div class="ap-card-alt">';
  html += '<span class="ap-card-alt-label">Alternate required</span>';
  const altText = alt ? altLabel(alt) : '—';
  const altClass = alt && (alt.faa || alt.easa) ? 'is-required' : 'is-none';
  html += `<span class="ap-card-alt-val ${altClass}">${esc(altText)}</span>`;
  html += `<span class="ap-card-alt-mode">${esc(modeLabel)}</span>`;
  html += '</div>';

  // --- Metric × model matrix ---
  // Best runway is a per-model field but is near-always the same across
  // models; show it once on the crosswind/headwind row labels.
  const bestRwy = present.map((m) => airport.models[m]?.best_runway_id).find((r) => r) ?? null;

  html += '<div class="ap-card-matrix-wrap"><table class="ap-card-matrix"><thead><tr>';
  html += '<th class="ap-card-metric-col"></th>';
  for (const m of present) html += `<th>${esc(m.toUpperCase())}</th>`;
  html += `<th class="ap-card-consensus-col" title="${esc(modeLabel)} consensus">${esc(modeLabel)}</th>`;
  html += '</tr></thead><tbody>';

  for (const row of MATRIX_ROWS) {
    const metric = row.metric;
    const diverges = getAgreementForMetric(consensus, metric) === 'divergent';
    const rowCls = `ap-card-row${diverges ? ' is-diverging' : ''}`;
    const unitBits: string[] = [];
    if (row.unit) unitBits.push(esc(row.unit));
    if ((metric === 'crosswind_kt' || metric === 'headwind_kt') && bestRwy) unitBits.push(`RWY ${esc(bestRwy)}`);
    const unitLabel = unitBits.length ? ` <span class="ap-card-unit">${unitBits.join(' · ')}</span>` : '';
    html += `<tr class="${rowCls}" data-metric="${esc(metric)}" tabindex="0" role="button" `
      + `title="Color the map by ${esc(METRIC_LABEL[metric].toLowerCase())}">`;
    html += `<td class="ap-card-metric-col">${esc(METRIC_LABEL[metric])}${unitLabel}`
      + `${diverges ? '<span class="ap-card-warn" title="Models disagree">⚠</span>' : ''}</td>`;
    for (const m of present) {
      const bg = getForecastColor(airport, metric, m);
      const val = compactCell(airport.models[m] as ModelForecast, metric);
      html += `<td style="background:${bg};color:${textOn(bg)}">${esc(val)}</td>`;
    }
    // Consensus cell
    const cbg = getForecastColor(airport, metric, consensusMode);
    const cval = compactCell(consensus as unknown as Record<string, any>, metric);
    html += `<td class="ap-card-consensus-col" style="background:${cbg};color:${textOn(cbg)}">${esc(cval)}</td>`;
    html += '</tr>';
  }

  // Temperature row (not a colored map metric — plain per-model values).
  const temps = present.map((m) => airport.models[m]?.temperature_c);
  if (temps.some((t) => t != null)) {
    html += '<tr class="ap-card-row ap-card-row-plain"><td class="ap-card-metric-col">Temp</td>';
    for (let i = 0; i < present.length; i++) {
      const t = temps[i];
      html += `<td>${t != null ? esc(`${Math.round(t)}°C`) : '—'}</td>`;
    }
    html += '<td class="ap-card-consensus-col">—</td></tr>';
  }

  html += '</tbody></table></div>';

  // --- Footer: init times on the left, the external link on the right. One
  // row rather than two — the panel's vertical space belongs to the matrix.
  // The link points at the same airport, valid time and model on Windy
  // (mirrors the cross-section's link — see briefing-ui.updateWindyLink).
  const initLine = formatInitTimes(modelInitTimes, present);
  const windyUrl = buildWindyUrl(airport.lat, airport.lon, validTime, model);
  html += '<div class="ap-card-footer">';
  html += `<span class="ap-card-footnote">${esc(initLine)}</span>`;
  html += `<a class="ap-card-link" href="${esc(windyUrl)}" target="_blank" rel="noopener">Open in Windy ↗</a>`;
  html += '</div>';

  html += '</div>';
  host.innerHTML = html;

  // Row click / keyboard → switch the map's colored metric.
  if (onMetricSelect) {
    host.querySelectorAll<HTMLElement>('.ap-card-row[data-metric]').forEach((row) => {
      const metric = row.getAttribute('data-metric') as ForecastMetric | null;
      if (!metric) return;
      row.addEventListener('click', () => onMetricSelect(metric));
      row.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onMetricSelect(metric); }
      });
    });
  }
}
