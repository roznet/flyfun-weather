"use strict";
(() => {
  // node_modules/zustand/esm/vanilla.mjs
  var createStoreImpl = (createState) => {
    let state;
    const listeners = /* @__PURE__ */ new Set();
    const setState = (partial, replace) => {
      const nextState = typeof partial === "function" ? partial(state) : partial;
      if (!Object.is(nextState, state)) {
        const previousState = state;
        state = (replace != null ? replace : typeof nextState !== "object" || nextState === null) ? nextState : Object.assign({}, state, nextState);
        listeners.forEach((listener) => listener(state, previousState));
      }
    };
    const getState = () => state;
    const getInitialState = () => initialState;
    const subscribe = (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    };
    const api = { setState, getState, getInitialState, subscribe };
    const initialState = state = createState(setState, getState, api);
    return api;
  };
  var createStore = ((createState) => createState ? createStoreImpl(createState) : createStoreImpl);

  // ts/adapters/api-adapter.ts
  var API_BASE = "/api";
  async function apiFetch(path, init2) {
    const resp = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...init2?.headers },
      ...init2
    });
    if (!resp.ok) {
      const body = await resp.text();
      let detail;
      try {
        detail = JSON.parse(body).detail || body;
      } catch {
        detail = body;
      }
      throw new Error(`API ${resp.status}: ${detail}`);
    }
    if (resp.status === 204) return void 0;
    return resp.json();
  }
  async function fetchRoutes() {
    return apiFetch("/routes");
  }
  async function fetchFlights() {
    return apiFetch("/flights");
  }
  async function createFlight(req) {
    return apiFetch("/flights", {
      method: "POST",
      body: JSON.stringify(req)
    });
  }
  async function deleteFlight(id) {
    return apiFetch(`/flights/${encodeURIComponent(id)}`, {
      method: "DELETE"
    });
  }
  async function fetchLatestPack(flightId) {
    return apiFetch(`/flights/${encodeURIComponent(flightId)}/packs/latest`);
  }

  // ts/store/flights-store.ts
  var flightsStore = createStore((set, get) => ({
    routes: [],
    flights: [],
    latestPacks: {},
    loading: false,
    error: null,
    loadRoutes: async () => {
      try {
        const routes = await fetchRoutes();
        set({ routes });
      } catch (err) {
        set({ error: `Failed to load routes: ${err}` });
      }
    },
    loadFlights: async () => {
      set({ loading: true, error: null });
      try {
        const flights = await fetchFlights();
        set({ flights, loading: false });
        const packs = {};
        await Promise.all(
          flights.map(async (f) => {
            try {
              packs[f.id] = await fetchLatestPack(f.id);
            } catch {
              packs[f.id] = null;
            }
          })
        );
        set({ latestPacks: packs });
      } catch (err) {
        set({ loading: false, error: `Failed to load flights: ${err}` });
      }
    },
    createFlight: async (routeName, targetDate, opts) => {
      set({ loading: true, error: null });
      try {
        const flight = await createFlight({
          route_name: routeName,
          target_date: targetDate,
          target_time_utc: opts?.targetTimeUtc,
          cruise_altitude_ft: opts?.cruiseAltitudeFt,
          flight_duration_hours: opts?.flightDurationHours
        });
        await get().loadFlights();
        set({ loading: false });
        return flight;
      } catch (err) {
        set({ loading: false, error: `Failed to create flight: ${err}` });
        throw err;
      }
    },
    deleteFlight: async (id) => {
      set({ loading: true, error: null });
      try {
        await deleteFlight(id);
        await get().loadFlights();
        set({ loading: false });
      } catch (err) {
        set({ loading: false, error: `Failed to delete flight: ${err}` });
      }
    }
  }));

  // ts/managers/flights-ui.ts
  function $(id) {
    return document.getElementById(id);
  }
  function formatDate(iso) {
    const d = /* @__PURE__ */ new Date(iso + "T00:00:00Z");
    return d.toLocaleDateString("en-GB", {
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC"
    });
  }
  function formatTime(hour) {
    return `${hour.toString().padStart(2, "0")}00Z`;
  }
  function formatAlt(ft) {
    if (ft >= 1e4) return `FL${Math.round(ft / 100)}`;
    return `${ft}ft`;
  }
  function assessmentClass(assessment) {
    if (!assessment) return "badge-none";
    switch (assessment.toUpperCase()) {
      case "GREEN":
        return "badge-green";
      case "AMBER":
        return "badge-amber";
      case "RED":
        return "badge-red";
      default:
        return "badge-none";
    }
  }
  function renderFlightList(flights, latestPacks, routes, onView, onDelete) {
    const container = $("flight-list");
    if (!container) return;
    if (flights.length === 0) {
      container.innerHTML = `
      <div class="empty-state">
        <p>No flights yet. Create one to get started.</p>
      </div>
    `;
      return;
    }
    const routeMap = new Map(routes.map((r) => [r.name, r]));
    container.innerHTML = flights.map((f) => {
      const pack = latestPacks[f.id];
      const route = routeMap.get(f.route_name);
      const waypoints = route ? route.waypoints.join(" \u2192 ") : f.route_name.replace(/_/g, " ").toUpperCase();
      const packInfo = pack ? `<span class="pack-info">D-${pack.days_out} (${new Date(pack.fetch_timestamp).toLocaleDateString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC)</span>
         <span class="badge ${assessmentClass(pack.assessment)}">${pack.assessment || "\u2014"}</span>` : '<span class="pack-info">No briefings yet</span>';
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
    }).join("");
    container.querySelectorAll(".btn-view").forEach((btn) => {
      btn.addEventListener("click", () => {
        onView(btn.dataset.id);
      });
    });
    container.querySelectorAll(".btn-delete").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.id;
        if (confirm(`Delete flight ${id}? This removes all briefing history.`)) {
          onDelete(id);
        }
      });
    });
  }
  function renderRouteOptions(routes) {
    const select = $("route-select");
    if (!select) return;
    select.innerHTML = '<option value="">Select a route...</option>' + routes.map(
      (r) => `<option value="${r.name}" data-alt="${r.cruise_altitude_ft}" data-dur="${r.flight_duration_hours}">${r.display_name} (${r.waypoints.join(" \u2192 ")})</option>`
    ).join("");
  }
  function renderLoading(loading) {
    const spinner = $("loading-spinner");
    if (spinner) {
      spinner.style.display = loading ? "block" : "none";
    }
  }
  function renderError(error) {
    const el = $("error-message");
    if (el) {
      el.textContent = error || "";
      el.style.display = error ? "block" : "none";
    }
  }
  function onRouteSelected(routes) {
    const select = $("route-select");
    if (!select) return;
    select.addEventListener("change", () => {
      const route = routes.find((r) => r.name === select.value);
      if (!route) return;
      const altInput = $("input-altitude");
      const durInput = $("input-duration");
      if (altInput) altInput.value = String(route.cruise_altitude_ft);
      if (durInput) durInput.value = String(route.flight_duration_hours);
    });
  }

  // ts/flights-main.ts
  function init() {
    const store = flightsStore;
    store.subscribe((state, prev) => {
      if (state.flights !== prev.flights || state.latestPacks !== prev.latestPacks || state.routes !== prev.routes) {
        renderFlightList(
          state.flights,
          state.latestPacks,
          state.routes,
          (id) => navigateToBriefing(id),
          (id) => store.getState().deleteFlight(id)
        );
      }
      if (state.routes !== prev.routes) {
        renderRouteOptions(state.routes);
        onRouteSelected(state.routes);
      }
      if (state.loading !== prev.loading) {
        renderLoading(state.loading);
      }
      if (state.error !== prev.error) {
        renderError(state.error);
      }
    });
    const form = document.getElementById("create-flight-form");
    if (form) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const routeName = document.getElementById("route-select").value;
        const targetDate = document.getElementById("input-date").value;
        const targetTime = parseInt(document.getElementById("input-time").value || "9", 10);
        const altitude = parseInt(document.getElementById("input-altitude").value || "8000", 10);
        const duration = parseFloat(document.getElementById("input-duration").value || "0");
        if (!routeName || !targetDate) {
          renderError("Please select a route and date.");
          return;
        }
        try {
          const flight = await store.getState().createFlight(routeName, targetDate, {
            targetTimeUtc: targetTime,
            cruiseAltitudeFt: altitude,
            flightDurationHours: duration
          });
          navigateToBriefing(flight.id);
        } catch {
        }
      });
    }
    store.getState().loadRoutes();
    store.getState().loadFlights();
  }
  function navigateToBriefing(flightId) {
    window.location.href = `/briefing.html?flight=${encodeURIComponent(flightId)}`;
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
//# sourceMappingURL=flights.js.map
