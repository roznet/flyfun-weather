/**
 * Shared currency support: the offered set, browser detection, and persistence.
 *
 * One currency drives BOTH how money is displayed and the default donate
 * currency. It's stored in the `display_currency` preference for logged-in users
 * (so the donate page and Settings stay in sync) and mirrored to localStorage
 * for anonymous donors / as a fast path. USD stays API-canonical; the server
 * returns an `fx` block for rendering.
 */

// Clean 2-decimal currencies only (ISK/HUF/TRY intentionally excluded to avoid
// Stripe minor-unit quirks). All are supported by the ECB/Frankfurter feed.
export const SUPPORTED_CURRENCIES = [
  'EUR', 'USD', 'GBP', 'CHF', 'NOK', 'SEK', 'DKK', 'PLN', 'CZK', 'RON',
] as const;
export type CurrencyCode = (typeof SUPPORTED_CURRENCIES)[number];

export const DEFAULT_CURRENCY: CurrencyCode = 'EUR';

const STORAGE_KEY = 'flyfun_currency';

// Region → currency for the non-EUR countries we support. Everything else
// (the whole euro zone, plus anything unknown) falls through to EUR.
const REGION_CURRENCY: Record<string, CurrencyCode> = {
  US: 'USD',
  GB: 'GBP',
  CH: 'CHF', LI: 'CHF',
  NO: 'NOK', SE: 'SEK', DK: 'DKK', PL: 'PLN', CZ: 'CZK', RO: 'RON',
};

export function isSupported(code: string | null | undefined): boolean {
  return !!code && (SUPPORTED_CURRENCIES as readonly string[]).includes(code.toUpperCase());
}

/** Best-effort currency from the browser locale; EUR when unknown. */
export function detectCurrency(): CurrencyCode {
  try {
    const region = new Intl.Locale(navigator.language).maximize().region;
    if (region && REGION_CURRENCY[region]) return REGION_CURRENCY[region];
  } catch {
    /* Intl.Locale unsupported or no navigator — fall through to default */
  }
  return DEFAULT_CURRENCY;
}

export function getStoredCurrency(): CurrencyCode | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return isSupported(v) ? (v!.toUpperCase() as CurrencyCode) : null;
  } catch {
    return null;
  }
}

export function setStoredCurrency(code: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, code.toUpperCase());
  } catch {
    /* private mode / storage disabled — ignore */
  }
}

/**
 * Resolve the initial currency: an explicit saved preference wins, else the
 * locally-stored choice, else browser detection, else EUR. `pref` is the raw
 * `display_currency` value ("auto"/unset means no explicit choice).
 */
export function resolveInitialCurrency(pref?: string | null): CurrencyCode {
  if (isSupported(pref)) return pref!.toUpperCase() as CurrencyCode;
  return getStoredCurrency() ?? detectCurrency();
}

/** A currency symbol for `code` via Intl, falling back to the code itself. */
export function currencySymbol(code: string): string {
  try {
    const parts = new Intl.NumberFormat(undefined, { style: 'currency', currency: code })
      .formatToParts(0);
    return parts.find((p) => p.type === 'currency')?.value || code;
  } catch {
    return code;
  }
}
