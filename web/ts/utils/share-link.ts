/** Share the current page URL.
 *
 * Tier 1: `navigator.share()` — opens the OS share sheet on iOS,
 * macOS Safari, Android Chrome, and recent Edge/Chrome on Windows.
 * Lets the user pick iMessage, Mail, AirDrop, etc. directly.
 *
 * Tier 2: `navigator.clipboard.writeText()` — desktop browsers
 * without share-sheet support. Returns `true` so the caller can
 * flash the trigger button.
 *
 * Tier 3: `prompt()` — last-resort for non-HTTPS origins where the
 * clipboard API is unavailable. Returns `false` so callers skip the
 * flash (the user already saw the URL inline).
 *
 * Mirrors `copyFlightShareLink` in utils.ts but for arbitrary URLs
 * driven by `UrlState` rather than a flight share code. */

import { t } from '../i18n/i18n';

export async function shareCurrentUrl(title?: string): Promise<boolean> {
  const url = window.location.href;
  const shareTitle = title ?? document.title;

  if (typeof navigator.share === 'function') {
    try {
      await navigator.share({ url, title: shareTitle });
      return true;
    } catch (err) {
      // User dismissed the sheet — treat as success (no toast).
      if (err instanceof DOMException && err.name === 'AbortError') return true;
      // Other errors (NotAllowedError on http://, unsupported payload)
      // drop through to the clipboard tier.
    }
  }

  try {
    await navigator.clipboard.writeText(url);
    return true;
  } catch {
    prompt(t('maps.shareLinkFallback'), url);
    return false;
  }
}
