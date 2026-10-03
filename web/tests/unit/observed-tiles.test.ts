/** Tiled observed layers on the route map (#652): which frame is drawn, under
 *  what URL, and what the badge says about it. */

import { describe, it, expect } from 'vitest';

import {
  SATELLITE_SOURCE,
  currentFrame,
  formatBadge,
  frameBadgeField,
  frameTileUrl,
  isTiledSource,
  resolveObservedSelection,
  staleBadge,
  type ObservedFramesInfo,
} from '../../ts/visualization/route-map/observed-overlay-geometry';

function listing(overrides: Partial<ObservedFramesInfo> = {}): ObservedFramesInfo {
  return {
    source: 'opera_dbzh',
    label: 'Radar reflectivity',
    frames: [
      { stamp: '20261003T1405', valid_time: '2026-10-03T14:05:00+00:00', age_minutes: 7 },
      { stamp: '20261003T1400', valid_time: '2026-10-03T14:00:00+00:00', age_minutes: 12 },
    ],
    stale: false,
    window_minutes: 10,
    attribution: { text: 'EUMETNET OPERA' },
    tile_url_template: '/api/observed/tiles/opera_dbzh/{stamp}/{z}/{x}/{y}.png',
    min_zoom: 3,
    max_zoom: 10,
    ...overrides,
  };
}

describe('tiled observed layers', () => {
  it('draws radar as tiles and cloud tops as a corridor image', () => {
    expect(isTiledSource('opera_dbzh')).toBe(true);
    expect(isTiledSource('opera_rate')).toBe(true);
    expect(isTiledSource('eumetsat_ctth')).toBe(false);
    expect(isTiledSource('eumetsat_ctth_temp')).toBe(false);
    expect(isTiledSource(null)).toBe(false);
    // The satellite is an underlay, never "the" observed measurement.
    expect(isTiledSource(SATELLITE_SOURCE)).toBe(false);
  });

  it('draws the newest frame', () => {
    expect(currentFrame(listing())?.stamp).toBe('20261003T1405');
  });

  it('draws nothing when the newest frame is stale', () => {
    // An old frame must not pass for the present sky.
    expect(currentFrame(listing({ stale: true }))).toBeNull();
    expect(currentFrame(listing({ frames: [] }))).toBeNull();
    expect(currentFrame(null)).toBeNull();
  });

  it('keys the tile URL by the frame stamp, leaving the XYZ placeholders', () => {
    const info = listing();
    const url = frameTileUrl(info, info.frames[1]);
    expect(url).toBe('/api/observed/tiles/opera_dbzh/20261003T1400/{z}/{x}/{y}.png');
    // Two frames, two URLs: a loop (#653) steps the URL, never re-fetches one.
    expect(frameTileUrl(info, info.frames[0])).not.toBe(url);
  });

  it('labels the badge from the drawn frame, aged against now', () => {
    const info = listing();
    const now = new Date('2026-10-03T14:17:00Z');
    const text = formatBadge(frameBadgeField(info, info.frames[0], now));
    expect(text).toContain('Radar reflectivity 14:05Z');
    expect(text).toContain('12 min old');
    expect(text).toContain('10 min rolling max');
    expect(text).toContain('EUMETNET OPERA');
  });

  it('says nothing about a rolling window the satellite does not have', () => {
    const info = listing({
      source: SATELLITE_SOURCE,
      label: 'Satellite infrared',
      window_minutes: 0,
      attribution: { text: 'EUMETSAT MTG FCI IR 10.5 µm via EUMETView' },
    });
    const text = formatBadge(frameBadgeField(info, info.frames[0], new Date('2026-10-03T14:30:00Z')));
    expect(text).toContain('Satellite infrared 14:05Z');
    expect(text).not.toContain('rolling');
  });
});

describe('observed layer selection', () => {
  const all = { opera_dbzh: true, opera_rate: true, eumetsat_ctth: true, eumetsat_li: true };

  it('"None" draws nothing, even when radar is available', () => {
    expect(resolveObservedSelection('', all)).toBe('');
  });

  it('keeps an available pick', () => {
    expect(resolveObservedSelection('eumetsat_ctth', all)).toBe('eumetsat_ctth');
  });

  it('falls back only when the pick was not collected', () => {
    expect(resolveObservedSelection('eumetsat_ctth', { opera_dbzh: true })).toBe('opera_dbzh');
    expect(resolveObservedSelection('opera_dbzh', { opera_rate: true })).toBe('opera_rate');
    expect(resolveObservedSelection('opera_dbzh', {})).toBe('');
  });

  it('a never-set pick takes the default', () => {
    expect(resolveObservedSelection(undefined, all)).toBe('opera_dbzh');
  });
});

describe('stale layer badge', () => {
  const now = new Date('2026-10-03T15:00:00Z');

  it('says a stale layer is hidden, and how old its newest frame is', () => {
    const text = staleBadge(listing({ stale: true }), now);
    expect(text).toContain('Radar reflectivity');
    expect(text).toContain('not shown');
    expect(text).toContain('14:05Z');
    expect(text).toContain('55 min old');
  });

  it('says nothing for a current layer or a missing listing', () => {
    expect(staleBadge(listing(), now)).toBe('');
    expect(staleBadge(null, now)).toBe('');
  });

  it('handles a stale listing with no frames at all', () => {
    expect(staleBadge(listing({ stale: true, frames: [] }), now)).toContain('no current frame');
  });
});
