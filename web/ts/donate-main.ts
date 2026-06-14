/**
 * Donate page — voluntary, impact-framed donations (web-only by design; the
 * iOS binary must not surface this page, per the App Store IAP rule).
 *
 * Shows the community's yearly coverage and the viewer's own running impact,
 * then hands off to Stripe Checkout. Amounts are entered in the viewer's
 * currency; the API records USD-canonical and returns an fx block for display.
 */

import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  createCheckout,
  fetchDonationSummary,
  fetchMyDonations,
  formatMoney,
  type DonationMe,
  type DonationSummary,
  type FxBlock,
} from './adapters/donations-adapter';
import { renderUserInfo } from './utils';
import { initTheme } from './theme';
import { initI18n } from './i18n/i18n';

const PRESETS = [10, 25, 50, 100];
const CURRENCIES = ['EUR', 'USD', 'GBP', 'CHF'];

let selectedCurrency = 'EUR';

async function init(): Promise<void> {
  await initI18n();
  initTheme();

  const user = await fetchCurrentUser(); // null when logged out — donations allow anon
  if (user) renderUserInfo(user, 'donate');

  document.getElementById('loading')!.style.display = 'none';
  document.getElementById('page-content')!.style.display = '';

  try {
    const summary = await fetchDonationSummary();
    renderCommunity(summary);
    initCurrency(summary.fx);
  } catch (err) {
    showError(`Could not load donation summary: ${err}`);
  }

  if (user) {
    try {
      renderPersonal(await fetchMyDonations());
    } catch {
      // Non-fatal: the donate form still works without the personal panel.
    }
  }

  initForm();
}

function showError(msg: string): void {
  const el = document.getElementById('error-message')!;
  el.textContent = msg;
  el.style.display = 'block';
}

function renderCommunity(s: DonationSummary): void {
  const headline = document.getElementById('community-headline')!;
  const sub = document.getElementById('community-sub')!;
  const total = formatMoney(s.total_year_usd, s.fx);
  if (s.impact.empty || s.total_year_usd <= 0) {
    headline.textContent = 'Be the first to chip in this year';
    sub.textContent = 'Your donation directly offsets what it costs to run the service.';
    return;
  }
  // Capitalize the phrasing for a headline.
  const phrase = s.impact.summary || 'donations are helping cover the running costs';
  headline.textContent = phrase.charAt(0).toUpperCase() + phrase.slice(1);
  sub.textContent = `${total} donated in ${s.year}.`;
}

function renderPersonal(me: DonationMe): void {
  if (me.total_usd <= 0) return; // nothing donated yet — keep the panel hidden
  document.getElementById('personal-section')!.style.display = '';
  const headline = document.getElementById('personal-headline')!;
  const sub = document.getElementById('personal-sub')!;
  headline.textContent = formatMoney(me.total_usd, me.fx);
  sub.textContent = me.impact.summary
    ? `Your support ${me.impact.summary}.`
    : 'Thank you for your support.';
}

function initCurrency(fx: FxBlock): void {
  const select = document.getElementById('input-currency') as HTMLSelectElement;
  // Default to the viewer's display currency when it's one we offer.
  const preferred = (fx.currency || 'EUR').toUpperCase();
  selectedCurrency = CURRENCIES.includes(preferred) ? preferred : 'EUR';
  select.value = selectedCurrency;
  select.addEventListener('change', () => {
    selectedCurrency = select.value;
    renderPresets();
  });
  renderPresets();
}

function currencySymbol(code: string): string {
  try {
    const parts = new Intl.NumberFormat(undefined, { style: 'currency', currency: code })
      .formatToParts(0);
    return parts.find((p) => p.type === 'currency')?.value || code;
  } catch {
    return code;
  }
}

function renderPresets(): void {
  const grid = document.getElementById('amount-grid')!;
  const amountInput = document.getElementById('input-amount') as HTMLInputElement;
  const sym = currencySymbol(selectedCurrency);
  grid.innerHTML = PRESETS.map(
    (v) => `<button type="button" class="amount-btn" data-amount="${v}">${sym}${v}</button>`,
  ).join('');
  grid.querySelectorAll<HTMLButtonElement>('.amount-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      amountInput.value = btn.dataset.amount || '';
      grid.querySelectorAll('.amount-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });
}

function initForm(): void {
  const btn = document.getElementById('btn-donate') as HTMLButtonElement;
  const amountInput = document.getElementById('input-amount') as HTMLInputElement;
  const recurring = document.getElementById('input-recurring') as HTMLInputElement;

  btn.addEventListener('click', async () => {
    const amount = parseFloat(amountInput.value);
    if (!Number.isFinite(amount) || amount < 1) {
      showError('Please enter an amount of at least 1.');
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Redirecting…';
    try {
      const { url } = await createCheckout({
        amount,
        currency: selectedCurrency,
        recurring: recurring.checked,
      });
      window.location.href = url; // hand off to Stripe Checkout
    } catch (err) {
      showError(`Could not start checkout: ${err}`);
      btn.disabled = false;
      btn.textContent = 'Donate';
    }
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
