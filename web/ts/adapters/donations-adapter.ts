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

/** One past donation for the contribution-history details list.
 * `amount`/`currency` are what the donor was charged (the truthful record);
 * `amount_usd` is the canonical converted value; `date` is ISO-8601. */
export interface DonationHistoryItem {
  date: string;
  amount: number;
  currency: string;
  amount_usd: number;
}

/** What the viewer's own briefings really cost the operator.
 *
 * `true_cost_usd` is recomputed on the program's real amortization basis;
 * `ledger_cost_usd` is what `cost_ledger` charged and is deliberately higher
 * (the rate card's volume estimate sat ~3x below actual until 2026-08-31).
 * Present for every pilot with briefings, donor or not — it is what the page
 * shows someone who has never given. */
export interface UsageFootprint {
  briefings: number;
  variable_usd: number;
  fixed_share_usd: number;
  true_cost_usd: number;
  ledger_cost_usd: number;
  unknown_variable_rows: number;
  empty: boolean;
  first_briefing_at: string | null;
  translation: TranslationChoice;
}

export interface DonationMe {
  total_usd: number;
  impact: DonationImpact;
  personal: PersonalImpact;
  usage: UsageFootprint;
  donations: DonationHistoryItem[];
  fx: FxBlock;
}

/** Transparency stats trio for the donate-page header. */
export interface DonationStats {
  active_pilots_30d: number;
  briefings_all_time: number;
  briefings_last_30d: number;
  // Trailing 365 days — what the annual campaign copy quotes.
  active_pilots_last_year: number;
  briefings_last_year: number;
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
  // When false, a logged-in donor's account email is NOT pre-filled into Stripe
  // Checkout, leaving the email field blank and editable. Defaults to true
  // server-side; only send false to opt out. No effect for anonymous donors.
  use_account_email?: boolean;
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


// ---------------------------------------------------------------------------
// Donate nudge (web-only)
// ---------------------------------------------------------------------------
//
// This lives on its own endpoint on purpose. Apple requires donations from
// non-registered-nonprofits to go through IAP, so a donate affordance in the
// app binary is a review rejection — and a flag riding on /api/flights or pack
// meta would eventually reach iOS. A separate web-only call makes that
// structurally impossible rather than merely discouraged.

/** Runtime substitutions the popover copy needs. Deliberately no money figures:
 * community activity stats give scale, a cost or donation amount sets an anchor
 * and invites the reader to price their own share. */
export interface NudgeSummary {
  briefing_count: number;
  first_briefing_at: string | null;
  pilots_last_year: number;
  briefings_last_year: number;
}

export interface NudgeResponse {
  show: boolean;
  kind: '' | 'evergreen' | 'campaign';
  rung: number;
  /** Stable identifier for why nothing is shown — for debugging, never rendered. */
  reason: string;
  summary: NudgeSummary;
}

export type NudgeAck = 'shown' | 'clicked' | 'dismissed';

/** Should we offer this pilot a donate chip right now? Requires auth. */
export async function fetchDonateNudge(): Promise<NudgeResponse> {
  return apiFetch<NudgeResponse>('/donations/nudge');
}

/** Record an impression or an answer against the open ask.
 *
 * `shown` must only be sent when the chip actually painted — the client also
 * suppresses beside a RED assessment, and acking a suppressed view would burn
 * an impression on something nobody saw.
 */
export async function ackDonateNudge(action: NudgeAck): Promise<void> {
  await apiFetch('/donations/nudge/ack', {
    method: 'POST',
    body: JSON.stringify({ action }),
  });
}
