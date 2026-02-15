/** Metric info popup — modal overlay showing detailed metric explanations. */

import { renderInfoPopupContent, renderLayerLegend } from '../helpers/metrics-helper';

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

  const numValue = value != null && value !== '' ? parseFloat(value) : undefined;
  popupEl.innerHTML = `
    <button class="metric-popup-close" aria-label="Close">\u00d7</button>
    ${renderInfoPopupContent(metricId, numValue)}
  `;

  backdropEl.classList.add('active');
  wirePopupButtons();
}

export function showLayerInfo(layerId: string, metricId: string): void {
  if (!popupEl || !backdropEl) return;

  const legendHtml = renderLayerLegend(layerId);
  popupEl.innerHTML = `
    <button class="metric-popup-close" aria-label="Close">\u00d7</button>
    ${renderInfoPopupContent(metricId)}
    ${legendHtml}
  `;

  backdropEl.classList.add('active');
  wirePopupButtons();
}

/** Show the popup with arbitrary HTML content. Used by advisory info buttons. */
export function showPopupContent(html: string): void {
  if (!popupEl || !backdropEl) return;

  popupEl.innerHTML = `
    <button class="metric-popup-close" aria-label="Close">\u00d7</button>
    ${html}
  `;

  backdropEl.classList.add('active');

  const closeBtn = popupEl.querySelector('.metric-popup-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', hideMetricInfo);
  }
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

  const discussSection = popupEl.querySelector('.popup-discuss-ai') as HTMLElement | null;
  if (!discussSection) return;

  const metricName = discussSection.dataset.metricName ?? 'this metric';
  const llmContext = discussSection.dataset.llmPrompt;
  const contextStr = llmContext ? ` In particular, ${llmContext}.` : '';
  const prompt = `Tell me more about ${metricName} in the context of aviation weather.${contextStr} `
    + `How should a VFR or IFR pilot interpret it, what are the key thresholds, `
    + `and how does it interact with other weather parameters for flight safety?`;

  const aiButtons = discussSection.querySelectorAll('.popup-ai-btn');
  const toast = discussSection.querySelector('.popup-discuss-toast') as HTMLElement | null;

  for (const btn of aiButtons) {
    btn.addEventListener('click', () => {
      navigator.clipboard.writeText(prompt).then(() => {
        if (toast) {
          toast.hidden = false;
          setTimeout(() => { toast.hidden = true; }, 3000);
        }
      });
    });
  }
}
