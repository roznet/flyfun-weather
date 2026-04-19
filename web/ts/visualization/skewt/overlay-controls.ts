/**
 * Toggle UI for Skew-T overlay layers and side panel variable selection.
 * Overlays: compact checkboxes.
 * Side panel: two dropdowns (primary + secondary variable).
 */

import { SKEWT_OVERLAYS } from './overlay-bands';
import { VARIABLE_REGISTRY } from './variable-panel';
import type { SkewTRenderer } from './renderer';

/** Render the overlay toggle controls and side panel selector. */
export function renderSkewtOverlayControls(
  container: HTMLElement,
  renderer: SkewTRenderer,
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

  // Overlay toggles
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
    }
    html += `</span>`;
  }
  html += '</div>';

  // Side panel variable dropdowns
  html += '<div class="skewt-panel-selector">';
  html += '<span class="skewt-overlay-group-label">Side panel</span>';

  // Primary dropdown
  html += `<select class="skewt-panel-dropdown" data-axis="primary" title="Primary variable (bottom axis)">`;
  for (const v of VARIABLE_REGISTRY) {
    const sel = v.id === primaryId ? 'selected' : '';
    html += `<option value="${v.id}" ${sel}>${v.shortLabel} — ${v.label}</option>`;
  }
  html += `</select>`;

  // Secondary dropdown
  html += `<select class="skewt-panel-dropdown" data-axis="secondary" title="Secondary variable (top axis)">`;
  html += `<option value=""${!secondaryId ? ' selected' : ''}>None</option>`;
  for (const v of VARIABLE_REGISTRY) {
    const sel = v.id === secondaryId ? 'selected' : '';
    html += `<option value="${v.id}" ${sel}>${v.shortLabel} — ${v.label}</option>`;
  }
  html += `</select>`;

  html += '</div>';
  html += '</div>';

  container.innerHTML = html;

  // Attach overlay toggle listeners
  container.querySelectorAll<HTMLInputElement>('input[data-overlay]').forEach(input => {
    input.addEventListener('change', () => {
      renderer.toggleOverlay(input.dataset.overlay!);
    });
  });

  // Attach dropdown listeners
  container.querySelectorAll<HTMLSelectElement>('.skewt-panel-dropdown').forEach(select => {
    select.addEventListener('change', () => {
      if (select.dataset.axis === 'primary') {
        renderer.setPrimaryVar(select.value);
      } else {
        renderer.setSecondaryVar(select.value || null);
      }
    });
  });
}

function groupLabel(group: string): string {
  switch (group) {
    case 'clouds': return 'Clouds';
    case 'icing': return 'Icing';
    case 'stability': return 'Stability';
    default: return group;
  }
}
