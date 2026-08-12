/** DOM management for the Flights list page. */

import type { BriefingStatusInfo, CoveragePending, DebriefStats, FlightResponse } from '../store/types';
import { fetchRouteAdvisories, type RefreshEntry } from '../adapters/api-adapter';
import { $, escapeHtml, formatDate, formatDepartureTime, formatAlt, isFlightPast, flightTitle, flightRouteCompact } from '../utils';
import { MAX_QUERY_LEN, matchesQuery, parseQuery } from '../helpers/flight-search';
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

/** Minimum section size before a filter box is worth its row (#542). Below
 *  this a user can just read the list, and the control is pure clutter. */
export const FILTER_MIN_FLIGHTS = 5;

export interface FilterHandlers {
  /** Client-side query over future + recent. */
  upcomingQuery: string;
  /** Server-side query over the paginated past section. */
  pastQuery: string;
  /** A past-filter round-trip is in flight — drives the section's busy state. */
  pastFiltering: boolean;
  onUpcomingQuery: (q: string) => void;
  onPastQuery: (q: string) => void;
}

/** Render the shared filter-box markup used by both sections. */
function renderFilterBox(id: string, value: string, placeholder: string): string {
  const clearLabel = escapeHtml(t('flights.filterClear'));
  return `
    <div class="list-filter">
      <svg class="list-filter-icon" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input type="text" id="${id}" class="list-filter-input" value="${escapeHtml(value)}"
             placeholder="${escapeHtml(placeholder)}" autocomplete="off"
             autocapitalize="characters" spellcheck="false" maxlength="${MAX_QUERY_LEN}">
      <button type="button" class="list-filter-clear"
              title="${clearLabel}" aria-label="${clearLabel}"${value ? '' : ' hidden'}>&times;</button>
    </div>
  `;
}

/** A section-scoped "nothing matched" line. Deliberately not the global
 *  `flights.empty` state — telling someone they have no flights because a
 *  filter is active reads as data loss. */
function renderNoMatch(query: string): string {
  return `
    <div class="list-filter-nomatch">
      <span>${escapeHtml(t('flights.filterNoMatch', { query }))}</span>
      <button type="button" class="btn btn-outline btn-sm btn-clear-filter">${escapeHtml(t('flights.filterNoMatchClear'))}</button>
    </div>
  `;
}

/** Show/hide + update the static upcoming-filter row above the list.
 *
 *  The row lives in index.html rather than in the list container so that a
 *  re-render (which replaces the container's innerHTML) can never destroy the
 *  input while it has focus. Only its value, visibility and count change here.
 *  Listeners are attached once by `wireUpcomingFilter`.
 */
function renderUpcomingFilter(
  filters: FilterHandlers | undefined,
  total: number,
  shown: number,
): void {
  const row = document.getElementById('upcoming-filter-row');
  const input = document.getElementById('upcoming-filter-input') as HTMLInputElement | null;
  const count = document.getElementById('upcoming-filter-count');
  const clear = document.getElementById('upcoming-filter-clear');
  if (!row || !input || !count || !clear) return;

  const query = filters?.upcomingQuery ?? '';
  // Keep the box while a query is live even if it filtered the list below the
  // threshold — otherwise it would disappear from under the cursor.
  const visible = filters != null && (total > FILTER_MIN_FLIGHTS || query.length > 0);
  row.hidden = !visible;
  if (!visible) return;

  input.placeholder = t('flights.filterPlaceholder');
  const clearLabel = t('flights.filterClear');
  clear.setAttribute('title', clearLabel);
  clear.setAttribute('aria-label', clearLabel);
  // Don't clobber what the user is typing; the store echoes it back verbatim.
  if (document.activeElement !== input && input.value !== query) input.value = query;
  clear.hidden = query.length === 0;
  count.textContent = query ? t('flights.filterCount', { shown, total }) : '';
}

/** Debounce for the past filter. The upcoming filter needs none (it re-renders
 *  from memory); the past filter issues a request per keystroke, so it waits
 *  for a typing pause. */
const PAST_QUERY_DEBOUNCE_MS = 250;
let pastQueryTimer: ReturnType<typeof setTimeout> | undefined;

function schedulePastQuery(value: string, onQuery: (q: string) => void): void {
  if (pastQueryTimer) clearTimeout(pastQueryTimer);
  pastQueryTimer = setTimeout(() => {
    pastQueryTimer = undefined;
    onQuery(value.trim());
  }, PAST_QUERY_DEBOUNCE_MS);
}

/** Apply immediately, cancelling any pending debounce (Escape / clear button —
 *  a queued keystroke must not resurrect the query the user just cleared). */
function flushPastQuery(value: string, onQuery: (q: string) => void): void {
  if (pastQueryTimer) clearTimeout(pastQueryTimer);
  pastQueryTimer = undefined;
  onQuery(value.trim());
}

/** Attach the upcoming-filter listeners. Called once at page init — the input
 *  is static markup, so unlike the past box it is never re-created. */
export function wireUpcomingFilter(onQuery: (q: string) => void): void {
  const input = document.getElementById('upcoming-filter-input') as HTMLInputElement | null;
  const clear = document.getElementById('upcoming-filter-clear');
  if (!input) return;

  // Set here rather than hardcoded in the markup so MAX_QUERY_LEN stays the
  // single source of truth across the HTML, the TS helper and the Python
  // matcher (the past box already renders it from the constant).
  input.maxLength = MAX_QUERY_LEN;

  input.addEventListener('input', () => onQuery(input.value.trim()));
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') ev.preventDefault();
    if (ev.key === 'Escape') {
      ev.preventDefault();
      input.value = '';
      onQuery('');
    }
  });
  clear?.addEventListener('click', () => {
    input.value = '';
    onQuery('');
    input.focus();
  });
}

interface FilterState {
  id: string;
  /** Live DOM value, which during the debounce window is *ahead* of the store. */
  value: string;
  focused: boolean;
  start: number | null;
  end: number | null;
}

/** Snapshot the in-container filter input before an innerHTML swap wipes it.
 *
 *  The value matters as much as the caret: the input is re-rendered from the
 *  last *committed* `pastQuery`, so a keystroke still inside the 250ms debounce
 *  window isn't in the store yet. Any unrelated re-render in that window —
 *  active-refresh polling picking up a status transition, a post-debrief
 *  reload, a selection change — would otherwise roll the box back to the
 *  previous query and the user watches their typing vanish (or, if they keep
 *  going, type into a half-reverted string).
 *
 *  Captured whether or not the input has focus, so a click that triggers a
 *  re-render mid-debounce doesn't discard the draft either.
 */
function captureFilterState(container: HTMLElement): FilterState | null {
  const input = container.querySelector('.list-filter-input') as HTMLInputElement | null;
  if (!input || !input.id) return null;
  const focused = document.activeElement === input;
  return {
    id: input.id,
    value: input.value,
    focused,
    start: focused ? input.selectionStart : null,
    end: focused ? input.selectionEnd : null,
  };
}

function restoreFilterState(state: FilterState | null): void {
  if (!state) return;
  const input = document.getElementById(state.id) as HTMLInputElement | null;
  if (!input) return;
  if (input.value !== state.value) input.value = state.value;
  if (!state.focused) return;
  input.focus();
  if (state.start != null && state.end != null) {
    try {
      input.setSelectionRange(state.start, state.end);
    } catch {
      // Some input types reject setSelectionRange; focus alone is enough.
    }
  }
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
  matchTokens: string[] = [],
): string {
  const wps = f.waypoints.length > 0
    ? f.waypoints
    : f.route_name.split('_').map(w => w.toUpperCase());
  const title = flightTitle(wps);
  // Tokens make the compact route keep (and bold) the waypoints that matched
  // the filter, so a hit on an intermediate fix isn't swallowed by the "…".
  const compactRoute = wps.length > 2 ? flightRouteCompact(wps, 4, matchTokens) : null;
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
  /** Drop selections whose row the active filter is hiding (#542). */
  onPruneSelection: (visibleIds: string[]) => void;
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
  filters?: FilterHandlers,
): void {
  const container = $('flight-list');
  if (!container) return;

  // The past filter input lives inside this container, so a re-render destroys
  // it. Capture focus + caret first and restore after the innerHTML swap,
  // otherwise typing in it loses focus after the first debounced keystroke.
  const focusState = captureFilterState(container);

  // A filter that matches nothing must never reach the global empty state —
  // "No flights yet" while a query is active reads as data loss. Those cases
  // fall through to the per-section no-match lines below.
  const filterActive = !!(filters?.upcomingQuery || filters?.pastQuery);

  if (flights.length === 0 && !filterActive) {
    // Don't claim "no flights" until the list has actually been fetched —
    // otherwise the empty-state flashes while the user's flights are still
    // downloading. The loading spinner covers the pre-fetch window.
    if (!loaded) {
      container.innerHTML = '';
      renderUpcomingFilter(filters, 0, 0);
      renderSelectionBar(0, [], [], selection);
      return;
    }
    container.innerHTML = `
      <div class="empty-state">
        <p>${t('flights.empty')}</p>
      </div>
    `;
    renderUpcomingFilter(filters, 0, 0);
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

  // --- Upcoming filter (client-side over future + recent) ---
  const upcomingQuery = filters?.upcomingQuery ?? '';
  const upcomingTokens = parseQuery(upcomingQuery);
  const upcomingTotal = future.length + recent.length;
  const keep = (f: FlightResponse) =>
    matchesQuery(f.waypoints, f.route_name, upcomingTokens);
  const futureShown = upcomingTokens.length > 0 ? future.filter(keep) : future;
  const recentShown = upcomingTokens.length > 0 ? recent.filter(keep) : recent;
  renderUpcomingFilter(filters, upcomingTotal, futureShown.length + recentShown.length);

  const futureCards = futureShown.map(f =>
    renderFlightCard(f, activeRefreshes[f.id], selectedIds.has(f.id), upcomingTokens),
  ).join('');

  let recentSection = '';
  // A section with no matches hides entirely rather than rendering "(0)".
  if (recentShown.length > 0) {
    const expandedClass = recentExpanded ? '' : ' collapsed';
    const recentCards = recentShown.map(f =>
      renderFlightCard(f, activeRefreshes[f.id], selectedIds.has(f.id), upcomingTokens),
    ).join('');
    recentSection = `
      <div class="recent-flights-section${expandedClass}">
        <button class="recent-flights-toggle" id="recent-flights-toggle">
          ${t('debrief.recentHeader')} (${recentShown.length})
        </button>
        <div class="recent-flights-list">${recentCards}</div>
      </div>
    `;
  }

  // Upcoming filter that matched nothing: say so where the cards would be.
  const upcomingNoMatch = upcomingTokens.length > 0 && futureShown.length === 0 && recentShown.length === 0
    ? renderNoMatch(upcomingQuery)
    : '';

  const statsSection = stats
    ? `<div class="debrief-stats-section">${renderDebriefStats(stats)}</div>`
    : '';

  // --- Past section (filter is server-side: see store.setPastQuery) ---
  const pastQuery = filters?.pastQuery ?? '';
  const pastTokens = parseQuery(pastQuery);
  let pastSection = '';
  // Under an active filter the section must survive a zero-match result —
  // otherwise the box you are typing into vanishes on the first miss.
  if (past.length > 0 || pastTokens.length > 0) {
    const expandedClass = pastExpanded ? '' : ' collapsed';
    const pastCards = past.map(f =>
      renderFlightCard(f, activeRefreshes[f.id], selectedIds.has(f.id), pastTokens),
    ).join('');
    // The past section is paginated: the header shows the full count while
    // only `past.length` rows are loaded. Under a filter the server reports
    // the number of *matches*, so both the header and "show more" describe the
    // filtered set rather than the whole history.
    const totalPast = Math.max(pastTotal, past.length);
    const remaining = totalPast - past.length;
    const showMoreBtn = remaining > 0
      ? `<button type="button" class="btn btn-outline btn-show-more-past" id="show-more-past">${t('flights.showMorePast', { count: remaining })}</button>`
      : '';
    // The past filter belongs to the open section, but it is *rendered*
    // unconditionally and hidden by CSS when collapsed — the toggle only flips
    // a class (no re-render), so a render-time `pastExpanded` check would leave
    // the box missing until something else re-rendered the list.
    const showPastFilter = filters != null
      && (totalPast > FILTER_MIN_FLIGHTS || pastTokens.length > 0);
    const pastFilter = showPastFilter
      ? `<div class="list-filter-row list-filter-row-inset">${renderFilterBox('past-filter-input', pastQuery, t('flights.filterPastPlaceholder'))}</div>`
      : '';
    const pastBody = past.length === 0 && pastTokens.length > 0
      ? renderNoMatch(pastQuery)
      : `<div class="past-flights-list">${pastCards}</div>${showMoreBtn}`;
    const busyClass = filters?.pastFiltering ? ' is-filtering' : '';
    pastSection = `
      <div class="past-flights-section${expandedClass}${busyClass}" aria-busy="${filters?.pastFiltering ? 'true' : 'false'}">
        <button class="past-flights-toggle" id="past-flights-toggle">
          ${t('flights.past', { count: totalPast })}
        </button>
        ${pastFilter}
        ${pastBody}
      </div>
    `;
  }

  container.innerHTML = futureCards + upcomingNoMatch + recentSection + statsSection + pastSection;

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

  // --- Past filter (#542) ---
  // Re-wired on every render because the input is inside this container.
  if (filters) {
    const pastInput = document.getElementById('past-filter-input') as HTMLInputElement | null;
    if (pastInput) {
      pastInput.addEventListener('input', () => {
        // Debounced: each keystroke is a server round-trip.
        schedulePastQuery(pastInput.value, filters.onPastQuery);
      });
      pastInput.addEventListener('keydown', (ev) => {
        const e = ev as KeyboardEvent;
        // Both boxes sit outside create-flight-form, so Enter has nothing to
        // submit — but stop it explicitly so that stays true if markup moves.
        if (e.key === 'Enter') e.preventDefault();
        if (e.key === 'Escape') {
          e.preventDefault();
          pastInput.value = '';
          flushPastQuery('', filters.onPastQuery);
        }
      });
    }
    container.querySelectorAll('.list-filter-clear').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (pastInput) pastInput.value = '';
        flushPastQuery('', filters.onPastQuery);
      });
    });
    // "Clear" inside a no-match line: clears whichever filter owns that block.
    container.querySelectorAll('.btn-clear-filter').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.closest('.past-flights-section')) {
          flushPastQuery('', filters.onPastQuery);
        } else {
          filters.onUpcomingQuery('');
        }
      });
    });
  }

  restoreFilterState(focusState);

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
  // Only *visible* rows are selectable: "Select all" followed by "Delete
  // selected" must never reach a flight the active filter is hiding. `past` is
  // already the filtered set (the server applied past_q), so it needs no
  // further narrowing here.
  //
  // Scoping select-all isn't sufficient on its own: a row ticked *before* the
  // filter was applied stays in `selectedIds` with its checkbox unrendered, so
  // it can't be unticked while the filter is active and bulk-delete would still
  // take it. Prune those, but only while a filter is actually active — with no
  // filter, `past` is merely the loaded page and pruning would silently drop
  // selections belonging to not-yet-loaded pages.
  let effectiveSelected = selectedIds;
  if (filterActive && selectedIds.size > 0) {
    const visible = new Set([...futureShown, ...recentShown, ...past].map(f => f.id));
    if ([...selectedIds].some(id => !visible.has(id))) {
      // Apply locally for the rest of *this* render, and push to the store on a
      // microtask. Calling the store inline would update it synchronously,
      // re-entering this function; the inner pass would draw the correct bar
      // and then the outer pass would carry on and repaint it from its stale
      // `selectedIds`, resurrecting the very selection we just dropped.
      effectiveSelected = new Set([...selectedIds].filter(id => visible.has(id)));
      queueMicrotask(() => selection.onPruneSelection([...visible]));
    }
  }

  const active = [...futureShown, ...recentShown];
  const selectableActive = active.filter(f => f.role !== 'subscriber').map(f => f.id);
  const selectablePast = past.filter(f => f.role !== 'subscriber').map(f => f.id);
  renderSelectionBar(
    effectiveSelected.size,
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
