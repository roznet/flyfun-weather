/** Metric info popup — modal overlay showing detailed metric explanations. */

import { renderInfoPopupContent, renderLayerLegend } from '../helpers/metrics-helper';
import { t } from '../i18n/i18n';

let popupEl: HTMLElement | null = null;
let backdropEl: HTMLElement | null = null;

export function initInfoPopup(): void {
  // Create backdrop
  backdropEl = document.createElement('div');
  backdropEl.className = 'metric-popup-backdrop';
  backdropEl.addEventListener('click', hideMetricInfo);

  // Create popup container
  popupEl = document.createElement('div');
  popupEl.className = 'metric-popup';
  popupEl.id = 'metric-info-popup';

  backdropEl.appendChild(popupEl);
  document.body.appendChild(backdropEl);

  // ESC to close
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hideMetricInfo();
  });

  // Stop clicks inside popup from closing it
  popupEl.addEventListener('click', (e) => e.stopPropagation());
}

export function showMetricInfo(metricId: string, value?: string): void {
  if (!popupEl || !backdropEl) return;

  // value may be a numeric string (e.g. an icing index) or a risk label
  // (e.g. "moderate"). Only keep it if it parses to a finite number — a
  // non-numeric label would otherwise surface as "Current: NaN".
  const parsed = value != null && value !== '' ? parseFloat(value) : NaN;
  const numValue = Number.isFinite(parsed) ? parsed : undefined;
  popupEl.innerHTML = `
    <button class="metric-popup-close" aria-label="${t('popup.close')}">\u00d7</button>
    ${renderInfoPopupContent(metricId, numValue)}
  `;

  backdropEl.classList.add('active');
  wirePopupButtons();
}

export function showLayerInfo(layerId: string, metricId: string): void {
  if (!popupEl || !backdropEl) return;

  const legendHtml = renderLayerLegend(layerId);
  popupEl.innerHTML = `
    <button class="metric-popup-close" aria-label="${t('popup.close')}">\u00d7</button>
    ${renderInfoPopupContent(metricId)}
    ${legendHtml}
  `;

  backdropEl.classList.add('active');
  wirePopupButtons();
}

/** Show the popup with arbitrary HTML content. Used by advisory info buttons
 * and the synoptic-tab Hewson info popups. If the supplied HTML includes a
 * .popup-discuss-ai block, the AI-prompt buttons will be wired automatically. */
export function showPopupContent(html: string): void {
  if (!popupEl || !backdropEl) return;

  popupEl.innerHTML = `
    <button class="metric-popup-close" aria-label="${t('popup.close')}">\u00d7</button>
    ${html}
  `;

  backdropEl.classList.add('active');
  wirePopupButtons();
}

export function hideMetricInfo(): void {
  if (backdropEl) {
    backdropEl.classList.remove('active');
  }
}

/** Wire close button and AI discuss buttons after rendering popup content. */
function wirePopupButtons(): void {
  if (!popupEl) return;

  const closeBtn = popupEl.querySelector('.metric-popup-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', hideMetricInfo);
  }

  // Generic in-popup drill-down: a `.popup-drill-metric[data-metric]` button in
  // the current content swaps the popup to that metric's full card, with a Back
  // button that restores what was showing. Listeners are attached directly to the
  // elements (clicks inside the popup are stopped from bubbling to document), so
  // any showPopupContent() consumer gets nested metric details for free.
  const drillBtns = popupEl.querySelectorAll('.popup-drill-metric');
  for (const b of drillBtns) {
    b.addEventListener('click', () => {
      if (!popupEl) return;
      const metricId = (b as HTMLElement).dataset.metric;
      if (!metricId) return;
      const prev = popupEl.innerHTML;
      popupEl.innerHTML = `
        <button class="metric-popup-close" aria-label="${t('popup.close')}">×</button>
        <button class="popup-back" type="button">← ${t('popup.back')}</button>
        ${renderInfoPopupContent(metricId)}
      `;
      wirePopupButtons();
      const back = popupEl.querySelector('.popup-back');
      back?.addEventListener('click', () => {
        if (!popupEl) return;
        popupEl.innerHTML = prev;
        wirePopupButtons();
      });
    });
  }

  const discussSection = popupEl.querySelector('.popup-discuss-ai') as HTMLElement | null;
  if (!discussSection) return;

  const metricName = discussSection.dataset.metricName ?? 'this metric';
  const llmContext = discussSection.dataset.llmPrompt;
  const contextStr = llmContext ? ` In particular, ${llmContext}.` : '';
  const prompt = t('popup.discussAi', { metric: metricName }) + contextStr;

  const aiButtons = discussSection.querySelectorAll('.popup-ai-btn');
  const toast = discussSection.querySelector('.popup-discuss-toast') as HTMLElement | null;

  for (const btn of aiButtons) {
    btn.addEventListener('click', () => {
      navigator.clipboard.writeText(prompt).then(() => {
        if (toast) {
          toast.textContent = 'Prompt copied! Paste it into the chat.';
          toast.hidden = false;
          setTimeout(() => { toast.hidden = true; }, 3000);
        }
      }).catch(() => {
        // Clipboard can reject (permission denied, insecure context).
        // Surface that to the pilot so they don't open the AI chat with
        // an empty buffer.
        if (toast) {
          toast.textContent = 'Copy failed — paste manually from the modal.';
          toast.hidden = false;
          setTimeout(() => { toast.hidden = true; }, 4000);
        }
      });
    });
  }
}
