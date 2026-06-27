/**
 * Toggle UI for Skew-T overlay layers, side panel variable selection,
 * and info (i) buttons for overlays, variables, and indices.
 */

import { SKEWT_OVERLAYS } from './overlay-bands';
import { VARIABLE_REGISTRY, VARIABLE_GROUPS } from './variable-panel';
import type { VariableDef } from './variable-panel';
import { renderInfoButton } from '../../helpers/metrics-helper';
import { modelLabel } from '../../utils';
import type { SkewTRenderer } from './renderer';
import type { SkewTCompareRenderer } from './compare-renderer';

/** Indices shown in the canvas panel — info buttons rendered here in HTML. */
const INDEX_METRICS = [
  { label: 'CAPE', metricId: 'cape_surface_jkg' },
  { label: 'CIN', metricId: 'cin_surface_jkg' },
  { label: 'LI', metricId: 'lifted_index' },
  { label: 'PW', metricId: 'precipitable_water_mm' },
  { label: '0\u00b0C', metricId: 'freezing_level_ft' },
];

/** Look up the metric catalog ID for a variable. */
function getVarMetricId(varId: string): string {
  const v = VARIABLE_REGISTRY.find(r => r.id === varId);
  return v?.metricId ?? '';
}

/** Common interface for renderers that expose side panel variable selection. */
interface SidePanelVarHost {
  getPrimaryVar(): string;
  getSecondaryVar(): string | null;
  setPrimaryVar(id: string): void;
  setSecondaryVar(id: string | null): void;
}

/** Render <option> entries grouped by VariableDef.group as <optgroup> blocks. */
function renderGroupedOptions(selectedId: string | null): string {
  let html = '';
  for (const group of VARIABLE_GROUPS) {
    const inGroup: VariableDef[] = VARIABLE_REGISTRY.filter(v => v.group === group);
    if (inGroup.length === 0) continue;
    html += `<optgroup label="${group}">`;
    for (const v of inGroup) {
      const sel = v.id === selectedId ? 'selected' : '';
      html += `<option value="${v.id}" ${sel}>${v.shortLabel} \u2014 ${v.label}</option>`;
    }
    html += '</optgroup>';
  }
  return html;
}

/** Generate HTML for primary + secondary side panel dropdowns with info buttons. */
function renderSidePanelSelectorHtml(primaryId: string, secondaryId: string | null): string {
  let html = '<div class="skewt-panel-selector">';
  html += '<span class="skewt-overlay-group-label">Side panel</span>';

  // Primary dropdown + info button
  html += '<select class="skewt-panel-dropdown" data-axis="primary" title="Primary variable (bottom axis)">';
  html += renderGroupedOptions(primaryId);
  html += '</select>';
  const primaryMetric = getVarMetricId(primaryId);
  if (primaryMetric) {
    html += `<button class="metric-info-btn skewt-panel-info" data-axis="primary" data-metric="${primaryMetric}" title="More info" aria-label="More info">\u24d8</button>`;
  }

  // Secondary dropdown + info button
  html += '<select class="skewt-panel-dropdown" data-axis="secondary" title="Secondary variable (top axis)">';
  html += `<option value=""${!secondaryId ? ' selected' : ''}>None</option>`;
  html += renderGroupedOptions(secondaryId);
  html += '</select>';
  const secondaryMetric = secondaryId ? getVarMetricId(secondaryId) : '';
  html += `<button class="metric-info-btn skewt-panel-info" data-axis="secondary" data-metric="${secondaryMetric}" title="More info" aria-label="More info"${!secondaryMetric ? ' style="display:none"' : ''}>\u24d8</button>`;

  html += '</div>';
  return html;
}

/** Wire change listeners on side panel dropdowns, updating renderer and info buttons. */
function wireSidePanelDropdowns(container: HTMLElement, renderer: SidePanelVarHost, onUserEdit?: () => void): void {
  container.querySelectorAll<HTMLSelectElement>('.skewt-panel-dropdown').forEach(select => {
    select.addEventListener('change', () => {
      const axis = select.dataset.axis;
      if (axis === 'primary') {
        renderer.setPrimaryVar(select.value);
      } else {
        renderer.setSecondaryVar(select.value || null);
      }
      onUserEdit?.();
      const infoBtn = container.querySelector(`.skewt-panel-info[data-axis="${axis}"]`) as HTMLElement | null;
      if (infoBtn) {
        const newMetricId = getVarMetricId(select.value);
        infoBtn.dataset.metric = newMetricId;
        infoBtn.style.display = newMetricId ? '' : 'none';
      }
    });
  });
}

/** Hooks for the single-model Skew-T controls (#308). */
export interface SkewtOverlayCallbacks {
  /** Fired after any user-initiated overlay/side-panel change so the caller can
   *  drop the active-preset label to "Custom" (mirrors the cross-section). */
  onUserEdit?: () => void;
  /** Fired when the "Help me read this graph" button is clicked. When omitted
   *  the button is not rendered. */
  onHelp?: () => void;
}

/** Render the overlay toggle controls and side panel selector. */
export function renderSkewtOverlayControls(
  container: HTMLElement,
  renderer: SkewTRenderer,
  callbacks: SkewtOverlayCallbacks = {},
): void {
  const overlayState = renderer.getOverlayState();
  const primaryId = renderer.getPrimaryVar();
  const secondaryId = renderer.getSecondaryVar();

  // Group overlays
  const groups = new Map<string, typeof SKEWT_OVERLAYS>();
  for (const overlay of SKEWT_OVERLAYS) {
    const list = groups.get(overlay.group) ?? [];
    list.push(overlay);
    groups.set(overlay.group, list);
  }

  let html = '<div class="skewt-controls-row">';

  // Overlay toggles with info buttons
  html += '<div class="skewt-overlay-controls">';
  for (const [group, overlays] of groups) {
    html += `<span class="skewt-overlay-group">`;
    html += `<span class="skewt-overlay-group-label">${groupLabel(group)}</span>`;
    for (const overlay of overlays) {
      const checked = overlayState[overlay.id] ? 'checked' : '';
      html += `<label class="skewt-overlay-toggle" title="${overlay.label}">`;
      html += `<input type="checkbox" data-overlay="${overlay.id}" ${checked}>`;
      html += `<span class="skewt-overlay-name">${overlay.label}</span>`;
      html += `</label>`;
      if (overlay.metricId) {
        html += renderInfoButton(overlay.metricId);
      }
    }
    html += `</span>`;
  }
  html += '</div>';

  // Side panel variable dropdowns with dynamic info buttons
  html += renderSidePanelSelectorHtml(primaryId, secondaryId);

  // Indices info buttons row
  html += '<div class="skewt-indices-info">';
  html += '<span class="skewt-overlay-group-label">Indices</span>';
  for (const idx of INDEX_METRICS) {
    html += `<button class="metric-info-btn skewt-index-info" data-metric="${idx.metricId}" title="${idx.label}" aria-label="${idx.label} info">${idx.label} \u24d8</button>`;
  }
  html += '</div>';

  // "Help me read this graph" button (#308 Phase B) — explains the current view.
  if (callbacks.onHelp) {
    html += '<div class="skewt-help-row">';
    html += `<button class="btn-chip skewt-help-btn" data-skewt-help="1" title="Explain this Skew-T in plain language">ⓘ Help me read this graph</button>`;
    html += '</div>';
  }

  html += '</div>';

  container.innerHTML = html;

  // Attach overlay toggle listeners
  container.querySelectorAll<HTMLInputElement>('input[data-overlay]').forEach(input => {
    input.addEventListener('change', () => {
      renderer.toggleOverlay(input.dataset.overlay!);
      callbacks.onUserEdit?.();
    });
  });

  // Attach dropdown listeners — update adjacent info button on change
  wireSidePanelDropdowns(container, renderer, callbacks.onUserEdit);

  // Wire the help button
  const helpBtn = container.querySelector<HTMLElement>('[data-skewt-help]');
  if (helpBtn && callbacks.onHelp) helpBtn.addEventListener('click', () => callbacks.onHelp!());
}

/** Callbacks for Skew-T compare controls. */
export interface SkewtCompareCallbacks {
  onModelToggle: (model: string, enabled: boolean) => void;
  onCapeCinToggle: () => void;
  onLevelMarkersToggle: () => void;
}

/** Render controls for Skew-T compare mode: model chips, toggles, side panel. */
export function renderSkewtCompareControls(
  container: HTMLElement,
  renderer: SkewTCompareRenderer,
  availableModels: string[],
  compareModels: Record<string, boolean>,
  primaryModel: string,
  modelColors: Record<string, string>,
  callbacks: SkewtCompareCallbacks,
): void {
  const primaryId = renderer.getPrimaryVar();
  const secondaryId = renderer.getSecondaryVar();

  let html = '<div class="skewt-controls-row">';

  // Model chips
  html += '<div class="skewt-compare-model-chips">';
  html += '<span class="skewt-overlay-group-label">Models</span>';
  for (const m of availableModels) {
    const enabled = compareModels[m] !== false;
    const activeClass = enabled ? ' active' : '';
    const isPrimary = m === primaryModel;
    const color = modelColors[m] ?? '#888';
    const borderStyle = enabled ? `border-color:${color}` : '';
    const badge = isPrimary ? ' <span class="chip-primary-badge">\u2605</span>' : '';
    html += `<button class="btn-chip${activeClass}" data-compare-model="${m}" style="${borderStyle}">${modelLabel(m)}${badge}</button>`;
  }
  html += '</div>';

  // Optional overlays
  html += '<div class="skewt-compare-toggles">';
  html += '<span class="skewt-overlay-group-label">Show</span>';
  const capeCinChecked = renderer.getShowCapeCin() ? 'checked' : '';
  html += `<label class="skewt-overlay-toggle"><input type="checkbox" data-toggle="cape-cin" ${capeCinChecked}><span class="skewt-overlay-name">CAPE/CIN</span></label>`;
  const levelsChecked = renderer.getShowLevelMarkers() ? 'checked' : '';
  html += `<label class="skewt-overlay-toggle"><input type="checkbox" data-toggle="level-markers" ${levelsChecked}><span class="skewt-overlay-name">Levels (LCL/LFC/EL)</span></label>`;
  html += '</div>';

  // Side panel variable dropdowns
  html += renderSidePanelSelectorHtml(primaryId, secondaryId);
  html += '</div>';

  container.innerHTML = html;

  // Wire model chips
  container.querySelectorAll<HTMLElement>('[data-compare-model]').forEach(btn => {
    btn.addEventListener('click', () => {
      const model = btn.dataset.compareModel!;
      const currentlyActive = btn.classList.contains('active');
      callbacks.onModelToggle(model, !currentlyActive);
    });
  });

  // Wire toggle checkboxes
  const capeCinInput = container.querySelector<HTMLInputElement>('input[data-toggle="cape-cin"]');
  if (capeCinInput) capeCinInput.addEventListener('change', () => callbacks.onCapeCinToggle());
  const levelsInput = container.querySelector<HTMLInputElement>('input[data-toggle="level-markers"]');
  if (levelsInput) levelsInput.addEventListener('change', () => callbacks.onLevelMarkersToggle());

  // Wire dropdown listeners
  wireSidePanelDropdowns(container, renderer);
}

function groupLabel(group: string): string {
  switch (group) {
    case 'clouds': return 'Clouds';
    case 'icing': return 'Icing';
    case 'stability': return 'Stability';
    default: return group;
  }
}
