/** DOM rendering for the trip page (#602).
 *
 * Progressive depth: the chain strip at the top says what the trip is, the
 * deterministic callout says which leg decides it, and the per-leg rows carry
 * the substance. Two things this page must never do, both load-bearing:
 *
 * 1. **Print a verdict for the trip.** A trip has no colour; only its legs do.
 *    The page ranks and directs attention.
 * 2. **Fold a long-range outlook into a traffic light.** They are mutually
 *    exclusive by design — an outlook is a tendency, not a verdict — so a
 *    beyond-horizon leg renders with its own soft badge and is reported
 *    separately from the chain status.
 */

import type {
  TripAiSummary,
} from '../adapters/trips-adapter';
import type { TripLeg, TripResponse, TripSummary } from '../store/types';
import { $, escapeHtml, formatDate } from '../utils';
import { t, getDateLocale } from '../i18n/i18n';
import { assessmentClass, outlookClass } from '../helpers/assessment-badges';

/** The badge for one leg — traffic light, soft outlook, or a neutral state. */
function legBadge(leg: TripLeg): string {
  if (leg.grade_kind === 'outlook') {
    const cls = outlookClass(leg.outlook);
    const label = t(`outlook.${(leg.outlook || '').toLowerCase()}`);
    return `<span class="badge badge-outlook ${cls}">${escapeHtml(label)}</span>`;
  }
  if (leg.grade_kind === 'pending_coverage') {
    return `<span class="badge badge-pending">${escapeHtml(t('flights.pendingTitle'))}</span>`;
  }
  if (leg.grade_kind === 'needs_briefing') {
    return `<span class="badge badge-none">${escapeHtml(t('trips.needsBriefing'))}</span>`;
  }
  return `<span class="badge ${assessmentClass(leg.assessment)}">${escapeHtml(leg.assessment || '—')}</span>`;
}

function legStateBadge(leg: TripLeg): string {
  if (leg.state === 'flown') {
    return `<span class="badge badge-past">${escapeHtml(t('trips.flown'))}</span> `;
  }
  if (leg.state === 'cancelled') {
    return `<span class="badge badge-past">${escapeHtml(t('trips.cancelled'))}</span> `;
  }
  return '';
}

function formatUtc(iso: string): string {
  return new Date(iso).toLocaleString(getDateLocale(), {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    timeZone: 'UTC',
  });
}

/**
 * Chain strip — one tile per leg, left to right, flown legs greyed, ground gaps
 * labelled between them. Making the shrinking scope literally visible is the
 * point: the greyed tiles are the legs that no longer matter.
 */
export function renderChainStrip(summary: TripSummary): void {
  const el = $('trip-chain-strip');
  if (!el) return;
  const tiles: string[] = [];
  summary.legs.forEach((leg, index) => {
    if (index > 0 && leg.gap_hours_before != null) {
      const label = leg.same_sortie_as_previous
        ? t('trips.sameSortie')
        : t('trips.gapNights', { hours: Math.round(leg.gap_hours_before) });
      tiles.push(`<span class="trip-gap" title="${escapeHtml(label)}">${escapeHtml(label)}</span>`);
    }
    const binding = leg.flight_id === summary.binding_leg_id ? ' trip-tile-binding' : '';
    const flown = leg.state === 'remaining' ? '' : ' trip-tile-flown';
    tiles.push(`
      <a class="trip-tile${binding}${flown}" href="/briefing.html?flight=${encodeURIComponent(leg.flight_id)}">
        <span class="trip-tile-label">${escapeHtml(leg.label)}</span>
        <span class="trip-tile-date">${escapeHtml(formatUtc(leg.departure_time))}Z</span>
        <span class="trip-tile-badge">${legBadge(leg)}</span>
      </a>`);
  });
  el.innerHTML = `<div class="trip-strip">${tiles.join('')}</div>`;
}

/**
 * The deterministic binding-constraint callout. One sentence, computed on the
 * server so web, iOS, the notification and the AI guardrail can never disagree
 * about which leg decides the trip.
 */
export function renderCallout(summary: TripSummary): void {
  const el = $('trip-callout');
  if (!el) return;
  const decidable = summary.decidable_from
    ? `<div class="trip-ripeness">${escapeHtml(t('trips.decidableFrom', { date: formatDate(summary.decidable_from) }))}</div>`
    : '';
  el.innerHTML = `
    <div class="trip-callout">
      <div class="trip-callout-label">${escapeHtml(t('trips.headline'))}</div>
      <p class="trip-callout-text">${escapeHtml(summary.headline)}</p>
      ${decidable}
    </div>
  `;
}

/** The AI paragraph, or the reason there isn't one. Secondary by design. */
export function renderAiSummary(ai: TripAiSummary | null): void {
  const section = $('trip-ai-section');
  const heading = $('trip-ai-heading');
  const body = $('trip-ai');
  if (!section || !body || !heading) return;
  heading.textContent = t('trips.aiSummary');
  if (ai?.text) {
    section.style.display = '';
    body.innerHTML = `<p class="trip-ai-text">${escapeHtml(ai.text)}</p>`;
    return;
  }
  if (ai?.unavailable_reason === 'ai_disabled') {
    // Worth saying out loud rather than silently omitting: the pilot turned it
    // off on a leg, and the trip inheriting that is the intended behaviour.
    section.style.display = '';
    body.innerHTML = `<p class="muted">${escapeHtml(t('trips.aiDisabled'))}</p>`;
    return;
  }
  section.style.display = 'none';
}

/** Soft continuity warnings — never a block; pilots reposition. */
export function renderContinuity(summary: TripSummary): void {
  const el = $('trip-continuity');
  if (!el) return;
  if (summary.continuity_warnings.length === 0) {
    el.innerHTML = '';
    return;
  }
  const items = summary.continuity_warnings.map(w =>
    `<li>${escapeHtml(t('trips.continuityWarning', { arrives: w.arrives, departs: w.departs }))}</li>`,
  ).join('');
  el.innerHTML = `<div class="trip-continuity-warning"><ul>${items}</ul></div>`;
}

export interface LegRowHandlers {
  onRemoveLeg: (flightId: string) => void;
}

/**
 * Per-leg detail rows. Each carries the two links out — Briefing and Edit —
 * as explicit controls rather than a click-the-card affordance: getting from
 * the trip to one leg's full detail is the main navigation this page exists to
 * serve.
 *
 * Deliberately **no per-leg refresh button**: refreshing is a trip-level action
 * (see the serial driver), and the refresh gate already skips legs with no new
 * data, so one button is genuinely sufficient.
 */
export function renderLegs(summary: TripSummary, handlers: LegRowHandlers): void {
  const heading = $('trip-legs-heading');
  if (heading) heading.textContent = t('trips.legs');
  const el = $('trip-legs');
  if (!el) return;

  if (summary.legs.length === 0) {
    // Not `trips.notFound` — that string is for a trip container that does not
    // exist, which is a 404 handled before we ever render. A trip that loads
    // with no legs is a different (and currently unreachable, since empty trips
    // are pruned server-side) state, and saying "not found" about a trip the
    // user is looking at would be actively misleading if pruning ever slips.
    el.innerHTML = `<p class="muted">${escapeHtml(t('trips.noLegs'))}</p>`;
    return;
  }

  el.innerHTML = summary.legs.map((leg) => {
    const binding = leg.flight_id === summary.binding_leg_id ? ' trip-leg-binding' : '';
    const flown = leg.state === 'remaining' ? '' : ' trip-leg-flown';
    // days_out sits beside the badge so the confidence behind a grade is
    // legible without a click — a D-7 amber and a D-1 amber are not the
    // same claim.
    const days = leg.days_out != null
      ? `<span class="pack-info">D-${leg.days_out}</span>` : '';
    const freshness = leg.fetch_timestamp
      ? `<span class="pack-info">${escapeHtml(formatUtc(leg.fetch_timestamp))} UTC</span>`
      : '';
    const chips = (leg.advisory_summary?.top ?? []).map(chip =>
      `<span class="badge ${chip.status === 'RED' ? 'badge-red' : 'badge-amber'}">${escapeHtml(chip.name)}</span>`,
    ).join(' ');
    const duration = leg.duration_hours
      ? `<span class="pack-info">${leg.duration_hours.toFixed(1)} h</span>` : '';
    return `
      <div class="trip-leg-row${binding}${flown}" data-flight-id="${escapeHtml(leg.flight_id)}">
        <div class="trip-leg-main">
          <div class="trip-leg-head">
            ${legStateBadge(leg)}<span class="flight-route">${escapeHtml(leg.label)}</span>
            <span class="flight-date">${escapeHtml(formatUtc(leg.departure_time))}Z</span>
            ${duration}
          </div>
          <div class="trip-leg-status">${legBadge(leg)} ${days} ${freshness}</div>
          <div class="trip-leg-chips">${chips}</div>
        </div>
        <div class="trip-leg-actions">
          <a class="btn btn-primary btn-sm" href="/briefing.html?flight=${encodeURIComponent(leg.flight_id)}">${escapeHtml(t('trips.btnBriefing'))}</a>
          <a class="btn btn-secondary btn-sm" href="/flight.html?id=${encodeURIComponent(leg.flight_id)}">${escapeHtml(t('trips.btnEdit'))}</a>
          <button type="button" class="btn btn-outline btn-sm btn-remove-leg" data-id="${escapeHtml(leg.flight_id)}">${escapeHtml(t('trips.btnRemove'))}</button>
        </div>
      </div>`;
  }).join('');

  el.querySelectorAll('.btn-remove-leg').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = (btn as HTMLElement).dataset.id!;
      if (confirm(t('trips.removeConfirm', { count: 1 }))) handlers.onRemoveLeg(id);
    });
  });
}

export interface ControlHandlers {
  onRefresh: () => void;
  onRename: (name: string) => void;
  onToggleAutoRefresh: (value: boolean) => void;
  onDelete: () => void;
}

/** Trip-level controls, including the single "Refresh trip" button. */
export function renderControls(trip: TripResponse, handlers: ControlHandlers): void {
  const el = $('trip-controls');
  if (!el) return;
  const busy = trip.refresh?.active === true;
  const progress = trip.refresh?.message
    ? `<div class="trip-refresh-progress">${escapeHtml(trip.refresh.message)}</div>` : '';
  el.innerHTML = `
    <div class="trip-controls">
      <button type="button" class="btn btn-primary btn-trip-refresh" ${busy ? 'disabled' : ''}>
        ${escapeHtml(busy ? t('trips.refreshing') : t('trips.refreshBtn'))}
      </button>
      <label class="trip-auto-refresh">
        <input type="checkbox" class="trip-auto-refresh-toggle" ${trip.auto_refresh ? 'checked' : ''}>
        ${escapeHtml(t('trips.autoRefresh'))}
      </label>
      <button type="button" class="btn btn-secondary btn-trip-rename">${escapeHtml(t('trips.btnRename'))}</button>
      <button type="button" class="btn btn-danger btn-trip-delete">${escapeHtml(t('trips.btnDelete'))}</button>
    </div>
    ${progress}
  `;

  el.querySelector('.btn-trip-refresh')?.addEventListener('click', () => handlers.onRefresh());
  el.querySelector('.trip-auto-refresh-toggle')?.addEventListener('change', (ev) => {
    handlers.onToggleAutoRefresh((ev.target as HTMLInputElement).checked);
  });
  el.querySelector('.btn-trip-rename')?.addEventListener('click', () => {
    const name = prompt(t('trips.btnRename'), trip.name);
    if (name != null && name.trim()) handlers.onRename(name.trim());
  });
  el.querySelector('.btn-trip-delete')?.addEventListener('click', () => {
    // The confirm spells out that the flights survive — this deletes a
    // container, not the (expensive) briefings underneath it.
    if (confirm(t('trips.deleteConfirm'))) handlers.onDelete();
  });
}

export function renderHeader(trip: TripResponse): void {
  const el = $('trip-header');
  if (!el) return;
  el.innerHTML = `
    <h1 class="trip-title">${escapeHtml(trip.name || trip.summary.chain_label)}</h1>
    <div class="trip-subtitle">${escapeHtml(trip.summary.chain_label)}</div>
  `;
  document.title = `Flyfun Weather — ${trip.name || trip.summary.chain_label}`;
}

export function renderError(message: string | null): void {
  const el = $('error-message');
  if (!el) return;
  el.textContent = message || '';
  el.style.display = message ? 'block' : 'none';
}

export function renderLoading(loading: boolean): void {
  const spinner = $('loading-spinner');
  if (spinner) spinner.style.display = loading ? 'block' : 'none';
}
