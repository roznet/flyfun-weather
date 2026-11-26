/** Zustand vanilla store for the Flights management page. */

import { createStore } from 'zustand/vanilla';
import type { ArchiveFacets, ArchiveRow, DebriefStats, FlightResponse, TripResponse } from './types';
import { archiveParams, EMPTY_PAST_FILTER, type PastFilterState } from '../helpers/past-archive';
import type { RefreshEntry } from '../adapters/api-adapter';
import * as api from '../adapters/api-adapter';
import { fetchDebriefStats } from '../adapters/debrief-adapter';
import { addTripLegs, createTrip, fetchTrips, removeTripLeg } from '../adapters/trips-adapter';
import { errorToMessage } from '../utils';

/** Past-section page size. Future + recent are never paginated; past comes
 *  from the archive endpoint (#1031), never from `/api/flights`. */
export const PAST_PAGE_SIZE = 20;

export interface FlightsState {
  // Data
  flights: FlightResponse[];
  /** The user's trips, each carrying its server-computed summary. Loaded
   *  alongside the flights so the list card can show the binding-leg chip
   *  without re-deriving the binding rule in the browser — it lives in exactly
   *  one place, on the server. Empty for a user with no trips. */
  trips: TripResponse[];
  /** Past rows from `GET /api/flights/archive` (#1032): owned flights only,
   *  filtered, paged and counted by the server. */
  pastRows: ArchiveRow[];
  pastTotal: number;  // matches for the current past filter (for the "Show more" gate)
  /** Server chip counts for the current past filter. */
  pastFacets: ArchiveFacets | null;
  activeRefreshes: Record<string, RefreshEntry>; // flight_id → active refresh entry
  debriefStats: DebriefStats | null;

  // UI state
  loading: boolean;
  loaded: boolean;  // a flights fetch has completed at least once
  loadingMore: boolean;  // a "Show more" past-page fetch is in flight
  pastFiltering: boolean;  // a past-filter query is in flight (server round-trip)
  error: string | null;
  selectedIds: Set<string>;  // flights ticked for bulk actions

  // Filters (#542). Two independent, section-scoped queries: `upcomingQuery`
  // filters future + recent purely in the browser (those sections always come
  // back in full), while the past filter (search + chips, #1032) round-trips
  // to the archive endpoint because the past section is paginated and counted
  // on the server.
  upcomingQuery: string;
  pastFilter: PastFilterState;

  // Actions
  loadFlights: () => Promise<void>;
  loadTrips: () => Promise<void>;
  groupAsTrip: (flightIds: string[]) => Promise<void>;
  addToTrip: (tripId: string, flightIds: string[]) => Promise<void>;
  /** Unlink one leg. Resolves to false (and sets `error`) on failure. */
  removeFromTrip: (tripId: string, flightId: string) => Promise<boolean>;
  loadMorePast: () => Promise<void>;
  setUpcomingQuery: (q: string) => void;
  setPastQuery: (q: string) => Promise<void>;
  /** Change chips / source / hide and reload the first past page. */
  setPastFilter: (patch: Partial<PastFilterState>) => Promise<void>;
  /** Reload the first past page for the current filter. */
  loadPast: () => Promise<void>;
  loadDebriefStats: () => Promise<void>;
  pollActiveRefreshes: () => Promise<void>;
  createFlight: (waypoints: string[], targetDate: string, opts?: {
    routeName?: string;
    targetTimeUtc?: number;
    targetMinuteUtc?: number;
    cruiseAltitudeFt?: number;
    flightCeilingFt?: number;
    flightDurationHours?: number;
    profileId?: number;
    aircraftId?: number;
    rawRoute?: string;  // original Field-15 input from the popup flow
    description?: string;
    flexibility?: 'none' | 'same_day' | 'prev_day' | 'next_day';
  }) => Promise<FlightResponse>;
  deleteFlight: (id: string) => Promise<void>;
  unsubscribeFlight: (id: string) => Promise<void>;
  toggleSelected: (id: string) => void;
  setSelected: (ids: string[]) => void;
  clearSelection: () => void;
  pruneSelection: (visibleIds: string[]) => void;
  bulkDeleteSelected: () => Promise<{ deleted: number; notFound: number }>;
}

/** Monotonic token shared by *every* async path that replaces the flights list:
 *  filter keystrokes, "show more past", and background reloads.
 *
 *  These race against each other, not just against themselves. A "show more"
 *  page or a post-debrief `loadFlights()` that resolves after a filter change
 *  would otherwise repaint the list with results for the *previous* query while
 *  the filter input still shows the new one — silently wrong, and invisible
 *  until you notice the rows don't match what you typed. Newest request started
 *  wins; superseded ones drop their data but still clear their own loading flag.
 */
let flightsLoadSeq = 0;

/** Same guard for the past rows: filter changes, "show more" and reloads of
 *  the archive race each other, and the newest request started wins. */
let pastLoadSeq = 0;

export const flightsStore = createStore<FlightsState>((set, get) => ({
  flights: [],
  trips: [],
  pastRows: [],
  pastTotal: 0,
  pastFacets: null,
  activeRefreshes: {},
  debriefStats: null,
  loading: false,
  loaded: false,
  loadingMore: false,
  pastFiltering: false,
  error: null,
  selectedIds: new Set(),
  upcomingQuery: '',
  pastFilter: EMPTY_PAST_FILTER,

  loadFlights: async () => {
    const seq = ++flightsLoadSeq;
    set({ loading: true, error: null });
    // Past rows come from the archive (#1032), fetched alongside: a failure
    // there must not hide the upcoming list.
    const past = get().loadPast();
    try {
      // The card and the debrief form read everything they need from each
      // flight's inline `latest_briefing`, so a single request paints the
      // page — no per-flight /packs/latest round-trips. `pastLimit: 0`: the
      // past section is the archive's, so /api/flights only sends future +
      // recent.
      const { flights } = await api.fetchFlights({ pastLimit: 0 });
      // Superseded (e.g. a reload after a debrief): drop the stale payload,
      // but still clear our own spinner.
      if (seq !== flightsLoadSeq) {
        set({ loading: false });
        return;
      }
      set({ flights, loading: false, loaded: true });
    } catch (err) {
      if (seq !== flightsLoadSeq) {
        set({ loading: false });
        return;
      }
      set({ loading: false, error: `Failed to load flights: ${err}` });
    }
    await past;
  },

  loadPast: async () => {
    const seq = ++pastLoadSeq;
    set({ pastFiltering: true });
    try {
      const resp = await api.fetchArchive(
        archiveParams(get().pastFilter, { limit: PAST_PAGE_SIZE, offset: 0 }),
      );
      // Only the newest request owns the rows and the busy flag.
      if (seq !== pastLoadSeq) return;
      set({ pastRows: resp.rows, pastTotal: resp.total, pastFacets: resp.facets, pastFiltering: false });
    } catch (err) {
      if (seq !== pastLoadSeq) return;
      set({ error: `Failed to load past flights: ${err}`, pastFiltering: false });
    }
  },

  loadTrips: async () => {
    try {
      set({ trips: await fetchTrips() });
    } catch {
      // Non-critical: without trips the list simply renders ungrouped cards.
    }
  },

  groupAsTrip: async (flightIds) => {
    set({ error: null });
    try {
      await createTrip(flightIds);
      get().clearSelection();
      await Promise.all([get().loadFlights(), get().loadTrips()]);
    } catch (err) {
      set({ error: errorToMessage(err) });
    }
  },

  addToTrip: async (tripId, flightIds) => {
    set({ error: null });
    try {
      await addTripLegs(tripId, flightIds);
      get().clearSelection();
      await Promise.all([get().loadFlights(), get().loadTrips()]);
    } catch (err) {
      set({ error: errorToMessage(err) });
    }
  },

  removeFromTrip: async (tripId, flightId) => {
    // Deliberately does NOT clear `error` on entry. A multi-leg unlink calls
    // this once per flight, and a `set({ error: null })` here would let a later
    // success erase an earlier failure's message — the pilot would see no error
    // at all while one flight silently stayed in the trip. Returns whether it
    // succeeded so the caller can stop at the first failure.
    try {
      await removeTripLeg(tripId, flightId);
      get().clearSelection();
      await Promise.all([get().loadFlights(), get().loadTrips()]);
      return true;
    } catch (err) {
      set({ error: errorToMessage(err) });
      return false;
    }
  },

  setUpcomingQuery: (q) => {
    // Pure client-side: future + recent are always fully loaded, so this is a
    // re-render, not a fetch.
    set({ upcomingQuery: q });
  },

  setPastQuery: async (q) => {
    // Reset to the first page: offsets from the previous query index into a
    // different result set. `pastFiltering` gives the section a busy state.
    set({ pastFilter: { ...get().pastFilter, q } });
    await get().loadPast();
  },

  setPastFilter: async (patch) => {
    set({ pastFilter: { ...get().pastFilter, ...patch } });
    await get().loadPast();
  },

  loadMorePast: async () => {
    // Guard against rapid double-clicks: until the in-flight page lands,
    // the offset wouldn't advance, so concurrent calls would all request the
    // same page and waste round-trips.
    // While a filter query is in flight the page offset is about to be
    // reset; paging now would also drop that query as stale and leave the
    // section stuck busy (`pastFiltering`).
    if (get().loadingMore || get().pastFiltering) return;
    const seq = ++pastLoadSeq;
    const current = get().pastRows;
    set({ loadingMore: true });
    try {
      const resp = await api.fetchArchive(
        archiveParams(get().pastFilter, { limit: PAST_PAGE_SIZE, offset: current.length }),
      );
      // Superseded by a filter change or reload started after this page:
      // appending would mix rows from the old filter into the new one.
      if (seq !== pastLoadSeq) {
        set({ loadingMore: false });
        return;
      }
      const existingIds = new Set(current.map((r) => r.id));
      const fresh = resp.rows.filter((r) => !existingIds.has(r.id));
      set({
        pastRows: [...current, ...fresh], pastTotal: resp.total, pastFacets: resp.facets,
        loadingMore: false,
      });
    } catch (err) {
      if (seq !== pastLoadSeq) {
        set({ loadingMore: false });
        return;
      }
      set({ error: `Failed to load more flights: ${err}`, loadingMore: false });
    }
  },

  loadDebriefStats: async () => {
    try {
      const stats = await fetchDebriefStats();
      set({ debriefStats: stats });
    } catch {
      // Non-critical — leave stats null on failure (panel just doesn't render).
    }
  },

  pollActiveRefreshes: async () => {
    try {
      const entries = await api.fetchActiveRefreshes();
      const map: Record<string, RefreshEntry> = {};
      for (const e of entries) {
        map[e.flight_id] = e;
      }
      // Skip set() if contents are identical — otherwise a fresh empty {}
      // every 5s churns object identity and triggers a full list re-render,
      // which wipes inline state (e.g. an open debrief form).
      const current = get().activeRefreshes;
      const currentKeys = Object.keys(current);
      const newKeys = Object.keys(map);
      const sameShape =
        currentKeys.length === newKeys.length &&
        newKeys.every((k) => current[k]?.status === map[k]?.status);
      if (!sameShape) {
        set({ activeRefreshes: map });
      }
    } catch {
      // Non-critical — silently ignore polling errors
    }
  },

  createFlight: async (waypoints, targetDate, opts) => {
    set({ loading: true, error: null });
    try {
      // Build ISO datetime from date + hour + minute
      const hour = (opts?.targetTimeUtc ?? 9).toString().padStart(2, '0');
      const minute = (opts?.targetMinuteUtc ?? 0).toString().padStart(2, '0');
      const departureTime = `${targetDate}T${hour}:${minute}:00Z`;

      const flight = await api.createFlight({
        waypoints,
        route_name: opts?.routeName,
        departure_time: departureTime,
        cruise_altitude_ft: opts?.cruiseAltitudeFt,
        flight_ceiling_ft: opts?.flightCeilingFt,
        flight_duration_hours: opts?.flightDurationHours,
        profile_id: opts?.profileId,
        aircraft_id: opts?.aircraftId,
        raw_route: opts?.rawRoute,
        description: opts?.description,
        flexibility: opts?.flexibility,
      });
      // Refresh the list
      await get().loadFlights();
      set({ loading: false });
      return flight;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Strip "API 422: " prefix to show the backend detail directly
      const detail = msg.replace(/^API \d+:\s*/, '');
      set({ loading: false, error: detail });
      throw err;
    }
  },

  deleteFlight: async (id) => {
    set({ loading: true, error: null });
    try {
      await api.deleteFlight(id);
      await get().loadFlights();
      set({ loading: false });
    } catch (err) {
      set({ loading: false, error: `Failed to delete flight: ${err}` });
    }
  },

  unsubscribeFlight: async (id) => {
    set({ loading: true, error: null });
    try {
      await api.unsubscribeFlight(id);
      await get().loadFlights();
      set({ loading: false });
    } catch (err) {
      set({ loading: false, error: errorToMessage(err) });
    }
  },

  toggleSelected: (id) => {
    const next = new Set(get().selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    set({ selectedIds: next });
  },

  setSelected: (ids) => {
    set({ selectedIds: new Set(ids) });
  },

  clearSelection: () => {
    set({ selectedIds: new Set() });
  },

  pruneSelection: (visibleIds) => {
    const current = get().selectedIds;
    const visible = new Set(visibleIds);
    const kept = [...current].filter((id) => visible.has(id));
    // No-op unless something was actually dropped: an unconditional set() would
    // churn `selectedIds` identity and re-render the list on every pass.
    if (kept.length === current.size) return;
    set({ selectedIds: new Set(kept) });
  },

  bulkDeleteSelected: async () => {
    const ids = Array.from(get().selectedIds);
    if (ids.length === 0) return { deleted: 0, notFound: 0 };
    set({ loading: true, error: null });
    try {
      const resp = await api.bulkDeleteFlights(ids);
      set({ selectedIds: new Set() });
      await get().loadFlights();
      set({ loading: false });
      return { deleted: resp.deleted.length, notFound: resp.not_found.length };
    } catch (err) {
      set({ loading: false, error: `Failed to delete flights: ${err}` });
      throw err;
    }
  },
}));
