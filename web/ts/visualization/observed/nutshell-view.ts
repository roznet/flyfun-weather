/** Observed nutshell (#690) — layer 0 of the Observed section.
 *
 * SYNC — paired with `ObservedNutshellCard` in
 * app/flyfun-weather/flyfun-weather/Views/Briefing/ObservedNutshellView.swift.
 * SYNC — the highlight block is paired with `ObservedHighlightCard` in
 * app/flyfun-weather/flyfun-weather/Views/Briefing/ObservedHighlightView.swift
 * (#697). Documented divergence: iOS folds the headline and the non-alert
 * lines under "Details"; the web leaves them unfolded (the desktop has room).
 *
 * Reading order (#697): the model-written highlight first, with its
 * "experimental · written HH:MMZ" caption and 👍/👎, then the alert-tier
 * lines, then the headline and the other lines. With no highlight (before the
 * first generation, a rejected one, after arrival) the headline takes its
 * slot, with no caption and no thumbs. The highlight is never styled as an
 * alert.
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
import type { LiveFocus, LiveGlance, LiveGlanceLine, LiveHighlight } from '../../store/types';
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
    + '</span>'
    + (tappable ? '<span class="glance-chevron" aria-hidden="true">▸</span>' : '')
    + `</${tappable ? 'button' : 'div'}>`;
}

/** The nutshell card's markup. Pure, so the wording and the alert/passed
 *  states can be asserted without a DOM. */
export function nutshellHtml(
  glance: LiveGlance, showMapButton: boolean, opts: NutshellOptions = {},
): string {
  // Indices stay those of `glance.lines`: the focus taps look them up there.
  const indexed = (glance.lines ?? []).map((line, i) => ({ line, i }));
  const alertLines = indexed.filter(({ line }) => line.alert).map(({ line, i }) => lineHtml(line, i)).join('');
  const otherLines = indexed.filter(({ line }) => !line.alert).map(({ line, i }) => lineHtml(line, i)).join('');
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
    + (highlight ? highlightHtml(highlight, opts) : headline)
    + (alertLines ? `<div class="glance-lines glance-lines-alert">${alertLines}</div>` : '')
    // Kept under the highlight too: it carries the observation time and age.
    + (highlight ? headline : '')
    + (otherLines ? `<div class="glance-lines">${otherLines}</div>` : '')
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
