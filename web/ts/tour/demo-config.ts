/**
 * Configuration for the first-run guided tour.
 *
 * `DEMO_BRIEFING_URL` is the briefing URL the welcome wizard's "Take the
 * interactive tour" button navigates to. The tour auto-starts because
 * `?tour=1` is appended (see `briefing-tour.ts > maybeAutoStartBriefingTour`).
 *
 * To set it up:
 *   1. Create a flight on a date with interesting weather (icing, fronts,
 *      convective — anything more memorable than a blue-sky day).
 *   2. Toggle the flight public (Settings → Sharing on the briefing page).
 *   3. Copy the briefing URL — it'll look like
 *      `/briefing.html?flight=42&pack=2026-05-19T08:00:00Z`
 *   4. Paste it below.
 *
 * Leave as `null` to hide the wizard button until a demo is configured.
 * The toolbar `?`-icon on any briefing page still works regardless — it
 * runs the tour against whatever briefing the user is viewing.
 */
export const DEMO_BRIEFING_URL: string | null = null;

/**
 * Append `tour=1` (preserving existing query params) so the briefing page
 * auto-starts the tour after data loads.
 */
export function buildDemoTourUrl(): string | null {
  if (!DEMO_BRIEFING_URL) return null;
  const sep = DEMO_BRIEFING_URL.includes('?') ? '&' : '?';
  return `${DEMO_BRIEFING_URL}${sep}tour=1`;
}
