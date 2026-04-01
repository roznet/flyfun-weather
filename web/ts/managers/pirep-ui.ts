/** PIREP UI — list view, detail cards, filters, and severity styling. */

import type { PirepResponse, PirepFilters } from '../adapters/pirep-adapter';
import { SEVERITY_COLORS, maxSeverity, hazardIconsRaw } from '../utils/pirep-helpers';
import { escapeHtml } from '../utils';

const SEVERITY_LABELS: Record<string, string> = {
  none: 'None',
  trace: 'Trace',
  light: 'Light',
  moderate: 'Moderate',
  severe: 'Severe',
};

function hazardIcons(pirep: PirepResponse): string {
  const icons: string[] = [];
  if (pirep.icing_intensity && pirep.icing_intensity !== 'none') {
    icons.push('<span class="pirep-icon pirep-icon-icing" title="Icing">&#10052;</span>');
  }
  if (pirep.turbulence_intensity && pirep.turbulence_intensity !== 'none') {
    icons.push('<span class="pirep-icon pirep-icon-turbulence" title="Turbulence">&#9084;</span>');
  }
  if (pirep.in_cloud || pirep.ceiling_msl_ft != null || pirep.tops_msl_ft != null) {
    icons.push('<span class="pirep-icon pirep-icon-cloud" title="Cloud">&#9729;</span>');
  }
  if (icons.length === 0) {
    icons.push('<span class="pirep-icon pirep-icon-clear" title="Clear">&#10003;</span>');
  }
  return icons.join('');
}

function ageCategory(observedAt: string): 'fresh' | 'recent' | 'stale' {
  const ageMs = Date.now() - new Date(observedAt).getTime();
  const ageMin = ageMs / 60000;
  if (ageMin <= 30) return 'fresh';
  if (ageMin <= 90) return 'recent';
  return 'stale';
}

function ageLabel(observedAt: string): string {
  const ageMs = Date.now() - new Date(observedAt).getTime();
  const ageMin = Math.round(ageMs / 60000);
  if (ageMin < 1) return 'just now';
  if (ageMin < 60) return `${ageMin} min ago`;
  const ageHr = Math.round(ageMin / 60);
  return `${ageHr}h ago`;
}

function altitudeStr(pirep: PirepResponse): string {
  const alt = pirep.reported_altitude_ft ?? pirep.gps_altitude_ft;
  return alt != null ? `${alt.toLocaleString()} ft` : '—';
}

// ---------------------------------------------------------------------------
// Detail card
// ---------------------------------------------------------------------------

export function renderPirepDetailCard(pirep: PirepResponse): string {
  const rows: string[] = [];

  const addRow = (label: string, value: string | null | undefined) => {
    if (value != null && value !== '') {
      rows.push(`<div class="pirep-detail-row"><span class="pirep-label">${label}</span><span class="pirep-value">${escapeHtml(value)}</span></div>`);
    }
  };

  addRow('Altitude', altitudeStr(pirep));
  if (pirep.icing_intensity) {
    let icing = SEVERITY_LABELS[pirep.icing_intensity] || pirep.icing_intensity;
    if (pirep.icing_type) icing += ` (${pirep.icing_type})`;
    addRow('Icing', icing);
  }
  if (pirep.turbulence_intensity) {
    addRow('Turbulence', SEVERITY_LABELS[pirep.turbulence_intensity] || pirep.turbulence_intensity);
  }
  if (pirep.in_cloud != null) {
    addRow('In cloud', pirep.in_cloud ? 'Yes' : 'No');
  }
  if (pirep.ceiling_msl_ft != null) {
    addRow('Ceiling', `${pirep.ceiling_msl_ft.toLocaleString()} ft MSL`);
  }
  if (pirep.tops_msl_ft != null) {
    let tops = `${pirep.tops_msl_ft.toLocaleString()} ft MSL`;
    if (pirep.tops_basis) tops += ` (${pirep.tops_basis.replace(/_/g, ' ')})`;
    addRow('Tops', tops);
  }
  if (pirep.temp_c != null) addRow('Temp', `${pirep.temp_c}°C`);
  if (pirep.wind_dir != null && pirep.wind_speed_kt != null) {
    addRow('Wind', `${String(pirep.wind_dir).padStart(3, '0')}° / ${pirep.wind_speed_kt} kt`);
  }
  if (pirep.aircraft_type) addRow('Aircraft', pirep.aircraft_type);
  addRow('Source', pirep.source);
  if (pirep.remarks) addRow('Remarks', pirep.remarks);

  return `<div class="pirep-detail-card">${rows.join('')}</div>`;
}

// ---------------------------------------------------------------------------
// List view
// ---------------------------------------------------------------------------

export function renderPirepList(
  container: HTMLElement,
  pireps: PirepResponse[],
  options: { showAge?: boolean } = {},
): void {
  const showAge = options.showAge !== false;

  if (pireps.length === 0) {
    container.innerHTML = '<p class="muted">No PIREPs found for this time window.</p>';
    return;
  }

  const html = pireps.map((p) => {
    const severity = maxSeverity(p);
    const color = SEVERITY_COLORS[severity] || '#999';
    const age = ageCategory(p.observed_at);
    const opacity = showAge ? (age === 'fresh' ? 1 : age === 'recent' ? 0.7 : 0.4) : 1;
    const staleTag = showAge && age === 'stale' ? '<span class="pirep-stale-tag">STALE</span>' : '';

    return `
      <div class="pirep-row" style="border-left: 4px solid ${color}; opacity: ${opacity};" data-pirep-id="${p.id}">
        <div class="pirep-row-summary">
          <span class="pirep-time">${ageLabel(p.observed_at)} ${staleTag}</span>
          <span class="pirep-icons">${hazardIcons(p)}</span>
          <span class="pirep-alt">${altitudeStr(p)}</span>
          ${p.is_own ? '<span class="pirep-own-badge">own</span>' : ''}
        </div>
        <div class="pirep-row-detail" style="display: none;">
          ${renderPirepDetailCard(p)}
        </div>
      </div>
    `;
  }).join('');

  container.innerHTML = html;

  // Click to expand/collapse
  container.querySelectorAll('.pirep-row').forEach((el) => {
    el.addEventListener('click', () => {
      const detail = el.querySelector('.pirep-row-detail') as HTMLElement;
      if (detail) detail.style.display = detail.style.display === 'none' ? 'block' : 'none';
    });
  });
}

// ---------------------------------------------------------------------------
// Filter controls
// ---------------------------------------------------------------------------

export interface PirepFilterState {
  hours: number;
  hazard: string;
  min_severity: string;
  altitude_min: string;
  altitude_max: string;
}

export function renderPirepFilters(
  container: HTMLElement,
  state: PirepFilterState,
  onChange: (state: PirepFilterState) => void,
): void {
  container.innerHTML = `
    <div class="pirep-filters">
      <label>Hours: <select id="pirep-filter-hours">
        <option value="3"${state.hours === 3 ? ' selected' : ''}>3h</option>
        <option value="6"${state.hours === 6 ? ' selected' : ''}>6h</option>
        <option value="12"${state.hours === 12 ? ' selected' : ''}>12h</option>
        <option value="24"${state.hours === 24 ? ' selected' : ''}>24h</option>
        <option value="48"${state.hours === 48 ? ' selected' : ''}>48h</option>
      </select></label>
      <label>Hazard: <select id="pirep-filter-hazard">
        <option value="">All</option>
        <option value="icing"${state.hazard === 'icing' ? ' selected' : ''}>Icing</option>
        <option value="turbulence"${state.hazard === 'turbulence' ? ' selected' : ''}>Turbulence</option>
        <option value="cloud"${state.hazard === 'cloud' ? ' selected' : ''}>Cloud</option>
      </select></label>
      <label>Min Severity: <select id="pirep-filter-severity">
        <option value="">Any</option>
        <option value="light"${state.min_severity === 'light' ? ' selected' : ''}>Light+</option>
        <option value="moderate"${state.min_severity === 'moderate' ? ' selected' : ''}>Moderate+</option>
        <option value="severe"${state.min_severity === 'severe' ? ' selected' : ''}>Severe</option>
      </select></label>
    </div>
  `;

  const read = (): PirepFilterState => ({
    hours: parseInt((document.getElementById('pirep-filter-hours') as HTMLSelectElement)?.value || '6'),
    hazard: (document.getElementById('pirep-filter-hazard') as HTMLSelectElement)?.value || '',
    min_severity: (document.getElementById('pirep-filter-severity') as HTMLSelectElement)?.value || '',
    altitude_min: state.altitude_min,
    altitude_max: state.altitude_max,
  });

  container.querySelectorAll('select').forEach((sel) => {
    sel.addEventListener('change', () => onChange(read()));
  });
}

export function filtersToParams(state: PirepFilterState): PirepFilters {
  const f: PirepFilters = {};
  if (state.hazard) f.hazard = state.hazard;
  if (state.min_severity) f.min_severity = state.min_severity;
  if (state.altitude_min) f.altitude_min = parseInt(state.altitude_min);
  if (state.altitude_max) f.altitude_max = parseInt(state.altitude_max);
  return f;
}
