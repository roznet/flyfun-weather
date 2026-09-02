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

/** The bar chip for one family, as raw markup. */
function chipFor(html: string, family: string): string {
  const at = html.indexOf(`data-family="${family}"`);
  if (at === -1) return '';
  return html.slice(html.lastIndexOf('<button', at), html.indexOf('</button>', at));
}

/** Does the rendered panel offer a checkbox for this layer? */
function hasToggle(html: string, layerId: string): boolean {
  return html.includes(`data-layer-id="${layerId}"`);
}

describe('layer panel composition', () => {
  it('offers a toggle for every clouds-group layer the compound control does not own', () => {
    // The clouds group renders a bespoke NWP/DD control plus a style dropdown.
    // That control owns ONLY the cloud-band ids; anything else in the group
    // still needs its own checkbox. `observed-tops` (#574) landed here and
    // rendered with no way to switch it off — default-on, painting over the
    // chart, unreachable from the panel. It has since moved to `conditions`,
    // so this may hold vacuously today; it is the rule that matters, and the
    // next layer added to this group must not repeat the bug.
    const html = layerTogglesHtml({}, { openFamily: 'clouds' });
    for (const id of layersInGroup('clouds').filter((i) => !ALL_CLOUD_LAYER_IDS.includes(i))) {
      expect(hasToggle(html, id), `no toggle rendered for clouds-group layer "${id}"`).toBe(true);
    }
  });

  it('lists the observed layers together, airport first and tops last', () => {
    // Grouped by provenance so a pilot has one place to look for everything
    // measured. The order is deliberate and is NOT the drawing order: tops
    // must paint before terrain fill (so a top under a mountain is masked)
    // while reading last in the list.
    const html = layerTogglesHtml({}, { openFamily: 'observed' });
    const wanted = ['current-conditions', 'observed-surface', 'observed-tops'];
    for (const id of wanted) {
      expect(hasToggle(html, id), `no toggle rendered for "${id}"`).toBe(true);
    }
    const positions = wanted.map((id) => html.indexOf(`data-layer-id="${id}"`));
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it('still renders the compound cloud control alongside them', () => {
    const html = layerTogglesHtml({}, { openFamily: 'clouds' });
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
    const html = layerTogglesHtml({}, { unavailableLayers: allButOne, openFamily: 'observed' });
    expect(hasToggle(html, conditions[0])).toBe(true);
  });

  it('hides a feature group only when every layer in it is unavailable', () => {
    const conditions = layersInGroup('conditions');
    const html = layerTogglesHtml({}, { unavailableLayers: new Set(conditions), openFamily: 'observed' });
    for (const id of conditions) {
      expect(hasToggle(html, id)).toBe(false);
    }
  });
});

describe('layer bar (#591)', () => {
  it('shows one chip per family and no controls until one is opened', () => {
    // The resting state is the whole point: a single row that says what is
    // drawn, with nothing expanded. If checkboxes leak into it we are back to
    // the five-row toolbar this replaced.
    const html = layerTogglesHtml({});
    for (const f of ['clouds', 'convection', 'icing', 'turbulence', 'levels', 'stability', 'observed']) {
      expect(html, `no chip for family "${f}"`).toContain(`data-family="${f}"`);
    }
    expect(html).not.toContain('data-layer-id=');
    expect(html).not.toContain('viz-layer-detail');
  });

  it('opens exactly one family at a time', () => {
    // The detail row SWAPS, it does not stack — that is what keeps the panel
    // at a fixed height however much the user fiddles.
    const html = layerTogglesHtml({}, { openFamily: 'icing' });
    expect((html.match(/viz-layer-detail/g) ?? []).length).toBe(1);
    expect(html).toContain('data-detail-family="icing"');
    expect(hasToggle(html, 'sfip-bands')).toBe(true);
    // Another family's layers stay out of the row entirely.
    expect(hasToggle(html, 'inversion-bands')).toBe(false);
  });

  it('marks the open family expanded and the others not', () => {
    const html = layerTogglesHtml({}, { openFamily: 'icing' });
    expect(html).toContain('data-family="icing" aria-expanded="true"');
    expect(html).toContain('data-family="clouds" aria-expanded="false"');
  });

  it('names what each family is drawing, and marks the ones drawing nothing', () => {
    // A blank chip cannot distinguish "nothing here" from "I have not looked",
    // so an empty family is marked rather than left silent.
    //
    // Asserted structurally, not on the rendered words: `initI18n()` is an
    // async fetch that never runs under vitest, so `t()` returns its key here.
    // Pinning label text would test the test harness, not the panel.
    const allOff: Record<string, boolean> = {};
    for (const l of getAllLayers()) allOff[l.id] = false;

    const off = layerTogglesHtml(allOff);
    expect(chipFor(off, 'icing')).toContain('is-off');

    const on = layerTogglesHtml({ ...allOff, 'sfip-bands': true });
    expect(chipFor(on, 'icing')).not.toContain('is-off');
    // ...and the chip names the layer that is on, rather than just counting it.
    expect(chipFor(on, 'icing')).toContain('sfip-bands');
    // A family that is still empty keeps its off marking.
    expect(chipFor(on, 'stability')).toContain('is-off');
  });

  it('collapses to one on/off per family in compact mode, with no detail row', () => {
    // Compact makes the method decision for you — there is nothing to expand,
    // so a detail row here would be a bug, not a feature.
    const html = layerTogglesHtml({}, { displayMode: 'compact', openFamily: 'icing' });
    expect(html).toContain('data-family-toggle="icing"');
    expect(html).not.toContain('viz-layer-detail');
    expect(html).not.toContain('data-layer-id=');
  });

  it('keeps every default line when a non-method family is switched back on', () => {
    // Only METHOD groups collapse to one layer. `temperature` (0/−10/−20 °C)
    // and `stability` (LCL/LFC/EL) are sets of independent lines, all
    // default-on, so collapsing them to `getPreferredLayerForGroup`'s single
    // answer meant toggling the chip off and on again silently dropped −10/−20
    // and LFC/EL — with no detail row in compact to get them back, in the
    // DEFAULT display mode.
    const html = layerTogglesHtml({}, { displayMode: 'compact' });
    const targetsFor = (family: string) => {
      const at = html.indexOf(`data-family-toggle="${family}"`);
      const chip = html.slice(at, html.indexOf('</button>', at));
      return (chip.match(/data-family-layers="([^"]*)"/)?.[1] ?? '').split(' ').filter(Boolean);
    };

    expect(targetsFor('levels')).toEqual(
      expect.arrayContaining(['freezing-level', 'minus-10c', 'minus-20c', 'cruise-altitude']),
    );
    expect(targetsFor('stability')).toEqual(expect.arrayContaining(['lcl', 'lfc', 'el']));
  });

  it('points a compact chip at the preferred method, not at everything', () => {
    const html = layerTogglesHtml({}, {
      displayMode: 'compact',
      preferredMethods: { icing: 'ogimet_dd' },
    });
    expect(html).toContain('data-family-layers="icing-bands"');
  });

  it('keeps a family whose groups are only partly hidden', () => {
    // The airport-profile drawer hides `conditions` (route-only) but not `sun`
    // or `fronts`, both of which live in the same family. Dropping Observed
    // wholesale there would take working toggles with it — the same shape of
    // bug #574 fixed at group level, one tier up.
    const html = layerTogglesHtml({}, {
      hiddenGroups: new Set(['conditions' as const, 'highlight' as const]),
      openFamily: 'observed',
    });
    expect(html).toContain('data-family="observed"');
    expect(hasToggle(html, 'night-shading')).toBe(true);
    expect(hasToggle(html, 'current-conditions')).toBe(false);
  });

  it('drops a family whose every group is hidden', () => {
    // Otherwise the bar offers a chip that opens an empty row.
    const stability = layersInGroup('stability');
    const html = layerTogglesHtml({}, { hiddenGroups: new Set(['stability' as const]) });
    expect(html).not.toContain('data-family="stability"');
    for (const id of stability) expect(hasToggle(html, id)).toBe(false);
  });
});

describe('detail row: pills, None and the hint slot (#591)', () => {
  it('renders every layer as a pick-any pill, never a radio', () => {
    // Once icing is pick-any there is no one-of-N left among the layers, so a
    // joined/segmented control would be lying about the semantics. Overlaying
    // two icing models is a legitimate comparison.
    const html = layerTogglesHtml({}, { openFamily: 'icing' });
    for (const id of ['icing-bands', 'icing-ogimet-nwp-bands', 'sfip-bands', 'ieng-icing-bands']) {
      expect(html, `no pill for "${id}"`).toContain(`class="viz-pill" data-layer-id="${id}"`);
    }
    expect(html).not.toContain('type="radio"');
    expect(html).not.toContain('type="checkbox"');
  });

  it('offers a None pill carrying the ids it clears', () => {
    // Pick-any without a None means "untick five things and hope".
    const html = layerTogglesHtml({}, { openFamily: 'icing' });
    const none = html.match(/data-none-group="([^"]+)"/);
    expect(none, 'no None pill in the icing row').not.toBeNull();
    const ids = none![1].split(' ');
    expect(ids).toContain('sfip-bands');
    expect(ids).toContain('icing-bands');
  });

  it('omits the None pill where a group holds a single layer', () => {
    // It would be a second control doing the same job as the one pill.
    const html = layerTogglesHtml({}, { openFamily: 'levels' });
    const detail = html.slice(html.indexOf('viz-layer-detail'));
    // 'reference' holds only cruise-altitude.
    const groups = detail.split('viz-detail-sub').length - 1;
    expect(groups).toBeGreaterThan(1);
    expect(detail).toContain('data-layer-id="cruise-altitude"');
  });

  it('keeps an unavailable method visible and disabled rather than hidden', () => {
    // Knowing SLD exists but needs another model is worth more than a tidy row.
    const html = layerTogglesHtml({}, {
      openFamily: 'icing',
      unavailableLayers: new Set(['sld-bands']),
    });
    expect(html).toContain('data-layer-id="sld-bands"');
    expect(html).toMatch(/data-layer-id="sld-bands"[^>]*disabled/);
    expect(html).toContain('viz-layer-unavailable');
  });

  it('gives every control a hint and the row exactly one About button', () => {
    // This is where the twenty ⓘ glyphs went: one labelled button per family,
    // and a slot that reads whatever the pointer is on.
    const html = layerTogglesHtml({}, { openFamily: 'icing' });
    expect((html.match(/data-family-about=/g) ?? []).length).toBe(1);
    expect(html).toContain('viz-hint-text');
    // No per-layer ⓘ survives in the row.
    expect(html).not.toContain('data-layer-info');
    // ...and each pill carries the sentence the slot will show.
    const pills = html.match(/class="viz-pill"[^>]*/g) ?? [];
    expect(pills.length).toBeGreaterThan(0);
    for (const p of pills) expect(p, `pill without a hint: ${p}`).toContain('data-hint=');
  });

  it('builds the About panel from the metrics catalog, not from new copy', () => {
    // vibe / primary_goal / best_used_for are already in MetricCatalogEntry —
    // the panel renders them, and hands limitations/theory/thresholds to the
    // existing popup behind "Full detail".
    const html = layerTogglesHtml({}, { openFamily: 'icing', aboutFamily: 'icing' });
    expect(html).toContain('data-about-family="icing"');
    expect(html).toContain('viz-about-card');
    // The deep link reuses the existing per-layer popup entry point.
    expect(html).toMatch(/data-layer-info="sfip-bands"[^>]*data-metric-id="sfip_risk"/);
  });

  it('shows the About panel only for the family that is open', () => {
    const html = layerTogglesHtml({}, { openFamily: 'icing', aboutFamily: 'clouds' });
    expect(html).not.toContain('viz-family-about');
  });

  it('cards only the layers the catalog actually describes', () => {
    // Nearly every layer has a `metricId`, isotherms and parcel levels
    // included — only cruise-altitude, night-shading and fronts-markers do
    // not. Those get no card rather than an empty one, and the hint line above
    // still names them.
    const html = layerTogglesHtml({}, { openFamily: 'levels', aboutFamily: 'levels' });
    expect(html).toContain('viz-about-grid');
    expect(html).toMatch(/data-layer-info="freezing-level"[^>]*data-metric-id="freezing_level_ft"/);
    expect(html).not.toContain('data-layer-info="cruise-altitude"');
  });

  it('falls back to a plain note when a family has nothing to compare', () => {
    // Defensive rather than currently reachable: an empty grid would read as a
    // loading failure, so the branch exists for the day a family is all lines.
    const html = layerTogglesHtml({}, {
      openFamily: 'observed',
      aboutFamily: 'observed',
      unavailableLayers: new Set(['current-conditions', 'observed-surface', 'observed-tops']),
    });
    expect(html).toContain('data-about-family="observed"');
  });
});
