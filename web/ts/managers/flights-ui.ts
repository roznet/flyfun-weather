/** DOM management for the Flights list page. */

import type { FlightResponse, PackMeta, RouteInfo } from '../store/types';

function $(id: string): HTMLElement {
  return document.getElementById(id)!;
}

/** Format a date string for display. */
function formatDate(iso: string): string {
  const d = new Date(iso + 'T00:00:00Z');
  return d.toLocaleDateString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
    timeZone: 'UTC',
  });
}

/** Format time as 4-digit UTC. */
function formatTime(hour: number): string {
  return `${hour.toString().padStart(2, '0')}00Z`;
}

/** Format altitude for display. */
function formatAlt(ft: number): string {
  if (ft >= 10000) return `FL${Math.round(ft / 100)}`;
  return `${ft}ft`;
}

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
  routes: RouteInfo[],
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
    const waypoints = f.waypoints.length > 0
      ? f.waypoints.join(' → ')
      : f.route_name.replace(/_/g, ' → ').toUpperCase();
    const packInfo = pack
      ? `<span class="pack-info">D-${pack.days_out} (${new Date(pack.fetch_timestamp).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC)</span>
         <span class="badge ${assessmentClass(pack.assessment)}">${pack.assessment || '—'}</span>`
      : '<span class="pack-info">No briefings yet</span>';

    return `
      <div class="flight-card" data-id="${f.id}">
        <div class="flight-header">
          <span class="flight-route">${waypoints}</span>
          <span class="flight-date">${formatDate(f.target_date)} ${formatTime(f.target_time_utc)}</span>
          <span class="flight-alt">${formatAlt(f.cruise_altitude_ft)}</span>
        </div>
        <div class="flight-status">
          ${packInfo}
        </div>
        <div class="flight-actions">
          <button class="btn btn-primary btn-view" data-id="${f.id}">View Briefing</button>
          <button class="btn btn-danger btn-delete" data-id="${f.id}">Delete</button>
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

export function renderRouteOptions(routes: RouteInfo[]): void {
  const select = $('route-select') as HTMLSelectElement;
  if (!select) return;

  select.innerHTML = '<option value="">—</option>' +
    routes.map((r) =>
      `<option value="${r.name}" data-alt="${r.cruise_altitude_ft}" data-dur="${r.flight_duration_hours}">${r.display_name} (${r.waypoints.join(' → ')})</option>`
    ).join('');
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

/** Fill in route defaults when a preset is selected. */
export function onRouteSelected(routes: RouteInfo[]): void {
  const select = $('route-select') as HTMLSelectElement;
  if (!select) return;

  select.addEventListener('change', () => {
    const route = routes.find((r) => r.name === select.value);
    if (!route) return;

    const wpInput = $('input-waypoints') as HTMLInputElement;
    const altInput = $('input-altitude') as HTMLInputElement;
    const ceilInput = $('input-ceiling') as HTMLInputElement;
    const durInput = $('input-duration') as HTMLInputElement;
    if (wpInput) wpInput.value = route.waypoints.join(' ');
    if (altInput) altInput.value = String(route.cruise_altitude_ft);
    if (ceilInput) ceilInput.value = String(route.flight_ceiling_ft);
    if (durInput) durInput.value = String(route.flight_duration_hours);
  });
}
