import { t } from '../i18n/i18n';
import { escapeHtml } from '../utils';
import { startBriefingTour } from './briefing-tour';
import { hasBeenOffered, markOffered } from './tour-storage';

export interface TourOfferGate {
  /** Pack currently loaded — tour targets exist in the DOM. */
  currentPack: unknown;
  /** Refresh stream still in flight — tour targets may be empty/hidden. */
  refreshing: boolean;
  /** Digest stage still pending — synopsis not yet rendered. */
  digestPending: boolean;
}

export function renderTourOfferBanner(host: HTMLElement): void {
  host.innerHTML = `
    <div class="tour-offer-content">
      <span class="tour-offer-icon" aria-hidden="true">✨</span>
      <span class="tour-offer-text">${escapeHtml(t('tour.offer.text'))}</span>
      <div class="tour-offer-actions">
        <button type="button" class="btn btn-primary btn-sm" data-action="accept">
          ${escapeHtml(t('tour.offer.acceptBtn'))}
        </button>
        <button type="button" class="btn btn-outline btn-sm" data-action="dismiss">
          ${escapeHtml(t('tour.offer.dismissBtn'))}
        </button>
      </div>
    </div>
  `;
  host.style.display = '';

  host.querySelector<HTMLButtonElement>('[data-action="accept"]')?.addEventListener('click', () => {
    markOffered();
    host.style.display = 'none';
    startBriefingTour();
  });
  host.querySelector<HTMLButtonElement>('[data-action="dismiss"]')?.addEventListener('click', () => {
    markOffered();
    host.style.display = 'none';
  });
}

export function maybeOfferTour(gate?: TourOfferGate): void {
  if (hasBeenOffered()) return;
  const params = new URLSearchParams(window.location.search);
  if (params.get('tour') === '1') return;
  // Wait until the briefing stream is fully complete so every tour target
  // (advisories, viz section, layer toggles, skew-t…) is rendered.
  if (gate && (!gate.currentPack || gate.refreshing || gate.digestPending)) return;
  const host = document.getElementById('tour-offer-banner');
  if (!host) return;
  // Idempotent: re-rendering the banner while it is already visible is fine,
  // but avoid replacing it if it has already been built once.
  if (host.dataset.rendered === '1') return;
  host.dataset.rendered = '1';
  renderTourOfferBanner(host);
}
