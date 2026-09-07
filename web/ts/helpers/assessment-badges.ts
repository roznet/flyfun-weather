/** Shared badge vocabulary for assessments and long-range outlooks (#602).
 *
 * Extracted so the flights list and the trip page cannot drift: an assessment
 * or outlook value added to one copy and not the other would render the same
 * weather in different colours on two pages of the same app.
 *
 * The two ladders stay separate on purpose. An assessment is a verdict
 * (GREEN/AMBER/RED); an outlook is a *tendency* beyond the high-resolution
 * horizon. They are mutually exclusive by design and must never be folded
 * together — see `designs/flight-trips.md`.
 */

/** Long-range outlook → soft badge class. */
export const OUTLOOK_BADGE_CLASS: Record<string, string> = {
  TRENDING_SETTLED: 'badge-outlook-settled',
  MIXED_SIGNALS: 'badge-outlook-mixed',
  TRENDING_UNSETTLED: 'badge-outlook-unsettled',
};

/** The soft badge class for an outlook, falling back to the neutral middle. */
export function outlookClass(outlook: string | null | undefined): string {
  return OUTLOOK_BADGE_CLASS[(outlook || '').toUpperCase()] ?? 'badge-outlook-mixed';
}

/** Traffic-light badge class for an assessment. */
export function assessmentClass(assessment: string | null | undefined): string {
  if (!assessment) return 'badge-none';
  switch (assessment.toUpperCase()) {
    case 'GREEN': return 'badge-green';
    case 'AMBER': return 'badge-amber';
    case 'RED': return 'badge-red';
    // A real verdict ("we could not assess this"), so it carries the bordered
    // badge rather than badge-none, which means "no verdict at all" (#392).
    case 'UNAVAILABLE': return 'badge-unavailable';
    default: return 'badge-none';
  }
}
