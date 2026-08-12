/** Route-token matching for the flights-list filter (#542).
 *
 *  There is no query syntax. A filter string is split on whitespace into
 *  tokens, and a flight matches when *every* token is a case-insensitive
 *  **prefix** of some route waypoint or of some word in the route name:
 *
 *    "LFMD"       -> every flight touching Cannes (endpoint or intermediate)
 *    "LFMD EGTF"  -> only flights touching both, in either direction
 *    "LF"         -> everything in France (prefix match earns this for free)
 *
 *  Mirrored verbatim from the Python side (`weatherbrief/api/flight_search.py`):
 *  the past section filters server-side because it is paginated, while future +
 *  recent filter here from the already-loaded list. Both sides have a parity
 *  test over the same cases — change one, change the other.
 */

/** Upper bound on a filter string; mirrors MAX_QUERY_LEN in Python. */
export const MAX_QUERY_LEN = 64;

/** Split a raw filter string into uppercased match tokens. Blank input yields
 *  an empty list, which callers treat as "no filter", not "match nothing". */
export function parseQuery(q: string | null | undefined): string[] {
  if (!q) return [];
  return q.split(/\s+/).filter((tok) => tok.length > 0).map((tok) => tok.toUpperCase());
}

/** Every token must prefix-match a waypoint or a route-name word.
 *  `tokens` must already be uppercased by `parseQuery`. */
export function matchesQuery(
  waypoints: string[] | undefined,
  routeName: string | undefined,
  tokens: string[],
): boolean {
  if (tokens.length === 0) return true;

  const haystack = (waypoints ?? []).filter(Boolean).map((w) => w.toUpperCase());
  for (const word of (routeName ?? '').split(/\s+/)) {
    if (word) haystack.push(word.toUpperCase());
  }
  if (haystack.length === 0) return false;

  return tokens.every((tok) => haystack.some((hay) => hay.startsWith(tok)));
}

/** Indices of the waypoints that a token matched — drives the "pull the match
 *  out of the ellipsis and bold it" behaviour in the compact route line. */
export function matchedWaypointIndices(
  waypoints: string[] | undefined,
  tokens: string[],
): Set<number> {
  const hits = new Set<number>();
  if (tokens.length === 0) return hits;
  (waypoints ?? []).forEach((w, i) => {
    const up = (w ?? '').toUpperCase();
    if (up && tokens.some((tok) => up.startsWith(tok))) hits.add(i);
  });
  return hits;
}
