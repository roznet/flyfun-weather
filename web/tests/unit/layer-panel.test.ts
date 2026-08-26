/** Layer-panel composition rules (`controls/panel.ts`).
 *
 * `panel.ts` had no test at all, which is how two related regressions shipped:
 * a group vanishing when only *one* of its layers was unavailable, and a group
 * with a bespoke compound control silently dropping every other layer's
 * checkbox. Both are pure string-composition properties, so they are testable
 * without a DOM.
 */

import { describe, it, expect } from 'vitest';

import { layerTogglesHtml } from '../../ts/visualization/controls/panel';
import { getAllLayers } from '../../ts/visualization/cross-section/layer-registry';
import { ALL_CLOUD_LAYER_IDS } from '../../ts/visualization/cross-section/layers/cloud-bands-factory';

/** Every layer registered in a group, by id. */
function layersInGroup(group: string): string[] {
  return getAllLayers().filter((l) => l.group === group).map((l) => l.id);
}

/** Does the rendered panel offer a checkbox for this layer? */
function hasToggle(html: string, layerId: string): boolean {
  return html.includes(`data-layer-id="${layerId}"`);
}

describe('layer panel composition', () => {
  it('offers a toggle for every clouds-group layer the compound control does not own', () => {
    // The clouds group renders a bespoke NWP/DD control plus a style dropdown.
    // That control owns ONLY the cloud-band ids; anything else in the group
    // still needs its own checkbox. `observed-tops` (#574) landed in this
    // group and rendered with no way to switch it off — default-on, painting
    // over the chart, and unreachable from the panel.
    const html = layerTogglesHtml({});
    const others = layersInGroup('clouds').filter((id) => !ALL_CLOUD_LAYER_IDS.includes(id));

    expect(others.length).toBeGreaterThan(0);
    for (const id of others) {
      expect(hasToggle(html, id), `no toggle rendered for clouds-group layer "${id}"`).toBe(true);
    }
  });

  it('still renders the compound cloud control alongside them', () => {
    const html = layerTogglesHtml({});
    expect(html).toContain('data-cloud-source="nwp"');
    expect(html).toContain('data-cloud-source="dd"');
    expect(html).toContain('data-cloud-style');
    // And the compound control's own ids stay out of the plain checkbox list,
    // or the group would show each style variant twice.
    for (const id of ALL_CLOUD_LAYER_IDS) {
      expect(hasToggle(html, id), `cloud-band id "${id}" should be owned by the compound control`).toBe(false);
    }
  });

  it('keeps a group visible while any one of its layers is available', () => {
    // The regression this pins: `conditions` holds both the METAR/SIGMET
    // layer and the observed radar layer. A briefing with no METAR but working
    // radar must still show the group — hiding it took a working layer with it.
    const conditions = layersInGroup('conditions');
    expect(conditions.length).toBeGreaterThan(1);

    const allButOne = new Set(conditions.slice(1));
    const html = layerTogglesHtml({}, { unavailableLayers: allButOne });
    expect(hasToggle(html, conditions[0])).toBe(true);
  });

  it('hides a feature group only when every layer in it is unavailable', () => {
    const conditions = layersInGroup('conditions');
    const html = layerTogglesHtml({}, { unavailableLayers: new Set(conditions) });
    for (const id of conditions) {
      expect(hasToggle(html, id)).toBe(false);
    }
  });
});
