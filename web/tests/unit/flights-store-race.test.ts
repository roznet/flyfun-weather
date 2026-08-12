/** Out-of-order response guard for the flights list (#542, PR #547 review).
 *
 *  `loadFlights`, `loadMorePast` and `setPastQuery` all replace the same list
 *  and race against *each other*, not just against themselves. A "show more"
 *  page or a background reload that resolves after a filter change must not
 *  repaint the list with results for the previous query — that state is
 *  silently wrong (rows don't match the filter box) and self-persists until
 *  the next fetch.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { FlightsPage } from '../../ts/adapters/api-adapter';

const fetchFlights = vi.fn();

vi.mock('../../ts/adapters/api-adapter', () => ({
  fetchFlights: (...args: unknown[]) => fetchFlights(...args),
  fetchActiveRefreshes: vi.fn(async () => []),
  createFlight: vi.fn(),
  deleteFlight: vi.fn(),
  unsubscribeFlight: vi.fn(),
  bulkDeleteFlights: vi.fn(),
}));

vi.mock('../../ts/adapters/debrief-adapter', () => ({
  fetchDebriefStats: vi.fn(async () => null),
}));

const { flightsStore, PAST_PAGE_SIZE } = await import('../../ts/store/flights-store');

/** Minimal flight row — only the fields the store itself reads. */
function flight(id: string, section: 'future' | 'recent' | 'past' = 'past') {
  return { id, section, waypoints: [id.toUpperCase()], route_name: id } as never;
}

/** A promise plus its resolver, so a test can land responses out of order. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

function page(flights: unknown[], pastTotal = flights.length): FlightsPage {
  return { flights, pastTotal } as FlightsPage;
}

beforeEach(() => {
  fetchFlights.mockReset();
  flightsStore.setState({
    flights: [], pastTotal: 0, loading: false, loaded: false,
    loadingMore: false, error: null, upcomingQuery: '', pastQuery: '',
  });
});

describe('flights store: out-of-order guard', () => {
  it('drops a "show more" page that lands after a filter change', async () => {
    flightsStore.setState({ flights: [flight('old-a'), flight('old-b')], pastTotal: 20 });

    const showMore = deferred<FlightsPage>();
    const filter = deferred<FlightsPage>();
    fetchFlights.mockReturnValueOnce(showMore.promise).mockReturnValueOnce(filter.promise);

    const more = flightsStore.getState().loadMorePast();
    const filtered = flightsStore.getState().setPastQuery('LESB');

    // The filter result lands first, then the stale page it superseded.
    filter.resolve(page([flight('lesb-1')], 1));
    await filtered;
    showMore.resolve(page([flight('old-c')], 20));
    await more;

    const state = flightsStore.getState();
    expect(state.flights.map((f) => f.id)).toEqual(['lesb-1']);
    expect(state.pastTotal).toBe(1);
    // The superseded path must still clear its own spinner.
    expect(state.loadingMore).toBe(false);
  });

  it('drops a background reload that lands after a filter change', async () => {
    const reload = deferred<FlightsPage>();
    const filter = deferred<FlightsPage>();
    fetchFlights.mockReturnValueOnce(reload.promise).mockReturnValueOnce(filter.promise);

    const reloading = flightsStore.getState().loadFlights();
    const filtered = flightsStore.getState().setPastQuery('LESB');

    filter.resolve(page([flight('lesb-1')], 1));
    await filtered;
    reload.resolve(page([flight('everything')], 42));
    await reloading;

    const state = flightsStore.getState();
    expect(state.flights.map((f) => f.id)).toEqual(['lesb-1']);
    expect(state.pastTotal).toBe(1);
    expect(state.loading).toBe(false);
  });

  it('still applies the newest request when it lands last', async () => {
    // The guard must not reject legitimate updates — only superseded ones.
    fetchFlights.mockResolvedValueOnce(page([flight('lesb-1')], 1));
    await flightsStore.getState().setPastQuery('LESB');

    expect(flightsStore.getState().flights.map((f) => f.id)).toEqual(['lesb-1']);
    expect(flightsStore.getState().pastQuery).toBe('LESB');
  });

  it('sends the active past query with a "show more" page', async () => {
    flightsStore.setState({ pastQuery: 'LESB', flights: [flight('lesb-1')] });
    fetchFlights.mockResolvedValueOnce(page([flight('lesb-2')], 2));

    await flightsStore.getState().loadMorePast();

    expect(fetchFlights).toHaveBeenCalledWith(
      expect.objectContaining({ pastQuery: 'LESB', pastOffset: 1, pastLimit: PAST_PAGE_SIZE }),
    );
  });
});
