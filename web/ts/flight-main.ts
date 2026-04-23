/** Flight detail page entry point — wires store, UI manager, map inset, and edit mode. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { fetchAircraft, type AircraftResponse } from './adapters/aircraft-adapter';
import { fetchProfiles, type ProfileResponse } from './adapters/profiles-adapter';
import { flightDetailStore } from './store/flight-detail-store';
import * as ui from './managers/flight-detail-ui';
import { RouteMapInset } from './components/route-map-inset';
import { copyFlightShareLink, redirectToLogin, renderUserInfo } from './utils';
import { initTheme } from './theme';
import { initI18n, t } from './i18n/i18n';
import { localToUtc, utcToLocal } from './utils/timezone';
import { interpretAndConfirmRoute } from './components/route-interpret';
import { createFlight, moveFlight } from './adapters/api-adapter';

async function init(): Promise<void> {
  await initI18n();
  // Auth check
  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    return;
  }
  initTheme();
  renderUserInfo(user);

  const store = flightDetailStore;

  // Get flight ID from URL
  const params = new URLSearchParams(window.location.search);
  const flightId = params.get('id');
  if (!flightId) {
    ui.renderError('No flight specified. Go back to flights list.');
    return;
  }

  // Load profiles and aircraft for the selectors
  let profiles: ProfileResponse[] = [];
  let aircraftList: AircraftResponse[] = [];
  try {
    [profiles, aircraftList] = await Promise.all([
      fetchProfiles().catch(() => [] as ProfileResponse[]),
      fetchAircraft().catch(() => [] as AircraftResponse[]),
    ]);
  } catch {
    // Selectors stay empty; editing still works without them
  }

  // --- Route map inset ---
  const mapContainer = document.getElementById('route-map-inset');
  let mapInset: RouteMapInset | null = null;
  if (mapContainer) {
    mapInset = new RouteMapInset(mapContainer);
  }

  // --- Wire edit mode ---
  function wireEditButtons(): void {
    const editBtn = document.getElementById('btn-edit-flight');
    editBtn?.addEventListener('click', () => {
      store.getState().startEditing();
    });
  }

  /** Internal UTC times — stored so TZ changes can re-display the same instant. */
  let editUtcHour = 9;
  let editUtcMinute = 0;
  let editAltUtcHour = 9;
  let editAltUtcMinute = 0;

  /** Helper to get the reference date from the current flight. */
  function getEditRefDate(): Date {
    const flight = store.getState().flight;
    const dateStr = flight?.target_date || new Date().toISOString().slice(0, 10);
    return new Date(`${dateStr}T12:00:00Z`);
  }

  /** Read the selected TZ and convert displayed local times to internal UTC. */
  function syncUtcFromLocal(): void {
    const hourEl = document.getElementById('edit-hour') as HTMLSelectElement;
    const minuteEl = document.getElementById('edit-minute') as HTMLSelectElement;
    const tzEl = document.getElementById('edit-timezone') as HTMLSelectElement;
    if (!hourEl || !minuteEl || !tzEl) return;
    const tz = tzEl.value;
    const refDate = getEditRefDate();
    const utc = localToUtc(parseInt(hourEl.value, 10), parseInt(minuteEl.value, 10), tz, refDate);
    editUtcHour = utc.hour;
    editUtcMinute = utc.minute;

    const altHourEl = document.getElementById('edit-alt-hour') as HTMLSelectElement;
    const altMinuteEl = document.getElementById('edit-alt-minute') as HTMLSelectElement;
    if (altHourEl && altMinuteEl) {
      const altUtc = localToUtc(parseInt(altHourEl.value, 10), parseInt(altMinuteEl.value, 10), tz, refDate);
      editAltUtcHour = altUtc.hour;
      editAltUtcMinute = altUtc.minute;
    }
  }

  /** Re-display internal UTC times in the currently selected timezone. */
  function redisplayTimesInTz(): void {
    const hourEl = document.getElementById('edit-hour') as HTMLSelectElement;
    const minuteEl = document.getElementById('edit-minute') as HTMLSelectElement;
    const tzEl = document.getElementById('edit-timezone') as HTMLSelectElement;
    if (!hourEl || !minuteEl || !tzEl) return;
    const tz = tzEl.value;
    const refDate = getEditRefDate();
    const local = utcToLocal(editUtcHour, editUtcMinute, tz, refDate);
    hourEl.value = String(local.hour);
    minuteEl.value = String(local.minute);

    const altHourEl = document.getElementById('edit-alt-hour') as HTMLSelectElement;
    const altMinuteEl = document.getElementById('edit-alt-minute') as HTMLSelectElement;
    if (altHourEl && altMinuteEl) {
      const altLocal = utcToLocal(editAltUtcHour, editAltUtcMinute, tz, refDate);
      altHourEl.value = String(altLocal.hour);
      altMinuteEl.value = String(altLocal.minute);
    }
  }

  /** Read the current edit-form state and detect whether the user changed any
   *  structural field (date, origin, destination). Mid-route waypoint changes
   *  are NOT structural — they're handled by PATCH/Save. */
  function detectStructuralChange(): { dateChanged: boolean; routeChanged: boolean; newWaypoints: string[]; newDate: string } {
    const flight = store.getState().flight;
    const dateEl = document.getElementById('edit-date') as HTMLInputElement;
    const wpEl = document.getElementById('edit-waypoints') as HTMLInputElement;
    const newDate = dateEl?.value || flight?.target_date || '';
    const wpRaw = (wpEl?.value || '').trim();
    const newWaypoints = wpRaw
      ? wpRaw.split(/[\s,]+/).filter(Boolean).map(w => w.toUpperCase())
      : (flight?.waypoints || []);
    const dateChanged = !!flight && newDate !== flight.target_date;
    const oldOrigin = flight?.waypoints[0] || '';
    const oldDest = flight?.waypoints[flight.waypoints.length - 1] || '';
    const newOrigin = newWaypoints[0] || '';
    const newDest = newWaypoints[newWaypoints.length - 1] || '';
    const routeChanged = !!flight && (newOrigin !== oldOrigin || newDest !== oldDest);
    return { dateChanged, routeChanged, newWaypoints, newDate };
  }

  /** Update Save vs Move/Duplicate button visibility + inline notes based on
   *  whether the user has changed a structural field. */
  function updateStructuralUi(): void {
    const { dateChanged, routeChanged } = detectStructuralChange();
    const structural = dateChanged || routeChanged;

    const saveBtn = document.getElementById('edit-save') as HTMLButtonElement | null;
    const moveBtn = document.getElementById('edit-move') as HTMLButtonElement | null;
    const dupBtn = document.getElementById('edit-duplicate') as HTMLButtonElement | null;
    if (saveBtn) saveBtn.style.display = structural ? 'none' : '';
    if (moveBtn) moveBtn.style.display = structural ? '' : 'none';
    if (dupBtn) dupBtn.style.display = structural ? '' : 'none';

    const dateNote = document.getElementById('edit-date-note');
    if (dateNote) {
      dateNote.style.display = dateChanged ? '' : 'none';
      if (dateChanged) {
        const packCount = store.getState().packs.length;
        dateNote.textContent = t('flightDetail.dateChangedNote', { count: packCount });
      }
    }
    const routeNote = document.getElementById('edit-route-note');
    if (routeNote) {
      routeNote.style.display = routeChanged ? '' : 'none';
      if (routeChanged) {
        const packCount = store.getState().packs.length;
        routeNote.textContent = t('flightDetail.routeChangedNote', { count: packCount });
      }
    }
  }

  function wireEditForm(): void {
    const saveBtn = document.getElementById('edit-save');
    const moveBtn = document.getElementById('edit-move');
    const dupBtn = document.getElementById('edit-duplicate');
    const cancelBtn = document.getElementById('edit-cancel');

    // Initialize internal UTC times from the flight
    const flight = store.getState().flight;
    if (flight) {
      const dt = new Date(flight.departure_time);
      editUtcHour = dt.getUTCHours();
      editUtcMinute = dt.getUTCMinutes();
      if (flight.alt_departure_time) {
        const altDt = new Date(flight.alt_departure_time);
        editAltUtcHour = altDt.getUTCHours();
        editAltUtcMinute = altDt.getUTCMinutes();
      }
    }

    // Wire timezone change → re-display both times in new TZ
    const tzEl = document.getElementById('edit-timezone') as HTMLSelectElement;
    tzEl?.addEventListener('change', redisplayTimesInTz);

    // Wire hour/minute change → track internal UTC values
    const hourEl = document.getElementById('edit-hour') as HTMLSelectElement;
    const minuteEl = document.getElementById('edit-minute') as HTMLSelectElement;
    hourEl?.addEventListener('change', syncUtcFromLocal);
    minuteEl?.addEventListener('change', syncUtcFromLocal);

    // Wire alt hour/minute change
    const altHourEl = document.getElementById('edit-alt-hour') as HTMLSelectElement;
    const altMinuteEl = document.getElementById('edit-alt-minute') as HTMLSelectElement;
    altHourEl?.addEventListener('change', syncUtcFromLocal);
    altMinuteEl?.addEventListener('change', syncUtcFromLocal);

    // Wire structural-field listeners → toggle Save vs Move/Duplicate buttons
    const dateEl = document.getElementById('edit-date') as HTMLInputElement;
    const wpEl = document.getElementById('edit-waypoints') as HTMLInputElement;
    dateEl?.addEventListener('change', updateStructuralUi);
    dateEl?.addEventListener('input', updateStructuralUi);
    wpEl?.addEventListener('input', updateStructuralUi);
    wpEl?.addEventListener('blur', updateStructuralUi);
    updateStructuralUi();  // initial state

    /** Build the merged structural payload for Move and Duplicate. Returns null if
     *  waypoints fail interpretation (errors already shown to user). */
    async function buildStructuralPayload(): Promise<{
      departure_time: string;
      waypoints: string[];
    } | null> {
      if (!flight) return null;
      syncUtcFromLocal();

      const wpRaw = (wpEl?.value || '').trim();
      let waypoints: string[] = flight.waypoints;
      if (wpRaw && wpRaw.toUpperCase() !== flight.waypoints.join(' ').toUpperCase()) {
        const result = await interpretAndConfirmRoute(wpRaw, (msg) => ui.renderError(msg));
        if (!result || !result.confirmed) return null;
        waypoints = result.waypoints;
      }

      const newDate = dateEl?.value || flight.target_date;
      const departureTime = `${newDate}T${editUtcHour.toString().padStart(2, '0')}:${editUtcMinute.toString().padStart(2, '0')}:00Z`;
      return { departure_time: departureTime, waypoints };
    }

    saveBtn?.addEventListener('click', async () => {
      if (!flight) return;

      const profileEl = document.getElementById('edit-profile') as HTMLSelectElement;
      const aircraftEl = document.getElementById('edit-aircraft') as HTMLSelectElement;
      const altEl = document.getElementById('edit-altitude') as HTMLInputElement;
      const ceilEl = document.getElementById('edit-ceiling') as HTMLInputElement;
      const durEl = document.getElementById('edit-duration') as HTMLInputElement;
      const altEnabledEl = document.getElementById('edit-alt-enabled') as HTMLInputElement;

      syncUtcFromLocal();

      const profileId = profileEl ? parseInt(profileEl.value, 10) : undefined;
      const aircraftId = aircraftEl?.value ? parseInt(aircraftEl.value, 10) : undefined;
      const altitude = parseInt(altEl.value, 10);
      const ceiling = parseInt(ceilEl.value, 10);
      const duration = parseFloat(durEl.value);

      // Save handles non-structural changes only — date/origin/dest stay the same.
      const departureTime = `${flight.target_date}T${editUtcHour.toString().padStart(2, '0')}:${editUtcMinute.toString().padStart(2, '0')}:00Z`;
      const altEnabled = altEnabledEl?.checked ?? false;
      const altDepartureTime = altEnabled
        ? `${flight.target_date}T${editAltUtcHour.toString().padStart(2, '0')}:${editAltUtcMinute.toString().padStart(2, '0')}:00Z`
        : '';

      // Mid-route waypoint changes still go through Save (origin/dest unchanged → not structural).
      let newWaypoints: string[] | undefined;
      const wpRaw = wpEl?.value?.trim();
      if (wpRaw && wpRaw.toUpperCase() !== flight.waypoints.join(' ').toUpperCase()) {
        const result = await interpretAndConfirmRoute(wpRaw, (msg) => ui.renderError(msg));
        if (!result || !result.confirmed) return;
        newWaypoints = result.waypoints;
      }

      await store.getState().saveFlight({
        profile_id: profileId,
        aircraft_id: aircraftId,
        departure_time: departureTime,
        alt_departure_time: altDepartureTime,
        cruise_altitude_ft: altitude,
        flight_ceiling_ft: ceiling,
        flight_duration_hours: duration,
        ...(newWaypoints ? { waypoints: newWaypoints } : {}),
      });

      if (newWaypoints) store.getState().loadWaypoints();
    });

    moveBtn?.addEventListener('click', async () => {
      if (!flight) return;
      const payload = await buildStructuralPayload();
      if (!payload) return;
      const packCount = store.getState().packs.length;
      const msg = packCount > 0
        ? t('flightDetail.moveConfirmWithPacks', { count: packCount })
        : t('flightDetail.moveConfirmNoPacks');
      if (!confirm(msg)) return;
      try {
        const newFlight = await moveFlight(flight.id, payload);
        window.location.href = `/flight.html?id=${encodeURIComponent(newFlight.id)}`;
      } catch (err) {
        const m = err instanceof Error ? err.message : String(err);
        ui.renderError(m.replace(/^API \d+:\s*/, ''));
      }
    });

    dupBtn?.addEventListener('click', async () => {
      if (!flight) return;
      const payload = await buildStructuralPayload();
      if (!payload) return;
      try {
        const newFlight = await createFlight({
          waypoints: payload.waypoints,
          departure_time: payload.departure_time,
          cruise_altitude_ft: flight.cruise_altitude_ft,
          flight_ceiling_ft: flight.flight_ceiling_ft,
          flight_duration_hours: flight.flight_duration_hours,
          profile_id: flight.profile_id ?? undefined,
          aircraft_id: flight.aircraft_id ?? undefined,
        });
        window.location.href = `/flight.html?id=${encodeURIComponent(newFlight.id)}`;
      } catch (err) {
        const m = err instanceof Error ? err.message : String(err);
        ui.renderError(m.replace(/^API \d+:\s*/, ''));
      }
    });

    cancelBtn?.addEventListener('click', () => {
      store.getState().cancelEditing();
    });
  }

  function wireSharingControls(): void {
    const banner = document.getElementById('flight-sharing-banner');
    if (!banner) return;
    banner.querySelector('.btn-subscribe-flight')?.addEventListener('click', () => {
      void store.getState().subscribe();
    });
    banner.querySelector('.btn-unsubscribe-flight')?.addEventListener('click', () => {
      if (confirm(t('flightDetail.unsubscribeConfirm'))) {
        void store.getState().unsubscribe();
      }
    });
    banner.querySelector('.btn-copy-share-link')?.addEventListener('click', async () => {
      const { flight, packs } = store.getState();
      if (!flight) return;
      const latestPack = packs.length > 0 ? packs[0] : null;
      const copied = await copyFlightShareLink(flight.id, latestPack?.fetch_timestamp);
      if (!copied) return;  // fell back to prompt(); user has already seen the URL
      const btn = banner.querySelector('.btn-copy-share-link') as HTMLButtonElement | null;
      if (btn) {
        const original = btn.textContent;
        btn.textContent = t('flightDetail.copyShareLinkCopied');
        btn.disabled = true;
        setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2000);
      }
    });
  }

  // --- Subscribe to state changes ---
  store.subscribe((state, prev) => {
    if (state.flight !== prev.flight || state.editing !== prev.editing || state.waypoints !== prev.waypoints) {
      ui.renderHeader(state.flight, state.editing);
      ui.renderFlightInfo(state.flight, state.editing, profiles, state.waypoints, aircraftList);
      ui.renderSharingBanner(state.flight);
      wireSharingControls();
      if (state.editing) {
        wireEditForm();
      } else {
        wireEditButtons();
      }
    }
    if (state.waypoints !== prev.waypoints) {
      if (mapInset && state.waypoints.length > 0) {
        mapInset.render(state.waypoints);
      }
    }
    if (state.packs !== prev.packs) {
      const latestPack = state.packs.length > 0 ? state.packs[0] : null;
      ui.renderLatestAssessment(latestPack);
      ui.renderBriefingHistory(state.packs, flightId);
    }
    if (state.invalidation !== prev.invalidation) {
      ui.renderInvalidationBanner(state.invalidation, flightId);
    }
    if (state.loading !== prev.loading) {
      ui.renderLoading(state.loading);
    }
    if (state.error !== prev.error) {
      ui.renderError(state.error);
    }
  });

  // --- Load flight data ---
  await store.getState().loadFlight(flightId);

  // Initial render (subscriptions don't fire on first load for existing data)
  const s = store.getState();
  ui.renderHeader(s.flight, s.editing);
  ui.renderFlightInfo(s.flight, s.editing, profiles, s.waypoints);
  ui.renderSharingBanner(s.flight);
  wireSharingControls();
  wireEditButtons();

  if (s.waypoints.length > 0 && mapInset) {
    mapInset.render(s.waypoints);
  }

  const latestPack = s.packs.length > 0 ? s.packs[0] : null;
  ui.renderLatestAssessment(latestPack);
  ui.renderBriefingHistory(s.packs, flightId);
  ui.renderLoading(s.loading);

  // Invalidate map size after layout settles
  requestAnimationFrame(() => {
    if (mapInset) mapInset.invalidateSize();
  });
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
