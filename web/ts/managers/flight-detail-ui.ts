/** DOM management for the Flight Detail page. */

import type { ConditionTagId, DebriefResponse, FlightResponse, PackMeta } from '../store/types';
import type { WaypointInfo } from '../adapters/api-adapter';
import type { AircraftResponse } from '../adapters/aircraft-adapter';
import type { ProfileResponse } from '../adapters/profiles-adapter';
import { $, escapeHtml, formatDate, formatDepartureTime, formatAlt, flightTitle, flightRouteCompact, isFlightPast } from '../utils';
import { getDateLocale, t } from '../i18n/i18n';
import { buildTimezoneOptions, utcToLocal } from '../utils/timezone';
import { renderDebriefForm } from '../components/debrief-form';
import { renderDebriefSummary } from '../components/debrief-summary';
import { showRoutePopup } from '../components/route-interpret';

/** Wire any `[data-route-popup]` triggers inside `root` to open the route
 *  popup for the given flight. Idempotent: safe to call after every
 *  render. */
function wireRoutePopupTriggers(root: HTMLElement, flight: FlightResponse): void {
  const open = () => {
    void showRoutePopup(
      { waypoints: flight.waypoints, rawRoute: flight.raw_route },
      (msg) => alert(msg),
    );
  };
  root.querySelectorAll('[data-route-popup]').forEach((el) => {
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      open();
    });
    el.addEventListener('keydown', (e) => {
      const ke = e as KeyboardEvent;
      if (ke.key === 'Enter' || ke.key === ' ') {
        ke.preventDefault();
        open();
      }
    });
  });
}

// --- Assessment badge ---

function assessmentClass(assessment: string | null): string {
  if (!assessment) return 'badge-none';
  switch (assessment.toUpperCase()) {
    case 'GREEN': return 'badge-green';
    case 'AMBER': return 'badge-amber';
    case 'RED': return 'badge-red';
    default: return 'badge-none';
  }
}

function assessmentDot(assessment: string | null): string {
  const color = !assessment ? '#999' :
    assessment.toUpperCase() === 'GREEN' ? 'var(--green)' :
    assessment.toUpperCase() === 'AMBER' ? 'var(--amber)' :
    assessment.toUpperCase() === 'RED' ? 'var(--red)' : '#999';
  return `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${color};margin-right:4px;vertical-align:middle;"></span>`;
}

// --- Flight info ---

export function renderFlightInfo(
  flight: FlightResponse | null,
  editing: boolean,
  profiles: ProfileResponse[] = [],
  waypoints: WaypointInfo[] = [],
  aircraft: AircraftResponse[] = [],
): void {
  const container = $('flight-info');
  if (!container || !flight) return;

  // Parse departure time for edit form defaults
  const dt = new Date(flight.departure_time);
  const utcHour = dt.getUTCHours();
  const utcMinute = dt.getUTCMinutes();
  const endTime = new Date(dt.getTime() + flight.flight_duration_hours * 3600_000);
  const endTimeStr = `${endTime.getUTCHours().toString().padStart(2, '0')}:${endTime.getUTCMinutes().toString().padStart(2, '0')}Z`;

  // Profile display name
  const currentProfile = profiles.find(p => p.id === flight.profile_id);
  const profileName = currentProfile ? currentProfile.name : '\u2014';

  // Alt departure time parsing
  const altDt = flight.alt_departure_time ? new Date(flight.alt_departure_time) : null;
  const altUtcHour = altDt ? altDt.getUTCHours() : -1;
  const altUtcMinute = altDt ? altDt.getUTCMinutes() : 0;
  const hasAlt = altUtcHour >= 0;

  if (editing) {
    // Reference date for TZ calculations
    const refDate = new Date(`${flight.target_date}T12:00:00Z`);

    // Build timezone options from route waypoints
    const tzOptions = buildTimezoneOptions(waypoints, refDate);
    let tzHtml = '<option value="UTC">UTC</option>';
    for (const opt of tzOptions) {
      if (opt.tz === 'UTC') continue;
      tzHtml += `<option value="${escapeHtml(opt.tz)}">${escapeHtml(opt.label)}</option>`;
    }

    // Display the primary time in the first route TZ (or UTC if no waypoints)
    const defaultTz = tzOptions.length > 0 ? tzOptions[0].tz : 'UTC';
    const localTime = utcToLocal(utcHour, utcMinute, defaultTz, refDate);
    const altLocalTime = hasAlt ? utcToLocal(altUtcHour, altUtcMinute, defaultTz, refDate) : { hour: 9, minute: 0 };

    // Build hour options for primary time
    let hourOptions = '';
    for (let h = 0; h < 24; h++) {
      const sel = h === localTime.hour ? ' selected' : '';
      hourOptions += `<option value="${h}"${sel}>${h.toString().padStart(2, '0')}</option>`;
    }
    // Build minute options for primary time
    const minuteOptions = [0, 15, 30, 45].map(m => {
      const sel = m === localTime.minute ? ' selected' : '';
      return `<option value="${m}"${sel}>${m.toString().padStart(2, '0')}</option>`;
    }).join('');

    // Build hour options for alt time
    let altHourOptions = '';
    for (let h = 0; h < 24; h++) {
      const sel = h === altLocalTime.hour ? ' selected' : '';
      altHourOptions += `<option value="${h}"${sel}>${h.toString().padStart(2, '0')}</option>`;
    }
    // Build alt minute options
    const altMinuteOptions = [0, 15, 30, 45].map(m => {
      const sel = m === altLocalTime.minute ? ' selected' : '';
      return `<option value="${m}"${sel}>${m.toString().padStart(2, '0')}</option>`;
    }).join('');

    // Build profile options
    const profileOptions = profiles.map(p => {
      const sel = p.id === flight.profile_id ? ' selected' : '';
      return `<option value="${p.id}"${sel}>${escapeHtml(p.name)}</option>`;
    }).join('');

    // Build aircraft options
    const aircraftOptions = '<option value="">None</option>' + aircraft.map(ac => {
      const label = ac.tail_number
        ? `${ac.tail_number} — ${ac.type_name}`
        : ac.type_name;
      const nickname = ac.nickname ? ` (${ac.nickname})` : '';
      const sel = ac.id === flight.aircraft_id ? ' selected' : '';
      return `<option value="${ac.id}"${sel}>${escapeHtml(label + nickname)}</option>`;
    }).join('');

    container.innerHTML = `
      <div class="flight-info-grid editing">
        <div class="info-row">
          <span class="info-label">Route</span>
          <span class="info-value">
            <div style="display:flex;gap:0.5rem;align-items:stretch;">
              <input type="text" id="edit-waypoints" class="edit-input" value="${escapeHtml(flight.raw_route?.trim() || flight.waypoints.join(' '))}" placeholder="e.g. EGTK LFPB LSGS or EGTK DCT MID DCT LSGS" style="flex:1;font-family:monospace;">
              <button type="button" id="edit-preview-route" class="btn btn-outline btn-sm" title="Preview interpreted route on a map">Interpret</button>
            </div>
            <div id="edit-route-note" class="edit-structural-note" style="display:none;"></div>
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Aircraft</span>
          <span class="info-value">
            <select id="edit-aircraft" class="edit-input">${aircraftOptions}</select>
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Profile</span>
          <span class="info-value">
            <select id="edit-profile" class="edit-input">${profileOptions}</select>
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Date</span>
          <span class="info-value">
            <input type="date" id="edit-date" class="edit-input" value="${escapeHtml(flight.target_date)}">
            <div id="edit-date-note" class="edit-structural-note" style="display:none;"></div>
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Time</span>
          <span class="info-value">
            <div class="time-input-group">
              <select id="edit-hour" class="edit-input">${hourOptions}</select>
              <span class="time-separator">:</span>
              <select id="edit-minute" class="edit-input">${minuteOptions}</select>
              <select id="edit-timezone" class="edit-input">${tzHtml}</select>
            </div>
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Alt Time</span>
          <span class="info-value">
            <label class="alt-time-toggle">
              <input type="checkbox" id="edit-alt-enabled" ${hasAlt ? 'checked' : ''}>
              <span>Alternate departure</span>
            </label>
            <div class="time-input-group" id="edit-alt-time-group" style="${hasAlt ? '' : 'display:none'}">
              <select id="edit-alt-hour" class="edit-input">${altHourOptions}</select>
              <span class="time-separator">:</span>
              <select id="edit-alt-minute" class="edit-input">${altMinuteOptions}</select>
            </div>
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Altitude</span>
          <span class="info-value">
            <input type="number" id="edit-altitude" class="edit-input" value="${flight.cruise_altitude_ft}" min="1000" max="45000" step="500" style="width:80px"> ft
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Ceiling</span>
          <span class="info-value">
            <input type="number" id="edit-ceiling" class="edit-input" value="${flight.flight_ceiling_ft}" min="1000" max="45000" step="500" style="width:80px"> ft
          </span>
        </div>
        <div class="info-row">
          <span class="info-label">Duration</span>
          <span class="info-value">
            <input type="number" id="edit-duration" class="edit-input" value="${flight.flight_duration_hours}" min="0" max="24" step="0.5" style="width:70px"> hrs
          </span>
        </div>
        <div class="info-row edit-actions">
          <button class="btn btn-primary btn-sm" id="edit-save">Save</button>
          <button class="btn btn-primary btn-sm" id="edit-move" style="display:none;">Move flight</button>
          <button class="btn btn-secondary btn-sm" id="edit-duplicate" style="display:none;">Duplicate as new</button>
          <button class="btn btn-sm" id="edit-cancel">Cancel</button>
        </div>
      </div>
    `;

    // Select the default timezone in the dropdown
    const tzSelect = document.getElementById('edit-timezone') as HTMLSelectElement;
    if (tzSelect && defaultTz !== 'UTC') {
      tzSelect.value = defaultTz;
    }

    // Toggle alt time group visibility
    const altCheckbox = document.getElementById('edit-alt-enabled') as HTMLInputElement;
    const altTimeGroup = document.getElementById('edit-alt-time-group');
    altCheckbox?.addEventListener('change', () => {
      if (altTimeGroup) altTimeGroup.style.display = altCheckbox.checked ? '' : 'none';
    });

    // When profile changes, update altitude/ceiling to match the selected profile
    const profileSelect = document.getElementById('edit-profile') as HTMLSelectElement;
    profileSelect?.addEventListener('change', () => {
      const id = parseInt(profileSelect.value, 10);
      const profile = profiles.find(p => p.id === id);
      if (profile?.settings) {
        const altInput = document.getElementById('edit-altitude') as HTMLInputElement;
        const ceilInput = document.getElementById('edit-ceiling') as HTMLInputElement;
        if (altInput && profile.settings.cruise_altitude_ft != null) {
          altInput.value = String(profile.settings.cruise_altitude_ft);
        }
        if (ceilInput && profile.settings.flight_ceiling_ft != null) {
          ceilInput.value = String(profile.settings.flight_ceiling_ft);
        }
      }
    });
  } else {
    // Alt time display with delta
    let altTimeHtml = '';
    if (altDt) {
      const deltaMs = altDt.getTime() - dt.getTime();
      const deltaH = deltaMs / 3600_000;
      const sign = deltaH >= 0 ? '+' : '';
      const deltaStr = Number.isInteger(deltaH) ? `${sign}${deltaH}h` : `${sign}${deltaH.toFixed(1)}h`;
      altTimeHtml = `
        <div class="info-row">
          <span class="info-label">Alt Time</span>
          <span class="info-value">${formatDepartureTime(flight.alt_departure_time!)} <span class="muted">(${deltaStr})</span></span>
        </div>`;
    }

    const compactRoute = flightRouteCompact(flight.waypoints);
    const routeDisplay = `<span class="route-clickable" role="button" tabindex="0" data-route-popup="1" title="${escapeHtml(compactRoute.fullText)}" aria-label="Show full route on map">${compactRoute.html}<span class="route-info-icon" aria-hidden="true">\u24d8</span></span>`;

    // "Original" row: show only when the pilot's raw input added something
    // beyond the resolved chain (DCT, airways, IFR/VFR, coords, etc).
    // If the raw input was just the waypoints with single spaces, the row
    // would duplicate "Route" \u2014 skip it.
    const rawRoute = (flight.raw_route ?? '').trim();
    const normalizedRaw = rawRoute.toUpperCase().split(/\s+/).join(' ');
    const normalizedWps = flight.waypoints.join(' ').toUpperCase();
    const showOriginal = rawRoute && normalizedRaw !== normalizedWps;
    // Long Field-15 strings can be just as bulky as a long waypoint
    // chain \u2014 apply the same clickable affordance to the Original row
    // so the pilot can pop the same map view from either label. We
    // don't truncate the raw text itself (no clean ellipsis rule) but
    // we let it wrap naturally.
    const originalHtml = showOriginal
      ? `
        <div class="info-row">
          <span class="info-label">Original</span>
          <span class="info-value">
            <span class="route-clickable" role="button" tabindex="0" data-route-popup="1" title="Show route on map" aria-label="Show route on map" style="font-family:monospace;">${escapeHtml(rawRoute)}<span class="route-info-icon" aria-hidden="true">\u24d8</span></span>
          </span>
        </div>`
      : '';

    // Aircraft display (privacy-gated by server: tail_number only if owner)
    const ac = flight.aircraft;
    const aircraftDisplay = ac
      ? (ac.tail_number ? `${ac.tail_number} — ${ac.type_name}` : ac.type_name)
        + (ac.nickname ? ` (${ac.nickname})` : '')
      : '\u2014';

    container.innerHTML = `
      <div class="flight-info-grid">
        <div class="info-row">
          <span class="info-label">Route</span>
          <span class="info-value" style="font-family:monospace;">${routeDisplay}</span>
        </div>
        ${originalHtml}
        <div class="info-row">
          <span class="info-label">Aircraft</span>
          <span class="info-value">${escapeHtml(aircraftDisplay)}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Profile</span>
          <span class="info-value">${escapeHtml(profileName)}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Date</span>
          <span class="info-value">${formatDate(flight.target_date)}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Time</span>
          <span class="info-value">${formatDepartureTime(flight.departure_time)}</span>
        </div>
        ${altTimeHtml}
        <div class="info-row">
          <span class="info-label">Altitude</span>
          <span class="info-value">${formatAlt(flight.cruise_altitude_ft)}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Ceiling</span>
          <span class="info-value">${formatAlt(flight.flight_ceiling_ft)}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Duration</span>
          <span class="info-value">${flight.flight_duration_hours}h</span>
        </div>
        <div class="info-row">
          <span class="info-label">End time</span>
          <span class="info-value">${endTimeStr}</span>
        </div>
      </div>
    `;
    wireRoutePopupTriggers(container, flight);
  }
}

// --- Flight header ---

export function renderHeader(
  flight: FlightResponse | null,
  editing: boolean,
): void {
  const el = $('flight-header');
  if (!el) return;

  if (!flight) {
    el.innerHTML = '<h1>Flight</h1>';
    return;
  }

  const wps = flight.waypoints.length > 0
    ? flight.waypoints
    : flight.route_name.split('_').map(w => w.toUpperCase());
  const title = flightTitle(wps);
  const compactRoute = wps.length > 2 ? flightRouteCompact(wps) : null;

  const isOwner = flight.role === 'owner';
  const editBtn = (editing || !isOwner)
    ? ''
    : '<button class="btn btn-sm" id="btn-edit-flight">Edit</button>';

  const routeLine = compactRoute
    ? `<div class="flight-detail-route">
         <span class="route-clickable" role="button" tabindex="0" data-route-popup="1" title="${escapeHtml(compactRoute.fullText)}" aria-label="Show full route on map">${compactRoute.html}<span class="route-info-icon" aria-hidden="true">ⓘ</span></span>
       </div>`
    : '';

  el.innerHTML = `
    <div class="flight-detail-title">
      <h1>${escapeHtml(title)} ${editBtn}</h1>
      ${routeLine}
    </div>
  `;
  wireRoutePopupTriggers(el, flight);
}

// --- Sharing banner ---

export function renderSharingBanner(flight: FlightResponse | null): void {
  const el = $('flight-sharing-banner');
  if (!el) return;
  if (!flight) {
    el.innerHTML = '';
    el.style.display = 'none';
    return;
  }

  const isOwner = flight.role === 'owner';
  if (isOwner) {
    // Private flights can't be shared — recipients would hit 404. Disable
    // the button (matching the briefing page) rather than just adding a
    // text note; the title/aria-label explain why.
    const disabledAttr = flight.private ? ' disabled' : '';
    const btnTitle = flight.private ? t('briefing.sharePrivateTitle') : t('briefing.shareTitle');
    const privateNote = flight.private
      ? `<span class="muted sharing-private-note">${t('flightDetail.sharingPrivateNote')}</span>`
      : '';
    el.innerHTML = `
      <div class="sharing-banner sharing-banner-owner">
        <button class="btn btn-sm btn-copy-share-link" type="button" title="${escapeHtml(btnTitle)}" aria-label="${escapeHtml(btnTitle)}"${disabledAttr}>${t('flightDetail.copyShareLink')}</button>
        ${privateNote}
      </div>
    `;
  } else {
    const ownerLabel = flight.owner_display_name
      ? t('flightDetail.sharedBy', { owner: escapeHtml(flight.owner_display_name) })
      : t('flightDetail.sharedByUnknown');
    const subscribeBtn = flight.is_subscribed
      ? `<button class="btn btn-sm btn-unsubscribe-flight" type="button">${t('flightDetail.btnUnsubscribe')}</button>`
      : `<button class="btn btn-sm btn-subscribe-flight" type="button">${t('flightDetail.btnSubscribe')}</button>`;
    el.innerHTML = `
      <div class="sharing-banner sharing-banner-subscriber">
        <span class="sharing-owner-label">${ownerLabel}</span>
        ${subscribeBtn}
      </div>
    `;
  }
  el.style.display = '';
}

// --- Latest assessment ---

export function renderLatestAssessment(pack: PackMeta | null): void {
  const el = $('latest-assessment');
  if (!el) return;

  if (!pack) {
    el.innerHTML = '<span class="muted">No briefings yet</span>';
    return;
  }

  const reason = pack.assessment_reason ? ` \u2014 "${escapeHtml(pack.assessment_reason)}"` : '';
  el.innerHTML = `
    <span class="badge ${assessmentClass(pack.assessment)}">
      ${escapeHtml(pack.assessment || '\u2014')}
    </span>
    <span class="assessment-reason">${reason}</span>
  `;
}

// --- Briefing history ---

export function renderBriefingHistory(
  packs: PackMeta[],
  flightId: string,
): void {
  const container = $('briefing-history');
  if (!container) return;

  if (packs.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p>No briefings yet. Go to the <a href="/briefing.html?flight=${encodeURIComponent(flightId)}">briefing page</a> to trigger a refresh.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = packs.map((pack) => {
    const ts = new Date(pack.fetch_timestamp);
    const dateStr = ts.toLocaleDateString(getDateLocale(), {
      day: 'numeric', month: 'short', timeZone: 'UTC',
    });
    const timeStr = ts.toLocaleTimeString(getDateLocale(), {
      hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
    });
    const briefingUrl = `/briefing.html?flight=${encodeURIComponent(flightId)}&pack=${encodeURIComponent(pack.fetch_timestamp)}`;

    return `
      <a href="${briefingUrl}" class="history-row">
        <span class="history-days-out">D-${pack.days_out}</span>
        <span class="history-date">${dateStr} ${timeStr} UTC</span>
        <span class="badge ${assessmentClass(pack.assessment)}">${escapeHtml(pack.assessment || '\u2014')}</span>
        <span class="history-arrow">\u2192</span>
      </a>
    `;
  }).join('');
}

// --- Invalidation banner ---

export function renderInvalidationBanner(invalidation: string | null, flightId: string): void {
  const el = $('invalidation-banner');
  if (!el) return;

  if (!invalidation) {
    el.style.display = 'none';
    return;
  }

  el.style.display = 'block';
  const briefingUrl = `/briefing.html?flight=${encodeURIComponent(flightId)}`;

  if (invalidation === 'advisories_only') {
    el.className = 'invalidation-banner invalidation-info';
    el.innerHTML = `Profile or altitude changed. Advisories can be recalculated from saved data on the <a href="${briefingUrl}">briefing page</a>.`;
  } else if (invalidation === 'refetch_needed') {
    el.className = 'invalidation-banner invalidation-warning';
    el.innerHTML = `Time or duration changed. A new briefing refresh is needed on the <a href="${briefingUrl}">briefing page</a> for updated forecasts.`;
  }
}

// --- Loading / Error ---

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

// --- Debrief section ---

/** Render the debrief panel — banner / summary / editor — for a past flight.
 *
 * Hides the entire section for future or non-owned flights. The editor
 * mounts into ``#debrief-host`` on demand (clicking Add/Edit). After save
 * or delete, ``onChanged`` is invoked so the page can refetch.
 */
export function renderDebriefSection(
  flight: FlightResponse | null,
  flaggedCategories: ConditionTagId[],
  onChanged: () => void,
): void {
  const section = $('debrief-section');
  const banner = $('debrief-banner');
  const summary = $('debrief-summary');
  const host = $('debrief-host') as HTMLElement | null;
  if (!section || !banner || !summary || !host) return;

  // Visibility rules: owner + past flight only.
  const isOwner = flight?.role === 'owner';
  const isPast = flight && isFlightPast(
    flight.target_date, flight.target_time_utc, flight.flight_duration_hours, flight.departure_time,
  );
  if (!flight || !isOwner || !isPast) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  host.dataset.flightId = flight.id;

  const debrief = flight.debrief ?? null;
  if (debrief) {
    banner.innerHTML = `<button type="button" class="btn btn-secondary" id="debrief-edit-btn">${t('debrief.editCta')}</button>`;
    summary.innerHTML = renderDebriefSummary(debrief);
  } else {
    banner.innerHTML = `<button type="button" class="btn btn-primary" id="debrief-edit-btn">${t('debrief.addCta')}</button>`;
    summary.innerHTML = '';
  }

  const openEditor = () => {
    if (host.dataset.open === '1') {
      host.dataset.open = '';
      host.innerHTML = '';
      return;
    }
    host.dataset.open = '1';
    renderDebriefForm(host, {
      existing: debrief,
      flaggedCategories,
      onSaved: () => { host.dataset.open = ''; host.innerHTML = ''; onChanged(); },
      onDeleted: () => { host.dataset.open = ''; host.innerHTML = ''; onChanged(); },
      onCancelled: () => { host.dataset.open = ''; host.innerHTML = ''; },
    });
  };
  document.getElementById('debrief-edit-btn')?.addEventListener('click', openEditor);
}

