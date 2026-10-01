/** Live layer (#637): out-of-order / stale `fetchLive` responses must never
 *  roll the snapshot back, and responses for a pack that is no longer on
 *  screen must be dropped. */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ForecastSnapshot, LiveLayer, PackMeta, RouteObservations } from '../../ts/store/types';

const fetchLive = vi.fn();
const fetchLatestPack = vi.fn();

vi.mock('../../ts/adapters/api-adapter', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../../ts/adapters/api-adapter')>();
  return {
    ...orig,
    fetchLive: (...args: unknown[]) => fetchLive(...args),
    fetchLatestPack: (...args: unknown[]) => fetchLatestPack(...args),
  };
});

const { briefingStore } = await import('../../ts/store/briefing-store');

const PACK_TS = '2026-10-01T09:00:00+00:00';

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

function obs(fetch_time: string, icao: string): RouteObservations {
  return {
    corridor_nm: 25, fetch_time, airports_found: 1, airports_with_metar: 1, airports_with_taf: 0,
    airports: [{ icao } as never], comparisons: [], worst_metar_category: null, worst_taf_category: null,
    has_conflicts: false, phenomena_along_route: [],
  };
}

function live(updated: string, icao: string, pack = PACK_TS): LiveLayer {
  return {
    flight_id: 'f1', pack_timestamp: pack, live_updated_at: updated,
    route_observations: obs(updated, icao), observations_updated_at: updated,
    route_sigmets: null, sigmets_updated_at: null, observed_conditions: null, observed_updated_at: null,
    changes: null, last_refresh_delta: null,
  };
}

const baseSnapshot: ForecastSnapshot = {
  route: { name: 'ZZAA-ZZBB', waypoints: [], cruise_altitude_ft: 6000 },
  target_date: '2026-10-01', fetch_date: '2026-10-01', days_out: 0, analyses: [],
  route_observations: obs('2026-10-01T09:00:00Z', 'ZZAA'),
};

function pack(ts: string): PackMeta {
  return { flight_id: 'f1', fetch_timestamp: ts, days_out: 0 } as PackMeta;
}

beforeEach(() => {
  fetchLive.mockReset();
  fetchLatestPack.mockReset();
  briefingStore.setState({
    flight: { id: 'f1' } as never,
    packs: [pack(PACK_TS)],
    currentPack: pack(PACK_TS),
    snapshot: baseSnapshot,
    live: null,
    refreshing: false,
  });
});

describe('briefing store: loadLive out-of-order guard', () => {
  it('a stale response resolving after a newer one does not overwrite it', async () => {
    const slow = deferred<LiveLayer>();
    const fast = deferred<LiveLayer>();
    fetchLive.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);

    const first = briefingStore.getState().loadLive();
    const second = briefingStore.getState().loadLive();

    fast.resolve(live('2026-10-01T11:00:00Z', 'ZZNEW'));
    await second;
    slow.resolve(live('2026-10-01T10:00:00+00:00', 'ZZOLD'));
    await first;

    const s = briefingStore.getState();
    expect(s.snapshot!.route_observations!.airports[0].icao).toBe('ZZNEW');
    expect(s.snapshot!.live_updated_at).toBe('2026-10-01T11:00:00Z');
    expect(s.live!.live_updated_at).toBe('2026-10-01T11:00:00Z');
  });

  it('drops a response that lands after the user switched packs', async () => {
    const d = deferred<LiveLayer>();
    fetchLive.mockReturnValueOnce(d.promise);
    const p = briefingStore.getState().loadLive();
    const otherSnap = { ...baseSnapshot };
    briefingStore.setState({ currentPack: pack('2026-09-30T18:00:00Z'), snapshot: otherSnap });
    d.resolve(live('2026-10-01T11:00:00Z', 'ZZNEW'));
    await p;
    expect(briefingStore.getState().snapshot).toBe(otherSnap);
    expect(briefingStore.getState().live).toBeNull();
  });

  it('ignores a layer relative to another pack', async () => {
    fetchLive.mockResolvedValueOnce(live('2026-10-01T11:00:00Z', 'ZZNEW', '2026-10-01T06:00:00Z'));
    await briefingStore.getState().loadLive();
    expect(briefingStore.getState().snapshot).toBe(baseSnapshot);
  });

  it('applies a layer whose pack timestamp is the same instant in another format', async () => {
    fetchLive.mockResolvedValueOnce(live('2026-10-01T11:00:00Z', 'ZZNEW', '2026-10-01T09:00:00Z'));
    await briefingStore.getState().loadLive();
    expect(briefingStore.getState().snapshot!.route_observations!.airports[0].icao).toBe('ZZNEW');
  });
});

describe('briefing store: syncLatest', () => {
  it('loads the live layer when live_updated_at moved on the same pack', async () => {
    fetchLatestPack.mockResolvedValueOnce({ ...pack('2026-10-01T09:00:00Z'), live_updated_at: '2026-10-01T11:00:00Z' });
    fetchLive.mockResolvedValueOnce(live('2026-10-01T11:00:00Z', 'ZZNEW'));
    await briefingStore.getState().syncLatest();
    expect(fetchLive).toHaveBeenCalledTimes(1);
    expect(briefingStore.getState().snapshot!.live_updated_at).toBe('2026-10-01T11:00:00Z');
  });

  it('does nothing when live_updated_at is already applied', async () => {
    briefingStore.setState({ snapshot: { ...baseSnapshot, live_updated_at: '2026-10-01T11:00:00+00:00' } });
    fetchLatestPack.mockResolvedValueOnce({ ...pack(PACK_TS), live_updated_at: '2026-10-01T11:00:00Z' });
    await briefingStore.getState().syncLatest();
    expect(fetchLive).not.toHaveBeenCalled();
  });

  it('does not poll when the user is viewing an older pack', async () => {
    briefingStore.setState({ packs: [pack('2026-10-01T12:00:00Z'), pack(PACK_TS)] });
    await briefingStore.getState().syncLatest();
    expect(fetchLatestPack).not.toHaveBeenCalled();
    expect(fetchLive).not.toHaveBeenCalled();
  });
});
