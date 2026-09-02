/** Visualization control panel: layout toggle, model selector, layer checkboxes, map controls. */

import type { VizLayout, VizSettings, CompareBandMode, LayerGroup } from '../types';
import type { DisplayMode } from '../../types/metrics';
import {
  getLayerGroups, getPreferredLayerForGroup, getPresets, getPreset,
  getLayerFamilies, familySummary, enabledInFamily, METHOD_GROUPS,
  type LayerFamily, type LayerFamilyInfo,
} from '../cross-section/layer-registry';
import { getAdvisoryPresets, getAdvisoryPreset, isAdvisoryPreset, advisoryPresetLabel, advisoryPresetCaption, advisoryPresetInterpretation } from '../cross-section/advisory-presets';
import {
  CLOUD_LAYER_BY_AXES,
  ALL_CLOUD_LAYER_IDS,
  parseCloudLayerId,
} from '../cross-section/layers/cloud-bands-factory';
import { getDdSubstituteId } from '../cross-section/nwp-fallback';
import { getComparableLayerGroups, getComparableLayer } from '../cross-section/compare-layers';
import { showLayerInfo, showPopupContent } from '../../components/info-popup';
import { renderFrontsInfo } from '../../helpers/fronts-info';
import { modelLabel, escapeHtml } from '../../utils';
import { getMetricOptions } from '../route-graph/metrics';
import { getMetric } from '../../helpers/metrics-helper';
import { getMapMetricOptions, MAP_METRIC_NONE } from '../route-map/metrics';
import { OBSERVED_OVERLAY_OPTIONS } from '../route-map/observed-overlay-geometry';
import { FORECAST_METRICS, METRIC_LABEL } from '../weather-map-format';
import { THEMES, getActiveThemeId, type ThemeId } from '../cross-section/theme';
import { showThemePreview } from '../cross-section/theme-preview';
import { t } from '../../i18n/i18n';

/** Groups that collapse to a single preferred-method toggle in compact mode.
 *  Shared with the Basic/Learn lens via {@link METHOD_GROUPS} so "Basic shows
 *  the same as compact" stays true without anyone maintaining two lists. */
const COMPACT_GROUPS = new Set<string>(METHOD_GROUPS);

/** Explanatory text for layer group info buttons. */
const GROUP_INFO: Record<string, () => string> = {
  clouds: () => t('viz.cloudMethods'),
  convection: () => t('viz.convectionMethods'),
  icing: () => t('viz.icingMethods'),
  obscuration: () => t('viz.obscurationMethod'),
};

/** Options shared between the main toolbar's layer-toggle block and the
 *  standalone {@link renderLayerToggles} helper. */
export interface LayerTogglesOptions {
  displayMode?: DisplayMode;
  preferredMethods?: Record<string, string>;
  unavailableLayers?: Set<string>;
  /** Layers auto-substituted at render time (DD standing in for unavailable
   *  NWP). Rendered as checked + dimmed with an explanatory tooltip so the
   *  panel state matches what's actually drawn. */
  substitutedLayers?: Set<string>;
  /** Persisted cloud-style preference; the compound control falls back to
   *  this when no cloud layers are enabled, so the dropdown remembers the
   *  user's last choice across re-renders and page reloads. */
  cloudStyle?: 'natural' | 'soft' | 'square';
  /** Fired when the user picks a different cloud style. Wire to the store
   *  so the choice persists alongside other viz settings. */
  onCloudStyleChange?: (style: 'natural' | 'soft' | 'square') => void;
  /** Layer groups to omit entirely from the toggle list. Used by the
   *  airport-profile drawer to drop the route-only `conditions` group. */
  hiddenGroups?: Set<LayerGroup>;
  /** Which family's detail row is open, if any. Callers pass the value they
   *  read back with {@link openFamilyIn} before re-rendering, so a re-render
   *  triggered by toggling a layer does not close the row you are working in. */
  openFamily?: LayerFamily | null;
  /** Which family's "About …" panel is open, if any. Same read-back reason as
   *  {@link openFamily}: the panel must survive toggling a layer, because
   *  reading about a method and then switching it on is the point of it. */
  aboutFamily?: LayerFamily | null;
}

/** Source state derived from which cloud layer ids are currently enabled.
 *  When no cloud layers are enabled the style falls back to `persistedStyle`
 *  (the value remembered in `vizSettings.cloudStyle`) so re-checking a
 *  source restores the user's last choice instead of the 'square' default. */
function cloudState(
  enabledLayers: Record<string, boolean>,
  persistedStyle: 'natural' | 'soft' | 'square' = 'square',
): {
  ddEnabled: boolean;
  nwpEnabled: boolean;
  style: 'natural' | 'soft' | 'square';
} {
  let ddEnabled = false;
  let nwpEnabled = false;
  let style: 'natural' | 'soft' | 'square' = persistedStyle;
  for (const id of ALL_CLOUD_LAYER_IDS) {
    if (!enabledLayers[id]) continue;
    const axes = parseCloudLayerId(id);
    if (!axes) continue;
    if (axes.source === 'dd') ddEnabled = true;
    if (axes.source === 'nwp') nwpEnabled = true;
    style = axes.style;  // last-wins; all enabled cloud layers share the same style by construction
  }
  return { ddEnabled, nwpEnabled, style };
}

/** Render the compound clouds control: per-source checkboxes + shared style dropdown. */
function cloudCompoundHtml(
  enabledLayers: Record<string, boolean>,
  unavailable: Set<string> | undefined,
  persistedStyle: 'natural' | 'soft' | 'square' | undefined,
  substituted: Set<string> | undefined,
): string {
  const { ddEnabled, nwpEnabled, style } = cloudState(enabledLayers, persistedStyle);
  // A source is "unavailable" iff its natural-style id (the canonical
  // data signal) is in the unavailable set. DD is sounding-derived so
  // generally always available; NWP requires native cloud-cover data.
  const ddUnavail = unavailable?.has('cloud-bands') ?? false;
  const nwpUnavail = unavailable?.has('nwp-cloud-bands') ?? false;
  // DD is auto-on (standing in for NWP) when the current-style DD layer was
  // injected by the render-time fallback rather than picked by the user.
  const ddSubstituted = substituted?.has(CLOUD_LAYER_BY_AXES.dd[style]) ?? false;

  // A source is a pill like every other layer control: NWP and DD overlaid is
  // the cross-check, not a mistake, so the shape has to permit both.
  const sourcePill = (
    source: 'dd' | 'nwp',
    label: string,
    checked: boolean,
    unavail: boolean,
    auto: boolean,
    hintKey: string,
  ): string => {
    const dimClass = unavail ? ' viz-layer-unavailable' : (auto ? ' viz-layer-substituted' : '');
    const hint = unavail
      ? t('viz.notAvailableModel')
      : (auto ? t('viz.substitutedNwp') : t(hintKey));
    const on = (checked || auto) && !unavail;
    return `<button type="button" class="viz-pill${dimClass}" data-cloud-source="${source}"`
      + ` aria-pressed="${on}"${unavail ? ' disabled' : ''}`
      + ` data-hint="${escapeHtml(hint)}">${escapeHtml(label)}</button>`;
  };

  let html = '';
  html += sourcePill('nwp', 'NWP', nwpEnabled, nwpUnavail, false, 'viz.cloudSourceHint.nwp');
  html += sourcePill('dd', 'DD', ddEnabled, ddUnavail, ddSubstituted, 'viz.cloudSourceHint.dd');
  html += `<select class="viz-model-select" data-cloud-style>`;
  html += `<option value="square"${style === 'square' ? ' selected' : ''}>${t('viz.cloudStyle.square')}</option>`;
  html += `<option value="natural"${style === 'natural' ? ' selected' : ''}>${t('viz.cloudStyle.natural')}</option>`;
  html += `<option value="soft"${style === 'soft' ? ' selected' : ''}>${t('viz.cloudStyle.soft')}</option>`;
  html += `</select>`;
  return html;
}

/** Fired on `window` after the layer bar replaces its own subtree (opening or
 *  closing a family). The open family is DOM state rather than store state, so
 *  this is the only signal that the node has been swapped. */
export const VIZ_LAYER_BAR_RERENDER = 'viz-layer-bar-rerender';

/** Which family the bar shows expanded, if any. Read back off the DOM rather
 *  than held in module state: `renderVizControls` replaces the container's
 *  innerHTML on every settings change, and toggling a layer IS a settings
 *  change — so without this the detail row would slam shut on every click,
 *  which is the one thing it must not do. Reading the DOM also keeps the two
 *  surfaces that render toggles (the briefing toolbar and the airport-profile
 *  drawer) independent for free. */
export function openFamilyIn(container: HTMLElement): LayerFamily | null {
  const el = container.querySelector('.viz-family[aria-expanded="true"]');
  return (el?.getAttribute('data-family') as LayerFamily) ?? null;
}

/** Which family's "About …" panel is showing, read back the same way and for
 *  the same reason as {@link openFamilyIn}. */
export function aboutFamilyIn(container: HTMLElement): LayerFamily | null {
  const el = container.querySelector('.viz-family-about');
  return (el?.getAttribute('data-about-family') as LayerFamily) ?? null;
}

/** Colour the family dot takes — the colour that family paints in, so the bar
 *  doubles as a key for the chart. Falls back to the ink colour for families
 *  whose layers are lines of several colours. */
const FAMILY_DOT: Record<LayerFamily, string> = {
  clouds: 'var(--xs-dot-cloud)',
  convection: 'var(--xs-dot-convection)',
  icing: 'var(--xs-dot-icing)',
  turbulence: 'var(--xs-dot-turbulence)',
  levels: 'var(--xs-dot-levels)',
  stability: 'var(--xs-dot-stability)',
  observed: 'var(--xs-dot-observed)',
};

/** Groups that hide entirely when EVERY layer in them is unavailable — front
 *  detection off or no on-track crossing, no D-0 observations and no observed
 *  frames. A group of disabled checkboxes is just noise. (Other groups keep
 *  their layers visible-but-dimmed so alternative methods stay discoverable.)
 *
 *  This used to name one layer per group and hide the group when that layer was
 *  unavailable. Adding the observed-surface layer (#574) to `conditions` made
 *  that wrong: with no METAR the whole group vanished, taking a perfectly good
 *  radar layer with it. Now the group hides only when nothing in it is
 *  available, and individual layers dim as everywhere else. */
const HIDE_GROUP_WHEN_ALL_UNAVAILABLE: readonly LayerGroup[] = ['fronts', 'conditions'];

/** One-line hint for a layer, shown in the detail row's hint slot on hover or
 *  focus. Prefers the metrics catalog's own `vibe` — the summary sentence it
 *  already carries — so this adds no content to write. Layers with no catalog
 *  entry (the isotherms, the parcel levels, cruise) fall back to an explicit
 *  `viz.layerHint.*` string, and to their label if even that is missing. */
function layerHint(layer: { id: string; metricId?: string }): string {
  if (layer.metricId) {
    const vibe = getMetric(layer.metricId)?.vibe;
    if (vibe) return vibe;
  }
  const key = `viz.layerHint.${layer.id}`;
  const s = t(key);
  return s === key ? t(`viz.layer.${layer.id}`) : s;
}

/** Render one layer as a pill.
 *
 *  Pills are gapped and rounded because every layer family is pick-ANY: two
 *  icing models overlaid is a legitimate comparison, not an error state. The
 *  shape carries that — a joined segmented control would say "pick one", which
 *  is now only true of Lens and Style. */
function layerPillHtml(
  layer: { id: string; metricId?: string },
  label: string,
  enabledLayers: Record<string, boolean>,
  unavailableLayers?: Set<string>,
  substitutedLayers?: Set<string>,
): string {
  const isUnavailable = unavailableLayers?.has(layer.id) ?? false;
  const isSubstituted = !isUnavailable && (substitutedLayers?.has(layer.id) ?? false);
  const showUnavailable = isUnavailable && !isSubstituted;
  const on = isSubstituted || (!showUnavailable && enabledLayers[layer.id] !== false);

  const cls = 'viz-pill'
    + (showUnavailable ? ' viz-layer-unavailable' : '')
    + (isSubstituted ? ' viz-layer-substituted' : '');
  // An unavailable method stays visible and struck through rather than hidden:
  // knowing SLD exists but needs another model is worth more than a clean row.
  const hint = showUnavailable
    ? t('viz.notAvailableModel')
    : (isSubstituted ? t('viz.substitutedNwp') : layerHint(layer));

  return `<button type="button" class="${cls}" data-layer-id="${layer.id}"`
    + ` aria-pressed="${on}"${showUnavailable ? ' disabled' : ''}`
    + ` data-hint="${escapeHtml(hint)}">${escapeHtml(label)}</button>`;
}

/** The `None` pill that clears a group in one click.
 *
 *  Needed precisely because every family is pick-any: without it, switching
 *  icing off means unticking five things and hoping. It carries the ids it
 *  clears so the handler needs no lookup. */
function nonePillHtml(layerIds: string[], anyOn: boolean): string {
  return `<button type="button" class="viz-pill viz-pill-none" data-none-group="${layerIds.join(' ')}"`
    + ` aria-pressed="${!anyOn}" data-hint="${escapeHtml(t('viz.noneHint'))}">${escapeHtml(t('viz.none'))}</button>`;
}

/** The families the bar shows, after dropping those the current briefing has
 *  nothing for. A family whose every group is hidden disappears entirely —
 *  otherwise the bar offers a chip that opens an empty row. */
function visibleFamilies(opts: LayerTogglesOptions): LayerFamilyInfo[] {
  const { unavailableLayers, hiddenGroups } = opts;
  const hidden = new Set<LayerGroup>(hiddenGroups ?? []);
  for (const groupId of HIDE_GROUP_WHEN_ALL_UNAVAILABLE) {
    const group = getLayerGroups().find((g) => g.group === groupId);
    if (!group || group.layers.length === 0) continue;
    if (group.layers.every((l) => unavailableLayers?.has(l.id))) hidden.add(groupId);
  }

  return getLayerFamilies()
    .map((f) => ({ ...f, groups: f.groups.filter((g) => !hidden.has(g.group)) }))
    .map((f) => ({ ...f, layers: f.groups.flatMap((g) => g.layers) }))
    .filter((f) => f.layers.length > 0);
}

/** The detail row for one family: its groups side by side, each with a faint
 *  sub-label, running ACROSS the width rather than down a column. That is what
 *  keeps the expansion at exactly one row whichever family is open (#591).
 *
 *  The row ends in a hint slot — one sentence about whatever the pointer is on,
 *  plus a single labelled "About …" button. That slot is where the panel's
 *  twenty ⓘ glyphs went: help now lives in the horizontal space that was going
 *  spare, instead of doubling the number of objects on every row. */
function familyDetailHtml(
  info: LayerFamilyInfo,
  enabledLayers: Record<string, boolean>,
  opts: LayerTogglesOptions,
): string {
  const { unavailableLayers, substitutedLayers, cloudStyle } = opts;

  let html = `<div class="viz-layer-detail" data-detail-family="${info.family}">`;
  html += `<span class="viz-detail-name">`;
  html += `<span class="viz-family-dot" style="background:${FAMILY_DOT[info.family]}"></span>`;
  html += `${escapeHtml(info.label)}</span>`;

  for (const group of info.groups) {
    html += `<span class="viz-detail-sub">${escapeHtml(group.label)}</span>`;

    // The clouds group's sources are pills over the cloud-band ids, which the
    // style axis multiplies out. The compound control owns ONLY those ids; any
    // other layer in the group still needs its own pill and falls through to
    // the loop below — which is how `observed-tops` (#574) once rendered with
    // no way to switch it off at all.
    let layers = group.layers;
    if (group.group === 'clouds') {
      html += `<span class="viz-pills">`;
      html += cloudCompoundHtml(enabledLayers, unavailableLayers, cloudStyle, substitutedLayers);
      html += `</span>`;
      layers = layers.filter((l) => !ALL_CLOUD_LAYER_IDS.includes(l.id));
      if (layers.length === 0) continue;
    }

    const ids = layers.map((l) => l.id);
    const anyOn = layers.some((l) => enabledLayers[l.id] !== false && !unavailableLayers?.has(l.id));
    html += `<span class="viz-pills">`;
    // A single-layer group is already one click from off; a None pill there
    // would just be a second control doing the same thing.
    if (layers.length > 1) html += nonePillHtml(ids, anyOn);
    for (const layer of layers) {
      html += layerPillHtml(layer, t('viz.layer.' + layer.id), enabledLayers, unavailableLayers, substitutedLayers);
    }
    html += `</span>`;
  }

  const famHintKey = `viz.familyHint.${info.family}`;
  const famHint = t(famHintKey) === famHintKey ? '' : t(famHintKey);
  html += `<span class="viz-detail-hint">`;
  html += `<span class="viz-hint-text" data-default-hint="${escapeHtml(famHint)}">${escapeHtml(famHint)}</span>`;
  html += `<button type="button" class="viz-about-btn" data-family-about="${info.family}"`
    + ` title="${escapeHtml(t('viz.aboutFamily', { family: info.label }))}">`
    + `<span class="viz-about-i" aria-hidden="true">i</span>`
    + `${escapeHtml(t('viz.aboutFamily', { family: info.label.toLowerCase() }))}</button>`;
  html += `</span>`;

  html += `</div>`;
  return html;
}

/** The "About …" panel: one summary card per option in the family, side by
 *  side, with a link into the full metric entry.
 *
 *  Per FAMILY, not per layer, because "what is NWP versus DD" is a single
 *  comparative question rather than two definitions — and a per-family panel
 *  can say the thing that actually matters, which is when to prefer one over
 *  the other. Twenty independent popups structurally cannot.
 *
 *  Almost none of this is new content: `MetricCatalogEntry` already splits the
 *  way the two tiers need — `vibe` is the summary line, `primary_goal` the
 *  goal, `best_used_for` what it is for, and `limitations` / `theory` /
 *  `thresholds` are the detail behind the button. This renders the first three
 *  and hands the rest to the existing popup.
 *
 *  It renders INLINE, under the detail row, rather than floating over the
 *  chart. Reading and adjusting are different modes: the no-covering rule
 *  protects adjusting, and while you are reading what dewpoint depression is
 *  you are not watching the bands. Displacing the chart needs no positioning
 *  code, and leaves the bar and the pills live above it — so you can read
 *  about DD, switch it on, and dismiss to see what changed.
 */
function familyAboutHtml(info: LayerFamilyInfo): string {
  const cards = info.layers
    .filter((l) => l.metricId && getMetric(l.metricId))
    .map((l) => {
      const m = getMetric(l.metricId!)!;
      let html = `<div class="viz-about-card">`;
      html += `<h5>${escapeHtml(t('viz.layer.' + l.id))}<span class="viz-about-tag">${escapeHtml(l.metricId!)}</span></h5>`;
      html += `<p>${escapeHtml(m.vibe)}</p>`;
      if (m.primary_goal) html += `<p><span class="viz-about-fld">${escapeHtml(t('viz.aboutGoal'))}</span>${escapeHtml(m.primary_goal)}</p>`;
      if (m.best_used_for) html += `<p><span class="viz-about-fld">${escapeHtml(t('viz.aboutBestFor'))}</span>${escapeHtml(m.best_used_for)}</p>`;
      html += `<button type="button" class="viz-about-more" data-layer-info="${l.id}" data-metric-id="${l.metricId}">`
        + `${escapeHtml(t('viz.aboutFullDetail'))}</button>`;
      html += `</div>`;
      return html;
    });

  const introKey = `viz.familyAbout.${info.family}`;
  const intro = t(introKey) === introKey ? '' : t(introKey);

  let html = `<div class="viz-family-about" data-about-family="${info.family}">`;
  html += `<div class="viz-about-head"><h4>${escapeHtml(info.label)}</h4>`;
  html += `<button type="button" class="viz-about-close">${escapeHtml(t('viz.aboutClose'))}</button></div>`;
  if (intro) html += `<p class="viz-about-intro">${escapeHtml(intro)}</p>`;
  html += cards.length > 0
    ? `<div class="viz-about-grid">${cards.join('')}</div>`
    // The line layers (isotherms, parcel levels, cruise) carry no catalog
    // entry, so there is nothing to compare. Say so rather than render an
    // empty grid that reads as a loading failure.
    : `<p class="viz-about-intro">${escapeHtml(t('viz.aboutNoDetail'))}</p>`;
  html += `</div>`;
  return html;
}

/** Build the layer bar plus, when a family is open, its detail row.
 *
 *  The bar is one line of family chips, each naming what it is currently
 *  drawing; opening one swaps a single detail row underneath rather than
 *  stacking a second (#591). In compact mode a chip is a plain on/off and the
 *  method is the preferred one, which is what `getCompactLayerOverrides()`
 *  already resolves — there is no detail row at all.
 *
 *  Exported so the composition rules can be tested without a DOM.
 */
export function layerTogglesHtml(
  enabledLayers: Record<string, boolean>,
  opts: LayerTogglesOptions = {},
): string {
  const { displayMode, preferredMethods, unavailableLayers, cloudStyle, substitutedLayers } = opts;
  const compact = displayMode === 'compact';
  const families = visibleFamilies(opts);
  const open = compact ? null : (opts.openFamily ?? null);

  let html = '<div class="viz-layer-toggles">';
  html += '<div class="viz-layer-bar">';
  html += `<span class="viz-bar-label">${t('viz.layers')}</span>`;

  for (const info of families) {
    if (compact) {
      // One on/off per category, with the method decision already made.
      //
      // Only a METHOD group collapses to a single layer — those hold several
      // ways of computing the same thing, so exactly one is right. A group
      // that is a set of independent lines does not: `temperature` has
      // 0/−10/−20 °C and `stability` has LCL/LFC/EL, all default-on, and
      // `getPreferredLayerForGroup` returns just the first default-on layer it
      // finds. Collapsing those the same way meant switching the chip off and
      // on again silently dropped −10 °C/−20 °C and LFC/EL — unrecoverably,
      // since compact has no detail row to get them back, and compact is the
      // default mode.
      const on = enabledInFamily(info, enabledLayers).length > 0;
      const targets = info.groups
        .flatMap((g) => (COMPACT_GROUPS.has(g.group)
          ? [getPreferredLayerForGroup(g.group, g.layers, preferredMethods?.[g.group], cloudStyle).id]
          : g.layers.filter((l) => l.defaultEnabled).map((l) => l.id)))
        .join(' ');
      html += `<button type="button" class="viz-family viz-family-toggle${on ? ' is-on' : ''}"`
        + ` data-family-toggle="${info.family}" data-family-layers="${targets}" aria-pressed="${on}">`;
      html += `<span class="viz-family-dot" style="background:${FAMILY_DOT[info.family]}"></span>`;
      html += `<span class="viz-family-name">${escapeHtml(info.label)}</span>`;
      html += `</button>`;
      continue;
    }

    const summary = familySummary(info, enabledLayers, cloudStyle);
    const expanded = open === info.family;
    html += `<button type="button" class="viz-family${summary.off ? ' is-off' : ''}"`
      + ` data-family="${info.family}" aria-expanded="${expanded}">`;
    html += `<span class="viz-family-dot" style="background:${FAMILY_DOT[info.family]}"></span>`;
    html += `<span class="viz-family-name">${escapeHtml(info.label)}</span>`;
    html += `<span class="viz-family-value">${escapeHtml(summary.text)}</span>`;
    html += `<span class="viz-family-caret" aria-hidden="true">${expanded ? '⌃' : '⌄'}</span>`;
    html += `</button>`;
  }
  html += '</div>';

  const openInfo = open ? families.find((f) => f.family === open) : undefined;
  if (openInfo) {
    html += familyDetailHtml(openInfo, enabledLayers, opts);
    if (opts.aboutFamily === openInfo.family) html += familyAboutHtml(openInfo);
  }

  html += '</div>';
  return html;
}

/** Wire the compound clouds control. Per-source checkboxes (DD, NWP) toggle
 *  each source independently; the shared style dropdown swaps the active
 *  layer-id for any source that's currently on. */
function wireCloudCompound(
  container: HTMLElement,
  enabledLayers: Record<string, boolean>,
  onToggle: (layerId: string) => void,
  onStyleChange?: (style: 'natural' | 'soft' | 'square') => void,
): void {
  const sourceCbs = container.querySelectorAll<HTMLButtonElement>('[data-cloud-source]');
  const styleSel = container.querySelector<HTMLSelectElement>('[data-cloud-style]');
  if (sourceCbs.length === 0 || !styleSel) return;

  // Layer id (if any) currently enabled for a given source.
  const enabledIdFor = (source: 'dd' | 'nwp'): string | null => {
    for (const id of ALL_CLOUD_LAYER_IDS) {
      if (!enabledLayers[id]) continue;
      const axes = parseCloudLayerId(id);
      if (axes?.source === source) return id;
    }
    return null;
  };

  for (const cb of Array.from(sourceCbs)) {
    cb.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (cb.disabled) return;
      const source = cb.dataset.cloudSource as 'dd' | 'nwp';
      const style = styleSel.value as 'natural' | 'soft' | 'square';
      const currentId = enabledIdFor(source);
      // The pill reports the state it is LEAVING, so invert it to get the
      // state the click is asking for.
      const wantOn = cb.getAttribute('aria-pressed') !== 'true';
      if (wantOn) {
        const targetId = CLOUD_LAYER_BY_AXES[source][style];
        if (currentId && currentId !== targetId) onToggle(currentId);
        if (currentId !== targetId) onToggle(targetId);
      } else if (currentId) {
        onToggle(currentId);
      }
    });
  }

  styleSel.addEventListener('change', () => {
    const newStyle = styleSel.value as 'natural' | 'soft' | 'square';
    // Persist the choice even if no sources are enabled — otherwise picking
    // a style with everything unchecked produces no toggle event and the
    // next re-render would discard the selection.
    onStyleChange?.(newStyle);
    // For each source that's currently enabled, swap from its current
    // style-layer to the new style-layer. Sources that are off stay off.
    for (const source of ['dd', 'nwp'] as const) {
      const currentId = enabledIdFor(source);
      if (!currentId) continue;
      const targetId = CLOUD_LAYER_BY_AXES[source][newStyle];
      if (currentId !== targetId) {
        onToggle(currentId);
        onToggle(targetId);
      }
    }
  });
}

/** Wire the layer bar: family chips open/close the detail row, compact chips
 *  toggle a whole family on or off.
 *
 *  Opening a family re-renders only the `.viz-layer-toggles` subtree rather
 *  than asking the caller to re-render everything. Two reasons: the two
 *  callers pass very different argument sets, and a full re-render would make
 *  the open row a caller responsibility — which is exactly how it would come
 *  to slam shut on the next unrelated settings change.
 */
function wireLayerBar(
  container: HTMLElement,
  enabledLayers: Record<string, boolean>,
  opts: LayerTogglesOptions,
  onToggle: (layerId: string) => void,
  rewire: (root: HTMLElement) => void,
): void {
  const rerender = (openFamily: LayerFamily | null, aboutFamily: LayerFamily | null = null): void => {
    // `container` is the wrapper on first render and the block itself after a
    // re-render, so accept both rather than assuming one.
    const block = container.classList.contains('viz-layer-toggles')
      ? container
      : container.querySelector('.viz-layer-toggles');
    if (!block) return;
    const holder = document.createElement('div');
    holder.innerHTML = layerTogglesHtml(enabledLayers, { ...opts, openFamily, aboutFamily });
    const next = holder.firstElementChild;
    if (!next) return;
    block.replaceWith(next);
    rewire(next as HTMLElement);
    // Opening a family replaces this node, which detaches anything holding a
    // reference to it — notably the product tour's driver.js cutout. Nothing
    // in the store changed (the open family is DOM state), so a store
    // subscriber cannot see this. Announce it instead.
    window.dispatchEvent(new CustomEvent(VIZ_LAYER_BAR_RERENDER));
  };

  container.querySelectorAll<HTMLButtonElement>('[data-family]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const family = btn.dataset.family as LayerFamily;
      const isOpen = btn.getAttribute('aria-expanded') === 'true';
      // Switching family closes the About panel: it explains the family you
      // were in, and leaving it up over a different one would just mislead.
      rerender(isOpen ? null : family, null);
    });
  });

  container.querySelectorAll<HTMLButtonElement>('[data-family-about]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const family = btn.dataset.familyAbout as LayerFamily;
      const showing = aboutFamilyIn(
        container.classList.contains('viz-layer-toggles') ? container : (container.querySelector('.viz-layer-toggles') as HTMLElement ?? container),
      );
      rerender(family, showing === family ? null : family);
    });
  });

  container.querySelectorAll<HTMLButtonElement>('.viz-about-close').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      rerender(openFamilyIn(
        container.classList.contains('viz-layer-toggles') ? container : (container.querySelector('.viz-layer-toggles') as HTMLElement ?? container),
      ), null);
    });
  });

  container.querySelectorAll<HTMLButtonElement>('[data-family-toggle]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const family = btn.dataset.familyToggle as LayerFamily;
      const info = getLayerFamilies().find((f) => f.family === family);
      if (!info) return;

      if (btn.getAttribute('aria-pressed') === 'true') {
        // Off: everything currently on in this family goes off.
        for (const layer of enabledInFamily(info, enabledLayers)) onToggle(layer.id);
      } else {
        // On: only the preferred layer of each group, which is the decision
        // compact mode makes for you.
        const targets = (btn.dataset.familyLayers ?? '').split(' ').filter(Boolean);
        for (const id of targets) if (enabledLayers[id] === false) onToggle(id);
      }
    });
  });
}

/** Wire `data-layer-info` and `data-group-info` buttons inside `container`
 *  to their respective info popups. Used by both the main toolbar and the
 *  standalone {@link renderLayerToggles} so an info ⓘ is interactive
 *  wherever the toggles render. */
function wireLayerInfoButtons(container: HTMLElement): void {
  container.querySelectorAll('[data-layer-info]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const el = btn as HTMLElement;
      const layerId = el.dataset.layerInfo!;
      const metricId = el.dataset.metricId!;
      showLayerInfo(layerId, metricId);
    });
  });
  container.querySelectorAll('[data-group-info]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const groupKey = (btn as HTMLElement).dataset.groupInfo!;
      const infoFn = GROUP_INFO[groupKey];
      if (infoFn) showPopupContent(infoFn());
    });
  });
  container.querySelectorAll('[data-front-info]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showPopupContent(renderFrontsInfo());
    });
  });
}

/** Render layer toggle checkboxes into a standalone container and wire
 *  change events + ⓘ info popups. For callers that only need the
 *  layer-selection part of the main toolbar (e.g. the airport-profile
 *  panel's settings drawer). */
export function renderLayerToggles(
  container: HTMLElement,
  enabledLayers: Record<string, boolean>,
  onToggle: (layerId: string) => void,
  opts: LayerTogglesOptions = {},
): void {
  // Carry the open family across the re-render this call performs; without it
  // every layer toggle would close the row the user is working in.
  const withOpen: LayerTogglesOptions = {
    ...opts,
    openFamily: opts.openFamily ?? openFamilyIn(container),
    aboutFamily: opts.aboutFamily ?? aboutFamilyIn(container),
  };
  container.innerHTML = layerTogglesHtml(enabledLayers, withOpen);
  wireToggleBlock(container, enabledLayers, withOpen, onToggle);
}

/** Wire everything inside a rendered `.viz-layer-toggles` block: the layer
 *  checkboxes, the compound clouds control, the ⓘ buttons and the bar itself.
 *  Called on first render and again after the bar re-renders its own subtree. */
function wireToggleBlock(
  root: HTMLElement,
  enabledLayers: Record<string, boolean>,
  opts: LayerTogglesOptions,
  onToggle: (layerId: string) => void,
): void {
  root.querySelectorAll<HTMLButtonElement>('.viz-pill[data-layer-id]').forEach((pill) => {
    pill.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!pill.disabled) onToggle(pill.dataset.layerId!);
    });
  });

  root.querySelectorAll<HTMLButtonElement>('[data-none-group]').forEach((pill) => {
    pill.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      // Clear only what is actually on — `onToggle` flips, so calling it for an
      // already-off layer would switch it ON, which is the opposite of None.
      for (const id of (pill.dataset.noneGroup ?? '').split(' ').filter(Boolean)) {
        if (enabledLayers[id] !== false) onToggle(id);
      }
    });
  });

  wireHintSlot(root);
  wireCloudCompound(root, enabledLayers, onToggle, opts.onCloudStyleChange);
  wireLayerInfoButtons(root);
  wireLayerBar(root, enabledLayers, opts, onToggle, (next) => {
    wireToggleBlock(next, enabledLayers, opts, onToggle);
  });
}

/** Point at a control, read what it is. The hint slot is the whole reason the
 *  panel can carry one ⓘ instead of twenty: help costs no height and no glyph
 *  because it lives in the horizontal space at the end of the row.
 *
 *  Focus writes it as hover does, so tabbing along the pills narrates them. */
function wireHintSlot(root: HTMLElement): void {
  const slot = root.querySelector<HTMLElement>('.viz-hint-text');
  if (!slot) return;
  const fallback = slot.dataset.defaultHint ?? '';

  root.querySelectorAll<HTMLElement>('[data-hint]').forEach((el) => {
    const write = (): void => { slot.textContent = el.dataset.hint ?? fallback; };
    const clear = (): void => { slot.textContent = fallback; };
    el.addEventListener('mouseenter', write);
    el.addEventListener('focus', write);
    el.addEventListener('mouseleave', clear);
    el.addEventListener('blur', clear);
  });
}

export interface VizControlCallbacks {
  onLayerToggle: (layerId: string) => void;
  onLayoutChange: (layout: VizLayout) => void;
  onModelChange?: (model: string) => void;
  onThemeChange?: (themeId: string) => void;
  /** Focus lens — one of the advisory presets, or null for Custom. */
  onPresetChange?: (presetId: string | null) => void;
  /** Tool emulation — GRAMET / Windy / ForeFlight, or null for our own
   *  conventions. Separate from the focus lens because they compose (#591). */
  onEmulationChange?: (presetId: string | null) => void;
  onCloudStyleChange?: (style: 'natural' | 'soft' | 'square') => void;
}

export interface RouteGraphControlCallbacks {
  onRouteGraphToggle: (visible: boolean) => void;
  onRouteGraphMetricChange: (axis: 'left' | 'right', metricId: string) => void;
  /** Corridor width for the observed metrics (#574). Every sampled radius is
   *  already in the payload, so this is a re-render, never a re-fetch. */
  onObservedRadiusChange?: (radiusNm: number) => void;
}

export interface MapControlCallbacks {
  onColorMetricChange: (metricId: string) => void;
  onWidthMetricChange: (metricId: string) => void;
  /** Toggle the experimental Hewson front overlay (#196). */
  onFrontsToggle?: (visible: boolean) => void;
  /** Toggle the airport forecast overlay (#424). */
  onForecastOverlayToggle?: (visible: boolean) => void;
  /** Change the metric the airport forecast overlay colours by (#424). */
  onForecastMetricChange?: (metricId: string) => void;
  /** Pick which observed layer the map draws (#574), '' for none. */
  onObservedOverlayChange?: (source: string) => void;
  /** Set the observed imagery opacity, 0–1. */
  onObservedOpacityChange?: (opacity: number) => void;
}

/** Which observed sources the current briefing actually carries. Options for
 *  anything absent are not offered: an overlay that renders an empty PNG is
 *  worse than a missing menu entry, because the pilot cannot tell "nothing
 *  there" from "not collected". */
export interface ObservedAvailability {
  reflectivity: boolean;
  rainRate: boolean;
  cloudTops: boolean;
  lightning: boolean;
}

/** Airport forecast overlay control state (#424). Present (and the cluster
 *  rendered) only when the flight is within the forecast horizon; absent when
 *  it is more than a week out. */
export interface MapForecastOverlayControls {
  /** Flight is within D-0..D-6 and has a snapshot to show. */
  available: boolean;
  /** User's show/hide preference. */
  visible: boolean;
  /** Active `FORECAST_METRICS` id. */
  metric: string;
  /** Short label for the snapshot valid-time (e.g. "Wed 12Z"); undefined while loading. */
  timeLabel?: string;
  /** Deep-link to the full forecast map seeded with day/hour/model/metric. */
  fullMapUrl?: string;
  /** The briefing's selected model has airport data for this day. When false the
   *  map is empty because of the model, not the weather — the cluster shows an
   *  explanatory note instead of the metric/time so a blank map never reads as
   *  "all clear" (designs/forecast-page.md). */
  modelSupported: boolean;
  /** Selected model id, for the "no data" note. */
  model: string;
}

export function renderVizControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: VizControlCallbacks,
  selectedModel?: string,
  availableModels?: string[],
  displayMode?: DisplayMode,
  preferredMethods?: Record<string, string>,
  unavailableLayers?: Set<string>,
  substitutedLayers?: Set<string>,
  hiddenGroups?: Set<LayerGroup>,
): void {
  let html = '<div class="viz-toolbar">';

  // Top row: Layout toggle + Model indicator + Render mode toggle
  html += '<div class="viz-toolbar-top">';

  // Layout toggle
  html += '<div class="viz-layout-toggle">';
  html += `<button class="btn-toggle${settings.layout === 'cross-section' ? ' active' : ''}" data-layout="cross-section" title="Cross-section only">${t('viz.xSection')}</button>`;
  html += `<button class="btn-toggle${settings.layout === 'compare' ? ' active' : ''}" data-layout="compare" title="Compare one layer across all models">${t('viz.compare')}</button>`;
  html += `<button class="btn-toggle${settings.layout === 'split' ? ' active' : ''}" data-layout="split" title="Side-by-side">${t('viz.split')}</button>`;
  html += `<button class="btn-toggle${settings.layout === 'map' ? ' active' : ''}" data-layout="map" title="Map only">${t('viz.map')}</button>`;
  html += '</div>';

  // Model selector
  if (selectedModel && availableModels && availableModels.length > 0) {
    html += `<div class="viz-model-selector">`;
    html += `<span class="viz-toggle-label">${t('viz.model')}</span>`;
    html += `<select id="viz-model-select" class="viz-model-select">`;
    for (const m of availableModels) {
      const selected = m === selectedModel ? ' selected' : '';
      html += `<option value="${m}"${selected}>${modelLabel(m)}</option>`;
    }
    html += `</select>`;
    html += `</div>`;
  } else if (selectedModel) {
    html += `<div class="viz-model-selector">`;
    html += `<span class="viz-toggle-label">${t('viz.model')}</span>`;
    html += `<span class="viz-model-name">${modelLabel(selectedModel)}</span>`;
    html += `</div>`;
  }

  // Theme selector
  if (settings.layout !== 'map') {
    const currentTheme = getActiveThemeId();
    html += `<div class="viz-theme-selector">`;
    html += `<span class="viz-toggle-label">${t('viz.theme')}</span>`;
    html += `<select id="viz-theme-select" class="viz-model-select">`;
    for (const [id, theme] of Object.entries(THEMES)) {
      const selected = id === currentTheme ? ' selected' : '';
      html += `<option value="${id}"${selected}>${theme.label}</option>`;
    }
    html += `</select>`;
    html += `<button id="viz-theme-preview" class="viz-layer-info-btn" title="${t('viz.previewTheme')}">\u{1f441}</button>`;
    html += `</div>`;
    // Lens: two selectors, because there are two questions (#591).
    //
    //   Focus   — what am I looking for  (the advisory lenses)
    //   Emulate — whose conventions      (GRAMET / Windy / ForeFlight)
    //
    // They compose rather than compete: Emulate picks the METHODS and the
    // look, Focus picks WHICH GROUPS are on, and a lens asking for "the
    // preferred layer of this group" resolves through whatever Emulate chose.
    // Splitting them is also what finally gives "Custom" something to be
    // custom *relative to*.
    const emulations = getPresets();
    const focusLenses = getAdvisoryPresets();
    const activeFocus = settings.activePreset ?? null;
    const activeEmulation = settings.activeEmulation ?? null;
    if (focusLenses.length > 0 || emulations.length > 0) {
      html += `<div class="viz-lens-selector">`;
      html += `<span class="viz-toggle-label">${t('viz.lens')}</span>`;
      if (focusLenses.length > 0) {
        html += `<span class="viz-toggle-sub">${t('viz.lensFocus')}</span>`;
        html += `<select id="viz-preset-select" class="viz-model-select">`;
        html += `<option value=""${activeFocus === null ? ' selected' : ''}>${t('viz.presetCustom')}</option>`;
        for (const preset of focusLenses) {
          html += `<option value="${preset.id}"${preset.id === activeFocus ? ' selected' : ''}>${escapeHtml(advisoryPresetLabel(preset))}</option>`;
        }
        html += `</select>`;
      }
      if (emulations.length > 0) {
        html += `<span class="viz-toggle-sub">${t('viz.lensEmulate')}</span>`;
        html += `<select id="viz-emulation-select" class="viz-model-select">`;
        html += `<option value=""${activeEmulation === null ? ' selected' : ''}>${t('viz.emulateNone')}</option>`;
        for (const preset of emulations) {
          html += `<option value="${preset.id}"${preset.id === activeEmulation ? ' selected' : ''}>${escapeHtml(preset.label)}</option>`;
        }
        html += `</select>`;
      }
      html += `</div>`;
    }
  }

  // Windy link placeholder (updated dynamically by updateWindyLink)
  html += `<span id="external-links" class="external-links" style="display: none;">`;
  html += `${t('viz.externalLinks')}<a id="windy-link" href="#" target="_blank" rel="noopener">${t('viz.windy')}</a>`;
  html += `</span>`;

  html += '</div>'; // .viz-toolbar-top

  // Layer toggles — only when cross-section visible. The HTML is built
  // by `layerTogglesHtml()` (also reused by `renderLayerToggles()` for
  // the airport-profile drawer); change listeners are wired below.
  const toggleOpts: LayerTogglesOptions = {
    displayMode, preferredMethods, unavailableLayers, substitutedLayers,
    cloudStyle: settings.cloudStyle, hiddenGroups,
    // Read BEFORE the innerHTML assignment below wipes the old markup: every
    // layer toggle re-renders this whole container, and losing the open family
    // here is what would make the detail row shut on each click (#591).
    openFamily: openFamilyIn(container),
    aboutFamily: aboutFamilyIn(container),
    onCloudStyleChange: callbacks.onCloudStyleChange,
  };
  if (settings.layout !== 'map') {
    html += layerTogglesHtml(settings.enabledLayers, toggleOpts);
  }
  html += '</div>'; // .viz-toolbar

  // Advisory-preset caption (#219): a one-line note explaining the active view.
  // Only emitted for advisory presets; absent for GRAMET/Custom (the panel
  // re-renders on every settings change, so it clears itself). Captions are
  // trusted static config literals today, but escape defensively so a future
  // i18n/API-sourced caption can't inject markup.
  if (settings.layout !== 'map' && isAdvisoryPreset(settings.activePreset)) {
    const ap = getAdvisoryPreset(settings.activePreset!);
    if (ap) {
      // Caption + a "Help me read this" (i) button surfacing the longer
      // interpretation blurb (#308 Phase B) — same text the Skew-T and the MCP
      // explanation use.
      html += `<div class="viz-preset-caption">${escapeHtml(advisoryPresetCaption(ap))}`
        + `<button class="viz-layer-info-btn viz-preset-help-btn" data-preset-help="${ap.id}" `
        + `title="${t('viz.helpReadGraph')}" aria-label="${t('viz.helpReadGraph')}">ⓘ</button></div>`;
    }
  }

  container.innerHTML = html;

  // Wire layout toggle
  container.querySelectorAll('[data-layout]').forEach((btn) => {
    btn.addEventListener('click', () => {
      callbacks.onLayoutChange((btn as HTMLElement).dataset.layout as VizLayout);
    });
  });

  // Wire the preset "Help me read this" (i) button → interpretation popup (#308).
  container.querySelectorAll('[data-preset-help]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = (btn as HTMLElement).dataset.presetHelp!;
      const ap = getAdvisoryPreset(id);
      if (!ap) return;
      showPopupContent(
        `<div class="skewt-help-popup"><h3>${escapeHtml(advisoryPresetLabel(ap))}</h3>`
        + `<p>${escapeHtml(advisoryPresetInterpretation(ap))}</p></div>`,
      );
    });
  });

  // Wire the layer bar, its checkboxes, the compound clouds control and the
  // ⓘ buttons — all of which re-wire themselves when the bar re-renders.
  wireToggleBlock(container, settings.enabledLayers, toggleOpts, callbacks.onLayerToggle);

  // Wire model selector
  const vizModelSelect = container.querySelector('#viz-model-select') as HTMLSelectElement | null;
  if (vizModelSelect && callbacks.onModelChange) {
    const cb = callbacks.onModelChange;
    vizModelSelect.addEventListener('change', () => {
      cb(vizModelSelect.value);
    });
  }

  // Wire theme selector
  const vizThemeSelect = container.querySelector('#viz-theme-select') as HTMLSelectElement | null;
  if (vizThemeSelect && callbacks.onThemeChange) {
    const cb = callbacks.onThemeChange;
    vizThemeSelect.addEventListener('change', () => {
      cb(vizThemeSelect.value);
    });
  }
  const themePreviewBtn = container.querySelector('#viz-theme-preview') as HTMLButtonElement | null;
  if (themePreviewBtn) {
    themePreviewBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const themeId = vizThemeSelect?.value ?? getActiveThemeId();
      showThemePreview(themeId as ThemeId, { cloudStyle: settings.cloudStyle });
    });
  }
  // Wire the two lens selectors. Focus goes through onPresetChange, which
  // already dispatches advisory presets to the method-resolving path; Emulate
  // goes through onEmulationChange, which applies theme + methods.
  const vizPresetSelect = container.querySelector('#viz-preset-select') as HTMLSelectElement | null;
  if (vizPresetSelect && callbacks.onPresetChange) {
    const presetCb = callbacks.onPresetChange;
    vizPresetSelect.addEventListener('change', () => {
      presetCb(vizPresetSelect.value || null);
    });
  }
  const vizEmulationSelect = container.querySelector('#viz-emulation-select') as HTMLSelectElement | null;
  if (vizEmulationSelect && callbacks.onEmulationChange) {
    const emulationCb = callbacks.onEmulationChange;
    vizEmulationSelect.addEventListener('change', () => {
      emulationCb(vizEmulationSelect.value || null);
    });
  }

  // Wire layer + group info ⓘ buttons (shared with renderLayerToggles).
  wireLayerInfoButtons(container);
}

/** Render route graph controls (toggle + metric dropdowns) into a separate container below the graph. */
export function renderRouteGraphControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: RouteGraphControlCallbacks,
  /** Sampled corridor widths (NM) from the observed payload. Empty (the
   *  default) hides the selector entirely — there is nothing to choose
   *  between when no observed data was collected. */
  observedRadiiNm: readonly number[] = [],
  /** The radius currently in effect, so the selector shows what is drawn
   *  even when settings hold the "widest" default. */
  activeObservedRadiusNm: number | null = null,
): void {
  const leftOptions = getMetricOptions(false);
  const rightOptions = getMetricOptions(true);
  const arrow = settings.routeGraphVisible ? '\u25BC' : '\u25B6';

  let html = '<div class="route-graph-controls">';

  // Toggle button
  html += `<button id="route-graph-toggle" class="route-graph-toggle-btn" title="Show/hide route graph">`;
  html += `<span class="route-graph-arrow">${arrow}</span> ${t('viz.routeGraph')}`;
  html += `</button>`;

  // Metric selectors (only shown when visible)
  if (settings.routeGraphVisible) {
    html += '<div class="route-graph-selectors">';

    html += '<label class="route-graph-select-label">';
    html += `<span class="viz-toggle-label">${t('viz.left')}</span>`;
    html += '<select id="route-graph-left-metric" class="route-graph-select">';
    for (const opt of leftOptions) {
      const selected = opt.id === settings.routeGraphLeftMetric ? ' selected' : '';
      html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
    }
    html += '</select>';
    html += '</label>';

    html += '<label class="route-graph-select-label">';
    html += `<span class="viz-toggle-label">${t('viz.right')}</span>`;
    html += '<select id="route-graph-right-metric" class="route-graph-select">';
    for (const opt of rightOptions) {
      const selected = opt.id === settings.routeGraphRightMetric ? ' selected' : '';
      html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
    }
    html += '</select>';
    html += '</label>';

    if (observedRadiiNm.length > 1) {
      const active = activeObservedRadiusNm ?? Math.max(...observedRadiiNm);
      html += '<label class="route-graph-select-label">';
      html += `<span class="viz-toggle-label">${t('observed.corridor')}</span>`;
      html += '<select id="route-graph-observed-radius" class="route-graph-select">';
      for (const radius of observedRadiiNm) {
        const selected = radius === active ? ' selected' : '';
        html += `<option value="${radius}"${selected}>${radius} NM</option>`;
      }
      html += '</select>';
      html += '</label>';
    }

    html += '</div>';
  }

  html += '</div>';

  container.innerHTML = html;

  // Wire toggle
  const graphToggle = container.querySelector('#route-graph-toggle') as HTMLButtonElement | null;
  if (graphToggle) {
    graphToggle.addEventListener('click', () => {
      callbacks.onRouteGraphToggle(!settings.routeGraphVisible);
    });
  }

  // Wire metric dropdowns
  const leftSelect = container.querySelector('#route-graph-left-metric') as HTMLSelectElement | null;
  if (leftSelect) {
    leftSelect.addEventListener('change', () => {
      callbacks.onRouteGraphMetricChange('left', leftSelect.value);
    });
  }
  const rightSelect = container.querySelector('#route-graph-right-metric') as HTMLSelectElement | null;
  if (rightSelect) {
    rightSelect.addEventListener('change', () => {
      callbacks.onRouteGraphMetricChange('right', rightSelect.value);
    });
  }
  const radiusSelect = container.querySelector('#route-graph-observed-radius') as HTMLSelectElement | null;
  if (radiusSelect && callbacks.onObservedRadiusChange) {
    radiusSelect.addEventListener('change', () => {
      callbacks.onObservedRadiusChange!(Number(radiusSelect.value));
    });
  }
}

/** Render map-specific controls (color + width metric dropdowns) into the map controls container. */
export function renderMapControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: MapControlCallbacks,
  frontsAvailable = false,
  forecastOverlay?: MapForecastOverlayControls,
  observed?: ObservedAvailability,
): void {
  const colorOptions = getMapMetricOptions(false);
  const widthOptions = getMapMetricOptions(true);

  let html = '<div class="map-controls">';

  html += '<label class="map-control-label">';
  html += `<span class="viz-toggle-label">${t('viz.color')}</span>`;
  html += '<select id="map-color-metric" class="map-control-select">';
  for (const opt of colorOptions) {
    const selected = opt.id === settings.mapColorMetric ? ' selected' : '';
    html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
  }
  html += '</select>';
  html += '</label>';

  html += '<label class="map-control-label">';
  html += `<span class="viz-toggle-label">${t('viz.width')}</span>`;
  html += '<select id="map-width-metric" class="map-control-select">';
  for (const opt of widthOptions) {
    const selected = opt.id === settings.mapWidthMetric ? ' selected' : '';
    html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
  }
  html += '</select>';
  html += '</label>';

  // Experimental Hewson front overlay — only surfaced when front data exists
  // for this briefing (i.e. the "Auto Front Detection" pref was on).
  if (frontsAvailable) {
    const checked = settings.mapFrontsVisible ? ' checked' : '';
    html += '<label class="map-control-label viz-toggle">';
    html += `<input type="checkbox" id="map-fronts-toggle"${checked}>`;
    html += `<span class="viz-toggle-label">${t('viz.fronts')}</span>`;
    html += '</label>';
  }

  // Airport forecast overlay (#424), right-aligned. Rendered only when the
  // flight is within the forecast horizon (otherwise nothing to show).
  if (forecastOverlay?.available) {
    const fo = forecastOverlay;
    const checked = fo.visible ? ' checked' : '';
    html += '<div class="map-forecast-controls">';
    html += '<label class="map-control-label viz-toggle">';
    html += `<input type="checkbox" id="map-forecast-toggle"${checked}>`;
    html += `<span class="viz-toggle-label">${t('viz.airports')}</span>`;
    html += '</label>';
    if (fo.modelSupported) {
      const timeLabel = fo.timeLabel ?? '…';
      html += `<span class="map-forecast-time" id="map-forecast-time" title="${escapeHtml(t('viz.airports.timeHint'))}">${escapeHtml(timeLabel)}</span>`;
      html += '<label class="map-control-label">';
      html += `<span class="viz-toggle-label">${t('viz.airports.show')}</span>`;
      html += '<select id="map-forecast-metric" class="map-control-select">';
      for (const m of FORECAST_METRICS) {
        const selected = m === fo.metric ? ' selected' : '';
        html += `<option value="${m}"${selected}>${escapeHtml(METRIC_LABEL[m])}</option>`;
      }
      html += '</select>';
      html += '</label>';
    } else {
      // Empty because the selected model has no airport data for this day, not
      // because the weather is clear — say so (forecast-page.md).
      html += `<span class="map-forecast-note">${escapeHtml(t('viz.airports.noModelData', { model: modelLabel(fo.model) }))}</span>`;
    }
    if (fo.fullMapUrl) {
      const openLabel = escapeHtml(t('viz.airports.openMap'));
      html += `<a class="map-forecast-open" id="map-forecast-open" href="${escapeHtml(fo.fullMapUrl)}" target="_blank" rel="noopener" title="${openLabel}" aria-label="${openLabel}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/></svg></a>`;
    }
    html += '</div>';
  }

  // Observed layer sits apart from Color/Width, and to the right: those two
  // style the FORECAST route line, this picks a MEASUREMENT of the real sky.
  // Grouped together they read as three knobs on one thing, which they are not.
  //
  // Outside the forecast-overlay block on purpose — nesting it there made the
  // selector appear only while the airport overlay happened to be on.
  //
  // One at a time: these are different measurements of the same sky, and
  // stacking them leaves a colour on the map whose source is ambiguous.
  if (observed && (observed.reflectivity || observed.rainRate || observed.cloudTops || observed.lightning)) {
    html += '<div class="map-controls-observed">';
    html += '<label class="map-control-label">';
    html += `<span class="viz-toggle-label">${t('viz.observed.label')}</span>`;
    html += '<select id="map-observed-overlay" class="map-control-select">';
    for (const opt of OBSERVED_OVERLAY_OPTIONS) {
      if (opt.needs && !observed[opt.needs]) continue;
      const selected = opt.id === settings.observedOverlay ? ' selected' : '';
      html += `<option value="${opt.id}"${selected}>${escapeHtml(t(opt.labelKey))}</option>`;
    }
    html += '</select>';
    html += '</label>';
    // Opacity, like the synoptic grid layer's. These rasters cover the whole
    // corridor, so a fixed value either buries the basemap or washes the data
    // out depending on the product — and the pilot needs to read place names
    // through it.
    if (settings.observedOverlay && settings.observedOverlay !== 'eumetsat_li') {
      const pct = Math.round((settings.observedOverlayOpacity ?? 0.75) * 100);
      html += '<label class="map-control-label map-observed-opacity">';
      html += `<span class="viz-toggle-label">${escapeHtml(t('viz.observed.opacity'))}</span>`;
      html += `<input type="range" id="map-observed-opacity" min="10" max="100" step="5" value="${pct}">`;
      html += `<span class="map-observed-opacity-value" id="map-observed-opacity-value">${pct}%</span>`;
      html += '</label>';
    }
    html += '</div>';
  }

  html += '</div>';

  container.innerHTML = html;

  const forecastToggle = container.querySelector('#map-forecast-toggle') as HTMLInputElement | null;
  if (forecastToggle) {
    forecastToggle.addEventListener('change', () => {
      callbacks.onForecastOverlayToggle?.(forecastToggle.checked);
    });
  }
  const forecastMetricSelect = container.querySelector('#map-forecast-metric') as HTMLSelectElement | null;
  if (forecastMetricSelect) {
    forecastMetricSelect.addEventListener('change', () => {
      callbacks.onForecastMetricChange?.(forecastMetricSelect.value);
    });
  }

  const frontsToggle = container.querySelector('#map-fronts-toggle') as HTMLInputElement | null;
  if (frontsToggle) {
    frontsToggle.addEventListener('change', () => {
      callbacks.onFrontsToggle?.(frontsToggle.checked);
    });
  }

  // Wire metric dropdowns
  const observedOpacity = container.querySelector('#map-observed-opacity') as HTMLInputElement | null;
  if (observedOpacity && callbacks.onObservedOpacityChange) {
    const readout = container.querySelector('#map-observed-opacity-value') as HTMLElement | null;
    observedOpacity.addEventListener('input', () => {
      const pct = Number(observedOpacity.value);
      if (readout) readout.textContent = `${pct}%`;
      callbacks.onObservedOpacityChange!(pct / 100);
    });
  }

  const observedSelect = container.querySelector('#map-observed-overlay') as HTMLSelectElement | null;
  if (observedSelect && callbacks.onObservedOverlayChange) {
    observedSelect.addEventListener('change', () => {
      callbacks.onObservedOverlayChange!(observedSelect.value);
    });
  }

  const colorSelect = container.querySelector('#map-color-metric') as HTMLSelectElement | null;
  if (colorSelect) {
    colorSelect.addEventListener('change', () => {
      callbacks.onColorMetricChange(colorSelect.value);
    });
  }
  const widthSelect = container.querySelector('#map-width-metric') as HTMLSelectElement | null;
  if (widthSelect) {
    widthSelect.addEventListener('change', () => {
      callbacks.onWidthMetricChange(widthSelect.value);
    });
  }
}

// --- Compare mode controls ---

export interface CompareControlCallbacks {
  onLayoutChange: (layout: VizLayout) => void;
  onCompareLayerChange: (layerId: string) => void;
  onCompareModelToggle: (model: string, enabled: boolean) => void;
  onCompareBandModeChange: (mode: CompareBandMode) => void;
  onThemeChange?: (themeId: string) => void;
  onPresetChange?: (presetId: string | null) => void;
}

/** Render compare-mode controls: layout toggle, layer dropdown, model chips. */
export function renderCompareControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: CompareControlCallbacks,
  availableModels: string[],
): void {
  const layerGroups = getComparableLayerGroups();

  let html = '<div class="viz-toolbar">';

  // Top row: Layout toggle
  html += '<div class="viz-toolbar-top">';

  // Layout toggle (same 4 buttons, Compare active)
  html += '<div class="viz-layout-toggle">';
  html += `<button class="btn-toggle" data-layout="cross-section" title="Cross-section only">${t('viz.xSection')}</button>`;
  html += `<button class="btn-toggle active" data-layout="compare" title="Compare one layer across all models">${t('viz.compare')}</button>`;
  html += `<button class="btn-toggle" data-layout="split" title="Side-by-side">${t('viz.split')}</button>`;
  html += `<button class="btn-toggle" data-layout="map" title="Map only">${t('viz.map')}</button>`;
  html += '</div>';

  // Layer selector
  html += '<div class="viz-compare-layer-selector">';
  html += `<span class="viz-toggle-label">${t('viz.layer')}</span>`;
  html += '<select id="compare-layer-select" class="viz-model-select">';
  for (const group of layerGroups) {
    html += `<optgroup label="${group.group}">`;
    for (const layer of group.layers) {
      const selected = layer.id === settings.compareLayer ? ' selected' : '';
      html += `<option value="${layer.id}"${selected}>${t('viz.layer.' + layer.id)}</option>`;
    }
    html += '</optgroup>';
  }
  html += '</select>';
  html += '</div>';

  // Band mode selector (only visible when a band layer is selected)
  const selectedLayer = getComparableLayer(settings.compareLayer);
  const isBand = selectedLayer?.type === 'band';
  const bandModeStyle = isBand ? '' : ' style="display:none"';
  const bm = settings.compareBandMode ?? 'consensus-outline';
  html += `<div class="viz-band-mode-selector"${bandModeStyle}>`;
  html += `<span class="viz-toggle-label">${t('viz.bandMode')}</span>`;
  html += '<select id="compare-band-mode-select" class="viz-model-select">';
  const bandModes: { value: CompareBandMode; label: string }[] = [
    { value: 'overlay', label: t('viz.bandMode.overlay') },
    { value: 'overlay-soft', label: t('viz.bandMode.overlaySoft') },
    { value: 'consensus', label: t('viz.bandMode.consensus') },
    { value: 'consensus-outline', label: t('viz.bandMode.consensusOutline') },
  ];
  for (const mode of bandModes) {
    const selected = bm === mode.value ? ' selected' : '';
    html += `<option value="${mode.value}"${selected}>${mode.label}</option>`;
  }
  html += '</select>';
  html += '</div>';

  // Theme selector
  const currentTheme = getActiveThemeId();
  html += `<div class="viz-theme-selector">`;
  html += `<span class="viz-toggle-label">${t('viz.theme')}</span>`;
  html += `<select id="viz-theme-select" class="viz-model-select">`;
  for (const [id, theme] of Object.entries(THEMES)) {
    const selected = id === currentTheme ? ' selected' : '';
    html += `<option value="${id}"${selected}>${theme.label}</option>`;
  }
  html += `</select>`;
  html += `<button id="viz-theme-preview" class="viz-layer-info-btn" title="${t('viz.previewTheme')}">\u{1f441}</button>`;
  html += `</div>`;

  html += '</div>'; // .viz-toolbar-top

  // Model chips
  if (availableModels.length > 0) {
    html += '<div class="viz-compare-model-chips">';
    html += `<span class="viz-toggle-label">${t('viz.models')}</span>`;
    for (const m of availableModels) {
      const enabled = settings.compareModels[m] !== false;
      const activeClass = enabled ? ' active' : '';
      html += `<button class="btn-chip${activeClass}" data-compare-model="${m}">${modelLabel(m)}</button>`;
    }
    html += '</div>';
  }

  html += '</div>'; // .viz-toolbar

  container.innerHTML = html;

  // Wire layout toggle
  container.querySelectorAll('[data-layout]').forEach((btn) => {
    btn.addEventListener('click', () => {
      callbacks.onLayoutChange((btn as HTMLElement).dataset.layout as VizLayout);
    });
  });

  // Wire layer selector
  const layerSelect = container.querySelector('#compare-layer-select') as HTMLSelectElement | null;
  if (layerSelect) {
    layerSelect.addEventListener('change', () => {
      callbacks.onCompareLayerChange(layerSelect.value);
    });
  }

  // Wire band mode selector
  const bandModeSelect = container.querySelector('#compare-band-mode-select') as HTMLSelectElement | null;
  if (bandModeSelect) {
    bandModeSelect.addEventListener('change', () => {
      callbacks.onCompareBandModeChange(bandModeSelect.value as CompareBandMode);
    });
  }

  // Wire model chips
  container.querySelectorAll('[data-compare-model]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const el = btn as HTMLElement;
      const model = el.dataset.compareModel!;
      const currentlyActive = el.classList.contains('active');
      callbacks.onCompareModelToggle(model, !currentlyActive);
    });
  });

  // Wire theme selector
  const vizThemeSelect = container.querySelector('#viz-theme-select') as HTMLSelectElement | null;
  if (vizThemeSelect && callbacks.onThemeChange) {
    const cb = callbacks.onThemeChange;
    vizThemeSelect.addEventListener('change', () => {
      cb(vizThemeSelect.value);
    });
  }
  const themePreviewBtn = container.querySelector('#viz-theme-preview') as HTMLButtonElement | null;
  if (themePreviewBtn) {
    themePreviewBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const themeId = vizThemeSelect?.value ?? getActiveThemeId();
      showThemePreview(themeId as ThemeId, { cloudStyle: settings.cloudStyle });
    });
  }
}
