/**
 * Flexibility / timing-scenario explainer — single source of truth for the
 * copy shown in two places (issue #352):
 *
 *  1. A first-time modal, shown the first time a user picks a scan mode on the
 *     flight editor, gated so it stops once they have genuinely run a scan.
 *  2. An always-reachable (i) popup on the briefing's Timing Scenarios header.
 *
 * Both render the *same* HTML via ``showPopupContent`` so the wording never
 * drifts. Acknowledge-only: a single "Got it" button that closes the modal and
 * sets the session flag; the user's dropdown selection stands (no revert).
 */

import { fetchUsageSummary } from '../adapters/preferences-adapter';
import { hideMetricInfo, showPopupContent } from './info-popup';

/** Session-scoped acknowledge flag — suppresses re-show for the rest of the
 *  sitting. Deliberately ``sessionStorage`` (not ``localStorage``, not an
 *  in-memory flag): survives cross-page navigation within a sitting but not a
 *  browser restart, so the durable count gate stays the thing that decides
 *  "shown enough". */
const ACK_KEY = 'wb_flex_explainer_ack';

/** Shared copy for both the first-time modal and the (i) popup. Keep the four
 *  beats: what it does / two-step / background / use-deliberately. */
export const flexibilityExplainerHtml = `
  <div class="flex-explainer popup-section">
    <h3>&#128336; About Flexibility scanning</h3>
    <p>
      Flexibility looks for a <strong>better departure window</strong> across the
      period you pick — it grades the weather at other times and surfaces any
      that come out calmer than your planned time.
    </p>
    <p>Because checking many times across many models is data-heavy, it runs in
      <strong>two steps</strong>:</p>
    <ol>
      <li><strong>First pass — ECMWF only.</strong> A fast scan on the
        locally-held ECMWF model finds candidate windows.</li>
      <li><strong>Confirm — all models.</strong> Tap a promising candidate to
        verify it against the other models before relying on it.</li>
    </ol>
    <p>
      It runs <strong>in the background</strong> — you don't have to wait. Leave
      the briefing and come back; the timing scenarios fill in automatically
      when the scan finishes.
    </p>
    <p>
      &#9888;&#65039; <strong>It's computationally and data-intensive.</strong>
      Please use Flexibility only when you're genuinely weighing a different
      time, not out of curiosity — a local yes/no flight doesn't need it.
    </p>
    <div class="flex-explainer-actions">
      <button type="button" class="btn btn-primary flex-explainer-gotit">Got it</button>
    </div>
  </div>
`;

/** True once the user has acknowledged the explainer this session. */
export function flexExplainerAcked(): boolean {
  try {
    return sessionStorage.getItem(ACK_KEY) === '1';
  } catch {
    return false;
  }
}

/** Mark the explainer acknowledged for the rest of the session. */
export function ackFlexExplainer(): void {
  try {
    sessionStorage.setItem(ACK_KEY, '1');
  } catch {
    /* sessionStorage may be blocked (private mode) — non-fatal */
  }
}

/**
 * Pure gate predicate (unit-tested). Show the first-time explainer only when
 * the user has never run a scan (durable, per-account) AND hasn't already
 * acknowledged it this session (ephemeral guard against re-fire while the
 * count is still 0 between "picks a mode" and "scan ran").
 */
export function shouldShowFlexibilityExplainer(opts: {
  timeScanUsed: boolean;
  sessionAcked: boolean;
}): boolean {
  return !opts.timeScanUsed && !opts.sessionAcked;
}

/** Open the explainer popup and wire "Got it" to acknowledge + close. Used
 *  directly by the always-reachable (i) button. */
export function openFlexibilityExplainer(): void {
  showPopupContent(flexibilityExplainerHtml);
  const gotIt = document.querySelector('.flex-explainer-gotit') as HTMLElement | null;
  gotIt?.addEventListener('click', () => {
    ackFlexExplainer();
    hideMetricInfo();
  });
}

/**
 * First-time trigger: evaluate the gate and, if it passes, open the explainer.
 * Called on a non-``none`` flexibility selection. Session-acked short-circuits
 * before the network call; the durable "used" flag comes from ``GET /usage``.
 * On fetch failure we fall back to "not used" so we still gently inform.
 *
 * The gate is resolved exactly once per session: right after evaluating it we
 * set the session-ack flag unconditionally, so subsequent dropdown toggles
 * short-circuit at ``flexExplainerAcked()`` above. This gives two properties
 * the "set only on Got it" approach lacked:
 *   - closing the modal via ×/Esc/backdrop (not just "Got it") still suppresses
 *     re-firing for the rest of the sitting;
 *   - an established user (``time_scan_used`` true) never re-hits ``/usage`` on
 *     every toggle — the gate becomes O(1) per session, not O(toggles).
 * Setting it is safe in every post-fetch branch: we either just showed the
 * modal (don't re-show this sitting) or the user has already used the feature
 * (durable, won't change this session). A new session still re-evaluates,
 * driven by the durable count.
 */
export async function maybeShowFlexibilityExplainer(): Promise<void> {
  if (flexExplainerAcked()) return;
  let timeScanUsed = false;
  try {
    timeScanUsed = (await fetchUsageSummary()).time_scan_used;
  } catch {
    timeScanUsed = false;
  }
  const show = shouldShowFlexibilityExplainer({
    timeScanUsed,
    sessionAcked: flexExplainerAcked(),
  });
  ackFlexExplainer();
  if (show) openFlexibilityExplainer();
}
