/** DOM management for the Flights list page. */

import type { BriefingStatusInfo, CoveragePending, DebriefStats, FlightResponse } from '../store/types';
import { fetchRouteAdvisories, type RefreshEntry } from '../adapters/api-adapter';
import { $, escapeHtml, formatDate, formatDepartureTime, formatAlt, isFlightPast, flightTitle, flightRouteCompact } from '../utils';
import { t, getDateLocale } from '../i18n/i18n';
import { renderDebriefForm } from '../components/debrief-form';
import { renderDebriefPill, renderDebriefSummary } from '../components/debrief-summary';
import { renderDebriefStats } from '../components/debrief-stats';
import { flaggedTagsFromAdvisories } from '../components/debrief-taxonomy';
import { showRoutePopup } from '../components/route-interpret';

/** Assessment badge color class. */
function assessmentClass(assessment: string | null): string {
  if (!assessment) return 'badge-none';
  switch (assessment.toUpperCase()) {
    case 'GREEN': return 'badge-green';
    case 'AMBER': return 'badge-amber';
    case 'RED': return 'badge-red';
    // A real verdict ("we could not assess this"), so it carries the bordered
    // badge rather than badge-none, which means "no verdict at all" (#392).
    case 'UNAVAILABLE': return 'badge-unavailable';
    default: return 'badge-none';
  }
}

// Long-range early outlook → soft badge class. Distinct from the GREEN/AMBER/RED
// traffic light: an outlook is a tendency ("what to expect"), not a verdict.
const OUTLOOK_BADGE_CLASS: Record<string, string> = {
  TRENDING_SETTLED: 'badge-outlook-settled',
  MIXED_SIGNALS: 'badge-outlook-mixed',
  TRENDING_UNSETTLED: 'badge-outlook-unsettled',
};

/** Status badge for a flight's latest briefing: long-range outlook badge when
 *  present, otherwise the GREEN/AMBER/RED assessment chip. */
function statusBadge(lb: BriefingStatusInfo): string {
  if (lb.outlook) {
    const key = lb.outlook.toUpperCase();
    const cls = OUTLOOK_BADGE_CLASS[key] ?? 'badge-outlook-mixed';
    const label = t(`outlook.${lb.outlook.toLowerCase()}`);
    return `<span class="badge badge-outlook ${cls}" title="${escapeHtml(t('outlook.early'))}">${escapeHtml(label)}</span>`;
  }
  return `<span class="badge ${assessmentClass(lb.assessment)}">${escapeHtml(lb.assessment || '—')}</span>`;
}

/** Neutral "pending · available dd/mm" chip for a flight saved beyond the
 *  forecast horizon. Replaces the traffic-light/outlook badge and the
 *  "no briefings" text — there is no assessment to show yet, only the date
 *  weather coverage begins. */
function pendingCoverageChip(cov: CoveragePending): string {
  const d = new Date(cov.available_date + 'T00:00:00Z');
  const dateStr = d.toLocaleDateString(getDateLocale(), { day: 'numeric', month: 'short', timeZone: 'UTC' });
  const label = t('flights.pendingAvailable', { date: dateStr });
  return `<span class="badge badge-pending" title="${escapeHtml(t('flights.pendingTitle'))}">${escapeHtml(label)}</span>`;
}

// Severity → badge color class + single-letter badge, matching the briefing
// page's advisory chips (briefing-ui.ts renderAdvisoryChips) for consistency.
const ADV_CHIP_CLASS: Record<string, string> = { RED: 'badge-red', AMBER: 'badge-amber' };
const ADV_CHIP_LETTER: Record<string, string> = { RED: 'R', AMBER: 'A' };

/** Per-flight advisory summary chips for the card's right side.
 *
 * An attention-director ("what to look at"), not a verdict — it complements
 * the GREEN/AMBER/RED status badge. Renders nothing when the flight has no
 * advisories or the summary is empty (all-green / old packs without the
 * denormalized column). */
function advisorySummaryHtml(lb: BriefingStatusInfo): string {
  if (!lb.has_advisories) return '';
  const summary = lb.advisory_summary;
  if (!summary || (summary.red === 0 && summary.amber === 0)) return '';

  const counts: string[] = [];
  if (summary.red > 0) {
    counts.push(`<span class="badge badge-red">${t('flights.advRedCount', { count: summary.red })}</span>`);
  }
  if (summary.amber > 0) {
    counts.push(`<span class="badge badge-amber">${t('flights.advAmberCount', { count: summary.amber })}</span>`);
  }

  const chips = summary.top.map(chip => {
    const cls = ADV_CHIP_CLASS[chip.status] ?? 'badge-none';
    const letter = ADV_CHIP_LETTER[chip.status] ?? '';
    return `<span class="adv-chip"><span class="badge ${cls}">${letter}</span> ${escapeHtml(chip.name)}</span>`;
  }).join('');

  // Concerns beyond the 3 shown rows — counts already convey the totals, this
  // just makes the overflow explicit ("+2 more"). Severity is in the counts.
  const moreCount = summary.red + summary.amber - summary.top.length;
  const more = moreCount > 0
    ? `<span class="adv-more">${escapeHtml(t('flights.advMore', { count: moreCount }))}</span>`
    : '';

  const label = t('flights.advSummaryTitle');
  return `<div class="flight-advisory-summary" title="${escapeHtml(label)}" role="group" aria-label="${escapeHtml(label)}">
    <div class="adv-counts">${counts.join(' ')}</div>
    <div class="adv-chips">${chips}${more}</div>
  </div>`;
}

/** Track whether the past-flights section is expanded. */
let pastExpanded = false;

/** Recent section starts expanded — it's the nudge to debrief. */
let recentExpanded = true;

/** Render a single flight card. */
function renderFlightCard(
  f: FlightResponse,
  refreshEntry: RefreshEntry | undefined,
  selected: boolean,
): string {
  const wps = f.waypoints.length > 0
    ? f.waypoints
    : f.route_name.split('_').map(w => w.toUpperCase());
  const title = flightTitle(wps);
  const compactRoute = wps.length > 2 ? flightRouteCompact(wps) : null;
  const past = isFlightPast(f.target_date, f.target_time_utc, f.flight_duration_hours, f.departure_time);
  const pastBadge = past ? `<span class="badge badge-past">${t('flights.pastBadge')}</span> ` : '';

  // Red "unseen" dot: this flight has a notify-qualifying briefing update the
  // pilot hasn't opened yet (same server predicate as the app-icon badge).
  // Clears naturally on the next flights load after the briefing is opened.
  const unseenLabel = t('flights.unseenDot');
  const unseenDot = f.latest_briefing?.unseen
    ? `<span class="unseen-dot" role="img" title="${escapeHtml(unseenLabel)}" aria-label="${escapeHtml(unseenLabel)}"></span>`
    : '';

  const isShared = f.role === 'subscriber';
  // When owner_display_name is null (no display_name set on the owner),
  // fall back to the generic shared-flight label instead of rendering
  // "Shared by " with a dangling trailing space.
  const sharedBadgeTitle = f.owner_display_name
    ? t('flights.sharedBadgeTitle', { owner: escapeHtml(f.owner_display_name) })
    : t('flightDetail.sharedByUnknown');
  const sharedBadge = isShared
    ? `<span class="badge badge-shared" title="${sharedBadgeTitle}">${t('flights.sharedBadge')}</span> `
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

  // Card status reads from the flight's inline latest_briefing \u2014 same three
  // fields the old per-flight /packs/latest call surfaced, with no extra round-trip.
  // A flight saved beyond the forecast horizon has no pack yet: show the neutral
  // pending-coverage chip instead of an assessment or "no briefings".
  const lb = f.latest_briefing;
  let packInfo: string;
  if (f.coverage) {
    packInfo = pendingCoverageChip(f.coverage);
  } else if (lb && lb.fetch_timestamp) {
    packInfo = `<span class="pack-info">D-${lb.days_out} (${new Date(lb.fetch_timestamp).toLocaleDateString(getDateLocale(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC)</span>
       ${statusBadge(lb)}`;
  } else {
    packInfo = `<span class="pack-info">${t('flights.noBriefings')}</span>`;
  }

  const routeLine = compactRoute
    ? `<div class="flight-route-detail">
         <span class="route-clickable" role="button" tabindex="0" data-flight-route="${escapeHtml(f.id)}" title="${escapeHtml(compactRoute.fullText)}" aria-label="Show full route on map">${compactRoute.html}<span class="route-info-icon" aria-hidden="true">ⓘ</span></span>
       </div>`
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

  // Owner-only debrief pill (read-only summary). Only shown for past flights
  // — future ones can't have been flown yet.
  let debriefPill = '';
  if (!isShared && past && f.debrief) {
    debriefPill = ` ${renderDebriefPill(f.debrief)}`;
  }

  // Recent-section CTA: clicking expands an inline form below the row.
  // Only rendered for owner-past-undebriefed flights in the recent section.
  const isRecent = f.section === 'recent';
  const debriefAction = isRecent && !isShared && !f.debrief
    ? `<button class="btn btn-secondary btn-debrief" data-id="${escapeHtml(f.id)}">${t('debrief.openCta')}</button>`
    : '';

  return `
    <div class="flight-card${selectedClass}${isShared ? ' flight-card-shared' : ''}" data-id="${escapeHtml(f.id)}">
      ${selectControl}
      <div class="flight-card-main">
        <div class="flight-card-body">
          <div class="flight-header">
            ${sharedBadge}${pastBadge}${unseenDot}<span class="flight-route">${escapeHtml(title)}</span>
            <span class="flight-date">${formatDate(f.target_date)} ${formatDepartureTime(f.departure_time)}</span>
            <span class="flight-alt">${formatAlt(f.cruise_altitude_ft)}</span>${debriefPill}
          </div>
          ${ownerLine}
          ${routeLine}
          <div class="flight-status">
            ${refreshBadge}${packInfo}
          </div>
          <div class="flight-actions">
            <button class="btn btn-primary btn-briefing" data-id="${escapeHtml(f.id)}">${t('flights.btnBriefing')}</button>
            ${debriefAction}
            ${ownerOnlyActions}
          </div>
          <div class="debrief-host" data-flight-id="${escapeHtml(f.id)}"></div>
        </div>
        ${lb ? advisorySummaryHtml(lb) : ''}
      </div>
    </div>
  `;
}

// --- Render functions ---

export interface SelectionHandlers {
  onToggle: (id: string) => void;
  onSelectAll: (allIds: string[]) => void;
  onSelectAllPast: (pastIds: string[]) => void;
  onBulkDelete: () => void;
  onClearSelection: () => void;
}

export function renderFlightList(
  flights: FlightResponse[],
  activeRefreshes: Record<string, RefreshEntry>,
  selectedIds: Set<string>,
  pastTotal: number,
  loaded: boolean,
  onBriefing: (id: string) => void,
  onEdit: (id: string) => void,
  onDelete: (id: string) => void,
  selection: SelectionHandlers,
  onShowMorePast: () => void,
  onUnsubscribe?: (id: string) => void,
  stats?: DebriefStats | null,
  onDebriefChanged?: () => void,
): void {
  const container = $('flight-list');
  if (!container) return;

  if (flights.length === 0) {
    // Don't claim "no flights" until the list has actually been fetched —
    // otherwise the empty-state flashes while the user's flights are still
    // downloading. The loading spinner covers the pre-fetch window.
    if (!loaded) {
      container.innerHTML = '';
      renderSelectionBar(0, [], [], selection);
      return;
    }
    container.innerHTML = `
      <div class="empty-state">
        <p>${t('flights.empty')}</p>
      </div>
    `;
    renderSelectionBar(0, [], [], selection);
    return;
  }

  // Bucket by server-supplied section (falls back to past/active split for
  // older API responses missing the field).
  const future: FlightResponse[] = [];
  const recent: FlightResponse[] = [];
  const past: FlightResponse[] = [];
  for (const f of flights) {
    const s = f.section
      ?? (isFlightPast(f.target_date, f.target_time_utc, f.flight_duration_hours, f.departure_time)
        ? 'past' : 'future');
    if (s === 'future') future.push(f);
    else if (s === 'recent') recent.push(f);
    else past.push(f);
  }

  const futureCards = future.map(f =>
    renderFlightCard(f, activeRefreshes[f.id], selectedIds.has(f.id)),
  ).join('');

  let recentSection = '';
  if (recent.length > 0) {
    const expandedClass = recentExpanded ? '' : ' collapsed';
    const recentCards = recent.map(f =>
      renderFlightCard(f, activeRefreshes[f.id], selectedIds.has(f.id)),
    ).join('');
    recentSection = `
      <div class="recent-flights-section${expandedClass}">
        <button class="recent-flights-toggle" id="recent-flights-toggle">
          ${t('debrief.recentHeader')} (${recent.length})
        </button>
        <div class="recent-flights-list">${recentCards}</div>
      </div>
    `;
  }

  const statsSection = stats
    ? `<div class="debrief-stats-section">${renderDebriefStats(stats)}</div>`
    : '';

  let pastSection = '';
  if (past.length > 0) {
    const expandedClass = pastExpanded ? '' : ' collapsed';
    const pastCards = past.map(f =>
      renderFlightCard(f, activeRefreshes[f.id], selectedIds.has(f.id)),
    ).join('');
    // The past section is paginated: the header shows the full count while
    // only `past.length` rows are loaded. "Show more" appears while more
    // remain to be fetched.
    const totalPast = Math.max(pastTotal, past.length);
    const remaining = totalPast - past.length;
    const showMoreBtn = remaining > 0
      ? `<button type="button" class="btn btn-outline btn-show-more-past" id="show-more-past">${t('flights.showMorePast', { count: remaining })}</button>`
      : '';
    pastSection = `
      <div class="past-flights-section${expandedClass}">
        <button class="past-flights-toggle" id="past-flights-toggle">
          ${t('flights.past', { count: totalPast })}
        </button>
        <div class="past-flights-list">${pastCards}</div>
        ${showMoreBtn}
      </div>
    `;
  }

  container.innerHTML = futureCards + recentSection + statsSection + pastSection;

  // Wire toggle
  const toggleBtn = document.getElementById('past-flights-toggle');
  toggleBtn?.addEventListener('click', () => {
    pastExpanded = !pastExpanded;
    const section = toggleBtn.closest('.past-flights-section');
    section?.classList.toggle('collapsed', !pastExpanded);
  });

  const showMoreBtn = document.getElementById('show-more-past');
  showMoreBtn?.addEventListener('click', () => onShowMorePast());

  const recentToggleBtn = document.getElementById('recent-flights-toggle');
  recentToggleBtn?.addEventListener('click', () => {
    recentExpanded = !recentExpanded;
    const section = recentToggleBtn.closest('.recent-flights-section');
    section?.classList.toggle('collapsed', !recentExpanded);
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

  // Wire route popup (click or Enter/Space on the compact route span)
  const openRoutePopup = (id: string) => {
    const flight = flights.find(f => f.id === id);
    if (!flight) return;
    void showRoutePopup(
      { waypoints: flight.waypoints, rawRoute: flight.raw_route },
      (msg) => renderError(msg),
    );
  };
  container.querySelectorAll('[data-flight-route]').forEach((el) => {
    const id = (el as HTMLElement).dataset.flightRoute!;
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      openRoutePopup(id);
    });
    el.addEventListener('keydown', (e) => {
      const ke = e as KeyboardEvent;
      if (ke.key === 'Enter' || ke.key === ' ') {
        ke.preventDefault();
        openRoutePopup(id);
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

  // Wire debrief CTAs (Recent section).
  container.querySelectorAll('.btn-debrief').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = (btn as HTMLElement).dataset.id!;
      const flight = flights.find(f => f.id === id);
      if (!flight) return;
      const card = (btn as HTMLElement).closest('.flight-card');
      const host = card?.querySelector('.debrief-host') as HTMLElement | null;
      if (!host) return;
      // Toggle: if already open, close it.
      if (host.dataset.open === '1') {
        host.dataset.open = '';
        host.innerHTML = '';
        return;
      }
      host.dataset.open = '1';
      host.innerHTML = `<div class="debrief-loading">${escapeHtml(t('debrief.loading'))}</div>`;
      // Lazy-fetch advisories for the latest pack so the form knows which
      // categories to surface as outcome questions. The flight's inline
      // latest_briefing already tells us whether advisories exist and at which
      // timestamp, so we only hit the network when there's something to fetch.
      // Empty list if the pack has no advisories or the call fails.
      const lb = flight.latest_briefing;
      let flaggedCategories: ReturnType<typeof flaggedTagsFromAdvisories> = [];
      if (lb && lb.has_advisories && lb.fetch_timestamp) {
        try {
          const manifest = await fetchRouteAdvisories(id, lb.fetch_timestamp);
          flaggedCategories = flaggedTagsFromAdvisories(manifest);
        } catch {
          flaggedCategories = [];
        }
      }
      // Bail if user closed it while we were fetching.
      if (host.dataset.open !== '1') return;
      renderDebriefForm(host, {
        existing: flight.debrief ?? null,
        flaggedCategories,
        onSaved: () => { host.dataset.open = ''; host.innerHTML = ''; onDebriefChanged?.(); },
        onDeleted: () => { host.dataset.open = ''; host.innerHTML = ''; onDebriefChanged?.(); },
        onCancelled: () => { host.dataset.open = ''; host.innerHTML = ''; },
      });
    });
  });

  // Subscribed flights aren't bulk-deletable — exclude them from "select all"/"select all past".
  // "Active" here = future + recent (everything pre-departure plus the recent
  // undebriefed window the pilot can still act on).
  const active = [...future, ...recent];
  const selectableActive = active.filter(f => f.role !== 'subscriber').map(f => f.id);
  const selectablePast = past.filter(f => f.role !== 'subscriber').map(f => f.id);
  renderSelectionBar(
    selectedIds.size,
    [...selectableActive, ...selectablePast],
    selectablePast,
    selection,
  );
}

/** Render the floating action bar shown when one or more flights are selected. */
function renderSelectionBar(
  selectedCount: number,
  allIds: string[],
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

  const showSelectAll = allIds.length > 0;
  const showSelectPast = pastIds.length > 0;
  bar.innerHTML = `
    <span class="selection-count">${t('flights.selected', { count: selectedCount })}</span>
    <div class="selection-actions">
      ${showSelectAll ? `<button type="button" class="btn btn-outline btn-sm btn-select-all">${t('flights.btnSelectAll')}</button>` : ''}
      ${showSelectPast ? `<button type="button" class="btn btn-outline btn-sm btn-select-past">${t('flights.btnSelectAllPast')}</button>` : ''}
      <button type="button" class="btn btn-outline btn-sm btn-clear-selection">${t('flights.btnClearSelection')}</button>
      <button type="button" class="btn btn-danger btn-sm btn-bulk-delete">${t('flights.btnDeleteSelected')}</button>
    </div>
  `;

  bar.querySelector('.btn-select-all')?.addEventListener('click', () => {
    selection.onSelectAll(allIds);
  });
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
