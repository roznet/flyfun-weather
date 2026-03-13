/**
 * Lightweight i18n module — key-value translation with parameter interpolation.
 *
 * Usage:
 *   import { t, setLocale, getLocale } from './i18n/i18n';
 *   t('nav.flights')                      // "Flights"
 *   t('flights.past', { count: 3 })       // "Past flights (3)"
 *   t('freshness.elapsed', { m: 2, s: 15 }) // "Refreshed in 2m 15s"
 *
 * Locale is persisted in localStorage and sent to the API via Accept-Language.
 */

const STORAGE_KEY = 'wb_locale';
const SUPPORTED_LOCALES = ['en', 'fr', 'de', 'es'] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

let currentLocale: Locale = 'en';
let messages: Record<string, string> = {};
let fallbackMessages: Record<string, string> = {};

/** Translate a key, with optional parameter interpolation.
 *  Parameters are replaced using {name} placeholders. */
export function t(key: string, params?: Record<string, string | number>): string {
  let msg = messages[key] ?? fallbackMessages[key] ?? key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      msg = msg.split(`{${k}}`).join(String(v));
    }
  }
  return msg;
}

/** Get the current locale. */
export function getLocale(): Locale {
  return currentLocale;
}

/** Get all supported locale codes. */
export function getSupportedLocales(): readonly Locale[] {
  return SUPPORTED_LOCALES;
}

/** Check if a string is a supported locale. */
export function isSupportedLocale(s: string): s is Locale {
  return (SUPPORTED_LOCALES as readonly string[]).includes(s);
}

/** Set the locale, load messages, and update the page.
 *  Returns a promise that resolves when messages are loaded. */
export async function setLocale(locale: Locale): Promise<void> {
  if (!isSupportedLocale(locale)) return;
  currentLocale = locale;
  localStorage.setItem(STORAGE_KEY, locale);
  document.documentElement.lang = locale;
  await loadMessages(locale);
}

/** Initialize i18n — detect locale and load messages.
 *  Call once from each entry point before rendering. */
export async function initI18n(): Promise<void> {
  const locale = detectLocale();
  currentLocale = locale;
  document.documentElement.lang = locale;
  await loadMessages(locale);
}

/** Detect locale from: localStorage > user preference > browser > default. */
function detectLocale(): Locale {
  // 1. localStorage (explicit user choice)
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored && isSupportedLocale(stored)) return stored;

  // 2. Browser language
  for (const lang of navigator.languages ?? [navigator.language]) {
    const code = lang.split('-')[0].toLowerCase();
    if (isSupportedLocale(code)) return code;
  }

  return 'en';
}

/** Load translation messages for a locale. English is always loaded as fallback. */
async function loadMessages(locale: Locale): Promise<void> {
  try {
    // Always load English as fallback
    if (Object.keys(fallbackMessages).length === 0) {
      const enModule = await import('./locales/en.json');
      fallbackMessages = enModule.default ?? enModule;
    }

    if (locale === 'en') {
      messages = fallbackMessages;
    } else {
      const module = await import(`./locales/${locale}.json`);
      messages = module.default ?? module;
    }
  } catch (err) {
    console.warn(`Failed to load locale "${locale}", falling back to English`, err);
    messages = fallbackMessages;
  }
}

/** Get a locale string suitable for Intl.DateTimeFormat / toLocaleDateString.
 *  Maps our short locale codes to region-specific variants for better formatting. */
export function getDateLocale(): string {
  const map: Record<string, string> = {
    en: 'en-GB',  // day-first format
    fr: 'fr-FR',
    de: 'de-DE',
    es: 'es-ES',
  };
  return map[currentLocale] ?? currentLocale;
}

/** Get the Accept-Language header value for API requests. */
export function getAcceptLanguage(): string {
  if (currentLocale === 'en') return 'en';
  return `${currentLocale}, en;q=0.5`;
}
