/** Donations adapter — checkout, viewer impact, public community summary.
 *
 * API responses are USD-canonical and carry an `fx` block; use `formatMoney`
 * to render the viewer's currency from a USD amount + the fx block.
 */

import { apiFetch } from '../utils';

/** Display-currency block carried on cost/donation responses. */
export interface FxBlock {
  currency: string; // ISO 4217, uppercase
  rate: number; // units of `currency` per 1 USD
  as_of?: string; // ECB rate date (may lag on weekends/holidays)
}

export interface DonationImpact {
  amount_usd: number;
  user_months: number;
  users_until_eoy: number;
  months_until_eoy: number;
  empty: boolean;
  summary: string; // natural-language phrasing ("" when empty)
}

export interface YearlyImpact {
  total_year_usd: number;
  months_covered: number;
  users_full_year: number;
  coverage_ratio: number;
  months_elapsed: number;
  surplus_months: number;
  empty: boolean;
  summary: string;
}

/** A pilot's lifetime coverage (retrospective, with "+N pilots" / forward overflow). */
export interface PersonalImpact {
  donation_total_usd: number;
  lifetime_cost_usd: number;
  own_months_covered: number;
  coverage_ratio: number | null; // null when no realized cost yet (donation ÷ 0)
  extra_pilots: number;
  future_months: number;
  service_months: number; // surplus as whole-platform run-months
  overflow_capped: boolean; // large overflow → service-months phrasing, not "+N pilots"
  band: 'retrospective' | 'covers_others' | 'future';
  empty: boolean;
  summary: string;
}

export interface DonationMe {
  total_usd: number;
  impact: DonationImpact;
  personal: PersonalImpact;
  fx: FxBlock;
}

/** Transparency stats trio for the donate-page header. */
export interface DonationStats {
  active_pilots_30d: number;
  briefings_all_time: number;
  analysis_words_all_time: number;
  analysis_books_equiv: number;
  words_summary: string;
}

/** Margin-excluded run cost (USD; render via the fx block). */
export interface RunCost {
  monthly_run_cost_usd: number;
  cost_per_user_month_usd: number;
}

export interface DonationSummary {
  year: number;
  total_year_usd: number;
  impact: YearlyImpact;
  stats: DonationStats;
  run_cost: RunCost;
  fx: FxBlock;
  enabled: boolean; // false when Stripe isn't configured → hide the donate UI
}

/** One chosen prospective-donation translation (the adaptive ladder result).
 *
 * `value` is the raw dimensionless quantity, whose meaning depends on `kind`
 * (pilot-months for `user_months`/`personal_months`, a pilot count for
 * `users_for_month`, a briefing count for `briefings`, months of service for
 * `service_months`). Prefer `summary` — it is the canonical human-readable form;
 * only read `value` if you intend to re-render it yourself per `kind`. */
export interface TranslationChoice {
  amount_usd: number;
  kind: string;
  value: number;
  summary: string;
  empty: boolean;
}

export interface DonationPreview {
  amount_usd: number;
  translation: TranslationChoice;
  fx: FxBlock;
}

export interface CheckoutRequest {
  amount: number;
  currency: string;
  recurring?: boolean;
}

/** Create a Stripe Checkout Session and return its hosted redirect URL. */
export async function createCheckout(req: CheckoutRequest): Promise<{ url: string }> {
  return apiFetch<{ url: string }>('/donations/checkout', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

/** The viewer's own donation total + impact (requires auth).
 * `currency` overrides the saved display currency for this response. */
export async function fetchMyDonations(currency?: string): Promise<DonationMe> {
  const q = currency ? `?currency=${encodeURIComponent(currency)}` : '';
  return apiFetch<DonationMe>(`/donations/me${q}`);
}

/** Public this-year community total + coverage framing (no auth required).
 * `currency` selects the display currency (needed for anonymous viewers). */
export async function fetchDonationSummary(currency?: string): Promise<DonationSummary> {
  const q = currency ? `?currency=${encodeURIComponent(currency)}` : '';
  return apiFetch<DonationSummary>(`/donations/summary${q}`);
}

/** Translate a prospective amount via the adaptive ladder.
 * `amount` is in the display `currency`; personal framing applies when the
 * viewer is logged in with usage history (resolved server-side). */
export async function fetchDonationPreview(
  amount: number,
  currency?: string,
): Promise<DonationPreview> {
  const params = new URLSearchParams({ amount: String(amount) });
  if (currency) params.set('currency', currency);
  return apiFetch<DonationPreview>(`/donations/preview?${params.toString()}`);
}

/** Render a USD amount in the viewer's currency using an fx block. */
export function formatMoney(amountUsd: number, fx: FxBlock): string {
  const local = amountUsd * (fx.rate || 1);
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: fx.currency || 'USD',
      maximumFractionDigits: 2,
    }).format(local);
  } catch {
    // Unknown currency code → fall back to a plain number + code.
    return `${local.toFixed(2)} ${fx.currency || 'USD'}`;
  }
}
