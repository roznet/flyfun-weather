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
  empty: boolean;
  summary: string;
}

export interface DonationMe {
  total_usd: number;
  impact: DonationImpact;
  fx: FxBlock;
}

export interface DonationSummary {
  year: number;
  total_year_usd: number;
  impact: YearlyImpact;
  fx: FxBlock;
  enabled: boolean; // false when Stripe isn't configured → hide the donate UI
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
