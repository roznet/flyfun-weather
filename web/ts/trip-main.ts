/** Trip page entry point (#602) — /trip.html?id=… */

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

function renderAll(ai: TripAiSummary | null): void {
  if (!trip) return;
  currentAi = ai;
  ui.renderHeader(trip);
  ui.renderChainStrip(trip.summary);
  ui.renderCallout(trip.summary);
  ui.renderAiSummary(ai);
  ui.renderContinuity(trip.summary);
  ui.renderLegs(trip.summary, { onRemoveLeg: handleRemoveLeg });
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

async function reload(withAi = true): Promise<void> {
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
  renderAll(withAi ? await loadAiSummary(id) : null);
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
    renderAll(null);
    if (status.active) startPolling();
  } catch (err) {
    ui.renderError(errorToMessage(err));
  }
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
  renderAll(null);
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
  // The AI paragraph loads second so the deterministic callout paints first —
  // it is the thing the eye should land on, and it must never wait on an LLM.
  renderAll(await loadAiSummary(tripId));

  window.addEventListener('beforeunload', stopPolling);
}

void init();
