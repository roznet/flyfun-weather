/** What a trip is titled with, as one testable rule (#728).
 *
 * The pilot's name is what makes a group of legs read as *a trip*, so it is the
 * title whenever there is one, with the chain beneath it. An unnamed trip is
 * titled with its chain alone — never the chain twice. The sibling of iOS's
 * `TripResponse.headerSubtitle`; the two must agree.
 */

export interface TripHeading {
  title: string;
  /** The chain under a named trip; null when the title already is the chain. */
  subtitle: string | null;
}

export function tripHeading(
  trip: { name: string; display_name?: string; summary: { chain_label: string } },
): TripHeading {
  const name = trip.name.trim();
  const chain = trip.summary.chain_label;
  // A trip with no airports yet (created empty, legs added later) has no
  // chain; fall back to the server's derived label rather than a blank title.
  if (!name) return { title: chain || trip.display_name || 'Trip', subtitle: null };
  return { title: name, subtitle: chain && chain !== name ? chain : null };
}
