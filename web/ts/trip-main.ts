/** Trip page entry point (#602) — /trip.html?id=… */

import { fetchActiveRefreshes, refreshBriefing } from './adapters/api-adapter';
import { settledLegIds } from './helpers/trip-leg-refresh';
import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  deleteTrip,
  fetchTrip,
  fetchTripAiSummary,
  fetchTripRefreshStatus,
  refreshTrip,
  removeTripLeg,
  updateTrip,
  type TripAiSummary,
} from './adapters/trips-adapter';
import type { RefreshEntry } from './adapters/api-adapter';
import type { TripResponse } from './store/types';
import * as ui from './managers/trip-ui';
import { errorToMessage, redirectToLogin, renderUserInfo } from './utils';
import { initTheme } from './theme';
import { initI18n } from './i18n/i18n';

/** Progress poll cadence while a trip refresh is running.
 *
 * The chain is server-driven and survives a closed tab, so this poll is purely
 * a progress readout — nothing about the refresh depends on it. A leg takes
 * minutes, so 5 s is plenty and matches the flights list's refresh poll. */
const REFRESH_POLL_MS = 5000;

let trip: TripResponse | null = null;
let pollTimer: number | null = null;
/** Legs refreshing right now, by flight id — including refreshes this page did
 *  not start (briefing page, Siri intent, scheduler, MCP). Its own always-on
 *  timer, separate from `pollTimer`: that one only lives for the duration of a
 *  trip run, which is exactly the case this map is *not* for. */
let legRefreshes: Record<string, RefreshEntry> = {};
let legPollTimer: number | null = null;
/** The AI paragraph currently on screen.
 *
 * Held so a re-render for an unrelated action keeps it. Renaming a trip or
 * toggling auto-refresh touches no leg's pack, debrief or state, so the
 * paragraph's cache key is unaffected — blanking the section on those actions
 * made it look like the summary had been withdrawn. Only a completed refresh
 * (or a reload) actually re-fetches it.
 */
let currentAi: TripAiSummary | null = null;

async function loadAiSummary(tripId: string): Promise<TripAiSummary | null> {
  try {
    // Keyed on the member (flight_id, fetch_timestamp) tuples server-side, so
    // reopening the page costs nothing when nothing has changed.
    return await fetchTripAiSummary(tripId);
  } catch {
    return null;
  }
}

/** Why a leg's Refresh click did nothing, by flight id — typically no new model
 *  run since its last briefing. Kept until that leg or the trip is refreshed
 *  again: a click with no visible effect reads as a broken button. */
let legNotices: Record<string, string> = {};

const legHandlers: ui.LegRowHandlers = {
  onRemoveLeg: handleRemoveLeg,
  onRefreshLeg: handleRefreshLeg,
};

function renderLegRows(): void {
  if (!trip) return;
  ui.renderLegs(
    trip.summary, legHandlers, legRefreshes, trip.refresh?.active === true, legNotices,
  );
}

function renderAll(ai: TripAiSummary | null): void {
  if (!trip) return;
  currentAi = ai;
  ui.renderHeader(trip);
  ui.renderChainStrip(trip.summary);
  ui.renderCallout(trip.summary);
  ui.renderAiSummary(ai);
  ui.renderContinuity(trip.summary);
  renderLegRows();
  ui.renderControls(trip, {
    onRefresh: handleRefresh,
    onRename: handleRename,
    onToggleAutoRefresh: handleAutoRefresh,
    onDelete: handleDelete,
  });
}

/** A 404 means the trip container was pruned — its last leg was unlinked.
 *
 * Shared rather than inlined in one handler: another tab or device can prune
 * the trip while this one is mid-poll, so every path that re-reads the trip
 * needs the same distinction between "it is gone" and "the request failed".
 */
function isMissingTrip(err: unknown): boolean {
  const message = err instanceof Error ? err.message : String(err);
  return message.startsWith('API 404:');
}

/** Re-read the trip and repaint.
 *
 * `fetchAi=false` keeps the paragraph already on screen instead of fetching it:
 * used when a leg lands mid-run, where a fetch would regenerate (and bill) a
 * paragraph the run's own completion is about to replace.
 */
async function reload(fetchAi = true): Promise<void> {
  if (!trip) return;
  const id = trip.id;
  try {
    trip = await fetchTrip(id);
  } catch (err) {
    if (isMissingTrip(err)) {
      window.location.href = '/index.html';
      return;
    }
    ui.renderError(errorToMessage(err));
    return;
  }
  renderAll(fetchAi ? await loadAiSummary(id) : currentAi);
}

/** Poll the shared active-refresh endpoint and repaint the leg rows on change.
 *
 * Same endpoint and cadence the flights list uses. The identity guard mirrors
 * the store's: repainting on every tick would rebuild the rows and rewire their
 * handlers for nothing.
 *
 * When a leg's refresh *settles* the trip is re-read, not just repainted: that
 * leg may have a new grade, and the aggregate is computed per read — this is
 * what keeps the callout honest after a single-leg refresh started anywhere.
 */
function startLegRefreshPolling(): void {
  if (legPollTimer != null) return;
  let inFlight = false;
  const tick = async () => {
    if (inFlight || !trip) return;
    inFlight = true;
    try {
      const members = new Set(trip.summary.legs.map(l => l.flight_id));
      const next: Record<string, RefreshEntry> = {};
      for (const e of await fetchActiveRefreshes()) {
        if (members.has(e.flight_id)) next[e.flight_id] = e;
      }
      const before = Object.keys(legRefreshes);
      const after = Object.keys(next);
      const same = before.length === after.length
        && after.every(k => legRefreshes[k]?.status === next[k]?.status);
      if (!same) {
        const settled = settledLegIds(legRefreshes, next);
        legRefreshes = next;
        if (settled.length > 0) {
          // Mid-run, keep the AI paragraph: the run regenerates it once on
          // completion, and a per-leg fetch would bill it once per leg.
          await reload(!trip.refresh?.active);
        } else {
          renderLegRows();
        }
      }
    } catch {
      // Non-critical: the badge is a readout, not a control.
    } finally {
      inFlight = false;
    }
  };
  void tick();
  legPollTimer = window.setInterval(tick, REFRESH_POLL_MS);
}

function stopLegRefreshPolling(): void {
  if (legPollTimer != null) {
    window.clearInterval(legPollTimer);
    legPollTimer = null;
  }
}

function stopPolling(): void {
  if (pollTimer != null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPolling(): void {
  if (pollTimer != null || !trip) return;
  const id = trip.id;
  // Guard against overlapping ticks: a slow response arriving after a fresher
  // one would otherwise write stale progress back over it.
  let inFlight = false;
  pollTimer = window.setInterval(async () => {
    if (inFlight) return;
    inFlight = true;
    try {
      const status = await fetchTripRefreshStatus(id);
      if (trip) {
        trip = { ...trip, refresh: status };
        ui.renderControls(trip, {
          onRefresh: handleRefresh,
          onRename: handleRename,
          onToggleAutoRefresh: handleAutoRefresh,
          onDelete: handleDelete,
        });
      }
      if (!status.active) {
        stopPolling();
        // The chain has landed: re-read the legs (and the AI paragraph, which
        // the server regenerates once per completed trip refresh).
        await reload();
      }
    } catch {
      stopPolling();
    } finally {
      inFlight = false;
    }
  }, REFRESH_POLL_MS);
}

async function handleRefresh(): Promise<void> {
  if (!trip) return;
  ui.renderError(null);
  try {
    const status = await refreshTrip(trip.id);
    trip = { ...trip, refresh: status };
    // A trip run re-checks every leg, so an earlier "no new data" is moot.
    legNotices = {};
    renderAll(null);
    if (status.active) startPolling();
  } catch (err) {
    ui.renderError(errorToMessage(err));
  }
}

/** Refresh one leg only — an ordinary per-flight refresh, no trip run.
 *
 * On a queued refresh the leg poll takes over and re-reads the trip when it
 * settles. Anything else was decided by the refresh gate without queuing:
 * no new model run (`already_fresh`), an observations-only update
 * (`realtime`), or no model reaching the date (`pending_coverage`).
 */
async function handleRefreshLeg(flightId: string): Promise<void> {
  if (!trip) return;
  ui.renderError(null);
  delete legNotices[flightId];
  let result;
  try {
    result = await refreshBriefing(flightId);
  } catch (err) {
    // A 409 here means a trip run claimed the leg since the last paint (the
    // button is off during one this page knows about).
    ui.renderError(errorToMessage(err));
    renderLegRows();
    return;
  }
  if (result.status === 'queued') {
    // Paint the badge now rather than on the next 5 s tick.
    legRefreshes = {
      ...legRefreshes,
      [flightId]: {
        flight_id: flightId, status: 'queued', triggered_by: 'user',
        stage: null, detail: null, queued_at: new Date().toISOString(),
      },
    };
    renderLegRows();
    return;
  }
  legNotices[flightId] = result.message;
  // Re-read rather than repaint: a realtime update can move the leg. Cheap —
  // the AI paragraph only regenerates if its member key actually changed.
  await reload();
}

async function handleRename(name: string): Promise<void> {
  if (!trip) return;
  try {
    trip = await updateTrip(trip.id, { name });
    // Keeps the AI paragraph: this action cannot have invalidated it.
    renderAll(currentAi);
  } catch (err) {
    ui.renderError(errorToMessage(err));
  }
}

async function handleAutoRefresh(value: boolean): Promise<void> {
  if (!trip) return;
  try {
    trip = await updateTrip(trip.id, { auto_refresh: value });
    // Keeps the AI paragraph: this action cannot have invalidated it.
    renderAll(currentAi);
  } catch (err) {
    ui.renderError(errorToMessage(err));
  }
}

async function handleRemoveLeg(flightId: string): Promise<void> {
  if (!trip) return;
  const id = trip.id;
  try {
    await removeTripLeg(id, flightId);
  } catch (err) {
    ui.renderError(errorToMessage(err));
    return;
  }
  try {
    trip = await fetchTrip(id);
  } catch (err) {
    // Expected when that was the trip's last leg; anything else is a real
    // failure and must say so rather than silently navigating away.
    if (isMissingTrip(err)) {
      window.location.href = '/index.html';
      return;
    }
    ui.renderError(errorToMessage(err));
    return;
  }
  // Re-fetch rather than clear: unlinking changes the member tuple, so the
  // stored paragraph could name a leg that is no longer in the trip. Blanking
  // it is safe but leaves the section empty until the next page load — which
  // would regenerate on the same stale key anyway, so this moves that cost
  // earlier rather than adding one.
  renderAll(await loadAiSummary(id));
}

async function handleDelete(): Promise<void> {
  if (!trip) return;
  try {
    await deleteTrip(trip.id);
    window.location.href = '/index.html';
  } catch (err) {
    ui.renderError(errorToMessage(err));
  }
}

async function init(): Promise<void> {
  await initI18n();
  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    return;
  }
  initTheme();
  renderUserInfo(user);

  const tripId = new URLSearchParams(window.location.search).get('id');
  if (!tripId) {
    ui.renderError('No trip specified.');
    return;
  }

  ui.renderLoading(true);
  try {
    trip = await fetchTrip(tripId);
  } catch (err) {
    ui.renderLoading(false);
    ui.renderError(errorToMessage(err));
    return;
  }
  ui.renderLoading(false);

  renderAll(null);
  if (trip.refresh?.active) startPolling();
  startLegRefreshPolling();
  // The AI paragraph loads second so the deterministic callout paints first —
  // it is the thing the eye should land on, and it must never wait on an LLM.
  renderAll(await loadAiSummary(tripId));

  window.addEventListener('beforeunload', () => {
    stopPolling();
    stopLegRefreshPolling();
  });
}

void init();
