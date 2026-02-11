/** DOM management for the Briefing report page.
 *
 * Renders all sections: header, assessment, synopsis, GRAMET,
 * model comparison, and Skew-T route view.
 */

import type {
  AltitudeBandComparison,
  BandModelSummary,
  ConvectiveRisk,
  FlightResponse,
  ForecastSnapshot,
  IcingRisk,
  PackMeta,
  SoundingAnalysis,
  WaypointAnalysis,
  WeatherDigest,
} from '../store/types';
import * as api from '../adapters/api-adapter';

function $(id: string): HTMLElement {
  return document.getElementById(id)!;
}

// --- Header ---

export function renderHeader(
  flight: FlightResponse | null,
  snapshot: ForecastSnapshot | null,
): void {
  const el = $('briefing-header');
  if (!el || !flight) return;

  // Use snapshot waypoints if available, otherwise derive from route name
  let routeStr: string;
  if (snapshot?.route?.waypoints) {
    routeStr = snapshot.route.waypoints.map((w) => w.icao).join(' \u2192 ');
  } else if (flight.waypoints?.length) {
    routeStr = flight.waypoints.join(' \u2192 ');
  } else {
    routeStr = flight.route_name.replace(/_/g, ' \u2192 ').toUpperCase();
  }

  const date = new Date(flight.target_date + 'T00:00:00Z');
  const dateStr = date.toLocaleDateString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
    timeZone: 'UTC',
  });
  const timeStr = `${flight.target_time_utc.toString().padStart(2, '0')}00Z`;
  const alt = flight.cruise_altitude_ft >= 10000
    ? `FL${Math.round(flight.cruise_altitude_ft / 100)}`
    : `${flight.cruise_altitude_ft}ft`;

  el.innerHTML = `
    <span class="route-summary">${routeStr}</span>
    <span class="date-summary">${dateStr} ${timeStr}</span>
    <span class="alt-summary">${alt}</span>
  `;
}

// --- History dropdown ---

export function renderHistoryDropdown(
  packs: PackMeta[],
  currentTimestamp: string | null,
  onSelect: (ts: string) => void,
): void {
  const select = $('history-select') as HTMLSelectElement;
  if (!select) return;

  select.innerHTML = packs.length === 0
    ? '<option>No briefings yet</option>'
    : packs.map((p) => {
        const date = new Date(p.fetch_timestamp);
        const label = `D-${p.days_out} (${date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC)`;
        const selected = p.fetch_timestamp === currentTimestamp ? ' selected' : '';
        return `<option value="${p.fetch_timestamp}"${selected}>${label}</option>`;
      }).join('');

  // Wire change event (remove old listener by replacing element)
  const newSelect = select.cloneNode(true) as HTMLSelectElement;
  select.parentNode!.replaceChild(newSelect, select);
  newSelect.addEventListener('change', () => {
    onSelect(newSelect.value);
  });
}

// --- Assessment banner ---

export function renderAssessment(pack: PackMeta | null): void {
  const el = $('assessment-banner');
  if (!el) return;

  if (!pack || !pack.assessment) {
    el.className = 'assessment-banner assessment-none';
    el.textContent = 'No assessment available';
    return;
  }

  const level = pack.assessment.toUpperCase();
  el.className = `assessment-banner assessment-${level.toLowerCase()}`;
  el.innerHTML = `
    <strong>${level}</strong>${pack.assessment_reason ? ` \u2014 ${pack.assessment_reason}` : ''}
  `;
}

// --- Synopsis (structured digest) ---

const DIGEST_SECTIONS: Array<{ key: keyof WeatherDigest; label: string; icon: string }> = [
  { key: 'synoptic', label: 'Synoptic', icon: '\uD83C\uDF0D' },
  { key: 'winds', label: 'Winds', icon: '\uD83D\uDCA8' },
  { key: 'cloud_visibility', label: 'Cloud & Visibility', icon: '\u2601\uFE0F' },
  { key: 'precipitation_convection', label: 'Precipitation & Convection', icon: '\uD83C\uDF27\uFE0F' },
  { key: 'icing', label: 'Icing', icon: '\u2744\uFE0F' },
  { key: 'specific_concerns', label: 'Specific Concerns', icon: '\u26A0\uFE0F' },
  { key: 'model_agreement', label: 'Model Agreement', icon: '\uD83D\uDCCA' },
  { key: 'trend', label: 'Trend', icon: '\uD83D\uDCC8' },
  { key: 'watch_items', label: 'Watch Items', icon: '\uD83D\uDC41\uFE0F' },
];

export function renderSynopsis(
  flight: FlightResponse | null,
  pack: PackMeta | null,
  digest: WeatherDigest | null,
): void {
  const el = $('synopsis-section');
  if (!el) return;

  if (!flight || !pack) {
    el.innerHTML = '<p class="muted">No briefing loaded.</p>';
    return;
  }

  if (digest) {
    el.innerHTML = DIGEST_SECTIONS.map(({ key, label, icon }) => {
      const text = digest[key];
      if (!text) return '';
      return `
        <div class="digest-section">
          <h4>${icon} ${label}</h4>
          <p>${escapeHtml(text as string)}</p>
        </div>
      `;
    }).join('');
    return;
  }

  if (pack.has_digest) {
    el.innerHTML = '<p class="muted">Loading digest...</p>';
    fetchAndRenderDigestJson(flight.id, pack.fetch_timestamp, el);
    return;
  }

  el.innerHTML = '<p class="muted">Synopsis not available. Trigger a refresh to generate.</p>';
}

async function fetchAndRenderDigestJson(
  flightId: string, timestamp: string, el: HTMLElement,
): Promise<void> {
  try {
    const url = api.digestJsonUrl(flightId, timestamp);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`${resp.status}`);
    const digest: WeatherDigest = await resp.json();

    el.innerHTML = DIGEST_SECTIONS.map(({ key, label, icon }) => {
      const text = digest[key];
      if (!text) return '';
      return `
        <div class="digest-section">
          <h4>${icon} ${label}</h4>
          <p>${escapeHtml(text as string)}</p>
        </div>
      `;
    }).join('');
  } catch {
    el.innerHTML = '<p class="muted">Failed to load digest.</p>';
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// --- GRAMET ---

export function renderGramet(
  flight: FlightResponse | null,
  pack: PackMeta | null,
): void {
  const el = $('gramet-section');
  if (!el) return;

  if (!flight || !pack || !pack.has_gramet) {
    el.innerHTML = '<p class="muted">GRAMET not available for this briefing.</p>';
    return;
  }

  const url = api.grametUrl(flight.id, pack.fetch_timestamp);
  el.innerHTML = `
    <img src="${url}" alt="GRAMET cross-section" class="gramet-img" loading="lazy">
  `;
}

// --- Model Comparison ---

export function renderModelComparison(snapshot: ForecastSnapshot | null): void {
  const el = $('comparison-section');
  if (!el) return;

  if (!snapshot || snapshot.analyses.length === 0) {
    el.innerHTML = '<p class="muted">No model comparison data available.</p>';
    return;
  }

  // Render comparison tables for each waypoint
  el.innerHTML = snapshot.analyses.map((a) => {
    if (a.model_divergence.length === 0) return '';

    const models = Object.keys(a.model_divergence[0]?.model_values || {});
    const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

    const rows = a.model_divergence.map((d) => {
      const valueCells = models.map((m) => {
        const val = d.model_values[m];
        return `<td>${val !== undefined ? val.toFixed(1) : '\u2014'}</td>`;
      }).join('');
      const agreeIcon = d.agreement === 'good' ? '&#10003;'
        : d.agreement === 'moderate' ? '&#9888;' : '&#10007;';
      const agreeClass = `agree-${d.agreement}`;
      return `
        <tr>
          <td class="var-name">${formatVarName(d.variable)}</td>
          ${valueCells}
          <td>${d.spread.toFixed(1)}</td>
          <td class="${agreeClass}">${agreeIcon}</td>
        </tr>
      `;
    }).join('');

    return `
      <div class="comparison-waypoint">
        <h4>${a.waypoint.icao} \u2014 ${a.waypoint.name}</h4>
        <table class="comparison-table">
          <thead>
            <tr>
              <th>Variable</th>
              ${headerCells}
              <th>Spread</th>
              <th>Agree</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }).join('');
}

function formatVarName(name: string): string {
  const labels: Record<string, string> = {
    'temperature_c': 'Temp (\u00B0C)',
    'wind_speed_kt': 'Wind (kt)',
    'wind_direction_deg': 'Wind dir (\u00B0)',
    'cloud_cover_pct': 'Cloud (%)',
    'precipitation_mm': 'Precip (mm)',
    'freezing_level_m': 'Freezing (m)',
    'freezing_level_ft': 'Freezing (ft)',
    'cape_surface_jkg': 'CAPE (J/kg)',
    'lcl_altitude_ft': 'LCL (ft)',
    'k_index': 'K-index',
    'total_totals': 'Total Totals',
    'precipitable_water_mm': 'PW (mm)',
    'lifted_index': 'Lifted Index',
    'bulk_shear_0_6km_kt': 'Shear 0-6km (kt)',
  };
  return labels[name] || name;
}

// --- Sounding Analysis ---

const RISK_COLORS: Record<string, string> = {
  none: '',
  light: 'risk-light',
  low: 'risk-light',
  moderate: 'risk-moderate',
  high: 'risk-high',
  severe: 'risk-severe',
  extreme: 'risk-severe',
};

function riskClass(risk: string): string {
  return RISK_COLORS[risk] || '';
}

export function renderSoundingAnalysis(snapshot: ForecastSnapshot | null): void {
  const el = $('sounding-section');
  if (!el) return;

  if (!snapshot || snapshot.analyses.length === 0) {
    el.innerHTML = '<p class="muted">No sounding analysis available.</p>';
    return;
  }

  const hasSounding = snapshot.analyses.some(
    (a) => a.sounding && Object.keys(a.sounding).length > 0,
  );
  if (!hasSounding) {
    el.innerHTML = '<p class="muted">Sounding analysis not available for this briefing.</p>';
    return;
  }

  el.innerHTML = snapshot.analyses.map((a) => {
    if (!a.sounding || Object.keys(a.sounding).length === 0) return '';
    return `
      <div class="sounding-waypoint">
        <h4>${a.waypoint.icao} \u2014 ${a.waypoint.name}</h4>
        ${renderConvectiveBanner(a.sounding)}
        ${renderAltitudeMarkers(a.sounding)}
        ${renderIcingZones(a.sounding)}
        ${renderEnhancedClouds(a.sounding)}
        ${renderBandComparisons(a.band_comparisons)}
      </div>
    `;
  }).join('');
}

function renderConvectiveBanner(soundings: Record<string, SoundingAnalysis>): string {
  const risks: string[] = [];
  for (const [model, sa] of Object.entries(soundings)) {
    if (sa.convective && sa.convective.risk_level !== 'none') {
      const mods = sa.convective.severe_modifiers.length > 0
        ? ` (${sa.convective.severe_modifiers.join('; ')})`
        : '';
      risks.push(`<span class="${riskClass(sa.convective.risk_level)}">[${model}] ${sa.convective.risk_level.toUpperCase()}${mods}</span>`);
    }
  }
  if (risks.length === 0) return '';
  return `<div class="convective-banner">Convective: ${risks.join(' ')}</div>`;
}

function renderAltitudeMarkers(soundings: Record<string, SoundingAnalysis>): string {
  const rows: string[] = [];
  for (const [model, sa] of Object.entries(soundings)) {
    if (!sa.indices) continue;
    const idx = sa.indices;
    const parts: string[] = [];
    if (idx.freezing_level_ft != null) parts.push(`0\u00B0C: ${idx.freezing_level_ft.toFixed(0)}ft`);
    if (idx.minus10c_level_ft != null) parts.push(`-10\u00B0C: ${idx.minus10c_level_ft.toFixed(0)}ft`);
    if (idx.minus20c_level_ft != null) parts.push(`-20\u00B0C: ${idx.minus20c_level_ft.toFixed(0)}ft`);
    if (idx.lcl_altitude_ft != null) parts.push(`LCL: ${idx.lcl_altitude_ft.toFixed(0)}ft`);
    if (parts.length > 0) {
      rows.push(`<div class="marker-row"><strong>${model}</strong>: ${parts.join(' | ')}</div>`);
    }
  }
  if (rows.length === 0) return '';
  return `<div class="altitude-markers"><h5>Key Altitudes</h5>${rows.join('')}</div>`;
}

function renderIcingZones(soundings: Record<string, SoundingAnalysis>): string {
  const rows: string[] = [];
  for (const [model, sa] of Object.entries(soundings)) {
    for (const zone of sa.icing_zones) {
      const sld = zone.sld_risk ? ' <span class="sld-badge">SLD</span>' : '';
      const tw = zone.mean_wet_bulb_c != null ? ` Tw=${zone.mean_wet_bulb_c.toFixed(0)}\u00B0C` : '';
      rows.push(
        `<div class="icing-row ${riskClass(zone.risk)}">` +
        `[${model}] ${zone.risk.toUpperCase()} ${zone.icing_type} ` +
        `${zone.base_ft.toFixed(0)}-${zone.top_ft.toFixed(0)}ft${tw}${sld}</div>`,
      );
    }
  }
  if (rows.length === 0) return '';
  return `<div class="icing-zones"><h5>Icing Zones</h5>${rows.join('')}</div>`;
}

function renderEnhancedClouds(soundings: Record<string, SoundingAnalysis>): string {
  const rows: string[] = [];
  for (const [model, sa] of Object.entries(soundings)) {
    for (const cl of sa.cloud_layers) {
      const t = cl.mean_temperature_c != null ? ` T=${cl.mean_temperature_c.toFixed(0)}\u00B0C` : '';
      rows.push(
        `<div class="cloud-row">[${model}] ${cl.coverage.toUpperCase()} ` +
        `${cl.base_ft.toFixed(0)}-${cl.top_ft.toFixed(0)}ft${t}</div>`,
      );
    }
  }
  if (rows.length === 0) return '';
  return `<div class="enhanced-clouds"><h5>Cloud Layers</h5>${rows.join('')}</div>`;
}

function renderBandComparisons(bands: AltitudeBandComparison[]): string {
  if (!bands || bands.length === 0) return '';

  const activeBands = bands.filter((bc) =>
    Object.values(bc.models).some(
      (s) => s.worst_icing_risk !== 'none' || s.cloud_coverage != null,
    ),
  );
  if (activeBands.length === 0) return '';

  return activeBands.map((bc) => {
    const models = Object.keys(bc.models);
    const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

    const agreeIcons: string[] = [];
    if (!bc.icing_agreement) agreeIcons.push('<span class="agree-poor" title="Icing disagree">&#10007; Ice</span>');
    if (!bc.cloud_agreement) agreeIcons.push('<span class="agree-poor" title="Cloud disagree">&#10007; Cloud</span>');
    const agreeStr = agreeIcons.length > 0 ? ` ${agreeIcons.join(' ')}` : '';

    const icingRow = models.map((m) => {
      const s = bc.models[m];
      if (s.worst_icing_risk === 'none') return '<td>\u2014</td>';
      const sld = s.sld_risk ? ' <span class="sld-badge">SLD</span>' : '';
      return `<td class="${riskClass(s.worst_icing_risk)}">${s.worst_icing_risk}/${s.worst_icing_type}${sld}</td>`;
    }).join('');

    const cloudRow = models.map((m) => {
      const s = bc.models[m];
      return s.cloud_coverage ? `<td>${s.cloud_coverage.toUpperCase()}</td>` : '<td>\u2014</td>';
    }).join('');

    const tempRow = models.map((m) => {
      const s = bc.models[m];
      if (s.temperature_min_c != null && s.temperature_max_c != null) {
        return `<td>${s.temperature_min_c.toFixed(0)}/${s.temperature_max_c.toFixed(0)}\u00B0C</td>`;
      }
      return '<td>\u2014</td>';
    }).join('');

    return `
      <details class="band-details">
        <summary>${bc.band.name}${agreeStr}</summary>
        <table class="band-table">
          <thead><tr><th></th>${headerCells}</tr></thead>
          <tbody>
            <tr><td class="var-name">Icing</td>${icingRow}</tr>
            <tr><td class="var-name">Cloud</td>${cloudRow}</tr>
            <tr><td class="var-name">Temp</td>${tempRow}</tr>
          </tbody>
        </table>
      </details>
    `;
  }).join('');
}

// --- Skew-T ---

export function renderSkewTs(
  flight: FlightResponse | null,
  pack: PackMeta | null,
  snapshot: ForecastSnapshot | null,
  selectedModel: string,
): void {
  const el = $('skewt-section');
  if (!el) return;

  if (!flight || !pack || !pack.has_skewt || !snapshot) {
    el.innerHTML = '<p class="muted">Skew-T diagrams not available.</p>';
    return;
  }

  const waypoints = snapshot.route.waypoints;
  el.innerHTML = `
    <div class="skewt-gallery">
      ${waypoints.map((wp) => {
        const url = api.skewtUrl(flight.id, pack.fetch_timestamp, wp.icao, selectedModel);
        return `
          <div class="skewt-card">
            <h4>${wp.icao}</h4>
            <img src="${url}" alt="Skew-T ${wp.icao} ${selectedModel}"
                 class="skewt-img" loading="lazy"
                 onerror="this.parentElement.classList.add('skewt-unavailable')">
            <div class="skewt-fallback">Not available</div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

// --- Loading / Error ---

export function renderLoading(loading: boolean): void {
  const el = $('loading-overlay');
  if (el) el.style.display = loading ? 'flex' : 'none';
}

export function renderRefreshing(refreshing: boolean): void {
  const btn = $('refresh-btn') as HTMLButtonElement;
  if (btn) {
    btn.disabled = refreshing;
    btn.textContent = refreshing ? 'Refreshing...' : 'Refresh';
  }
}

export function renderEmailing(emailing: boolean): void {
  const btn = $('email-btn') as HTMLButtonElement;
  if (btn) {
    btn.disabled = emailing;
    btn.textContent = emailing ? 'Sending...' : 'Send Email';
  }
}

export function renderError(error: string | null): void {
  const el = $('error-message');
  if (el) {
    el.textContent = error || '';
    el.style.display = error ? 'block' : 'none';
  }
}
