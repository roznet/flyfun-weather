/** DOM management for the Flights list page. */

import type { ArchiveFacets, ArchiveRow, BriefingStatusInfo, CoveragePending, DebriefStats, FlightResponse, TripResponse, TripSummary } from '../store/types';
import { EMPTY_PAST_FILTER, HAZARD_CHIPS, OUTCOME_CHIPS, pastFilterActive, toggleValue, type PastFilterState } from '../helpers/past-archive';
import type { RefreshEntry } from '../adapters/api-adapter';
import { $, escapeHtml, formatDate, formatDepartureTime, formatAlt, isFlightPast, flightTitle, flightRouteCompact } from '../utils';
import { MAX_QUERY_LEN, matchesQuery, parseQuery } from '../helpers/flight-search';
import { buildTripSelection, type TripSelectionContext } from '../helpers/trip-selection';
import { tripCardRefresh } from '../helpers/trip-refresh-indicator';
import { tripHeading } from '../helpers/trip-heading';
import { actionGroupStart, selectionBarButtons } from '../helpers/selection-bar-order';
import { assessmentClass, outlookClass } from '../helpers/assessment-badges';
import { t, getDateLocale } from '../i18n/i18n';
import { renderDebriefPill, renderDebriefSummary } from '../components/debrief-summary';
import { renderDebriefStats } from '../components/debrief-stats';
import { showRoutePopup } from '../components/route-interpret';

/** Status badge for a flight's latest briefing: long-range outlook badge when
 *  present, otherwise the GREEN/AMBER/RED assessment chip. */
function statusBadge(lb: BriefingStatusInfo): string {
  if (lb.outlook) {
    const key = lb.outlook.toUpperCase();
    const cls = outlookClass(key);
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
  /** Past search + chips (#1032), applied by the archive endpoint. */
  pastFilter: PastFilterState;
  /** A past-filter round-trip is in flight — drives the section's busy state. */
  pastFiltering: boolean;
  /** The loaded past rows (archive, owned flights only). */
  pastRows: ArchiveRow[];
  /** Server chip counts for the current past filter. */
  pastFacets: ArchiveFacets | null;
  onUpcomingQuery: (q: string) => void;
  onPastQuery: (q: string) => void;
  onPastFilter: (patch: Partial<PastFilterState>) => void;
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

// --- Trip cards (#602) ---

/** Which trip cards the pilot has opened.
 *
 * Per-trip rather than the single boolean the past/recent sections use, and
 * persisted so a reload doesn't re-collapse what was opened. Default is
 * **collapsed**: the collapsed card already carries the binding-leg chip, so
 * auto-expanding a red trip would add noise rather than information — and make
 * the list jump around as forecasts change. Remember the choice instead. */
const TRIP_EXPANDED_KEY = 'flights.tripsExpanded';

function loadExpandedTrips(): Set<string> {
  try {
    const raw = localStorage.getItem(TRIP_EXPANDED_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

let tripExpanded: Set<string> = loadExpandedTrips();

function persistExpandedTrips(): void {
  try {
    localStorage.setItem(TRIP_EXPANDED_KEY, JSON.stringify([...tripExpanded]));
  } catch {
    // Private mode / storage disabled — expansion just doesn't survive a reload.
  }
}

/** Binding-leg chip for the collapsed trip card.
 *
 * Renders the server's computed binding leg — never a colour for the trip as a
 * whole. When the binding leg was picked on a long-range *outlook* rather than
 * a traffic light, it shows the soft outlook badge, because folding an outlook
 * into a traffic light is exactly how the signal dies. */
function bindingChip(summary: TripSummary): string {
  const leg = summary.legs.find(l => l.flight_id === summary.binding_leg_id);
  if (!leg) {
    if (summary.remaining_legs === 0) return '';
    return `<span class="badge badge-none">${escapeHtml(t('trips.noGrade'))}</span>`;
  }
  const badge = summary.binding_basis === 'outlook'
    ? `<span class="badge badge-outlook ${outlookClass(leg.outlook)}">${escapeHtml(t(`outlook.${(leg.outlook || '').toLowerCase()}`))}</span>`
    : `<span class="badge ${assessmentClass(leg.assessment)}">${escapeHtml(leg.assessment || '—')}</span>`;
  const days = leg.days_out != null ? ` <span class="pack-info">D-${leg.days_out}</span>` : '';
  return `<span class="trip-binding" title="${escapeHtml(summary.headline)}">${escapeHtml(t('trips.decidedBy', { leg: leg.label }))} ${badge}${days}</span>`;
}

/** One collapsible trip card, holding its future/recent member legs.
 *
 * Placement and scope, both deliberate:
 *
 * - The card sits in the section of its earliest un-flown leg, which keeps the
 *   trip (not the leg) as the unit of attention and resolves the straddle case
 *   with one rule instead of a rendering special case.
 * - It holds only the **future + recent** members. The past section is
 *   server-paginated and server-filtered, so a past leg pulled into this card
 *   could vanish or duplicate depending on which page is loaded; past legs keep
 *   rendering individually with a trip badge instead. The "n of m legs ahead"
 *   count comes from the server's leg total, so it stays honest either way, and
 *   the trip page shows the whole chain including flown legs. */
function renderTripCard(
  trip: TripResponse,
  members: FlightResponse[],
  activeRefreshes: Record<string, RefreshEntry>,
  selectedIds: Set<string>,
  matchTokens: string[],
): string {
  const summary = trip.summary;
  const expanded = tripExpanded.has(trip.id);
  const dates = summary.legs.length > 0
    ? `${formatDate(summary.legs[0].departure_time.slice(0, 10))} – ${formatDate(summary.legs[summary.legs.length - 1].departure_time.slice(0, 10))}`
    : '';
  const ahead = t('trips.legsAhead', {
    remaining: summary.remaining_legs,
    total: summary.total_legs,
  });
  // Covers a member leg refreshed outside a trip run, which a collapsed card
  // would otherwise report as idle. Rule and rationale in the helper.
  const indicator = tripCardRefresh(trip, members.map(m => m.id), activeRefreshes);
  let refreshing = '';
  if (indicator?.kind === 'trip') {
    refreshing = `<span class="badge badge-refreshing">${escapeHtml(indicator.message || t('trips.refreshing'))}<span class="dots-spinner"></span></span>`;
  } else if (indicator?.kind === 'leg') {
    const label = indicator.status === 'queued'
      ? t('flights.queuedBadge') : t('flights.refreshingBadge');
    const spinner = indicator.status === 'refreshing' ? '<span class="dots-spinner"></span>' : '';
    refreshing = `<span class="badge badge-refreshing">${escapeHtml(label)}${spinner}</span>`;
  }
  const heading = tripHeading(trip);
  const cards = members.map(f =>
    renderFlightCard(f, activeRefreshes[f.id], selectedIds.has(f.id), matchTokens),
  ).join('');

  return `
    <div class="trip-card${expanded ? '' : ' collapsed'}" data-trip-id="${escapeHtml(trip.id)}">
      <div class="trip-card-header">
        <button type="button" class="trip-toggle" data-trip-toggle="${escapeHtml(trip.id)}"
                aria-expanded="${expanded ? 'true' : 'false'}">
          <span class="trip-heading">
            <span class="trip-chain">${escapeHtml(heading.title)}</span>
            <span class="trip-meta">
              ${heading.subtitle ? `<span class="trip-chain-sub">${escapeHtml(heading.subtitle)}</span>` : ''}
              <span class="trip-dates">${escapeHtml(dates)}</span>
              <span class="trip-count">${escapeHtml(ahead)}</span>
            </span>
          </span>
        </button>
        <div class="trip-card-status">${refreshing}${bindingChip(summary)}</div>
        <a class="btn btn-secondary btn-sm trip-open" href="/trip.html?id=${encodeURIComponent(trip.id)}">${escapeHtml(t('trips.open'))}</a>
      </div>
      <div class="trip-legs">${cards}</div>
    </div>
  `;
}

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

  // Trip badge (#602). A trip card groups only its future/recent legs — the
  // past section is server-paginated, so a past leg pulled into the card could
  // duplicate or vanish depending on which page is loaded. Past legs therefore
  // render individually, and this badge is the only thing that says they belong
  // to a chain at all; without it a flown outbound leg is indistinguishable
  // from an ungrouped flight. Rendered on every card: inside the trip card it
  // is redundant but harmless, and omitting it there would need a second code
  // path for the same row.
  const tripBadge = f.trip
    ? `<a class="badge badge-trip" href="/trip.html?id=${encodeURIComponent(f.trip.id)}" title="${escapeHtml(f.trip.name)}">${escapeHtml(t('trips.legOf', { position: f.trip.position, total: f.trip.total }))}</a> `
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

  const descriptionLine = f.description
    ? `<div class="flight-description">${escapeHtml(f.description)}</div>`
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

  // #1024: the debrief lives on the briefing page's Debrief section. The CTA
  // shows while the report has unanswered questions, or on a recent flight
  // with no debrief yet (the server's "please debrief" nudge).
  const questionsOpen = f.questions_open ?? 0;
  const needsDebrief = !isShared && past && (questionsOpen > 0 || (f.section === 'recent' && !f.debrief));
  const debriefAction = needsDebrief
    ? `<a class="btn btn-secondary btn-debrief" data-testid="flight-debrief-link"`
      + ` href="/briefing.html?flight=${encodeURIComponent(f.id)}#debrief">${t('debrief.openCta')}`
      + `${questionsOpen > 0 ? ` · ${escapeHtml(questionsOpen === 1
        ? t('debriefSection.questionsOpenOne')
        : t('debriefSection.questionsOpenMany', { n: questionsOpen }))}` : ''}</a>`
    : '';

  return `
    <div class="flight-card${selectedClass}${isShared ? ' flight-card-shared' : ''}" data-id="${escapeHtml(f.id)}">
      ${selectControl}
      <div class="flight-card-main">
        <div class="flight-card-body">
          <div class="flight-header">
            ${sharedBadge}${tripBadge}${pastBadge}${unseenDot}<span class="flight-route">${escapeHtml(title)}</span>
            <span class="flight-date">${formatDate(f.target_date)} ${formatDepartureTime(f.departure_time)}</span>
            <span class="flight-alt">${formatAlt(f.cruise_altitude_ft)}</span>${debriefPill}
          </div>
          ${ownerLine}
          ${routeLine}
          ${descriptionLine}
          <div class="flight-status">
            ${refreshBadge}${packInfo}
          </div>
          <div class="flight-actions">
            <button class="btn btn-primary btn-briefing" data-id="${escapeHtml(f.id)}">${t('flights.btnBriefing')}</button>
            ${debriefAction}
            ${ownerOnlyActions}
          </div>
        </div>
        ${lb ? advisorySummaryHtml(lb) : ''}
      </div>
    </div>
  `;
}

// --- Past rows and chips (#1032) ---

/** Small amber / red dots for a row's forecast hazards, worst first. */
function hazardDots(tags: Record<string, 'amber' | 'red'>): string {
  const order = (s: string) => (s === 'red' ? 0 : 1);
  return HAZARD_CHIPS
    .filter(c => tags[c.tag])
    .sort((a, b) => order(tags[a.tag]) - order(tags[b.tag]))
    .map(c => {
      const label = `${t(c.labelKey)}: ${tags[c.tag] === 'red' ? t('past.statusRed') : t('past.statusAmber')}`;
      return `<span class="past-hazard past-hazard-${tags[c.tag]}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">${escapeHtml(t(c.labelKey))}</span>`;
    })
    .join('');
}

/** One archive row: compact card with the same select / briefing / delete
 *  controls as the full card, so bulk actions keep working on Past. */
export function renderPastRow(r: ArchiveRow, selected: boolean): string {
  const title = [r.departure, r.destination].filter(Boolean).join(' → ') || r.route_name;
  const date = new Date(r.departure_time);
  const dateText = date.toLocaleDateString(getDateLocale(), { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
  const tripBadge = r.trip
    ? `<a class="badge badge-trip" href="/trip.html?id=${encodeURIComponent(r.trip.id)}" title="${escapeHtml(r.trip.name)}">${escapeHtml(r.trip.name)}</a> `
    : '';
  const outcome = r.decision
    ? `<span class="badge badge-outcome badge-outcome-${r.decision}">${escapeHtml(t(`past.decision.${r.decision}`))}</span>`
    : '';
  const differs = r.differs_from_forecast
    ? ` <span class="badge badge-outcome">${escapeHtml(t('past.chipDiffers'))}</span>`
    : '';
  const hazards = r.tags_recorded
    ? (Object.keys(r.forecast_tags).length > 0
      ? hazardDots(r.forecast_tags)
      : `<span class="past-hazard-none">${escapeHtml(t('past.noForecastHazard'))}</span>`)
    : `<span class="past-hazard-none">${escapeHtml(t('past.notRecorded'))}</span>`;
  const reported = r.reported_tags.length > 0
    ? `<div class="past-reported">${escapeHtml(t('past.reported'))}: ${r.reported_tags
      .map(tag => escapeHtml(t(HAZARD_CHIPS.find(c => c.tag === tag)?.labelKey ?? '') || tag)).join(', ')}</div>`
    : '';
  const id = escapeHtml(r.id);
  return `
    <div class="flight-card past-row${selected ? ' selected' : ''}" data-id="${id}">
      <label class="flight-select" title="${escapeHtml(t('flights.selectToggle'))}">
        <input type="checkbox" class="flight-select-checkbox" data-id="${id}"${selected ? ' checked' : ''}>
      </label>
      <div class="flight-card-main">
        <div class="flight-card-body">
          <div class="flight-header">
            ${tripBadge}<span class="flight-route">${escapeHtml(title)}</span>
            <span class="flight-date">${escapeHtml(dateText)}</span> ${outcome}${differs}
          </div>
          <div class="past-hazards">${hazards}</div>
          ${reported}
          <div class="flight-actions">
            <button class="btn btn-primary btn-briefing" data-id="${id}">${t('flights.btnBriefing')}</button>
            <button class="btn btn-danger btn-delete" data-id="${id}">${t('flights.btnDelete')}</button>
          </div>
        </div>
      </div>
    </div>
  `;
}

/** The chip row under the past search box, with the server's counts. */
export function renderPastChips(f: PastFilterState, facets: ArchiveFacets | null): string {
  const count = (n: number | undefined) =>
    n != null ? ` <span class="past-chip-count">${n}</span>` : '';
  const hazardClass = f.source === 'reported' ? 'past-chip past-chip-reported' : 'past-chip';
  const hazards = HAZARD_CHIPS.map(c => {
    const on = f.tags.includes(c.tag);
    return `<button type="button" class="${hazardClass}${on ? ' is-on' : ''}" data-past-tag="${c.tag}" aria-pressed="${on}">${escapeHtml(t(c.labelKey))}${count(facets?.tags[c.tag])}</button>`;
  }).join('');
  const reported = f.source === 'reported';
  const source = `<button type="button" class="past-chip past-chip-reported${reported ? ' is-on' : ''}" data-past-source aria-pressed="${reported}">${escapeHtml(t('past.chipReported'))}</button>`;
  const outcomes = OUTCOME_CHIPS.map(c => {
    const on = f.outcomes.includes(c.key);
    return `<button type="button" class="past-chip${on ? ' is-on' : ''}" data-past-outcome="${c.key}" aria-pressed="${on}">${escapeHtml(t(c.labelKey))}${count(facets?.outcomes[c.key])}</button>`;
  }).join('');
  const hide = `<button type="button" class="past-chip${f.hideInactive ? ' is-on' : ''}" data-past-hide aria-pressed="${f.hideInactive}">${escapeHtml(t('past.chipHideInactive'))}</button>`;
  return `
    <div class="past-chips" role="group" aria-label="${escapeHtml(t('past.chipsLabel'))}">
      <div class="past-chip-row">${hazards}${source}</div>
      <div class="past-chip-row">${outcomes}${hide}</div>
    </div>
  `;
}

function renderPastNoMatch(): string {
  return `
    <div class="list-filter-nomatch">
      <span>${escapeHtml(t('past.noMatch'))}</span>
      <button type="button" class="btn btn-outline btn-sm btn-clear-past-filter">${escapeHtml(t('flights.filterNoMatchClear'))}</button>
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
  /** Group the selected flights into a new trip (#602). */
  onGroupAsTrip?: (ids: string[]) => void;
  /** Add the selected flights to the one trip already represented. */
  onAddToTrip?: (tripId: string, ids: string[]) => void;
  /** Unlink the selected flights from their trip — never a delete. */
  onRemoveFromTrip?: (pairs: { tripId: string; flightId: string }[]) => void;
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
  trips: TripResponse[] = [],
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
  const filterActive = !!(filters?.upcomingQuery || (filters && pastFilterActive(filters.pastFilter)));

  const pastRows = filters?.pastRows ?? [];
  if (flights.length === 0 && pastRows.length === 0 && !filterActive) {
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
    matchesQuery(f.waypoints, f.route_name, upcomingTokens, f.description, f.trip?.name);
  const futureShown = upcomingTokens.length > 0 ? future.filter(keep) : future;
  const recentShown = upcomingTokens.length > 0 ? recent.filter(keep) : recent;
  renderUpcomingFilter(filters, upcomingTotal, futureShown.length + recentShown.length);

  // --- Trip grouping (#602) ---
  // Group *before* sectioning, then place each group at its first un-flown
  // member. Ungrouped flights keep exactly today's path — the trip case is
  // additive, not a rewrite.
  const tripsById = new Map(trips.map(t => [t.id, t]));
  const emittedTrips = new Set<string>();

  const renderSectionCards = (rows: FlightResponse[], tokens: string[]): string =>
    rows.map((f) => {
      const trip = f.trip ? tripsById.get(f.trip.id) : undefined;
      if (!trip) {
        return renderFlightCard(f, activeRefreshes[f.id], selectedIds.has(f.id), tokens);
      }
      if (emittedTrips.has(trip.id)) return '';  // already inside its card
      emittedTrips.add(trip.id);
      const members = [...futureShown, ...recentShown].filter(
        m => m.trip?.id === trip.id,
      );
      return renderTripCard(trip, members, activeRefreshes, selectedIds, tokens);
    }).join('');

  const futureCards = renderSectionCards(futureShown, upcomingTokens);

  let recentSection = '';
  // A section with no matches hides entirely rather than rendering "(0)".
  if (recentShown.length > 0) {
    const expandedClass = recentExpanded ? '' : ' collapsed';
    const recentCards = renderSectionCards(recentShown, upcomingTokens);
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

  // --- Past section (#1032): rows, chips and counts all come from the
  // archive endpoint (store.loadPast); nothing is filtered or counted here.
  const pastFilter = filters?.pastFilter ?? EMPTY_PAST_FILTER;
  const pastQuery = pastFilter.q;
  const pastTokens = parseQuery(pastQuery);
  const pastActive = pastFilterActive(pastFilter);
  let pastSection = '';
  if (past.length > 0 || pastRows.length > 0 || pastActive) {
    const expandedClass = pastExpanded ? '' : ' collapsed';
    // Rows from an older /api/flights (no archive) still render as cards.
    const pastCards = pastRows.length > 0
      ? pastRows.map(r => renderPastRow(r, selectedIds.has(r.id))).join('')
      : past.map(f => renderFlightCard(f, activeRefreshes[f.id], selectedIds.has(f.id), pastTokens)).join('');
    const loadedPast = pastRows.length || past.length;
    const totalPast = Math.max(pastTotal, loadedPast);
    const remaining = totalPast - loadedPast;
    const showMoreBtn = remaining > 0
      ? `<button type="button" class="btn btn-outline btn-show-more-past" id="show-more-past">${t('flights.showMorePast', { count: remaining })}</button>`
      : '';
    // The past filter belongs to the open section, but it is *rendered*
    // regardless of the open state (toggling the section only flips a class,
    // no re-render). The chips sit with it: they are the finder.
    const showPastFilter = filters != null && (totalPast > FILTER_MIN_FLIGHTS || pastActive);
    const pastFilterHtml = showPastFilter
      ? `<div class="list-filter-row list-filter-row-inset">${renderFilterBox('past-filter-input', pastQuery, t('flights.filterPastPlaceholder'))}</div>
         ${renderPastChips(pastFilter, filters!.pastFacets)}`
      : '';
    const pastBody = loadedPast === 0 && pastActive
      ? renderPastNoMatch()
      : `<div class="past-flights-list">${pastCards}</div>${showMoreBtn}`;
    const busyClass = filters?.pastFiltering ? ' is-filtering' : '';
    pastSection = `
      <div class="past-flights-section${expandedClass}${busyClass}" aria-busy="${filters?.pastFiltering ? 'true' : 'false'}">
        <button class="past-flights-toggle" id="past-flights-toggle">
          ${t('flights.past', { count: totalPast })}
        </button>
        ${pastFilterHtml}
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

  // Trip cards: toggle a class (no re-render) and remember the choice.
  container.querySelectorAll('[data-trip-toggle]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tripId = (btn as HTMLElement).dataset.tripToggle!;
      const card = btn.closest('.trip-card');
      const open = tripExpanded.has(tripId);
      if (open) tripExpanded.delete(tripId);
      else tripExpanded.add(tripId);
      persistExpandedTrips();
      card?.classList.toggle('collapsed', open);
      btn.setAttribute('aria-expanded', open ? 'false' : 'true');
    });
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

  // --- Past chips (#1032) ---
  if (filters) {
    const f = filters.pastFilter;
    container.querySelectorAll<HTMLElement>('[data-past-tag]').forEach((chip) => {
      chip.addEventListener('click', () => {
        filters.onPastFilter({ tags: toggleValue(f.tags, chip.dataset.pastTag!) });
      });
    });
    container.querySelectorAll<HTMLElement>('[data-past-outcome]').forEach((chip) => {
      chip.addEventListener('click', () => {
        filters.onPastFilter({ outcomes: toggleValue(f.outcomes, chip.dataset.pastOutcome!) });
      });
    });
    container.querySelector('[data-past-source]')?.addEventListener('click', () => {
      filters.onPastFilter({ source: f.source === 'reported' ? 'forecast' : 'reported' });
    });
    container.querySelector('[data-past-hide]')?.addEventListener('click', () => {
      filters.onPastFilter({ hideInactive: !f.hideInactive });
    });
    container.querySelector('.btn-clear-past-filter')?.addEventListener('click', () => {
      const input = document.getElementById('past-filter-input') as HTMLInputElement | null;
      if (input) input.value = '';
      if (pastQueryTimer) clearTimeout(pastQueryTimer);
      pastQueryTimer = undefined;
      filters.onPastFilter({ ...EMPTY_PAST_FILTER });
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
    const visible = new Set([...[...futureShown, ...recentShown, ...past].map(f => f.id), ...pastRows.map(r => r.id)]);
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
  // Archive rows are owned flights only, so every one is selectable.
  const selectablePast = pastRows.length > 0
    ? pastRows.map(r => r.id)
    : past.filter(f => f.role !== 'subscriber').map(f => f.id);
  renderSelectionBar(
    effectiveSelected.size,
    [...selectableActive, ...selectablePast],
    selectablePast,
    selection,
    buildTripSelection(flights, effectiveSelected),
  );
}

/** Render the floating action bar shown when one or more flights are selected. */
function renderSelectionBar(
  selectedCount: number,
  allIds: string[],
  pastIds: string[],
  selection: SelectionHandlers,
  tripCtx?: TripSelectionContext,
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

  // Order and visual weight live in one pure rule — see selection-bar-order.
  // Context-sensitivity of the trip actions themselves is buildTripSelection's.
  const buttons = selectionBarButtons({
    hasSelectAll: allIds.length > 0,
    hasSelectPast: pastIds.length > 0,
    canGroupAsTrip: Boolean(tripCtx && selection.onGroupAsTrip && tripCtx.tripCount === 0),
    canAddToTrip: Boolean(
      tripCtx && selection.onAddToTrip
      && tripCtx.tripCount === 1 && tripCtx.ungroupedIds.length > 0,
    ),
    canRemoveFromTrip: Boolean(
      tripCtx && selection.onRemoveFromTrip && tripCtx.memberships.length > 0,
    ),
  });

  const LABELS: Record<string, () => string> = {
    'select-all': () => t('flights.btnSelectAll'),
    'select-past': () => t('flights.btnSelectAllPast'),
    'clear': () => t('flights.btnClearSelection'),
    'delete': () => t('flights.btnDeleteSelected'),
    'remove-trip': () => t('trips.btnRemove'),
    'group-trip': () => t('trips.btnGroup'),
    'add-trip': () => t('trips.btnAddTo', { name: tripCtx!.singleTrip!.name }),
  };
  const CLASSES: Record<string, string> = {
    'select-all': 'btn-select-all',
    'select-past': 'btn-select-past',
    'clear': 'btn-clear-selection',
    'delete': 'btn-bulk-delete',
    'remove-trip': 'btn-remove-trip',
    'group-trip': 'btn-group-trip',
    'add-trip': 'btn-add-trip',
  };

  const boundary = actionGroupStart(buttons);
  const html = buttons.map((b, i) => {
    // A hairline between the two groups — without it five near-identical pills
    // read as one undifferentiated set and the grouping buys nothing.
    const divider = i === boundary && i > 0
      ? '<span class="selection-divider" aria-hidden="true"></span>' : '';
    return `${divider}<button type="button" class="btn btn-${b.variant} btn-sm ${CLASSES[b.key]}">${escapeHtml(LABELS[b.key]())}</button>`;
  }).join('');

  bar.innerHTML = `
    <span class="selection-count">${t('flights.selected', { count: selectedCount })}</span>
    <div class="selection-actions">${html}</div>
  `;

  bar.querySelector('.btn-group-trip')?.addEventListener('click', () => {
    selection.onGroupAsTrip?.(tripCtx!.selectedIds);
  });
  bar.querySelector('.btn-add-trip')?.addEventListener('click', () => {
    selection.onAddToTrip?.(tripCtx!.singleTrip!.id, tripCtx!.ungroupedIds);
  });
  bar.querySelector('.btn-remove-trip')?.addEventListener('click', () => {
    // An unlink, and the confirm says so — the flights and their (expensive)
    // packs are untouched.
    if (confirm(t('trips.removeConfirm', { count: tripCtx!.memberships.length }))) {
      selection.onRemoveFromTrip?.(tripCtx!.memberships);
    }
  });

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
