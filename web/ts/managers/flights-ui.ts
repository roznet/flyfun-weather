/** DOM management for the Flights list page. */

import type { FlightResponse, PackMeta } from '../store/types';
import type { RefreshEntry } from '../adapters/api-adapter';
import { $, escapeHtml, formatDate, formatTime, formatAlt, isFlightPast } from '../utils';

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

// --- Render functions ---

export function renderFlightList(
  flights: FlightResponse[],
  latestPacks: Record<string, PackMeta | null>,
  activeRefreshes: Record<string, RefreshEntry>,
  onView: (id: string) => void,
  onDelete: (id: string) => void,
): void {
  const container = $('flight-list');
  if (!container) return;

  if (flights.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p>No flights yet. Create one to get started.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = flights.map((f) => {
    const pack = latestPacks[f.id];
    const refreshEntry = activeRefreshes[f.id];
    const waypoints = f.waypoints.length > 0
      ? f.waypoints.join(' → ')
      : f.route_name.replace(/_/g, ' → ').toUpperCase();
    const past = isFlightPast(f.target_date, f.target_time_utc, f.flight_duration_hours);
    const pastBadge = past ? '<span class="badge badge-past">Past</span> ' : '';

    let refreshBadge = '';
    if (refreshEntry) {
      const label = refreshEntry.status === 'queued' ? 'Queued' : 'Refreshing';
      const spinner = refreshEntry.status === 'refreshing' ? '<span class="dots-spinner"></span>' : '';
      refreshBadge = `<span class="badge badge-refreshing">${label}${spinner}</span> `;
    }

    const packInfo = pack
      ? `<span class="pack-info">D-${pack.days_out} (${new Date(pack.fetch_timestamp).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC)</span>
         <span class="badge ${assessmentClass(pack.assessment)}">${escapeHtml(pack.assessment || '\u2014')}</span>`
      : '<span class="pack-info">No briefings yet</span>';

    return `
      <div class="flight-card" data-id="${escapeHtml(f.id)}">
        <div class="flight-header">
          ${pastBadge}<span class="flight-route">${escapeHtml(waypoints)}</span>
          <span class="flight-date">${formatDate(f.target_date)} ${formatTime(f.target_time_utc)}</span>
          <span class="flight-alt">${formatAlt(f.cruise_altitude_ft)}</span>
        </div>
        <div class="flight-status">
          ${refreshBadge}${packInfo}
        </div>
        <div class="flight-actions">
          <button class="btn btn-primary btn-view" data-id="${escapeHtml(f.id)}">View Briefing</button>
          <button class="btn btn-danger btn-delete" data-id="${escapeHtml(f.id)}">Delete</button>
        </div>
      </div>
    `;
  }).join('');

  // Wire up event listeners
  container.querySelectorAll('.btn-view').forEach((btn) => {
    btn.addEventListener('click', () => {
      onView((btn as HTMLElement).dataset.id!);
    });
  });
  container.querySelectorAll('.btn-delete').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = (btn as HTMLElement).dataset.id!;
      if (confirm(`Delete flight ${id}? This removes all briefing history.`)) {
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

