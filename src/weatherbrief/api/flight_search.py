"""Route-token matching for the flights-list filter (#542).

The rule is deliberately tiny — there is no query syntax. A filter string is
split on whitespace into tokens, and a flight matches when *every* token is a
case-insensitive **prefix** of some route waypoint or of some word in the
flight's route name:

    "LFMD"        -> every flight touching Cannes (endpoint or intermediate)
    "LFMD EGTF"   -> only flights touching both, in either direction
    "LF"          -> everything in France (prefix match earns this for free)

Prefix-matching is what makes progressive narrowing (``L`` -> ``LF`` -> ``LFM``)
and the country-prefix shorthand work; AND-across-tokens is what makes two codes
mean "that route" rather than "either airport".

Mirrored verbatim in the web client (``web/ts/helpers/flight-search.ts``): the
past section filters here because it is paginated server-side, while future +
recent filter in the browser from the already-loaded list. Both sides have a
parity test over the same cases (``tests/test_flight_search.py`` and
``web/tests/flight-search.test.ts``) — change one, change the other.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

# Upper bound on a filter string. Long enough for a handful of ICAO codes,
# short enough that the query can never be used to push a large payload.
# Mirrored as MAX_QUERY_LEN in the TypeScript helper.
MAX_QUERY_LEN = 64


def parse_query(q: str | None) -> list[str]:
    """Split a raw filter string into uppercased match tokens.

    Returns an empty list for ``None``/blank input, which callers treat as
    "no filter" rather than "match nothing".
    """
    if not q:
        return []
    return [tok.upper() for tok in q.split() if tok]


def matches(
    waypoints: Sequence[str] | None,
    route_name: str | None,
    tokens: Iterable[str],
) -> bool:
    """True when every token prefix-matches a waypoint or a route-name word.

    ``tokens`` must already be uppercased by :func:`parse_query`. An empty
    token list matches everything.
    """
    tokens = list(tokens)
    if not tokens:
        return True

    haystack = [w.upper() for w in (waypoints or []) if w]
    haystack.extend(word.upper() for word in (route_name or "").split() if word)
    if not haystack:
        return False

    return all(any(hay.startswith(tok) for hay in haystack) for tok in tokens)
