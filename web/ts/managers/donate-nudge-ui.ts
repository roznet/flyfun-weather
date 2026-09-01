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
import { escapeHtml } from '../utils';

const SLOT_ID = 'donate-nudge-slot';

/** Assessment grades the chip refuses to appear beside. */
const SUPPRESSED_ASSESSMENTS = new Set(['RED']);

let wired = false;

/**
 * Ask the server whether to offer a donate chip, and paint it if so.
 *
 * Safe to call once per briefing page load; a second call is a no-op so a
 * re-render never re-fetches or double-counts an impression. Every failure path
 * is silent — a donation ask is the least important thing on this page, and it
 * must never surface an error or delay anything else.
 */
export async function initDonateNudge(assessment: string | null | undefined): Promise<void> {
  if (wired) return;
  const slot = document.getElementById(SLOT_ID);
  if (!slot) return;

  // Bail before the request when we already know we would suppress: no point
  // opening an ask on the server for a view that cannot render it.
  if (shouldSuppressNudge(assessment)) return;

  let nudge: NudgeResponse;
  try {
    nudge = await fetchDonateNudge();
  } catch {
    return; // not logged in, offline, donations off — all mean "no chip"
  }
  if (!nudge.show) return;

  wired = true;
  render(slot, nudge);
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
  slot.style.display = '';

  const chip = slot.querySelector<HTMLButtonElement>('.donate-nudge-chip')!;
  const popover = slot.querySelector<HTMLElement>('.donate-nudge-popover')!;
  const later = slot.querySelector<HTMLButtonElement>('.donate-nudge-later')!;
  const contribute = slot.querySelector<HTMLAnchorElement>('.donate-nudge-contribute')!;

  // The chip painted, so the impression is real: ack it and count it.
  void ackDonateNudge('shown').catch(() => {});
  track(EVENTS.DONATE_NUDGE_SHOWN, { kind: nudge.kind, rung: nudge.rung });

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

  const answer = (action: 'clicked' | 'dismissed'): void => {
    void ackDonateNudge(action).catch(() => {});
    track(
      action === 'clicked' ? EVENTS.DONATE_NUDGE_CLICKED : EVENTS.DONATE_NUDGE_DISMISSED,
      { kind: nudge.kind, rung: nudge.rung },
    );
  };

  later.addEventListener('click', () => {
    answer('dismissed');
    setOpen(false);
    slot.innerHTML = '';
    slot.style.display = 'none';
  });
  // Let the navigation proceed; the ack is in flight and the page is leaving.
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
  const month = formatMonth(nudge.summary.first_briefing_at);
  // No usable first-briefing date → a variant that does not need one, rather
  // than "since ." with a hole in it.
  const lead = month
    ? t('donate.nudge.evergreenLead', { count, month })
    : t('donate.nudge.evergreenLeadNoMonth', { count });
  return para(lead) + closing;
}

/** One escaped paragraph, with `**bold**` honoured.
 *
 * Same order as `formatDigestBody`: escape everything first, then inject our
 * own tags — so the emphasis the copy carries survives without opening the
 * markup to whatever came back from the API.
 */
function para(text: string): string {
  const bold = escapeHtml(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  return `<p>${bold}</p>`;
}

/** "April" in the viewer's locale; "" when there is no usable date. */
function formatMonth(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString(undefined, { month: 'long' });
}


/** Test seam: forget that a chip was already wired on this page. */
export function _resetDonateNudgeForTests(): void {
  wired = false;
}
