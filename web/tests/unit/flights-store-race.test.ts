/** Out-of-order response guard for the past list (#542, PR #547 review;
 *  archive since #1032).
 *
 *  `loadPast`, `loadMorePast`, `setPastQuery` and `setPastFilter` all replace
 *  the same past rows and race against *each other*. A "show more" page or a
 *  background reload that resolves after a filter change must not repaint the
 *  list with results for the previous filter — that state is silently wrong
 *  (rows don't match the chips) and self-persists until the next fetch.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ArchiveListResponse } from '../../ts/store/types';
import { EMPTY_PAST_FILTER } from '../../ts/helpers/past-archive';

const fetchArchive = vi.fn();
const fetchFlights = vi.fn(async () => ({ flights: [], pastTotal: 0 }));

vi.mock('../../ts/adapters/api-adapter', () => ({
  fetchFlights: (...args: unknown[]) => fetchFlights(...(args as [])),
  fetchArchive: (...args: unknown[]) => fetchArchive(...args),
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

/** Minimal archive row — only the fields the store itself reads. */
function row(id: string) {
  return { id } as never;
}

/** A promise plus its resolver, so a test can land responses out of order. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

function page(rows: unknown[], total = rows.length): ArchiveListResponse {
  return { view: 'list', rows, total, limit: 20, offset: 0, facets: { source: 'forecast', tags: {}, outcomes: {}, not_recorded: 0 } } as ArchiveListResponse;
}

beforeEach(() => {
  fetchArchive.mockReset();
  fetchFlights.mockClear();
  flightsStore.setState({
    flights: [], pastRows: [], pastTotal: 0, loading: false, loaded: false,
    loadingMore: false, pastFiltering: false, error: null, upcomingQuery: '', pastFilter: EMPTY_PAST_FILTER,
  });
});

describe('flights store: out-of-order guard', () => {
  it('drops a "show more" page that lands after a filter change', async () => {
    flightsStore.setState({ pastRows: [row('old-a'), row('old-b')], pastTotal: 20 });

    const showMore = deferred<ArchiveListResponse>();
    const filter = deferred<ArchiveListResponse>();
    fetchArchive.mockReturnValueOnce(showMore.promise).mockReturnValueOnce(filter.promise);

    const more = flightsStore.getState().loadMorePast();
    const filtered = flightsStore.getState().setPastQuery('LESB');

    // The filter result lands first, then the stale page it superseded.
    filter.resolve(page([row('lesb-1')], 1));
    await filtered;
    showMore.resolve(page([row('old-c')], 20));
    await more;

    const state = flightsStore.getState();
    expect(state.pastRows.map((r) => r.id)).toEqual(['lesb-1']);
    expect(state.pastTotal).toBe(1);
    // The superseded path must still clear its own spinner.
    expect(state.loadingMore).toBe(false);
  });

  it('drops a background reload that lands after a chip change', async () => {
    const reload = deferred<ArchiveListResponse>();
    const filter = deferred<ArchiveListResponse>();
    fetchArchive.mockReturnValueOnce(reload.promise).mockReturnValueOnce(filter.promise);

    const reloading = flightsStore.getState().loadFlights();
    const filtered = flightsStore.getState().setPastFilter({ tags: ['ICE'] });

    filter.resolve(page([row('ice-1')], 1));
    await filtered;
    reload.resolve(page([row('everything')], 42));
    await reloading;

    const state = flightsStore.getState();
    expect(state.pastRows.map((r) => r.id)).toEqual(['ice-1']);
    expect(state.pastTotal).toBe(1);
    expect(state.loading).toBe(false);
    // /api/flights no longer carries the past section.
    expect(fetchFlights).toHaveBeenCalledWith({ pastLimit: 0 });
  });

  it('ignores "show more" while a filter query is in flight', async () => {
    flightsStore.setState({ pastRows: [row('old-a')], pastTotal: 20 });
    const filter = deferred<ArchiveListResponse>();
    fetchArchive.mockReturnValueOnce(filter.promise);

    const filtered = flightsStore.getState().setPastFilter({ tags: ['ICE'] });
    await flightsStore.getState().loadMorePast();  // no-op: would drop the filter result
    filter.resolve(page([row('ice-1')], 1));
    await filtered;

    const state = flightsStore.getState();
    expect(fetchArchive).toHaveBeenCalledTimes(1);
    expect(state.pastRows.map((r) => r.id)).toEqual(['ice-1']);
    expect(state.pastFiltering).toBe(false);
  });

  it('still applies the newest request when it lands last', async () => {
    fetchArchive.mockResolvedValueOnce(page([row('lesb-1')], 1));
    await flightsStore.getState().setPastQuery('LESB');

    expect(flightsStore.getState().pastRows.map((r) => r.id)).toEqual(['lesb-1']);
    expect(flightsStore.getState().pastFilter.q).toBe('LESB');
  });

  it('sends the active past filter with a "show more" page', async () => {
    flightsStore.setState({
      pastFilter: { ...EMPTY_PAST_FILTER, q: 'LESB', tags: ['TS'] }, pastRows: [row('lesb-1')],
    });
    fetchArchive.mockResolvedValueOnce(page([row('lesb-2')], 2));

    await flightsStore.getState().loadMorePast();

    const params = fetchArchive.mock.calls[0][0] as URLSearchParams;
    expect(params.get('q')).toBe('LESB');
    expect(params.get('tags')).toBe('TS');
    expect(params.get('offset')).toBe('1');
    expect(params.get('limit')).toBe(String(PAST_PAGE_SIZE));
    expect(flightsStore.getState().pastRows.map((r) => r.id)).toEqual(['lesb-1', 'lesb-2']);
  });
});

describe('flights store: pruneSelection', () => {
  it('drops selections whose row the filter is hiding', () => {
    // The hazard: a row ticked before the filter was applied keeps its id in
    // `selectedIds` with no checkbox rendered, so it cannot be unticked while
    // the filter is active — and bulk delete would still take it.
    flightsStore.setState({ selectedIds: new Set(['visible-1', 'hidden-1']) });

    flightsStore.getState().pruneSelection(['visible-1', 'visible-2']);

    expect([...flightsStore.getState().selectedIds]).toEqual(['visible-1']);
  });

  it('keeps identity stable when nothing needs dropping', () => {
    // An unconditional set() would churn `selectedIds` identity and re-render
    // the list on every pass, since the render path calls this during render.
    const before = new Set(['a', 'b']);
    flightsStore.setState({ selectedIds: before });

    flightsStore.getState().pruneSelection(['a', 'b', 'c']);

    expect(flightsStore.getState().selectedIds).toBe(before);
  });

  it('clears everything when nothing is visible', () => {
    flightsStore.setState({ selectedIds: new Set(['a', 'b']) });

    flightsStore.getState().pruneSelection([]);

    expect(flightsStore.getState().selectedIds.size).toBe(0);
  });
});
