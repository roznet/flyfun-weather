import { escapeHtml } from '../utils';
import type { MotionState } from './state';
import type { AssociationRecord, FeatureRecord, Interval, MotionTime, ScalarObservation } from './types';

export interface ObservedMotionViewCallbacks {
  onToggle(active: boolean): void;
  onSelectTime(time: MotionTime): void;
  onSelectFeature(featureId: string | null): void;
  onSelectAssociation(associationId: string | null): void;
  onSourceToggle(family: 'radar_echo' | 'high_cloud_top', enabled: boolean): void;
}

const reasonText = (reason: string): string => reason.replace(/_/g, ' ');
const utcDate = (value: string): string => `${value.slice(0, 10)} ${value.slice(11, 16)}Z`;
const number = (value: number | null, suffix: string): string => value == null ? 'Unavailable' : `${value.toFixed(Number.isInteger(value) ? 0 : 1)} ${suffix}`;

function contour(feature: FeatureRecord): string {
  return feature.family === 'radar_echo' ? 'Radar echo contour ≥ 5 dBZ' : 'High cloud-top contour ≥ 15,000 ft MSL';
}

function reasons(values: string[]): string {
  return values.length ? `<span class="motion-reasons">${values.map(reason => escapeHtml(reasonText(reason))).join(' · ')}</span>` : '';
}

function observationLine(observation: ScalarObservation): string {
  const label = observation.kind === 'rain_rate_max' ? 'Rain rate' : observation.kind === 'cloud_top_max' ? 'Cloud top' : 'Reflectivity';
  const unit = observation.unit === 'mm_h' ? 'mm/h' : observation.unit === 'm_msl' ? 'm MSL' : 'dBZ';
  const paired = observation.kind === 'cloud_top_max' && observation.paired_temperature_k != null
    ? `; paired temperature ${observation.paired_temperature_k.toFixed(1)} K` : '';
  return `${label}: ${number(observation.value, unit)}${paired}${observation.observed_at ? ` observed ${utcDate(observation.observed_at)}` : ''}`;
}

function intervalText(value: Interval): string {
  return `${utcDate(value.start_at)}–${utcDate(value.end_at)}`;
}

function completenessText(value: Record<string, unknown>): string {
  const count = (raw: unknown): string => typeof raw === 'number' && Number.isSafeInteger(raw) && raw >= 0 ? String(raw) : 'unknown';
  const category = reasonText(String(value.category ?? 'unknown scope')) + (value.category === 'small_detections' ? ' (untracked)' : '');
  return `${category}: ${reasonText(String(value.status ?? 'unknown'))}; considered ${count(value.considered_count)}, emitted ${count(value.emitted_count)}, omitted ${count(value.omitted_count)}`;
}

function featureDetail(feature: FeatureRecord, selectedTime: MotionTime): string {
  const motion = feature.motion.status === 'accepted'
    ? `Ground speed ${number(feature.motion.ground_speed_kt, 'kt')}; bearing ${number(feature.motion.bearing_deg_true, '° true')}`
    : `Motion unavailable ${reasons(feature.motion.reason_codes)}`;
  const observations = feature.observations.length
    ? `<ul>${feature.observations.map(item => `<li>${escapeHtml(observationLine(item))}${reasons(item.reason_codes)}</li>`).join('')}</ul>` : '<p>Scalar evidence unavailable.</p>';
  const lightning = feature.lightning_evidence.reported_detection_count == null
    ? `Lightning evidence unavailable ${reasons(feature.lightning_evidence.reason_codes)}`
    : `${feature.lightning_evidence.reported_detection_count} reported detections; ${feature.lightning_evidence.emitted_marker_count} map markers${feature.lightning_evidence.evaluation_complete ? '' : ' (partial/lower bound)'} ${reasons(feature.lightning_evidence.reason_codes)}`;
  const rows = feature.route_rows.map(row => `<tr><th scope="row">${escapeHtml(`${row.from_label}–${row.to_label}`)}</th><td>${escapeHtml(utcDate(row.at))}</td><td>${escapeHtml(number(row.distance_nm, 'NM'))}</td><td>${row.closure_kt == null ? 'Not applicable' : `Closure ${escapeHtml(number(row.closure_kt, 'kt'))}${row.closure_interval ? ` over ${escapeHtml(intervalText(row.closure_interval))}` : ''}`}</td><td>${escapeHtml(row.relationship)}</td><td>Planned overlap at this instant: ${row.planned_overlap_at_time == null ? 'Unavailable' : row.planned_overlap_at_time ? 'Yes' : 'No'}</td></tr>`).join('');
  const overlap = feature.planned_overlap.status === 'available'
    ? feature.planned_overlap.intervals.map(item => `${escapeHtml(item.leg_id)}: ${intervalText(item)} (${item.contact}, approximate)`).join('<br>') || 'No overlap calculated for this tracked contour under this model in the evaluated interval (complete evaluation).'
    : `Unavailable ${reasons(feature.planned_overlap.reason_codes)}`;
  const selectedProjection = selectedTime === 'observed' ? '' : (() => {
    const projection = feature.projections.find(item => item.at === selectedTime);
    return projection?.status === 'available' && projection.display_geometry.status === 'available'
      ? `<p>Projected contour available for ${escapeHtml(utcDate(selectedTime))}. ${reasons(projection.reason_codes)}</p>`
      : `<p>Projection unavailable for ${escapeHtml(utcDate(selectedTime))}. ${reasons(projection?.reason_codes ?? ['unsupported_time'])}</p>`;
  })();
  return `<article class="observed-motion-detail-card">
    <h5>${escapeHtml(contour(feature))}</h5>
    <p>Source reference ${escapeHtml(utcDate(feature.reference_at))}. ${escapeHtml(motion)}</p>
    ${reasons([...feature.reason_codes, ...feature.motion.reason_codes, ...feature.display_geometry.reason_codes])}
    ${observations}
    <p>${lightning}<br>Evaluated lightning window: ${feature.lightning_evidence.evaluated_window ? escapeHtml(intervalText(feature.lightning_evidence.evaluated_window)) : 'Unavailable'}. ${feature.lightning_evidence.evaluation_complete ? 'Complete for stated local inputs; not a coverage guarantee.' : 'Evaluation incomplete; positive counts are lower bounds.'}</p>
    ${selectedProjection}
    <div class="motion-table-wrap"><table id="observed-motion-route-table"><caption>Selected-feature route leg and server time results. Positive closure is toward this leg and is not ground speed.</caption><thead><tr><th>Leg</th><th>At UTC</th><th>Distance</th><th>Closure</th><th>Relationship</th><th>Planned timing</th></tr></thead><tbody>${rows || '<tr><td colspan="6">No evaluated route rows.</td></tr>'}</tbody></table></div>
    <p id="observed-motion-overlap"><strong>Approximate planned-overlap intervals:</strong><br>Evaluated planned interval: ${feature.planned_overlap.evaluated_interval ? escapeHtml(intervalText(feature.planned_overlap.evaluated_interval)) : 'Unavailable'}.<br>${overlap}<br><small>Model- and interval-scoped analysis, not a route verdict, aircraft alarm or clearance.</small></p>
  </article>`;
}

function associationDetail(association: AssociationRecord, features: FeatureRecord[]): string {
  const radar = features.find(feature => feature.feature_id === association.radar_feature_id);
  const cloud = features.find(feature => feature.feature_id === association.cloud_feature_id);
  return `<article class="observed-motion-detail-card"><h5>Source-time association</h5>
    <p>${escapeHtml(contour(radar!))} and ${escapeHtml(contour(cloud!))}; ${escapeHtml(association.relation ?? 'unavailable')} at ${association.comparison_at ? escapeHtml(utcDate(association.comparison_at)) : 'an unavailable comparison time'}.</p>
    ${reasons(association.reason_codes)}<p>Association measurements use analysis-grid contours and retain each feature's independent vector.</p></article>`;
}

export class ObservedMotionView {
  constructor(private container: HTMLElement, private state: MotionState, private callbacks: ObservedMotionViewCallbacks) {}

  render(): void {
    const active = document.activeElement as HTMLElement | null;
    const focusKey = active?.id || active?.dataset.motionFeatureId || active?.dataset.motionAssociationId || null;
    const motion = this.state.current?.motion;
    let html = `<button type="button" id="observed-motion-toggle" class="btn-toggle observed-motion-toggle" aria-pressed="${this.state.modeActive}">Experimental motion</button>`;
    if (this.state.modeActive) {
      const status = this.statusText();
      const selectedFeature = motion?.features.find(feature => feature.feature_id === this.state.selectedFeatureId);
      const selectedAssociation = motion?.associations.find(association => association.association_id === this.state.selectedAssociationId);
      const familyControl = (family: 'radar_echo' | 'high_cloud_top', label: string): string => {
        const features = motion?.features.filter(feature => feature.family === family) ?? [];
        const sourceRecords = features.map(feature => motion?.sources.find(source => source.source_id === feature.source_id)).filter(Boolean);
        const available = features.length > 0 && sourceRecords.some(source => source?.status === 'available');
        const sourceReasons = sourceRecords.flatMap(source => source?.reason_codes ?? []);
        const why = available ? '' : ` <span class="motion-reasons">Unavailable: ${escapeHtml(reasonText(sourceReasons[0] ?? 'missing_source'))}</span>`;
        return `<label><input type="checkbox" data-motion-family="${family}" ${this.state.sourceEnabled[family] ? 'checked' : ''}${available ? '' : ' disabled'}> ${label}${why}</label>`;
      };
      const allReasons = motion ? [...new Set([
        ...motion.reason_codes,
        ...((motion.analysis_domain?.reason_codes as string[] | undefined) ?? []),
        ...motion.sources.flatMap(source => [...source.reason_codes, ...source.geolocation.reason_codes]),
        ...motion.features.flatMap(feature => [...feature.reason_codes, ...feature.motion.reason_codes, ...feature.display_geometry.reason_codes,
          ...feature.projections.flatMap(projection => [...projection.reason_codes, ...projection.display_geometry.reason_codes]),
          ...feature.route_rows.flatMap(row => [...row.reason_codes, ...row.planned_time_reason_codes]), ...feature.planned_overlap.reason_codes,
          ...feature.lightning_evidence.reason_codes]),
        ...motion.associations.flatMap(association => association.reason_codes),
        ...motion.completeness.flatMap(item => Array.isArray(item.reason_codes) ? item.reason_codes.filter((value): value is string => typeof value === 'string') : []),
        ...this.state.current!.validationReasons,
      ])] : this.state.presentationReasons;
      html += `<section id="observed-motion-panel" class="observed-motion-panel" aria-label="Experimental observed motion explorer">
        <p id="observed-motion-status" class="observed-motion-status" role="status">${escapeHtml(status)}</p>
        <p class="motion-experimental-note">Experimental constant-motion analysis. Server contours and route results only; no probability, storm diagnosis, safe-route, vertical-clearance, forecast-skill or clearance claim. Ground speed, edge-to-leg closure and simultaneous planned overlap are distinct.</p>
        <div class="observed-motion-controls">
          ${familyControl('radar_echo', 'Radar echo ≥ 5 dBZ')}
          ${familyControl('high_cloud_top', 'High cloud top ≥ 15,000 ft MSL')}
          <label>Server UTC geometry <select id="observed-motion-time"><option value="observed">Observed source times</option>${(motion?.projection_times ?? []).map(time => `<option value="${escapeHtml(time)}"${this.state.selectedTime === time ? ' selected' : ''}>${escapeHtml(utcDate(time))}</option>`).join('')}</select></label>
        </div>
        <div class="observed-motion-grid">
          <div><h4>Features</h4><div class="observed-motion-feature-list" role="list">${(motion?.features ?? []).map(feature => `<button type="button" role="listitem" class="observed-motion-feature" data-motion-feature-id="${escapeHtml(feature.feature_id)}" aria-pressed="${feature.feature_id === this.state.selectedFeatureId}"><strong>${escapeHtml(contour(feature))}</strong><span>${escapeHtml(utcDate(feature.reference_at))}</span><span>${feature.motion.status === 'accepted' ? `${escapeHtml(number(feature.motion.ground_speed_kt, 'kt'))} · ${escapeHtml(number(feature.motion.bearing_deg_true, '° true'))}` : 'Motion unavailable'}</span>${reasons([...feature.reason_codes, ...feature.motion.reason_codes])}</button>`).join('') || '<p>No validated feature records are available.</p>'}</div></div>
          <div><h4>Associations</h4>${(motion?.associations ?? []).map(association => `<button type="button" class="observed-motion-association" data-motion-association-id="${escapeHtml(association.association_id)}" aria-pressed="${association.association_id === this.state.selectedAssociationId}">${escapeHtml(association.relation ?? 'Unavailable')} · ${association.comparison_at ? escapeHtml(utcDate(association.comparison_at)) : 'time unavailable'}</button>`).join('') || '<p>No validated source-time associations.</p>'}</div>
        </div>
        <div id="observed-motion-detail">${selectedFeature ? featureDetail(selectedFeature, this.state.selectedTime) : selectedAssociation ? associationDetail(selectedAssociation, motion?.features ?? []) : '<p>Select a feature or association for source-timed evidence and route results.</p>'}</div>
        <details class="observed-motion-limitations" open><summary>Limitations and unavailable evidence</summary><ul>${(motion?.completeness ?? []).map(item => `<li>${escapeHtml(completenessText(item))}</li>`).join('')}</ul>${allReasons.length ? `<ul>${allReasons.map(reason => `<li>${escapeHtml(reasonText(reason))}</li>`).join('')}</ul>` : '<p>No additional reason codes were supplied.</p>'}</details>
      </section>`;
    }
    this.container.innerHTML = html;
    this.bind();
    if (focusKey) {
      const target = this.container.querySelector<HTMLElement>(`#${CSS.escape(focusKey)}, [data-motion-feature-id="${CSS.escape(focusKey)}"], [data-motion-association-id="${CSS.escape(focusKey)}"]`);
      target?.focus();
    }
  }

  destroy(): void { this.container.replaceChildren(); }

  private statusText(): string {
    const reasons = this.state.presentationReasons;
    if (reasons.includes('refresh_failed')) return 'Refresh failed. Stored analysis retains its original source and calculation times.';
    if (reasons.includes('expired')) return `Stored analysis · Expired selection ${this.state.selectedTime === 'observed' ? '' : utcDate(this.state.selectedTime)}. Refresh or inspect Observed.`;
    if (this.state.capability === 'disabled') return 'Stored analysis · server capability is disabled; active projected geometry is removed.';
    if (this.state.capability === 'unknown') return 'Stored analysis · capability unavailable; active prediction styling is not authorized.';
    if (reasons.includes('stored_analysis')) return 'Stored analysis · current motion authority does not authorize active prediction styling.';
    if (this.state.selectedTime !== 'observed') return `Experimental constant-motion projection · ${utcDate(this.state.selectedTime)}.`;
    return this.state.current?.status === 'available' ? 'Observed source-timed analysis. Select a server UTC projection to inspect it.'
      : `Motion unavailable · ${reasonText(this.state.current?.unavailableReason ?? 'missing legacy data')}.`;
  }

  private bind(): void {
    this.container.querySelector('#observed-motion-toggle')?.addEventListener('click', () => this.callbacks.onToggle(!this.state.modeActive));
    this.container.querySelector('#observed-motion-time')?.addEventListener('change', event => this.callbacks.onSelectTime((event.currentTarget as HTMLSelectElement).value));
    this.container.querySelectorAll<HTMLInputElement>('[data-motion-family]').forEach(input => input.addEventListener('change', () =>
      this.callbacks.onSourceToggle(input.dataset.motionFamily as 'radar_echo' | 'high_cloud_top', input.checked)));
    this.container.querySelectorAll<HTMLElement>('[data-motion-feature-id]').forEach(button => button.addEventListener('click', () =>
      this.callbacks.onSelectFeature(button.dataset.motionFeatureId === this.state.selectedFeatureId ? null : button.dataset.motionFeatureId ?? null)));
    this.container.querySelectorAll<HTMLElement>('[data-motion-association-id]').forEach(button => button.addEventListener('click', () =>
      this.callbacks.onSelectAssociation(button.dataset.motionAssociationId === this.state.selectedAssociationId ? null : button.dataset.motionAssociationId ?? null)));
  }
}
