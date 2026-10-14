/** Debrief recap and single-tick replay helpers (#769). */

import { describe, it, expect, vi } from 'vitest';

vi.mock('../../ts/i18n/i18n', () => ({ t: (key: string) => key }));
import {
  aircraftTimeAt,
  clipBand,
  combineSeverities,
  departureGapText,
  forecastLaneBands,
  gridRows,
  modeBarText,
  observedLive,
  recapRibbon,
  recapTimePoints,
  REPLAY_BASE,
  snapshotAtTick,
  startTick,
  tickAtOrBefore,
  tickFailed,
  tickForRoutePoint,
  tickLoaded,
  tickTimePoints,
  timeBaseCaption,
  type ReplayState,
} from '../../ts/helpers/debrief-replay';
import { segmentIndexAt } from '../../ts/visualization/observed/ribbon-core';
import type {
  ForecastSnapshot,
  LiveLayer,
  LiveTickEntry,
  LiveTickResponse,
  RecapForecast,
  RecapResponse,
  RibbonWeather,
} from '../../ts/store/types';

const T = (hhmm: string) => `2026-06-12T${hhmm}:00+00:00`;
const ms = (hhmm: string) => new Date(T(hhmm)).getTime();

function recap(over: Partial<RecapResponse> = {}): RecapResponse {
  return {
    flight_id: 'zz-flight', note: '', status: 'ok', message: null, cancelled: false,
    time_base: 'planned', departed_at: T('10:00'), arrival_at: T('11:00'),
    ticks: 3, capped: false, forecast: null, alerts: [], route_nm: 100, segment_nm: 50,
    waypoints: [
      { icao: 'ZZAA', along_nm: 0, eta: T('10:00') },
      { icao: 'ZZBB', along_nm: 100, eta: T('11:00') },
    ],
    segments: [
      { index: 0, from_nm: 0, to_nm: 50, at: T('10:15'), status: 'observed', tick_at: T('10:10'),
        radar_time: T('10:05'), radar_status: 'measured', radar_max_dbz: 30, weather_status: 'available' },
      { index: 1, from_nm: 50, to_nm: 100, at: T('10:45'), status: 'not_reached' },
    ],
    weather: [], storms: [], packs: [],
    ...over,
  };
}

const ticks: LiveTickEntry[] = [
  { tick_at: T('09:50'), flown_nm: 0 },
  { tick_at: T('10:00'), flown_nm: 0 },
  { tick_at: T('10:10'), flown_nm: 17 },
  { tick_at: T('10:20'), flown_nm: 33 },
  { tick_at: T('10:30'), flown_nm: 50 },
];

describe('time-base caption', () => {
  it('names the base, cancelled first', () => {
    expect(timeBaseCaption({ time_base: 'observed', cancelled: false })).toBe('actual times');
    expect(timeBaseCaption({ time_base: 'partial', cancelled: false })).toBe('partly observed');
    expect(timeBaseCaption({ time_base: 'planned', cancelled: false })).toBe('planned schedule');
    expect(timeBaseCaption({ time_base: 'planned', cancelled: true })).toBe('planned, flight cancelled');
    expect(timeBaseCaption({ time_base: null, cancelled: false })).toBeNull();
  });

  it('shows the departure gap the forecast was graded at', () => {
    expect(departureGapText('2026-06-12T16:45:00Z', '2026-06-12T17:40:00Z', 'observed'))
      .toBe('forecast for 16:45Z departure, flown 17:40Z');
    expect(departureGapText('2026-06-12T16:45:00Z', '2026-06-12T17:40:00Z', 'planned'))
      .toBe('forecast for 16:45Z departure, planned 17:40Z');
    expect(departureGapText('2026-06-12T16:45:00Z', '2026-06-12T16:45:00+00:00', 'observed'))
      .toBe('forecast for 16:45Z departure, flown on time');
    expect(departureGapText(null, '2026-06-12T17:40:00Z', 'observed')).toBeNull();
  });
});

describe('tick picking from a route point', () => {
  it('interpolates the aircraft time between known points, clamped', () => {
    const pts = recapTimePoints(recap());
    expect(aircraftTimeAt(25, pts)).toBe(ms('10:15'));
    expect(aircraftTimeAt(50, pts)).toBe(ms('10:30'));
    expect(aircraftTimeAt(-5, pts)).toBe(ms('10:00'));
    expect(aircraftTimeAt(500, pts)).toBe(ms('11:00'));
    expect(aircraftTimeAt(10, [])).toBeNaN();
  });

  it('serves the last tick at or before the time, as /live/at does', () => {
    expect(tickAtOrBefore(ticks, ms('10:15'))?.tick_at).toBe(T('10:10'));
    expect(tickAtOrBefore(ticks, ms('10:10'))?.tick_at).toBe(T('10:10'));
    expect(tickAtOrBefore(ticks, ms('09:00'))).toBeNull();
    expect(tickAtOrBefore(ticks, NaN)).toBeNull();
  });

  it('maps a route point to the aircraft time there, then the tick', () => {
    // 40 NM: aircraft at 10:20 on the recap's times → the 10:20 tick.
    expect(tickForRoutePoint(40, recapTimePoints(recap()), ticks)?.tick_at).toBe(T('10:20'));
    // 33 NM: 10:19.8 → the 10:10 tick (not the nearest, the last before).
    expect(tickForRoutePoint(33, recapTimePoints(recap()), ticks)?.tick_at).toBe(T('10:10'));
  });

  it('uses the ticks own positions during the window', () => {
    const pts = tickTimePoints(ticks);
    // Parked at 0 before departure: the aircraft left at the last of them.
    expect(pts[0]).toEqual({ along_nm: 0, ms: ms('10:00') });
    expect(tickForRoutePoint(0, pts, ticks)?.tick_at).toBe(T('10:00'));
    expect(tickForRoutePoint(40, pts, ticks)?.tick_at).toBe(T('10:20'));
  });
});

describe('forecast lane band merging', () => {
  const lane = (over: Partial<RecapForecast> = {}): RecapForecast => ({
    pack_timestamp: T('06:00'), chosen_by: 'default', available: true,
    models: ['gfs', 'ecmwf', 'icon'], aggregation: 'worst',
    advisories: [
      { advisory_id: 'fiki_icing', per_model: [
        { model: 'gfs', ribbon: [[0, 30, 'green'], [30, 60, 'amber'], [60, 100, 'green']] },
        { model: 'ecmwf', ribbon: [[0, 40, 'green'], [40, 100, 'red']] },
        { model: 'icon', ribbon: [[0, 100, 'green']] },
      ] },
      { advisory_id: 'convective', per_model: [
        { model: 'gfs', ribbon: [[0, 100, 'amber']] },
      ] },
      // Not a route-weather advisory: never in the lane.
      { advisory_id: 'flight_category', per_model: [{ model: 'gfs', ribbon: [[0, 100, 'red']] }] },
    ],
    ...over,
  });

  it('takes the worst advisory at each stretch and merges equal neighbours', () => {
    const bands = forecastLaneBands(lane());
    expect(bands.map((b) => [b.from_nm, b.to_nm, b.severity])).toEqual([
      [0, 40, 'amber'],
      [40, 100, 'red'],
    ]);
    // Merged on severity; the advisories behind it are the union.
    expect([...bands[0].advisories].sort()).toEqual(['convective', 'fiki_icing']);
    expect(bands[1].advisories).toEqual(['fiki_icing']);
  });

  it('applies the pack aggregation across models (majority)', () => {
    const bands = forecastLaneBands(lane({
      aggregation: 'majority',
      advisories: [lane().advisories![0]],
    }));
    // 30–40: gfs amber, ecmwf green, icon green → green; 40–60: amber, red,
    // green → tie, worst of tied = red; 60–100: green, red, green → green.
    expect(bands.map((b) => [b.from_nm, b.to_nm, b.severity])).toEqual([
      [0, 40, 'green'],
      [40, 60, 'red'],
      [60, 100, 'green'],
    ]);
  });

  it('ignores unavailable, and is empty without a lane', () => {
    expect(combineSeverities(['unavailable', 'amber'], 'worst')).toBe('amber');
    expect(combineSeverities(['unavailable'], 'majority')).toBe('unavailable');
    expect(forecastLaneBands(null)).toEqual([]);
    expect(forecastLaneBands(lane({ available: false }))).toEqual([]);
  });
});

describe('replay mode state', () => {
  const live = { flight_id: 'zz', pack_timestamp: T('06:00'), live_updated_at: T('10:40') } as LiveLayer;
  const view = { ...live, live_updated_at: T('10:10'), tick_at: T('10:10') } as LiveTickResponse;

  it('live updates do not replace the view while a tick is on screen', () => {
    let s: ReplayState = startTick(REPLAY_BASE, T('10:10'));
    expect(s.kind).toBe('loading');
    s = tickLoaded(s, T('10:10'), view);
    expect(observedLive(s, live)).toBe(view);
    // A newer live layer lands from the poll: still the tick.
    const newer = { ...live, live_updated_at: T('10:50') } as LiveLayer;
    expect(observedLive(s, newer)).toBe(view);
    expect(modeBarText(s)).toBe('Viewing 10:10Z');
  });

  it('back to live restores the latest', () => {
    const s = tickLoaded(startTick(REPLAY_BASE, T('10:10')), T('10:10'), view);
    const back = REPLAY_BASE;
    const newer = { ...live, live_updated_at: T('10:50') } as LiveLayer;
    expect(observedLive(back, newer)).toBe(newer);
    expect(modeBarText(back)).toBeNull();
    expect(s.kind).toBe('tick');
  });

  it('drops a slow response for a tick no longer asked for', () => {
    let s = startTick(REPLAY_BASE, T('10:10'));
    s = startTick(s, T('10:20'));
    expect(tickLoaded(s, T('10:10'), view)).toBe(s);
    // Went back before it landed: stays on the base view.
    expect(tickLoaded(REPLAY_BASE, T('10:10'), view)).toBe(REPLAY_BASE);
    // A failed fetch falls back to what was on screen.
    const shown = tickLoaded(startTick(REPLAY_BASE, T('10:10')), T('10:10'), view);
    const asking = startTick(shown, T('10:20'));
    expect(tickFailed(asking, T('10:20'), shown)).toBe(shown);
  });

  it('folds the tick into the snapshot, without newer pack data leaking in', () => {
    const snap = {
      route_observations: { fetch_time: T('10:40') }, route_sigmets: { sigmets: [] },
      observed_conditions: { computed_at: T('10:40') }, live_changes: null,
    } as unknown as ForecastSnapshot;
    const out = snapshotAtTick(snap, { ...view, observed_conditions: null, route_sigmets: null,
      route_observations: null, changes: null, last_refresh_delta: null });
    expect(out.observed_conditions).toBeNull();
    expect(out.route_observations).toBeNull();
    expect(out.live_updated_at).toBe(T('10:10'));
  });
});

describe('recap as a ribbon', () => {
  it('draws stretches not observed as no sample, never as no echo', () => {
    const { ribbon } = recapRibbon(recap());
    expect(ribbon.segments?.[0].radar_status).toBe('measured');
    expect(ribbon.segments?.[1].radar_status).toBe('no_sample');
    expect(ribbon.segments?.[1].radar_max_dbz).toBeNull();
    // The only observed stretch had the cells feed: bands are on.
    expect(ribbon.weather_status).toBe('available');
  });

  it('clips a band to the stretch it was read for', () => {
    const band: RibbonWeather = {
      id: 'rain-1', tier: 'rain', from_nm: 30, to_nm: 80, side: 'right', near_nm: 2, far_nm: 10,
      profile: [[35, 2, 8], [55, 2, 10], [75, 3, 9]],
    };
    const clipped = clipBand(band, 0, 50)!;
    expect([clipped.from_nm, clipped.to_nm]).toEqual([30, 50]);
    expect(clipped.profile).toEqual([[35, 2, 8]]);
    expect(clipBand(band, 90, 100)).toBeNull();
    const { ribbon } = recapRibbon(recap({ weather: [{ ...band, segment_index: 0, tick_at: T('10:10') }] }));
    expect(ribbon.weather?.map((w) => [w.from_nm, w.to_nm])).toEqual([[30, 50]]);
  });

  it('a tap on the drawing finds its stretch (the last one past the end)', () => {
    const { ribbon } = recapRibbon(recap());
    // 340 px wide: the route runs 20..320, 100 NM.
    expect(segmentIndexAt(ribbon, 20 + 300 * 0.25, 340)).toBe(0);
    expect(segmentIndexAt(ribbon, 20 + 300 * 0.75, 340)).toBe(1);
    expect(segmentIndexAt(ribbon, 400, 340)).toBe(1);
  });

  it('falls back to the radar strip when a stretch had no cells feed', () => {
    const r = recap();
    r.segments[0].weather_status = 'stale';
    expect(recapRibbon(r).ribbon.weather_status).toBe('unavailable');
  });
});

describe('distance × time grid', () => {
  it('marks the stretches the recap took as the diagonal', () => {
    const rows = gridRows([
      { tick_at: T('10:00'), flown_nm: 0, segments: [{ from_nm: 0, to_nm: 50 }, { from_nm: 50, to_nm: 100 }] },
      { tick_at: T('10:10'), flown_nm: 17, segments: [{ from_nm: 0, to_nm: 50, radar_status: 'measured', radar_max_dbz: 30 }, { from_nm: 50, to_nm: 100 }] },
      { tick_at: T('10:20'), flown_nm: 33 },
    ], recap());
    expect(rows).toHaveLength(2);
    expect(rows[0].segments.map((c) => c.diagonal)).toEqual([false, false]);
    expect(rows[1].segments.map((c) => c.diagonal)).toEqual([true, false]);
    expect(rows[1].segments[0].seg.radar_max_dbz).toBe(30);
  });
});
