/** Observed nutshell (#690) — layer 0 of the Observed section.
 *
 * SYNC — paired with `ObservedNutshellCard` in
 * app/flyfun-weather/flyfun-weather/Views/Briefing/ObservedNutshellView.swift.
 *
 * The headline comparing with the briefing, then one line per phase
 * (departure / en route / arrival). **Every word is the server's**
 * (`tasks/live_glance.py`): iOS, web and the agent `live` block show the same
 * text, so nothing here re-words, re-orders or re-grades a line. The client
 * adds exactly three things: the phase label, an alert bar when the server
 * marked the phase alert-tier, and the tap that opens the map on what the
 * line summarises.
 */

import { escapeHtml } from '../../utils';
import type { LiveFocus, LiveGlance, LiveGlanceLine } from '../../store/types';
import { phaseLabel } from './ribbon-core';

export interface NutshellHandlers {
  /** Open the route map framed on this item with its layers on. */
  onFocus: (focus: LiveFocus) => void;
  /** Open the route map with the observed layers on (no particular item). */
  onShowMap: () => void;
}

function lineHtml(line: LiveGlanceLine, index: number): string {
  const tappable = line.focus != null;
  // "unavailable" is never "clear" (#689): the server already says so in the
  // text, and this marks which source it was so it reads at a glance.
  const missing = (line.unavailable ?? []).length > 0
    ? `<span class="glance-missing">${escapeHtml((line.unavailable ?? []).join(', '))} unavailable</span>`
    : '';
  return `<${tappable ? 'button type="button"' : 'div'} class="glance-line`
    + `${line.alert ? ' glance-line-alert' : ''}${line.passed ? ' glance-line-passed' : ''}"`
    + `${tappable ? ` data-glance-focus="${index}"` : ''}`
    + ` data-testid="glance-line-${escapeHtml(line.phase ?? String(index))}">`
    + '<span class="glance-bar" aria-hidden="true"></span>'
    + '<span class="glance-body">'
    + `<span class="glance-phase">${escapeHtml(phaseLabel(line.phase))}</span>`
    + `<span class="glance-text">${escapeHtml(line.text ?? '')}</span>${missing}`
    + '</span>'
    + (tappable ? '<span class="glance-chevron" aria-hidden="true">▸</span>' : '')
    + `</${tappable ? 'button' : 'div'}>`;
}

/** The nutshell card's markup. Pure, so the wording and the alert/passed
 *  states can be asserted without a DOM. */
export function nutshellHtml(glance: LiveGlance, showMapButton: boolean): string {
  const lines = (glance.lines ?? []).map(lineHtml).join('');
  // `data-live-age`, not `data-live-asof`: the headline already opens with
  // "Observed HH:MMZ", so this adds only the age, which `refreshLiveAges`
  // rewrites in place each minute. The as-of variant would restate the time.
  const asOf = glance.as_of
    ? `<span class="glance-asof" data-live-age="${escapeHtml(glance.as_of)}"></span>`
    : '';
  return '<div class="glance-card" data-testid="observed-nutshell">'
    + `<div class="glance-headline" data-testid="glance-headline">`
    + `${escapeHtml(glance.headline ?? '')}${asOf}</div>`
    + `<div class="glance-lines">${lines}</div>`
    + (showMapButton
      ? '<button type="button" class="btn-secondary glance-map-btn" data-glance-showmap>'
        + 'Show radar &amp; cells on map</button>'
      : '')
    + '</div>';
}

/** Render the nutshell into `el` and wire its taps. */
export function mountNutshell(
  el: HTMLElement,
  glance: LiveGlance,
  showMapButton: boolean,
  handlers: NutshellHandlers,
): void {
  el.innerHTML = nutshellHtml(glance, showMapButton);
  el.querySelectorAll<HTMLElement>('[data-glance-focus]').forEach((node) => {
    node.addEventListener('click', () => {
      const focus = (glance.lines ?? [])[Number(node.dataset.glanceFocus)]?.focus;
      if (focus) handlers.onFocus(focus);
    });
  });
  el.querySelector<HTMLElement>('[data-glance-showmap]')
    ?.addEventListener('click', () => handlers.onShowMap());
}
