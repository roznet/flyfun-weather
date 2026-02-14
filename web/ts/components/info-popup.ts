/** Metric info popup — modal overlay showing detailed metric explanations. */

import { renderInfoPopupContent } from '../helpers/metrics-helper';

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

  // Wire close button
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
