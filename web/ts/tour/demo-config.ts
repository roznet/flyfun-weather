// Set this to the briefing URL of a public flight with interesting weather
// (e.g. /briefing.html?flight=42&pack=2026-05-19T08:00:00Z). Tour entry
// points on the welcome wizard and help page hide themselves when null.
export const DEMO_BRIEFING_URL: string | null = null;

export function buildDemoTourUrl(): string | null {
  if (!DEMO_BRIEFING_URL) return null;
  const sep = DEMO_BRIEFING_URL.includes('?') ? '&' : '?';
  return `${DEMO_BRIEFING_URL}${sep}tour=1`;
}
