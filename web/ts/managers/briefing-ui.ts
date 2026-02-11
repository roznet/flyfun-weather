/** DOM management for the Briefing report page.
 *
 * Renders all sections: header, assessment, synopsis, GRAMET,
 * model comparison, and Skew-T route view.
 */

import type { FlightResponse, ForecastSnapshot, PackMeta, WeatherDigest } from '../store/types';
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
  };
  return labels[name] || name;
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

export function renderError(error: string | null): void {
  const el = $('error-message');
  if (el) {
    el.textContent = error || '';
    el.style.display = error ? 'block' : 'none';
  }
}
