/**
 * Toggle UI for Skew-T overlay layers.
 * Renders a compact row of checkboxes grouped by category.
 */

import { SKEWT_OVERLAYS, type SkewTOverlayId } from './overlay-bands';
import type { SkewTRenderer } from './renderer';

/** Render the overlay toggle controls into a container element. */
export function renderSkewtOverlayControls(
  container: HTMLElement,
  renderer: SkewTRenderer,
): void {
  const state = renderer.getOverlayState();

  // Group overlays
  const groups = new Map<string, typeof SKEWT_OVERLAYS>();
  for (const overlay of SKEWT_OVERLAYS) {
    const list = groups.get(overlay.group) ?? [];
    list.push(overlay);
    groups.set(overlay.group, list);
  }

  let html = '<div class="skewt-overlay-controls">';
  for (const [group, overlays] of groups) {
    html += `<span class="skewt-overlay-group">`;
    html += `<span class="skewt-overlay-group-label">${groupLabel(group)}</span>`;
    for (const overlay of overlays) {
      const checked = state[overlay.id] ? 'checked' : '';
      html += `<label class="skewt-overlay-toggle" title="${overlay.label}">`;
      html += `<input type="checkbox" data-overlay="${overlay.id}" ${checked}>`;
      html += `<span class="skewt-overlay-name">${overlay.label}</span>`;
      html += `</label>`;
    }
    html += `</span>`;
  }
  html += '</div>';
  container.innerHTML = html;

  // Attach listeners
  container.querySelectorAll<HTMLInputElement>('input[data-overlay]').forEach(input => {
    input.addEventListener('change', () => {
      renderer.toggleOverlay(input.dataset.overlay!);
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
