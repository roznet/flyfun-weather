/** Observed nutshell (#690) — layer 0 of the Observed section.
 *
 * SYNC — paired with `ObservedNutshellCard` in
 * app/flyfun-weather/flyfun-weather/Views/Briefing/ObservedNutshellView.swift.
 * SYNC — the highlight block is paired with `ObservedHighlightCard` in
 * app/flyfun-weather/flyfun-weather/Views/Briefing/ObservedHighlightView.swift
 * (#697). Documented divergence: iOS folds the headline and the non-alert
 * lines under "Details"; the web leaves them unfolded (the desktop has room).
 *
 * Reading order (#816, around the pilot's in-flight questions): the flight
 * strip (where the flight is, ETA), the alert-tier lines, the model-written
 * highlight with its "experimental · written HH:MMZ" caption and 👍/👎 (the
 * headline in its slot when there is none, #697), the lines still ahead, the
 * "Destination at your ETA" block (it stands in for a non-alert arrival
 * line), then the passed lines, dimmed. After landing the strip says so and
 * the rest reads as it was. The highlight is never styled as an alert.
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
import { t } from '../../i18n/i18n';
import { formatHhmmZ } from '../../helpers/live-layer';
import type {
  LiveFocus, LiveGlance, LiveGlanceDestination, LiveGlanceFlight, LiveGlanceLine, LiveHighlight,
} from '../../store/types';
import { phaseLabel } from './ribbon-core';

export type HighlightSentiment = 'up' | 'down';

export interface NutshellHandlers {
  /** Open the route map framed on this item with its layers on. */
  onFocus: (focus: LiveFocus) => void;
  /** Open the route map with the observed layers on (no particular item). */
  onShowMap: () => void;
  /** Post a 👍/👎 on the highlight; rejects on failure (the form stays
   *  open with the error). Absent: no thumbs are drawn. */
  onRateHighlight?: (
    highlight: LiveHighlight, sentiment: HighlightSentiment, comment: string, contactOk: boolean,
  ) => Promise<void>;
}

export interface NutshellOptions {
  /** Draw the 👍/👎 under the highlight. */
  rateable?: boolean;
  /** This highlight was already rated in this page view: thanks, no thumbs. */
  highlightRated?: boolean;
}

/** Dedup key for a highlight rating: one per flight and rated line. */
export function highlightRatingKey(flightId: string, highlight: LiveHighlight): string {
  return `${flightId}|${highlight.facts_hash}`;
}

/** "Experimental, still being calibrated. … · written 08:20Z". */
export function highlightCaption(highlight: LiveHighlight): string {
  const written = formatHhmmZ(highlight.generated_at);
  const note = t('observed.highlight.experimental');
  return written ? `${note} · ${t('observed.highlight.written', { time: written })}` : note;
}

function thumbsHtml(opts: NutshellOptions): string {
  if (opts.highlightRated) {
    return `<div class="glance-highlight-feedback"><span class="digest-feedback-thanks">${escapeHtml(t('feedback.thumbs.thanks'))}</span></div>`;
  }
  if (!opts.rateable) return '';
  const up = escapeHtml(t('observed.highlight.up'));
  const down = escapeHtml(t('observed.highlight.down'));
  return '<div class="glance-highlight-feedback" data-hl-feedback>'
    + `<button type="button" class="digest-thumb-btn" data-hl-thumb="up" aria-label="${up}" title="${up}">👍</button>`
    + `<button type="button" class="digest-thumb-btn" data-hl-thumb="down" aria-label="${down}" title="${down}">👎</button>`
    + '<div class="digest-feedback-form" data-hl-form style="display:none;">'
    + '<p class="digest-feedback-helper" aria-live="polite" data-hl-helper></p>'
    + '<textarea data-hl-comment rows="3" maxlength="2000"></textarea>'
    + '<label class="digest-feedback-consent">'
    + `<input type="checkbox" data-hl-contact checked> ${escapeHtml(t('feedback.contactOk'))}</label>`
    + '<div class="digest-feedback-actions">'
    + `<button type="button" class="btn" data-hl-cancel>${escapeHtml(t('feedback.cancel'))}</button>`
    + `<button type="button" class="btn btn-primary" data-hl-send>${escapeHtml(t('feedback.thumbs.send'))}</button>`
    + '</div></div>'
    + '<div class="digest-feedback-error" data-hl-error></div>'
    + '</div>';
}

/** The highlight block: the server's line, the caption, the thumbs. */
export function highlightHtml(highlight: LiveHighlight, opts: NutshellOptions = {}): string {
  return '<div class="glance-highlight" data-testid="glance-highlight">'
    + `<p class="glance-highlight-text">${escapeHtml(highlight.text)}</p>`
    + `<div class="glance-highlight-caption">${escapeHtml(highlightCaption(highlight))}</div>`
    + thumbsHtml(opts)
    + '</div>';
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
    // #767: a terminal read through a nearby station says which, in gray.
    + (line.proxy_caption ? `<span class="glance-proxy">${escapeHtml(line.proxy_caption)}</span>` : '')
    + '</span>'
    + (tappable ? '<span class="glance-chevron" aria-hidden="true">▸</span>' : '')
    + `</${tappable ? 'button' : 'div'}>`;
}

/** The flight strip (#816): the server's line, a phase class for colour. */
export function flightStripHtml(flight: LiveGlanceFlight): string {
  return `<div class="glance-flight glance-flight-${escapeHtml(flight.phase)}" data-testid="glance-flight">`
    + `<span class="glance-flight-icon" aria-hidden="true">${flight.phase === 'landed' ? '🛬' : '✈︎'}</span>`
    + `<span class="glance-flight-text">${escapeHtml(flight.text)}</span>`
    + '</div>';
}

/** "Destination at your ETA" (#816): every clause is the server's; the
 *  client adds the title, the ETA and the wind advisory's colour. Cells with
 *  a focus are buttons (`data-dest-cell`), the title opens the field. */
export function destinationHtml(dest: LiveGlanceDestination): string {
  const eta = formatHhmmZ(dest.eta ?? null);
  const title = `${t('observed.destination.title')} · ${dest.icao}${dest.name ? ` ${dest.name}` : ''}`
    + (eta ? ` · ${eta}` : '');
  const row = (label: string, text: string, extra = '') =>
    `<div class="glance-dest-row${extra}"><span class="glance-dest-label">${escapeHtml(label)}</span>`
    + `<span class="glance-dest-text">${escapeHtml(text)}</span></div>`;
  const cells = (dest.cells ?? []).map((c, i) => (c.focus
    ? `<button type="button" class="glance-dest-cell" data-dest-cell="${i}">${escapeHtml(c.text)}`
      + '<span class="glance-chevron" aria-hidden="true">▸</span></button>'
    : `<div class="glance-dest-cell">${escapeHtml(c.text)}</div>`)).join('');
  const missing = (dest.unavailable ?? []).length > 0
    ? `<span class="glance-missing">${escapeHtml((dest.unavailable ?? []).join(', '))} unavailable</span>`
    : '';
  const advisory = dest.wind_advisory ? ` glance-dest-wind-${escapeHtml(dest.wind_advisory)}` : '';
  return `<div class="glance-dest${dest.alert ? ' glance-dest-alert' : ''}" data-testid="glance-destination">`
    + (dest.focus
      ? `<button type="button" class="glance-dest-title" data-dest-focus>${escapeHtml(title)}`
        + '<span class="glance-chevron" aria-hidden="true">▸</span></button>'
      : `<div class="glance-dest-title">${escapeHtml(title)}</div>`)
    + row('METAR', dest.metar_text)
    + (dest.proxy_caption ? `<span class="glance-proxy">${escapeHtml(dest.proxy_caption)}</span>` : '')
    + row('TAF', dest.taf_text)
    + (dest.wind_text ? row('Wind', dest.wind_text, advisory) : '')
    + row('Cells', dest.cells_text)
    + (cells ? `<div class="glance-dest-cells">${cells}</div>` : '')
    + (dest.lightning_text ? row('Lightning', dest.lightning_text) : '')
    + missing
    + '</div>';
}

/** The nutshell card's markup. Pure, so the wording and the alert/passed
 *  states can be asserted without a DOM. */
export function nutshellHtml(
  glance: LiveGlance, showMapButton: boolean, opts: NutshellOptions = {},
): string {
  // Indices stay those of `glance.lines`: the focus taps look them up there.
  const indexed = (glance.lines ?? []).map((line, i) => ({ line, i }));
  const dest = glance.destination ?? null;
  const html = (list: typeof indexed) => list.map(({ line, i }) => lineHtml(line, i)).join('');
  const alertLines = html(indexed.filter(({ line }) => line.alert));
  // The destination block stands in for a non-alert arrival line (#816).
  const rest = indexed.filter(({ line }) => !line.alert && !(dest && line.phase === 'arrival'));
  const aheadLines = html(rest.filter(({ line }) => !line.passed));
  const passedLines = html(rest.filter(({ line }) => line.passed));
  // `data-live-age`, not `data-live-asof`: the headline already opens with
  // "Observed HH:MMZ", so this adds only the age, which `refreshLiveAges`
  // rewrites in place each minute. The as-of variant would restate the time.
  const asOf = glance.as_of
    ? `<span class="glance-asof" data-live-age="${escapeHtml(glance.as_of)}"></span>`
    : '';
  const highlight = glance.highlight ?? null;
  const headline = `<div class="glance-headline" data-testid="glance-headline">`
    + `${escapeHtml(glance.headline ?? '')}${asOf}</div>`;
  return '<div class="glance-card" data-testid="observed-nutshell">'
    + (glance.flight ? flightStripHtml(glance.flight) : '')
    + (alertLines ? `<div class="glance-lines glance-lines-alert">${alertLines}</div>` : '')
    + (highlight ? highlightHtml(highlight, opts) : headline)
    // Kept under the highlight too: it carries the observation time and age.
    + (highlight ? headline : '')
    + (aheadLines ? `<div class="glance-lines" data-testid="glance-ahead">${aheadLines}</div>` : '')
    + (dest ? destinationHtml(dest) : '')
    + (passedLines ? `<div class="glance-lines glance-lines-passed">${passedLines}</div>` : '')
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
  opts: Omit<NutshellOptions, 'rateable'> = {},
): void {
  el.innerHTML = nutshellHtml(glance, showMapButton, { ...opts, rateable: !!handlers.onRateHighlight });
  const highlight = glance.highlight ?? null;
  const widget = el.querySelector<HTMLElement>('[data-hl-feedback]');
  if (highlight && widget && handlers.onRateHighlight) {
    attachHighlightFeedback(widget, highlight, handlers.onRateHighlight);
  }
  el.querySelectorAll<HTMLElement>('[data-glance-focus]').forEach((node) => {
    node.addEventListener('click', () => {
      const focus = (glance.lines ?? [])[Number(node.dataset.glanceFocus)]?.focus;
      if (focus) handlers.onFocus(focus);
    });
  });
  const dest = glance.destination ?? null;
  el.querySelector<HTMLElement>('[data-dest-focus]')?.addEventListener('click', () => {
    if (dest?.focus) handlers.onFocus(dest.focus);
  });
  el.querySelectorAll<HTMLElement>('[data-dest-cell]').forEach((node) => {
    node.addEventListener('click', () => {
      const focus = (dest?.cells ?? [])[Number(node.dataset.destCell)]?.focus;
      if (focus) handlers.onFocus(focus);
    });
  });
  el.querySelector<HTMLElement>('[data-glance-showmap]')
    ?.addEventListener('click', () => handlers.onShowMap());
}

/** Wire the highlight's 👍/👎: either thumb opens the optional-comment form
 *  (as the digest's does), Send posts it. Mirrors `attachDigestFeedback`
 *  (managers/briefing-ui.ts), scoped to data attributes so the widget can
 *  sit beside the digest's on the same page. */
function attachHighlightFeedback(
  widget: HTMLElement,
  highlight: LiveHighlight,
  rate: NonNullable<NutshellHandlers['onRateHighlight']>,
): void {
  const q = <T extends HTMLElement>(sel: string): T => widget.querySelector<T>(sel)!;
  const upBtn = q<HTMLButtonElement>('[data-hl-thumb="up"]');
  const downBtn = q<HTMLButtonElement>('[data-hl-thumb="down"]');
  const form = q<HTMLElement>('[data-hl-form]');
  const helper = q<HTMLElement>('[data-hl-helper]');
  const comment = q<HTMLTextAreaElement>('[data-hl-comment]');
  const contact = q<HTMLInputElement>('[data-hl-contact]');
  const sendBtn = q<HTMLButtonElement>('[data-hl-send]');
  const cancelBtn = q<HTMLButtonElement>('[data-hl-cancel]');
  const errorEl = q<HTMLElement>('[data-hl-error]');
  let pending: HighlightSentiment = 'down';
  const buttons = [upBtn, downBtn, sendBtn, cancelBtn];

  function openForm(sentiment: HighlightSentiment): void {
    // A comment typed for one thumb must not ride along on the other.
    if (sentiment !== pending || form.style.display === 'none') {
      comment.value = '';
      errorEl.textContent = '';
    }
    pending = sentiment;
    upBtn.classList.toggle('active', sentiment === 'up');
    downBtn.classList.toggle('active', sentiment === 'down');
    helper.textContent = t(sentiment === 'up' ? 'observed.highlight.helperUp' : 'observed.highlight.helperDown');
    comment.placeholder = t(sentiment === 'up' ? 'feedback.thumbs.placeholderUp' : 'feedback.thumbs.placeholder');
    form.style.display = '';
    comment.focus();
  }

  upBtn.addEventListener('click', () => openForm('up'));
  downBtn.addEventListener('click', () => openForm('down'));
  cancelBtn.addEventListener('click', () => {
    form.style.display = 'none';
    upBtn.classList.remove('active');
    downBtn.classList.remove('active');
    errorEl.textContent = '';
  });
  sendBtn.addEventListener('click', () => {
    errorEl.textContent = '';
    buttons.forEach((b) => { b.disabled = true; });
    rate(highlight, pending, comment.value.trim(), contact.checked)
      .then(() => {
        widget.innerHTML = `<span class="digest-feedback-thanks">${escapeHtml(t('feedback.thumbs.thanks'))}</span>`;
      })
      .catch((err: unknown) => {
        errorEl.textContent = t('feedback.failedSubmit', { error: String(err) });
        buttons.forEach((b) => { b.disabled = false; });
      });
  });
}
