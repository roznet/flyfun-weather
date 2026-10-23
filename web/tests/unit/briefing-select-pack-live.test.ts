/** #898: selecting the latest pack inside the live window reads /live once.
 *  The server now writes the new pack's layer before a refresh completes, so
 *  the snapshot overlay already carries its stamp and the poll would never
 *  load the glance / highlight / storms / ribbon that ride only on /live. */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ForecastSnapshot, PackMeta } from '../../ts/store/types';

const fetchLive = vi.fn();
const fetchPack = vi.fn();
const fetchSnapshot = vi.fn();

vi.mock('../../ts/adapters/api-adapter', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../../ts/adapters/api-adapter')>();
  const reject = () => Promise.reject(new Error('not under test'));
  return {
    ...orig,
    fetchLive: (...args: unknown[]) => fetchLive(...args),
    fetchPack: (...args: unknown[]) => fetchPack(...args),
    fetchSnapshot: (...args: unknown[]) => fetchSnapshot(...args),
    fetchRouteAnalyses: reject,
    fetchElevationProfile: reject,
    fetchRouteAdvisories: reject,
    fetchRouteFronts: reject,
    fetchAltitudeTableCached: reject,
  };
});

const { briefingStore } = await import('../../ts/store/briefing-store');

const HOUR = 3600_000;
const PACK_TS = '2026-10-01T09:00:00+00:00';
const OLD_TS = '2026-09-30T09:00:00+00:00';

function pack(ts: string, days_out: number): PackMeta {
  return { flight_id: 'f1', fetch_timestamp: ts, days_out } as PackMeta;
}

const snapshot = {
  route: { name: 'ZZAA-ZZBB', waypoints: [], cruise_altitude_ft: 6000 },
  target_date: '2026-10-01', fetch_date: '2026-10-01', days_out: 0, analyses: [],
} as unknown as ForecastSnapshot;

function flightDeparting(inMs: number) {
  return {
    id: 'f1', departure_time: new Date(Date.now() + inMs).toISOString(),
    target_date: '2026-10-01', target_time_utc: 12, flight_duration_hours: 2,
  } as never;
}

async function select(ts: string, daysOut: number) {
  fetchPack.mockResolvedValue(pack(ts, daysOut));
  await briefingStore.getState().selectPack(ts);
}

beforeEach(() => {
  fetchLive.mockReset().mockRejectedValue(new Error('offline'));
  fetchPack.mockReset();
  fetchSnapshot.mockReset().mockResolvedValue(snapshot);
  briefingStore.setState({
    packs: [pack(PACK_TS, 1), pack(OLD_TS, 2)], currentPack: null, snapshot: null, live: null,
  });
});

describe('selectPack reads the live layer inside the window (#898)', () => {
  it('loads /live for the latest pack inside the window', async () => {
    briefingStore.setState({ flight: flightDeparting(HOUR) });
    await select(PACK_TS, 1);
    expect(fetchLive).toHaveBeenCalledTimes(1);
  });

  it('loads /live for a D-0 latest pack even before the window opens', async () => {
    briefingStore.setState({ flight: flightDeparting(6 * HOUR) });
    await select(PACK_TS, 0);
    expect(fetchLive).toHaveBeenCalledTimes(1);
  });

  it('does not load /live outside the window', async () => {
    briefingStore.setState({ flight: flightDeparting(30 * HOUR) });
    await select(PACK_TS, 1);
    expect(fetchLive).not.toHaveBeenCalled();
  });

  it('does not load /live for an older pack picked from the list', async () => {
    briefingStore.setState({ flight: flightDeparting(HOUR) });
    await select(OLD_TS, 0);
    expect(fetchLive).not.toHaveBeenCalled();
  });
});
