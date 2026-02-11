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
  async function fetchFlight(id) {
    return apiFetch(`/flights/${encodeURIComponent(id)}`);
  }
  async function fetchPacks(flightId) {
    return apiFetch(`/flights/${encodeURIComponent(flightId)}/packs`);
  }
  async function fetchPack(flightId, timestamp) {
    return apiFetch(
      `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}`
    );
  }
  async function refreshBriefing(flightId) {
    return apiFetch(
      `/flights/${encodeURIComponent(flightId)}/packs/refresh`,
      { method: "POST" }
    );
  }
  async function fetchSnapshot(flightId, timestamp) {
    return apiFetch(
      `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/snapshot`
    );
  }
  function grametUrl(flightId, timestamp) {
    return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/gramet`;
  }
  function skewtUrl(flightId, timestamp, icao, model) {
    return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/skewt/${encodeURIComponent(icao)}/${encodeURIComponent(model)}`;
  }
  function digestUrl(flightId, timestamp) {
    return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/digest`;
  }

  // ts/store/briefing-store.ts
  var briefingStore = createStore((set, get) => ({
    flight: null,
    packs: [],
    currentPack: null,
    snapshot: null,
    selectedModel: "gfs",
    loading: false,
    refreshing: false,
    error: null,
    loadFlight: async (id) => {
      set({ loading: true, error: null });
      try {
        const flight = await fetchFlight(id);
        set({ flight, loading: false });
        await get().loadPacks();
        await get().selectLatest();
      } catch (err) {
        set({ loading: false, error: `Failed to load flight: ${err}` });
      }
    },
    loadPacks: async () => {
      const flight = get().flight;
      if (!flight) return;
      try {
        const packs = await fetchPacks(flight.id);
        set({ packs });
      } catch (err) {
        set({ error: `Failed to load packs: ${err}` });
      }
    },
    selectPack: async (timestamp) => {
      const flight = get().flight;
      if (!flight) return;
      set({ loading: true, error: null });
      try {
        const pack = await fetchPack(flight.id, timestamp);
        let snapshot = null;
        try {
          snapshot = await fetchSnapshot(flight.id, timestamp);
        } catch {
        }
        set({ currentPack: pack, snapshot, loading: false });
      } catch (err) {
        set({ loading: false, error: `Failed to load pack: ${err}` });
      }
    },
    selectLatest: async () => {
      const { packs } = get();
      if (packs.length > 0) {
        await get().selectPack(packs[0].fetch_timestamp);
      }
    },
    refresh: async () => {
      const flight = get().flight;
      if (!flight) return;
      set({ refreshing: true, error: null });
      try {
        const newPack = await refreshBriefing(flight.id);
        await get().loadPacks();
        await get().selectPack(newPack.fetch_timestamp);
        set({ refreshing: false });
      } catch (err) {
        set({ refreshing: false, error: `Refresh failed: ${err}` });
      }
    },
    setSelectedModel: (model) => {
      set({ selectedModel: model });
    }
  }));

  // ts/managers/briefing-ui.ts
  function $(id) {
    return document.getElementById(id);
  }
  function renderHeader(flight) {
    const el = $("briefing-header");
    if (!el || !flight) return;
    const route = flight.route_name.replace(/_/g, " \u2192 ").toUpperCase();
    const date = /* @__PURE__ */ new Date(flight.target_date + "T00:00:00Z");
    const dateStr = date.toLocaleDateString("en-GB", {
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC"
    });
    const timeStr = `${flight.target_time_utc.toString().padStart(2, "0")}00Z`;
    const alt = flight.cruise_altitude_ft >= 1e4 ? `FL${Math.round(flight.cruise_altitude_ft / 100)}` : `${flight.cruise_altitude_ft}ft`;
    el.innerHTML = `
    <span class="route-summary">${route}</span>
    <span class="date-summary">${dateStr} ${timeStr}</span>
    <span class="alt-summary">${alt}</span>
  `;
  }
  function renderHistoryDropdown(packs, currentTimestamp, onSelect) {
    const select = $("history-select");
    if (!select) return;
    select.innerHTML = packs.length === 0 ? "<option>No briefings yet</option>" : packs.map((p) => {
      const date = new Date(p.fetch_timestamp);
      const label = `D-${p.days_out} (${date.toLocaleDateString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC)`;
      const selected = p.fetch_timestamp === currentTimestamp ? " selected" : "";
      return `<option value="${p.fetch_timestamp}"${selected}>${label}</option>`;
    }).join("");
    const newSelect = select.cloneNode(true);
    select.parentNode.replaceChild(newSelect, select);
    newSelect.addEventListener("change", () => {
      onSelect(newSelect.value);
    });
  }
  function renderAssessment(pack) {
    const el = $("assessment-banner");
    if (!el) return;
    if (!pack || !pack.assessment) {
      el.className = "assessment-banner assessment-none";
      el.textContent = "No assessment available";
      return;
    }
    const level = pack.assessment.toUpperCase();
    el.className = `assessment-banner assessment-${level.toLowerCase()}`;
    el.innerHTML = `
    <strong>${level}</strong>${pack.assessment_reason ? ` \u2014 ${pack.assessment_reason}` : ""}
  `;
  }
  function renderSynopsis(flight, pack) {
    const el = $("synopsis-section");
    if (!el) return;
    if (!flight || !pack || !pack.has_digest) {
      el.innerHTML = '<p class="muted">Synopsis not available. Trigger a refresh to generate.</p>';
      return;
    }
    el.innerHTML = '<p class="muted">Loading digest...</p>';
    digestUrl(flight.id, pack.fetch_timestamp);
  }
  function renderGramet(flight, pack) {
    const el = $("gramet-section");
    if (!el) return;
    if (!flight || !pack || !pack.has_gramet) {
      el.innerHTML = '<p class="muted">GRAMET not available for this briefing.</p>';
      return;
    }
    const url = grametUrl(flight.id, pack.fetch_timestamp);
    el.innerHTML = `
    <img src="${url}" alt="GRAMET cross-section" class="gramet-img" loading="lazy">
  `;
  }
  function renderModelComparison(snapshot) {
    const el = $("comparison-section");
    if (!el) return;
    if (!snapshot || snapshot.analyses.length === 0) {
      el.innerHTML = '<p class="muted">No model comparison data available.</p>';
      return;
    }
    el.innerHTML = snapshot.analyses.map((a) => {
      if (a.model_divergence.length === 0) return "";
      const models = Object.keys(a.model_divergence[0]?.model_values || {});
      const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join("");
      const rows = a.model_divergence.map((d) => {
        const valueCells = models.map((m) => {
          const val = d.model_values[m];
          return `<td>${val !== void 0 ? val.toFixed(1) : "\u2014"}</td>`;
        }).join("");
        const agreeIcon = d.agreement === "good" ? "&#10003;" : d.agreement === "moderate" ? "&#9888;" : "&#10007;";
        const agreeClass = `agree-${d.agreement}`;
        return `
        <tr>
          <td class="var-name">${formatVarName(d.variable)}</td>
          ${valueCells}
          <td>${d.spread.toFixed(1)}</td>
          <td class="${agreeClass}">${agreeIcon}</td>
        </tr>
      `;
      }).join("");
      return `
      <div class="comparison-waypoint">
        <h4>${a.waypoint.icao} \u2014 ${a.waypoint.name}</h4>
        <table class="comparison-table">
          <thead>
            <tr>
              <th>Variable</th>
              ${headerCells}
              <th>Spread</th>
              <th>Agree</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
    }).join("");
  }
  function formatVarName(name) {
    const labels = {
      "temperature_c": "Temp (\xB0C)",
      "wind_speed_kt": "Wind (kt)",
      "wind_direction_deg": "Wind dir (\xB0)",
      "cloud_cover_pct": "Cloud (%)",
      "precipitation_mm": "Precip (mm)",
      "freezing_level_m": "Freezing (m)"
    };
    return labels[name] || name;
  }
  function renderSkewTs(flight, pack, snapshot, selectedModel) {
    const el = $("skewt-section");
    if (!el) return;
    if (!flight || !pack || !pack.has_skewt || !snapshot) {
      el.innerHTML = '<p class="muted">Skew-T diagrams not available.</p>';
      return;
    }
    const waypoints = snapshot.route.waypoints;
    el.innerHTML = `
    <div class="skewt-gallery">
      ${waypoints.map((wp) => {
      const url = skewtUrl(flight.id, pack.fetch_timestamp, wp.icao, selectedModel);
      return `
          <div class="skewt-card">
            <h4>${wp.icao}</h4>
            <img src="${url}" alt="Skew-T ${wp.icao} ${selectedModel}"
                 class="skewt-img" loading="lazy"
                 onerror="this.parentElement.classList.add('skewt-unavailable')">
            <div class="skewt-fallback">Not available</div>
          </div>
        `;
    }).join("")}
    </div>
  `;
  }
  function renderLoading(loading) {
    const el = $("loading-overlay");
    if (el) el.style.display = loading ? "flex" : "none";
  }
  function renderRefreshing(refreshing) {
    const btn = $("refresh-btn");
    if (btn) {
      btn.disabled = refreshing;
      btn.textContent = refreshing ? "Refreshing..." : "Refresh";
    }
  }
  function renderError(error) {
    const el = $("error-message");
    if (el) {
      el.textContent = error || "";
      el.style.display = error ? "block" : "none";
    }
  }

  // ts/briefing-main.ts
  function init() {
    const store = briefingStore;
    const params = new URLSearchParams(window.location.search);
    const flightId = params.get("flight");
    if (!flightId) {
      renderError("No flight specified. Go back to flights list.");
      return;
    }
    store.subscribe((state, prev) => {
      if (state.flight !== prev.flight) {
        renderHeader(state.flight);
      }
      if (state.packs !== prev.packs || state.currentPack !== prev.currentPack) {
        renderHistoryDropdown(
          state.packs,
          state.currentPack?.fetch_timestamp || null,
          (ts) => store.getState().selectPack(ts)
        );
      }
      if (state.currentPack !== prev.currentPack || state.snapshot !== prev.snapshot) {
        renderAssessment(state.currentPack);
        renderSynopsis(state.flight, state.currentPack);
        renderGramet(state.flight, state.currentPack);
        renderModelComparison(state.snapshot);
        renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel);
      }
      if (state.selectedModel !== prev.selectedModel) {
        renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel);
      }
      if (state.loading !== prev.loading) {
        renderLoading(state.loading);
      }
      if (state.refreshing !== prev.refreshing) {
        renderRefreshing(state.refreshing);
      }
      if (state.error !== prev.error) {
        renderError(state.error);
      }
    });
    const refreshBtn = document.getElementById("refresh-btn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        store.getState().refresh();
      });
    }
    const modelSelect = document.getElementById("model-select");
    if (modelSelect) {
      modelSelect.addEventListener("change", () => {
        store.getState().setSelectedModel(modelSelect.value);
      });
    }
    const backBtn = document.getElementById("back-btn");
    if (backBtn) {
      backBtn.addEventListener("click", () => {
        window.location.href = "/";
      });
    }
    store.getState().loadFlight(flightId);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
//# sourceMappingURL=briefing.js.map
