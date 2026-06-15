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
} from './adapters/donations-adapter';
import { fetchPreferences, savePreferences } from './adapters/preferences-adapter';
import {
  SUPPORTED_CURRENCIES,
  currencySymbol,
  resolveInitialCurrency,
  setStoredCurrency,
} from './currency';
import { renderUserInfo } from './utils';
import { initTheme } from './theme';
import { initI18n } from './i18n/i18n';

const PRESETS = [10, 25, 50, 100];

let selectedCurrency = 'EUR';
let isLoggedIn = false;

async function init(): Promise<void> {
  await initI18n();
  initTheme();

  const user = await fetchCurrentUser(); // null when logged out — donations allow anon
  isLoggedIn = !!user;
  if (user) renderUserInfo(user, 'donate');

  document.getElementById('loading')!.style.display = 'none';
  document.getElementById('page-content')!.style.display = '';

  // Resolve the one currency that drives both display and the donate amount:
  // an explicit saved preference wins, else the local choice, else browser
  // detection, else EUR. Then persist it so display + pay stay in sync.
  let savedPref: string | null = null;
  if (user) {
    try {
      savedPref = (await fetchPreferences()).display_currency;
    } catch {
      /* fall back to local detection below */
    }
  }
  selectedCurrency = resolveInitialCurrency(savedPref);
  persistCurrency(selectedCurrency, savedPref);

  let summary: DonationSummary;
  try {
    summary = await fetchDonationSummary(selectedCurrency);
  } catch (err) {
    showError(`Could not load donation summary: ${err}`);
    return;
  }

  // Single on/off switch: Stripe not configured → donations aren't live, so
  // show the "not available" notice and render nothing else. (The Settings link
  // is hidden too; this guards direct navigation.)
  if (!summary.enabled) {
    showDonationsDisabled();
    return;
  }

  renderCommunity(summary);
  initCurrency();

  if (user) {
    try {
      renderPersonal(await fetchMyDonations(selectedCurrency));
    } catch {
      // Non-fatal: the donate form still works without the personal panel.
    }
  }

  initForm();
}

function showDonationsDisabled(): void {
  document.querySelector<HTMLElement>('.intro')?.style.setProperty('display', 'none');
  document.querySelectorAll<HTMLElement>('#page-content > .section').forEach((el) => {
    el.style.display = el.id === 'donations-disabled' ? '' : 'none';
  });
}

/** Persist the chosen currency so display + pay stay one synced setting:
 * the display_currency preference for logged-in users (also read by Settings),
 * and localStorage for everyone (anon fast path). Skips the server write when
 * the preference already matches. */
function persistCurrency(code: string, savedPref: string | null): void {
  setStoredCurrency(code);
  if (isLoggedIn && (savedPref || '').toUpperCase() !== code) {
    savePreferences({ display_currency: code }).catch(() => {
      /* non-fatal: localStorage still carries the choice this session */
    });
  }
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

function initCurrency(): void {
  const select = document.getElementById('input-currency') as HTMLSelectElement;
  // Single source of truth for the offered set.
  select.innerHTML = SUPPORTED_CURRENCIES.map(
    (c) => `<option value="${c}">${c} ${currencySymbol(c)}</option>`,
  ).join('');
  select.value = selectedCurrency;

  // Changing the currency is the one setting: it re-displays every total in the
  // new currency AND becomes the donate currency, and it persists.
  select.addEventListener('change', async () => {
    selectedCurrency = select.value;
    persistCurrency(selectedCurrency, null); // explicit change → always persist
    renderPresets();
    try {
      renderCommunity(await fetchDonationSummary(selectedCurrency));
      if (isLoggedIn) renderPersonal(await fetchMyDonations(selectedCurrency));
    } catch {
      /* keep the previously rendered totals on a refetch hiccup */
    }
  });
  renderPresets();
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
