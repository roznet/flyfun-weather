/** DOM management for the Flights list page. */

import type { FlightResponse, PackMeta } from '../store/types';
import type { RefreshEntry } from '../adapters/api-adapter';
import { $, escapeHtml, formatDate, formatDepartureTime, formatAlt, isFlightPast, flightTitle, flightRoute } from '../utils';
import { t, getDateLocale } from '../i18n/i18n';

/** Assessment badge color class. */
function assessmentClass(assessment: string | null): string {
  if (!assessment) return 'badge-none';
  switch (assessment.toUpperCase()) {
    case 'GREEN': return 'badge-green';
    case 'AMBER': return 'badge-amber';
    case 'RED': return 'badge-red';
    default: return 'badge-none';
  }
}

/** Track whether the past-flights section is expanded. */
let pastExpanded = false;

/** Render a single flight card. */
function renderFlightCard(
  f: FlightResponse,
  pack: PackMeta | null,
  refreshEntry: RefreshEntry | undefined,
): string {
  const wps = f.waypoints.length > 0
    ? f.waypoints
    : f.route_name.split('_').map(w => w.toUpperCase());
  const title = flightTitle(wps);
  const route = wps.length > 2 ? flightRoute(wps) : '';
  const past = isFlightPast(f.target_date, f.target_time_utc, f.flight_duration_hours, f.departure_time);
  const pastBadge = past ? `<span class="badge badge-past">${t('flights.pastBadge')}</span> ` : '';

  let refreshBadge = '';
  if (refreshEntry) {
    const label = refreshEntry.status === 'queued' ? t('flights.queuedBadge') : t('flights.refreshingBadge');
    const spinner = refreshEntry.status === 'refreshing' ? '<span class="dots-spinner"></span>' : '';
    refreshBadge = `<span class="badge badge-refreshing">${label}${spinner}</span> `;
  }

  const packInfo = pack
    ? `<span class="pack-info">D-${pack.days_out} (${new Date(pack.fetch_timestamp).toLocaleDateString(getDateLocale(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC)</span>
       <span class="badge ${assessmentClass(pack.assessment)}">${escapeHtml(pack.assessment || '\u2014')}</span>`
    : `<span class="pack-info">${t('flights.noBriefings')}</span>`;

  const routeLine = route
    ? `<div class="flight-route-detail">${escapeHtml(route)}</div>`
    : '';

  return `
    <div class="flight-card" data-id="${escapeHtml(f.id)}">
      <div class="flight-header">
        ${pastBadge}<span class="flight-route">${escapeHtml(title)}</span>
        <span class="flight-date">${formatDate(f.target_date)} ${formatDepartureTime(f.departure_time)}</span>
        <span class="flight-alt">${formatAlt(f.cruise_altitude_ft)}</span>
      </div>
      ${routeLine}
      <div class="flight-status">
        ${refreshBadge}${packInfo}
      </div>
      <div class="flight-actions">
        <button class="btn btn-primary btn-briefing" data-id="${escapeHtml(f.id)}">${t('flights.btnBriefing')}</button>
        <button class="btn btn-secondary btn-edit" data-id="${escapeHtml(f.id)}">${t('flights.btnEdit')}</button>
        <button class="btn btn-secondary btn-duplicate" data-id="${escapeHtml(f.id)}">${t('flights.btnDuplicate')}</button>
        <button class="btn btn-danger btn-delete" data-id="${escapeHtml(f.id)}">${t('flights.btnDelete')}</button>
      </div>
    </div>
  `;
}

// --- Render functions ---

export function renderFlightList(
  flights: FlightResponse[],
  latestPacks: Record<string, PackMeta | null>,
  activeRefreshes: Record<string, RefreshEntry>,
  onBriefing: (id: string) => void,
  onEdit: (id: string) => void,
  onDuplicate: (id: string) => void,
  onDelete: (id: string) => void,
): void {
  const container = $('flight-list');
  if (!container) return;

  if (flights.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p>${t('flights.empty')}</p>
      </div>
    `;
    return;
  }

  // Split into active and past flights
  const active: FlightResponse[] = [];
  const past: FlightResponse[] = [];
  for (const f of flights) {
    if (isFlightPast(f.target_date, f.target_time_utc, f.flight_duration_hours, f.departure_time)) {
      past.push(f);
    } else {
      active.push(f);
    }
  }

  const activeCards = active.map(f =>
    renderFlightCard(f, latestPacks[f.id], activeRefreshes[f.id]),
  ).join('');

  let pastSection = '';
  if (past.length > 0) {
    const expandedClass = pastExpanded ? '' : ' collapsed';
    const pastCards = past.map(f =>
      renderFlightCard(f, latestPacks[f.id], activeRefreshes[f.id]),
    ).join('');
    pastSection = `
      <div class="past-flights-section${expandedClass}">
        <button class="past-flights-toggle" id="past-flights-toggle">
          ${t('flights.past', { count: past.length })}
        </button>
        <div class="past-flights-list">${pastCards}</div>
      </div>
    `;
  }

  container.innerHTML = activeCards + pastSection;

  // Wire toggle
  const toggleBtn = document.getElementById('past-flights-toggle');
  toggleBtn?.addEventListener('click', () => {
    pastExpanded = !pastExpanded;
    const section = toggleBtn.closest('.past-flights-section');
    section?.classList.toggle('collapsed', !pastExpanded);
  });

  // Wire up event listeners
  container.querySelectorAll('.btn-briefing').forEach((btn) => {
    btn.addEventListener('click', () => {
      onBriefing((btn as HTMLElement).dataset.id!);
    });
  });
  container.querySelectorAll('.btn-edit').forEach((btn) => {
    btn.addEventListener('click', () => {
      onEdit((btn as HTMLElement).dataset.id!);
    });
  });
  container.querySelectorAll('.btn-duplicate').forEach((btn) => {
    btn.addEventListener('click', () => {
      onDuplicate((btn as HTMLElement).dataset.id!);
    });
  });
  container.querySelectorAll('.btn-delete').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = (btn as HTMLElement).dataset.id!;
      if (confirm(t('flights.deleteConfirm', { id }))) {
        onDelete(id);
      }
    });
  });
}

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
