/** Zustand vanilla store for the Flights management page. */

import { createStore } from 'zustand/vanilla';
import type { DebriefStats, FlightResponse } from './types';
import type { RefreshEntry } from '../adapters/api-adapter';
import * as api from '../adapters/api-adapter';
import { fetchDebriefStats } from '../adapters/debrief-adapter';
import { errorToMessage } from '../utils';

/** Past-section page size. Future + recent are never paginated. */
export const PAST_PAGE_SIZE = 20;

export interface FlightsState {
  // Data
  flights: FlightResponse[];
  pastTotal: number;  // full count of "past" flights (for the "Show more" gate)
  activeRefreshes: Record<string, RefreshEntry>; // flight_id → active refresh entry
  debriefStats: DebriefStats | null;

  // UI state
  loading: boolean;
  loaded: boolean;  // a flights fetch has completed at least once
  loadingMore: boolean;  // a "Show more" past-page fetch is in flight
  error: string | null;
  selectedIds: Set<string>;  // flights ticked for bulk actions

  // Filters (#542). Two independent, section-scoped queries: `upcomingQuery`
  // filters future + recent purely in the browser (those sections always come
  // back in full), while `pastQuery` round-trips to the server because the
  // past section is paginated and the matches may not be loaded yet.
  upcomingQuery: string;
  pastQuery: string;

  // Actions
  loadFlights: () => Promise<void>;
  loadMorePast: () => Promise<void>;
  setUpcomingQuery: (q: string) => void;
  setPastQuery: (q: string) => Promise<void>;
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
    flexibility?: 'none' | 'same_day' | 'prev_day' | 'next_day';
  }) => Promise<FlightResponse>;
  deleteFlight: (id: string) => Promise<void>;
  unsubscribeFlight: (id: string) => Promise<void>;
  toggleSelected: (id: string) => void;
  setSelected: (ids: string[]) => void;
  clearSelection: () => void;
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

export const flightsStore = createStore<FlightsState>((set, get) => ({
  flights: [],
  pastTotal: 0,
  activeRefreshes: {},
  debriefStats: null,
  loading: false,
  loaded: false,
  loadingMore: false,
  error: null,
  selectedIds: new Set(),
  upcomingQuery: '',
  pastQuery: '',

  loadFlights: async () => {
    const seq = ++flightsLoadSeq;
    set({ loading: true, error: null });
    try {
      // The card and the debrief form read everything they need from each
      // flight's inline `latest_briefing`, so a single request paints the
      // page — no per-flight /packs/latest round-trips. Only the past section
      // is paginated; future + recent always come back in full.
      // Any active past filter rides along so a background reload (e.g. after
      // a debrief) doesn't silently drop it.
      const { flights, pastTotal } = await api.fetchFlights({
        pastLimit: PAST_PAGE_SIZE,
        pastQuery: get().pastQuery || undefined,
      });
      // Superseded (e.g. the user typed a filter while this was in flight):
      // drop the stale payload, but still clear our own spinner.
      if (seq !== flightsLoadSeq) {
        set({ loading: false });
        return;
      }
      set({ flights, pastTotal, loading: false, loaded: true });
    } catch (err) {
      if (seq !== flightsLoadSeq) {
        set({ loading: false });
        return;
      }
      set({ loading: false, error: `Failed to load flights: ${err}` });
    }
  },

  setUpcomingQuery: (q) => {
    // Pure client-side: future + recent are always fully loaded, so this is a
    // re-render, not a fetch.
    set({ upcomingQuery: q });
  },

  setPastQuery: async (q) => {
    const seq = ++flightsLoadSeq;
    // Reset to the first page: offsets from the previous query index into a
    // different result set.
    set({ pastQuery: q });
    try {
      const { flights, pastTotal } = await api.fetchFlights({
        pastLimit: PAST_PAGE_SIZE,
        pastQuery: q || undefined,
      });
      if (seq !== flightsLoadSeq) return;  // superseded by a later load
      set({ flights, pastTotal, loaded: true });
    } catch (err) {
      if (seq !== flightsLoadSeq) return;
      set({ error: `Failed to filter flights: ${err}` });
    }
  },

  loadMorePast: async () => {
    // Guard against rapid double-clicks: until the in-flight page lands,
    // loadedPast wouldn't advance, so concurrent calls would all request the
    // same offset and waste round-trips.
    if (get().loadingMore) return;
    const seq = ++flightsLoadSeq;
    const current = get().flights;
    const loadedPast = current.filter((f) => f.section === 'past').length;
    set({ loadingMore: true });
    try {
      const { flights: page, pastTotal } = await api.fetchFlights({
        pastLimit: PAST_PAGE_SIZE,
        pastOffset: loadedPast,
        // Under a filter, `loadedPast` counts loaded *matches*, which is the
        // right offset into the filtered set the server is paging.
        pastQuery: get().pastQuery || undefined,
      });
      // Superseded by a filter change or reload started after this page: both
      // `current` and the page itself describe the old query, so appending
      // would revert the list to stale rows under a filter that no longer
      // matches them.
      if (seq !== flightsLoadSeq) {
        set({ loadingMore: false });
        return;
      }
      // The page also re-lists future + recent (always returned in full); keep
      // only the genuinely new past rows and append them to the past bucket.
      const existingIds = new Set(current.map((f) => f.id));
      const newPast = page.filter((f) => f.section === 'past' && !existingIds.has(f.id));
      set({ flights: [...current, ...newPast], pastTotal, loadingMore: false });
    } catch (err) {
      if (seq !== flightsLoadSeq) {
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
