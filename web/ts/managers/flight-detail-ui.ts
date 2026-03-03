/** DOM management for the Flight Detail page. */

import type { FlightResponse, PackMeta } from '../store/types';
import type { WaypointInfo } from '../adapters/api-adapter';
import { $, escapeHtml, formatDate, formatDepartureTime, formatAlt } from '../utils';

// --- Assessment badge ---

function assessmentClass(assessment: string | null): string {
  if (!assessment) return 'badge-none';
  switch (assessment.toUpperCase()) {
    case 'GREEN': return 'badge-green';
    case 'AMBER': return 'badge-amber';
    case 'RED': return 'badge-red';
    default: return 'badge-none';
  }
}

function assessmentDot(assessment: string | null): string {
  const color = !assessment ? '#999' :
    assessment.toUpperCase() === 'GREEN' ? 'var(--green)' :
    assessment.toUpperCase() === 'AMBER' ? 'var(--amber)' :
    assessment.toUpperCase() === 'RED' ? 'var(--red)' : '#999';
  return `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${color};margin-right:4px;vertical-align:middle;"></span>`;
}

// --- Flight info ---

export function renderFlightInfo(
  flight: FlightResponse | null,
  editing: boolean,
): void {
  const container = $('flight-info');
  if (!container || !flight) return;

  const waypointDisplay = flight.waypoints.length > 0
    ? flight.waypoints.join(' \u2192 ')
    : flight.route_name.replace(/_/g, ' \u2192 ').toUpperCase();

  // Parse departure time for edit form defaults
  const dt = new Date(flight.departure_time);
  const utcHour = dt.getUTCHours();
  const utcMinute = dt.getUTCMinutes();
  const endTime = new Date(dt.getTime() + flight.flight_duration_hours * 3600_000);
  const endTimeStr = `${endTime.getUTCHours().toString().padStart(2, '0')}:${endTime.getUTCMinutes().toString().padStart(2, '0')}Z`;

  if (editing) {
    // Build hour options
    let hourOptions = '';
    for (let h = 0; h < 24; h++) {
      const sel = h === utcHour ? ' selected' : '';
      hourOptions += `<option value="${h}"${sel}>${h.toString().padStart(2, '0')}</option>`;
    }
    // Build minute options
    const minuteOptions = [0, 15, 30, 45].map(m => {
      const sel = m === nearestMinute(utcMinute) ? ' selected' : '';
      return `<option value="${m}"${sel}>${m.toString().padStart(2, '0')}</option>`;
    }).join('');

    container.innerHTML = `
      <div class="flight-info-grid editing">
        <div class="info-row">
          <span class="info-label">Date</span>
          <span class="info-value">${formatDate(flight.target_date)}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Time (UTC)</span>
          <span class="info-value">
            <select id="edit-hour" class="edit-input">${hourOptions}</select>
            <span class="time-separator">:</span>
            <select id="edit-minute" class="edit-input">${minuteOptions}</select>
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Altitude</span>
          <span class="info-value">
            <input type="number" id="edit-altitude" class="edit-input" value="${flight.cruise_altitude_ft}" min="1000" max="45000" step="500" style="width:80px"> ft
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Ceiling</span>
          <span class="info-value">
            <input type="number" id="edit-ceiling" class="edit-input" value="${flight.flight_ceiling_ft}" min="1000" max="45000" step="500" style="width:80px"> ft
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Duration</span>
          <span class="info-value">
            <input type="number" id="edit-duration" class="edit-input" value="${flight.flight_duration_hours}" min="0" max="24" step="0.5" style="width:70px"> hrs
          </span>
        </div>
        <div class="info-row edit-actions">
          <button class="btn btn-primary btn-sm" id="edit-save">Save</button>
          <button class="btn btn-sm" id="edit-cancel">Cancel</button>
        </div>
      </div>
    `;
  } else {
    container.innerHTML = `
      <div class="flight-info-grid">
        <div class="info-row">
          <span class="info-label">Date</span>
          <span class="info-value">${formatDate(flight.target_date)}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Time</span>
          <span class="info-value">${formatDepartureTime(flight.departure_time)}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Altitude</span>
          <span class="info-value">${formatAlt(flight.cruise_altitude_ft)}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Ceiling</span>
          <span class="info-value">${formatAlt(flight.flight_ceiling_ft)}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Duration</span>
          <span class="info-value">${flight.flight_duration_hours}h</span>
        </div>
        <div class="info-row">
          <span class="info-label">End time</span>
          <span class="info-value">${endTimeStr}</span>
        </div>
      </div>
    `;
  }
}

// --- Flight header ---

export function renderHeader(
  flight: FlightResponse | null,
  editing: boolean,
): void {
  const el = $('flight-header');
  if (!el) return;

  if (!flight) {
    el.innerHTML = '<h1>Flight</h1>';
    return;
  }

  const waypointDisplay = flight.waypoints.length > 0
    ? flight.waypoints.join(' \u2192 ')
    : flight.route_name.replace(/_/g, ' \u2192 ').toUpperCase();

  const editBtn = editing
    ? ''
    : '<button class="btn btn-sm" id="btn-edit-flight">Edit</button>';

  el.innerHTML = `
    <div class="flight-detail-title">
      <a href="/" class="breadcrumb-link">\u2190 Flights</a>
      <h1>Flight: ${escapeHtml(waypointDisplay)} ${editBtn}</h1>
    </div>
  `;
}

// --- Latest assessment ---

export function renderLatestAssessment(pack: PackMeta | null): void {
  const el = $('latest-assessment');
  if (!el) return;

  if (!pack) {
    el.innerHTML = '<span class="muted">No briefings yet</span>';
    return;
  }

  const reason = pack.assessment_reason ? ` \u2014 "${escapeHtml(pack.assessment_reason)}"` : '';
  el.innerHTML = `
    <span class="badge ${assessmentClass(pack.assessment)}">
      ${escapeHtml(pack.assessment || '\u2014')}
    </span>
    <span class="assessment-reason">${reason}</span>
  `;
}

// --- Briefing history ---

export function renderBriefingHistory(
  packs: PackMeta[],
  flightId: string,
): void {
  const container = $('briefing-history');
  if (!container) return;

  if (packs.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p>No briefings yet. Go to the <a href="/briefing.html?flight=${encodeURIComponent(flightId)}">briefing page</a> to trigger a refresh.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = packs.map((pack) => {
    const ts = new Date(pack.fetch_timestamp);
    const dateStr = ts.toLocaleDateString('en-GB', {
      day: 'numeric', month: 'short', timeZone: 'UTC',
    });
    const timeStr = ts.toLocaleTimeString('en-GB', {
      hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
    });
    const briefingUrl = `/briefing.html?flight=${encodeURIComponent(flightId)}&pack=${encodeURIComponent(pack.fetch_timestamp)}`;

    return `
      <a href="${briefingUrl}" class="history-row">
        <span class="history-days-out">D-${pack.days_out}</span>
        <span class="history-date">${dateStr} ${timeStr} UTC</span>
        <span class="badge ${assessmentClass(pack.assessment)}">${escapeHtml(pack.assessment || '\u2014')}</span>
        <span class="history-arrow">\u2192</span>
      </a>
    `;
  }).join('');
}

// --- Invalidation banner ---

export function renderInvalidationBanner(invalidation: string | null, flightId: string): void {
  const el = $('invalidation-banner');
  if (!el) return;

  if (!invalidation) {
    el.style.display = 'none';
    return;
  }

  el.style.display = 'block';
  const briefingUrl = `/briefing.html?flight=${encodeURIComponent(flightId)}`;

  if (invalidation === 'advisories_only') {
    el.className = 'invalidation-banner invalidation-info';
    el.innerHTML = `Altitude/ceiling changed. Advisories can be recalculated from saved data on the <a href="${briefingUrl}">briefing page</a>.`;
  } else if (invalidation === 'refetch_needed') {
    el.className = 'invalidation-banner invalidation-warning';
    el.innerHTML = `Time or duration changed. A new briefing refresh is needed on the <a href="${briefingUrl}">briefing page</a> for updated forecasts.`;
  }
}

// --- Loading / Error ---

export function renderLoading(loading: boolean): void {
  const spinner = $('loading-spinner');
  if (spinner) {
    spinner.style.display = loading ? 'block' : 'none';
  }
}

export function renderError(error: string | null): void {
  const el = $('error-message');
  if (el) {
    el.textContent = error || '';
    el.style.display = error ? 'block' : 'none';
  }
}

// --- Helpers ---

function nearestMinute(m: number): number {
  const options = [0, 15, 30, 45];
  return options.reduce((best, o) => Math.abs(o - m) < Math.abs(best - m) ? o : best);
}
