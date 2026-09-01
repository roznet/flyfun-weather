/**
 * Donate nudge — the `♥ Donate` chip and its popover in the briefing toolbar.
 *
 * Web-only by design (App Store IAP rule): this module is imported by
 * `briefing-main.ts` and nothing else, and it talks to its own endpoint rather
 * than reading a flag off a payload iOS also consumes.
 *
 * The server decides *whether an ask exists* and *whether it may render today*.
 * Two things are decided here, because only the client knows them:
 *
 * 1. **Never beside a RED assessment.** Asking for money next to a
 *    serious-hazard grade competes with the safety content. The server does not
 *    know the assessment, so the suppression is ours.
 * 2. **No `shown` ack when we suppress.** An impression must cost the pilot's
 *    budget only when they actually saw something. Same rule for any other
 *    client-side reason the chip does not paint.
 *
 * See `designs/plans/donate-nudge.md` for the lifecycle this hooks into.
 */

import {
  ackDonateNudge,
  fetchDonateNudge,
  type NudgeResponse,
} from '../adapters/donations-adapter';
import { track } from '../analytics/track';
import { EVENTS } from '../analytics/events';
import { t } from '../i18n/i18n';
import { boldify, formatMonthName } from '../helpers/copy-format';
import { escapeHtml } from '../utils';

const SLOT_ID = 'donate-nudge-slot';

/** Assessment grades the chip refuses to appear beside. */
const SUPPRESSED_ASSESSMENTS = new Set(['RED']);

// Four separate pieces of state, because they answer four different questions
// and conflating them is what lets a chip outlive its assessment:
//   `asked`     — have we opened an ask on the server? At most once per load.
//   `painted`   — the chip in the DOM, whose visibility must be kept current.
//   `impressed` — has this ask's one impression been acked? Only on first
//                 *visible* paint, so a chip that painted hidden and is
//                 revealed later still counts exactly one.
//   `answered`  — has the pilot answered? Then nothing paints again this load.
let asked = false;
let painted: { slot: HTMLElement; nudge: NudgeResponse } | null = null;
let impressed = false;
let answered = false;
/** The assessment of the most recent render, so a response that lands after a
 *  pack switch is shown or hidden against the pack actually on screen. */
let lastAssessment: string | null | undefined;

/**
 * Ask the server whether to offer a donate chip, paint it if so — and keep its
 * visibility honest as the assessment changes.
 *
 * **Call this on every render that can change the assessment**, not just once
 * on load. The pilot can swap packs from the history dropdown and a refresh can
 * land a new one, both in place; a chip painted beside a GREEN pack and never
 * re-checked would still be sitting there when a RED pack replaced it, which is
 * exactly the one thing this feature must never do. A first render on RED
 * likewise must not cost the pilot the chip for the rest of the session.
 *
 * Re-entrant by design: only the *first* non-suppressed call opens an ask and
 * counts an impression. Later calls just re-apply suppression, so no re-render
 * re-fetches or double-counts.
 *
 * Every failure path is silent — a donation ask is the least important thing on
 * this page, and it must never surface an error or delay anything else.
 */
export async function initDonateNudge(assessment: string | null | undefined): Promise<void> {
  const slot = document.getElementById(SLOT_ID);
  if (!slot || answered) return;

  lastAssessment = assessment;

  // A chip is already up: the assessment is the only thing that can have
  // changed, so re-apply suppression and stop.
  if (painted) {
    applyVisibility();
    return;
  }

  // Don't open an ask on the server for a view that cannot render it. `asked`
  // stays false, so a later non-RED render still gets its chance.
  if (shouldSuppressNudge(assessment) || asked) return;
  asked = true;

  let nudge: NudgeResponse;
  try {
    nudge = await fetchDonateNudge();
  } catch {
    return; // not logged in, offline, donations off — all mean "no chip"
  }
  if (!nudge.show) return;

  // The pilot can have switched to a RED pack while the request was in flight.
  // The ask is open server-side either way, so paint the chip — but paint it
  // against `lastAssessment`, not the assessment this call started with, so it
  // never flashes onto a briefing that has since gone RED.
  render(slot, nudge);
}

/**
 * Show or hide the painted chip for the current assessment, and count the
 * impression the first time it is actually visible.
 *
 * The ack lives here rather than at paint time so the two stay in step: a chip
 * that painted hidden (a RED pack landed mid-flight) must not burn an
 * impression, and must still count one if the pilot switches away and sees it.
 */
function applyVisibility(): void {
  if (!painted) return;
  const { slot, nudge } = painted;
  const suppressed = shouldSuppressNudge(lastAssessment);
  slot.style.display = suppressed ? 'none' : '';
  if (suppressed || impressed) return;
  impressed = true;
  void ackDonateNudge('shown').catch(() => {});
  track(EVENTS.DONATE_NUDGE_SHOWN, { kind: nudge.kind, rung: nudge.rung });
}

/** Whether the chip refuses to appear beside this assessment.
 *
 * Exported so the rule can be pinned directly: asking for money next to a
 * serious-hazard grade is the one thing this feature must never do, and it is
 * decided here because the server never sees the assessment.
 */
export function shouldSuppressNudge(assessment: string | null | undefined): boolean {
  return SUPPRESSED_ASSESSMENTS.has((assessment || '').toUpperCase());
}

function render(slot: HTMLElement, nudge: NudgeResponse): void {
  slot.innerHTML = chipHtml(nudge);
  painted = { slot, nudge };

  const chip = slot.querySelector<HTMLButtonElement>('.donate-nudge-chip')!;
  const popover = slot.querySelector<HTMLElement>('.donate-nudge-popover')!;
  const later = slot.querySelector<HTMLButtonElement>('.donate-nudge-later')!;
  const contribute = slot.querySelector<HTMLAnchorElement>('.donate-nudge-contribute')!;

  // Visible only if the pack on screen right now allows it; the impression is
  // acked there, not here.
  applyVisibility();

  const setOpen = (open: boolean): void => {
    popover.hidden = !open;
    chip.setAttribute('aria-expanded', String(open));
  };

  chip.addEventListener('click', () => setOpen(popover.hidden));

  // Esc and click-outside close the popover but answer nothing — they must not
  // consume the ask. The impression already counted, which is what limits it.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') setOpen(false);
  });
  document.addEventListener('click', (e) => {
    if (!popover.hidden && !slot.contains(e.target as Node)) setOpen(false);
  });

  // One answer per ask. Guards a double click on Contribute (whose navigation
  // leaves the chip on screen for a moment) as well as any later re-render.
  const answer = (action: 'clicked' | 'dismissed'): void => {
    if (answered) return;
    answered = true;
    void ackDonateNudge(action).catch(() => {});
    track(
      action === 'clicked' ? EVENTS.DONATE_NUDGE_CLICKED : EVENTS.DONATE_NUDGE_DISMISSED,
      { kind: nudge.kind, rung: nudge.rung },
    );
  };

  later.addEventListener('click', () => {
    answer('dismissed');
    setOpen(false);
    painted = null;
    slot.innerHTML = '';
    slot.style.display = 'none';
  });
  // The navigation proceeds; `ackDonateNudge` sends with `keepalive` so the
  // ack survives the unload it races.
  contribute.addEventListener('click', () => answer('clicked'));
}

function chipHtml(nudge: NudgeResponse): string {
  return `
    <button type="button" class="donate-nudge-chip" aria-expanded="false"
            aria-controls="donate-nudge-popover" title="${escapeHtml(t('donate.nudge.chip'))}">
      ${escapeHtml(t('donate.nudge.chip'))}
    </button>
    <div class="donate-nudge-popover" id="donate-nudge-popover" role="dialog"
         aria-label="${escapeHtml(t('donate.nudge.chip'))}" hidden>
      ${bodyHtml(nudge)}
      <div class="donate-nudge-actions">
        <a class="btn btn-primary donate-nudge-contribute" href="/donate.html">
          ${escapeHtml(t('donate.nudge.contribute'))}
        </a>
        <button type="button" class="donate-nudge-later">
          ${escapeHtml(t('donate.nudge.later'))}
        </button>
      </div>
    </div>`;
}

/** The two-sentence body. Operator-approved copy; no money figures in either
 * variant — community activity stats are deliberate and fine, a cost or
 * donation amount is not. */
function bodyHtml(nudge: NudgeResponse): string {
  const closing = para(
    t(nudge.kind === 'campaign' ? 'donate.nudge.campaignBody' : 'donate.nudge.evergreenBody'),
  );

  if (nudge.kind === 'campaign') {
    const { pilots_last_year: pilots, briefings_last_year: briefings } = nudge.summary;
    // Suppress the stats sentence rather than render a figure that undercuts
    // the point — the same neutral-empty-state rule impact.py follows.
    const lead =
      pilots > 0 && briefings > 0
        ? t('donate.nudge.campaignLead', {
            pilots: pilots.toLocaleString(),
            briefings: briefings.toLocaleString(),
          })
        : t('donate.nudge.campaignLeadPlain');
    return para(lead) + closing;
  }

  const count = nudge.summary.briefing_count.toLocaleString();
  const month = formatMonthName(nudge.summary.first_briefing_at);
  // No usable first-briefing date → a variant that does not need one, rather
  // than "since ." with a hole in it.
  const lead = month
    ? t('donate.nudge.evergreenLead', { count, month })
    : t('donate.nudge.evergreenLeadNoMonth', { count });
  return para(lead) + closing;
}

/** One escaped paragraph, with `**bold**` honoured. */
function para(text: string): string {
  return `<p>${boldify(text)}</p>`;
}


/** Test seam: forget everything remembered about this page load. */
export function _resetDonateNudgeForTests(): void {
  asked = false;
  painted = null;
  impressed = false;
  answered = false;
  lastAssessment = undefined;
}
