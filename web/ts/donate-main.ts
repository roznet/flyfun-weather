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
  fetchDonationPreview,
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
  } catch {
    showError('Could not load donations right now. Please try again later.');
    return;
  }

  // Single on/off switch: Stripe not configured → donations aren't live, so
  // show the "not available" notice and render nothing else. (The Settings link
  // is hidden too; this guards direct navigation.)
  if (!summary.enabled) {
    showDonationsDisabled();
    return;
  }

  renderStats(summary);
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

/** Transparency stats trio + the run-cost note. Hidden until data is present. */
function renderStats(s: DonationSummary): void {
  const stats = s.stats;
  const section = document.getElementById('stats-section')!;
  // Nothing meaningful yet → leave the header hidden.
  if (!stats || (stats.briefings_all_time <= 0 && stats.active_pilots_30d <= 0)) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';
  document.getElementById('stat-pilots')!.textContent = stats.active_pilots_30d.toLocaleString();
  document.getElementById('stat-briefings')!.textContent =
    stats.briefings_all_time.toLocaleString();

  // Words is the headline; the ~N books equivalence is an optional flourish.
  const words = stats.analysis_words_all_time;
  document.getElementById('stat-words')!.textContent = formatWords(words);
  const books = Math.round(stats.analysis_books_equiv);
  const booksNote = books >= 1 ? ` (~${books} ${books === 1 ? 'novel' : 'novels'})` : '';
  document.getElementById('stat-words-label')!.textContent =
    `words of AI weather analysis${booksNote}`;

  // Cost note: qualitative for now — we intentionally don't surface the dollar
  // figure here. A link to a cost-detail breakdown may follow later.
  const note = document.getElementById('run-cost-note')!;
  note.textContent =
    'Running the site means real costs — cloud hardware, weather-data ' +
    'subscriptions and AI models — and they grow with every pilot and ' +
    'briefing. Your donations help offset them.';
}

/** Compact word count: 2.3M / 45k / 900. */
function formatWords(words: number): string {
  if (words >= 1_000_000) return `${(words / 1_000_000).toFixed(1)}M`;
  if (words >= 1_000) return `${Math.round(words / 1_000)}k`;
  return words.toLocaleString();
}

function renderPersonal(me: DonationMe): void {
  if (me.total_usd <= 0) return; // nothing donated yet — keep the panel hidden
  document.getElementById('personal-section')!.style.display = '';
  const headline = document.getElementById('personal-headline')!;
  const sub = document.getElementById('personal-sub')!;
  headline.textContent = formatMoney(me.total_usd, me.fx);
  // Prefer the retrospective personal panel; fall back to program-average impact.
  const phrase = me.personal && !me.personal.empty ? me.personal.summary : me.impact.summary;
  sub.textContent = phrase ? `Your support ${phrase}.` : 'Thank you for your support.';
  renderDonationHistory(me);
}

/** Populate the expandable per-donation list (date + charged amount) and wire
 * its toggle. Amounts show what was actually charged — a truthful record that
 * doesn't shift when the display currency changes. */
function renderDonationHistory(me: DonationMe): void {
  const btn = document.getElementById('btn-details') as HTMLButtonElement | null;
  const panel = document.getElementById('donation-details');
  const list = document.getElementById('donation-list');
  if (!btn || !panel || !list) return;

  const items = me.donations || [];
  if (items.length === 0) {
    btn.style.display = 'none';
    return;
  }
  btn.style.display = '';

  list.innerHTML = items
    .map((d) => {
      const date = new Date(d.date).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      });
      return `<li><span class="d-date">${date}</span><span class="d-amount">${formatCharged(
        d.amount,
        d.currency,
      )}</span></li>`;
    })
    .join('');

  // Attach the toggle once (renderPersonal re-runs on currency change).
  if (btn.dataset.wired !== '1') {
    btn.dataset.wired = '1';
    btn.addEventListener('click', () => {
      const open = btn.getAttribute('aria-expanded') === 'true';
      btn.setAttribute('aria-expanded', String(!open));
      panel.hidden = open;
    });
  }
}

/** Format a charged amount in its own currency (not the display fx currency). */
function formatCharged(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
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
      const s = await fetchDonationSummary(selectedCurrency);
      renderStats(s);
      renderCommunity(s);
      if (isLoggedIn) renderPersonal(await fetchMyDonations(selectedCurrency));
    } catch {
      /* keep the previously rendered totals on a refetch hiccup */
    }
    previewAmount(); // re-translate the entered amount in the new currency
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
      previewAmount();
    });
  });
}

let previewTimer: ReturnType<typeof setTimeout> | undefined;
let previewSeq = 0; // guards against out-of-order responses

/** Translate the entered amount via the backend ladder and show the phrasing.
 * Debounced so typing doesn't spam the endpoint. */
function previewAmount(): void {
  const note = document.getElementById('amount-preview');
  const amountInput = document.getElementById('input-amount') as HTMLInputElement | null;
  if (!note || !amountInput) return;
  const amount = parseFloat(amountInput.value);
  if (!Number.isFinite(amount) || amount < 1) {
    note.textContent = '';
    return;
  }
  clearTimeout(previewTimer);
  const seq = ++previewSeq;
  previewTimer = setTimeout(async () => {
    try {
      const { translation } = await fetchDonationPreview(amount, selectedCurrency);
      if (seq !== previewSeq) return; // a newer request superseded this one
      note.textContent = translation.empty ? '' : translation.summary;
    } catch {
      if (seq === previewSeq) note.textContent = '';
    }
  }, 250);
}

function initForm(): void {
  const btn = document.getElementById('btn-donate') as HTMLButtonElement;
  const amountInput = document.getElementById('input-amount') as HTMLInputElement;
  const recurring = document.getElementById('input-recurring') as HTMLInputElement;
  const useAccountEmail = document.getElementById('input-use-account-email') as HTMLInputElement;

  // The "use my account email" opt-out only makes sense when there's an account
  // email to pre-fill — anonymous donors always type their email at Checkout.
  if (isLoggedIn) {
    document.getElementById('email-opt-row')!.style.display = '';
  }

  // Typing a custom amount clears any active preset highlight + re-translates.
  amountInput.addEventListener('input', () => {
    document
      .getElementById('amount-grid')
      ?.querySelectorAll('.amount-btn')
      .forEach((b) => b.classList.remove('active'));
    previewAmount();
  });

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
        // Only meaningful for logged-in donors; harmless (and ignored) otherwise.
        use_account_email: useAccountEmail.checked,
      });
      // Defense-in-depth: only ever navigate to a Stripe Checkout URL, never an
      // arbitrary (e.g. javascript:) scheme if the response were ever tampered with.
      if (!url.startsWith('https://checkout.stripe.com/')) {
        showError('Unexpected checkout URL — please try again.');
        btn.disabled = false;
        btn.textContent = 'Donate';
        return;
      }
      window.location.href = url; // hand off to Stripe Checkout
    } catch {
      showError('Could not start checkout. Please try again.');
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
