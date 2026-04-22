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
  selected: boolean,
): string {
  const wps = f.waypoints.length > 0
    ? f.waypoints
    : f.route_name.split('_').map(w => w.toUpperCase());
  const title = flightTitle(wps);
  const route = wps.length > 2 ? flightRoute(wps) : '';
  const past = isFlightPast(f.target_date, f.target_time_utc, f.flight_duration_hours, f.departure_time);
  const pastBadge = past ? `<span class="badge badge-past">${t('flights.pastBadge')}</span> ` : '';

  const isShared = f.role === 'subscriber';
  const sharedBadge = isShared
    ? `<span class="badge badge-shared" title="${t('flights.sharedBadgeTitle', { owner: escapeHtml(f.owner_display_name || '') })}">${t('flights.sharedBadge')}</span> `
    : '';
  const ownerLine = isShared && f.owner_display_name
    ? `<div class="flight-shared-owner">${t('flights.sharedBy', { owner: escapeHtml(f.owner_display_name) })}</div>`
    : '';

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

  const checkedAttr = selected ? ' checked' : '';
  const selectedClass = selected ? ' selected' : '';

  const ownerOnlyActions = isShared
    ? `<button class="btn btn-outline btn-unsubscribe" data-id="${escapeHtml(f.id)}">${t('flights.btnUnsubscribe')}</button>`
    : `<button class="btn btn-secondary btn-edit" data-id="${escapeHtml(f.id)}">${t('flights.btnEdit')}</button>
       <button class="btn btn-danger btn-delete" data-id="${escapeHtml(f.id)}">${t('flights.btnDelete')}</button>`;
  // Subscribed flights can't be bulk-deleted — hide the checkbox too.
  const selectControl = isShared
    ? ''
    : `<label class="flight-select" title="${escapeHtml(t('flights.selectToggle'))}">
         <input type="checkbox" class="flight-select-checkbox" data-id="${escapeHtml(f.id)}"${checkedAttr}>
       </label>`;

  return `
    <div class="flight-card${selectedClass}${isShared ? ' flight-card-shared' : ''}" data-id="${escapeHtml(f.id)}">
      ${selectControl}
      <div class="flight-card-main">
        <div class="flight-header">
          ${sharedBadge}${pastBadge}<span class="flight-route">${escapeHtml(title)}</span>
          <span class="flight-date">${formatDate(f.target_date)} ${formatDepartureTime(f.departure_time)}</span>
          <span class="flight-alt">${formatAlt(f.cruise_altitude_ft)}</span>
        </div>
        ${ownerLine}
        ${routeLine}
        <div class="flight-status">
          ${refreshBadge}${packInfo}
        </div>
        <div class="flight-actions">
          <button class="btn btn-primary btn-briefing" data-id="${escapeHtml(f.id)}">${t('flights.btnBriefing')}</button>
          ${ownerOnlyActions}
        </div>
      </div>
    </div>
  `;
}

// --- Render functions ---

export interface SelectionHandlers {
  onToggle: (id: string) => void;
  onSelectAllPast: (pastIds: string[]) => void;
  onBulkDelete: () => void;
  onClearSelection: () => void;
}

export function renderFlightList(
  flights: FlightResponse[],
  latestPacks: Record<string, PackMeta | null>,
  activeRefreshes: Record<string, RefreshEntry>,
  selectedIds: Set<string>,
  onBriefing: (id: string) => void,
  onEdit: (id: string) => void,
  onDelete: (id: string) => void,
  selection: SelectionHandlers,
  onUnsubscribe?: (id: string) => void,
): void {
  const container = $('flight-list');
  if (!container) return;

  if (flights.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p>${t('flights.empty')}</p>
      </div>
    `;
    renderSelectionBar(0, [], selection);
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
    renderFlightCard(f, latestPacks[f.id], activeRefreshes[f.id], selectedIds.has(f.id)),
  ).join('');

  let pastSection = '';
  if (past.length > 0) {
    const expandedClass = pastExpanded ? '' : ' collapsed';
    const pastCards = past.map(f =>
      renderFlightCard(f, latestPacks[f.id], activeRefreshes[f.id], selectedIds.has(f.id)),
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
  container.querySelectorAll('.btn-delete').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = (btn as HTMLElement).dataset.id!;
      if (confirm(t('flights.deleteConfirm', { id }))) {
        onDelete(id);
      }
    });
  });
  container.querySelectorAll('.btn-unsubscribe').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = (btn as HTMLElement).dataset.id!;
      if (onUnsubscribe && confirm(t('flights.unsubscribeConfirm'))) {
        onUnsubscribe(id);
      }
    });
  });

  // Wire checkboxes
  container.querySelectorAll('.flight-select-checkbox').forEach((box) => {
    box.addEventListener('change', (e) => {
      e.stopPropagation();
      selection.onToggle((box as HTMLElement).dataset.id!);
    });
    // Don't let the card intercept the click
    box.addEventListener('click', (e) => e.stopPropagation());
  });

  // Subscribed flights aren't bulk-deletable — exclude them from "select all past".
  renderSelectionBar(
    selectedIds.size,
    past.filter(f => f.role !== 'subscriber').map(f => f.id),
    selection,
  );
}

/** Render the floating action bar shown when one or more flights are selected. */
function renderSelectionBar(
  selectedCount: number,
  pastIds: string[],
  selection: SelectionHandlers,
): void {
  let bar = document.getElementById('selection-bar') as HTMLDivElement | null;

  if (selectedCount === 0) {
    bar?.remove();
    return;
  }

  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'selection-bar';
    bar.className = 'selection-bar';
    document.body.appendChild(bar);
  }

  const showSelectPast = pastIds.length > 0;
  bar.innerHTML = `
    <span class="selection-count">${t('flights.selected', { count: selectedCount })}</span>
    <div class="selection-actions">
      ${showSelectPast ? `<button type="button" class="btn btn-outline btn-sm btn-select-past">${t('flights.btnSelectAllPast')}</button>` : ''}
      <button type="button" class="btn btn-outline btn-sm btn-clear-selection">${t('flights.btnClearSelection')}</button>
      <button type="button" class="btn btn-danger btn-sm btn-bulk-delete">${t('flights.btnDeleteSelected')}</button>
    </div>
  `;

  bar.querySelector('.btn-select-past')?.addEventListener('click', () => {
    selection.onSelectAllPast(pastIds);
  });
  bar.querySelector('.btn-clear-selection')?.addEventListener('click', () => {
    selection.onClearSelection();
  });
  bar.querySelector('.btn-bulk-delete')?.addEventListener('click', () => {
    if (confirm(t('flights.bulkDeleteConfirm', { count: selectedCount }))) {
      selection.onBulkDelete();
    }
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
