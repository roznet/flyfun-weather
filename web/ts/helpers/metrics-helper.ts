/** Metric lookup, threshold matching, and HTML rendering helpers. */

import catalog from '../data/metrics-catalog.json';
import displayConfig from '../data/metrics-display.json';
import type {
  DisplayMode,
  MetricCatalog,
  MetricCatalogEntry,
  MetricsDisplayConfig,
  ThresholdMatch,
  Tier,
} from '../types/metrics';

const CATALOG = catalog as MetricCatalog;
const DISPLAY = displayConfig as MetricsDisplayConfig;

// --- Variable name → metric ID mapping ---

/** Maps backend variable names to metric catalog IDs. */
const VARIABLE_TO_METRIC: Record<string, string> = {
  'temperature_c': 'temperature_c',
  'wind_speed_kt': 'wind_speed_kt',
  'wind_direction_deg': 'wind_direction_deg',
  'cloud_cover_pct': 'cloud_cover_pct',
  'precipitation_mm': 'precipitation_mm',
  'freezing_level_m': 'freezing_level_m',
  'freezing_level_ft': 'freezing_level_ft',
  'cape_surface_jkg': 'cape_surface_jkg',
  'lcl_altitude_ft': 'lcl_altitude_ft',
  'k_index': 'k_index',
  'total_totals': 'total_totals',
  'precipitable_water_mm': 'precipitable_water_mm',
  'lifted_index': 'lifted_index',
  'bulk_shear_0_6km_kt': 'bulk_shear_0_6km_kt',
  'max_omega_pa_s': 'max_omega_pa_s',
};

// --- Core lookup functions ---

export function getMetric(id: string): MetricCatalogEntry | undefined {
  return CATALOG[id];
}

export function getDisplayConfig(): MetricsDisplayConfig {
  return DISPLAY;
}

export function variableToMetricId(variable: string): string | null {
  return VARIABLE_TO_METRIC[variable] ?? null;
}

export function matchThreshold(id: string, value: number): ThresholdMatch | null {
  const entry = CATALOG[id];
  if (!entry || entry.thresholds.length === 0) return null;

  for (const t of entry.thresholds) {
    const aboveMin = t.min === null || value >= t.min;
    const belowMax = t.max === null || value < t.max;
    if (aboveMin && belowMax) {
      return { label: t.label, risk: t.risk, meaning: t.meaning };
    }
  }
  return null;
}

// --- Tier helpers ---

export function getMetricTier(sectionId: string, metricId: string): Tier | null {
  const section = DISPLAY.sections[sectionId];
  if (!section) return null;
  const metric = section.metrics.find((m) => m.id === metricId);
  return metric?.tier ?? null;
}

export function isMetricVisible(
  sectionId: string,
  metricId: string,
  tierVisibility: Record<Tier, boolean>,
): boolean {
  const tier = getMetricTier(sectionId, metricId);
  if (!tier) return true; // unknown metrics always visible
  return tierVisibility[tier];
}

export function getTierDefaults(): Record<Tier, boolean> {
  return { ...DISPLAY.tierDefaults } as Record<Tier, boolean>;
}

// --- Risk CSS class ---

const RISK_CSS: Record<string, string> = {
  none: '',
  low: 'risk-light',
  moderate: 'risk-moderate',
  high: 'risk-high',
  severe: 'risk-severe',
};

export function riskCssClass(risk: string): string {
  return RISK_CSS[risk] || '';
}

// --- HTML rendering helpers ---

export function renderInfoButton(metricId: string, value?: number | string): string {
  const dataVal = value != null ? ` data-value="${value}"` : '';
  return `<button class="metric-info-btn" data-metric="${metricId}"${dataVal} title="More info" aria-label="More info">\u24d8</button>`;
}

export function renderAnnotationRow(
  metricId: string,
  value: number | null,
  mode: DisplayMode,
  colSpan: number,
): string {
  if (mode === 'compact') return '';

  const entry = CATALOG[metricId];
  if (!entry) return '';

  let annotationText = entry.primary_goal;
  let thresholdLabel = '';
  let riskClass = '';

  if (value != null) {
    const match = matchThreshold(metricId, value);
    if (match) {
      thresholdLabel = match.label;
      riskClass = riskCssClass(match.risk);
    }
  }

  const infoBtn = renderInfoButton(metricId, value ?? undefined);
  const thresholdHtml = thresholdLabel
    ? `<span class="metric-threshold ${riskClass}">${thresholdLabel}</span>`
    : '';

  return `<tr class="metric-annotation-row">
    <td class="metric-annotation" colspan="${colSpan}">
      <span class="metric-vibe">${annotationText}</span>
      ${thresholdHtml}
      ${infoBtn}
    </td>
  </tr>`;
}

/** Render the threshold scale bar for the info popup. */
export function renderThresholdScale(metricId: string, value?: number): string {
  const entry = CATALOG[metricId];
  if (!entry || entry.thresholds.length === 0) return '';

  // Only render for numeric thresholds (those with min/max)
  const numericThresholds = entry.thresholds.filter(
    (t) => t.min !== null || t.max !== null,
  );
  if (numericThresholds.length === 0) return '';

  const segments = numericThresholds.map((t) => {
    const riskClass = riskCssClass(t.risk);
    const isActive = value != null && matchThreshold(metricId, value)?.label === t.label;
    const activeClass = isActive ? ' threshold-active' : '';
    return `<div class="threshold-segment ${riskClass}${activeClass}">
      <span class="threshold-label">${t.label}</span>
    </div>`;
  });

  const marker = value != null
    ? `<div class="threshold-current-value">Current: ${value}${entry.unit ? ' ' + entry.unit : ''}</div>`
    : '';

  return `<div class="threshold-bar">${segments.join('')}</div>${marker}`;
}

/** Render the full info popup content for a metric. */
export function renderInfoPopupContent(metricId: string, value?: number): string {
  const entry = CATALOG[metricId];
  if (!entry) return `<p>No information available for this metric.</p>`;

  const thresholdScale = renderThresholdScale(metricId, value);

  const thresholdList = entry.thresholds.length > 0
    ? `<div class="popup-thresholds">
        <h4>Thresholds</h4>
        ${thresholdScale}
        <table class="popup-threshold-table">
          <tbody>
            ${entry.thresholds.map((t) => {
              const riskClass = riskCssClass(t.risk);
              const isActive = value != null && matchThreshold(metricId, value)?.label === t.label;
              const activeClass = isActive ? ' threshold-row-active' : '';
              const rangeStr = formatThresholdRange(t.min, t.max, entry.unit);
              return `<tr class="${riskClass}${activeClass}">
                <td class="popup-thr-range">${rangeStr}</td>
                <td class="popup-thr-label">${t.label}</td>
                <td>${t.meaning}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>`
    : '';

  return `
    <div class="popup-header">
      <h3>${entry.name}${entry.unit ? ` <span class="popup-unit">(${entry.unit})</span>` : ''}</h3>
      <p class="popup-vibe">${entry.vibe}</p>
    </div>
    <div class="popup-body">
      <div class="popup-section">
        <h4>Goal</h4>
        <p>${entry.primary_goal}</p>
      </div>
      <div class="popup-section">
        <h4>Best used for</h4>
        <p>${entry.best_used_for}</p>
      </div>
      ${thresholdList}
      <div class="popup-section popup-limitations">
        <h4>Limitations</h4>
        <p>${entry.limitations}</p>
      </div>
    </div>
  `;
}

function formatThresholdRange(min: number | null, max: number | null, unit: string): string {
  if (min === null && max === null) return '';
  if (min === null) return `< ${max}${unit ? ' ' + unit : ''}`;
  if (max === null) return `\u2265 ${min}${unit ? ' ' + unit : ''}`;
  return `${min}\u2013${max}`;
}
