/** The "Now" tab's `now.layers` key (#796). */

import { describe, it, expect } from 'vitest';
import {
  DEFAULT_NOW_LAYERS, NOW_URL_SCHEMA, decodeNowLayers, encodeNowLayers, type NowLayers,
} from '../../ts/visualization/now-url';

const parse = (raw: string) => NOW_URL_SCHEMA['now.layers'].codec.parse(raw);

describe('now.layers', () => {
  it('elides the default set, so a bare link stays bare', () => {
    expect(encodeNowLayers({ ...DEFAULT_NOW_LAYERS })).toBe('');
  });

  it('carries a custom set in a fixed order', () => {
    const layers: NowLayers = { ...DEFAULT_NOW_LAYERS, satellite: false, rain: true };
    expect(encodeNowLayers(layers)).toBe('airports,radar,lightning,cells,rain');
  });

  it('distinguishes "everything off" from "no key"', () => {
    const none = Object.fromEntries(Object.keys(DEFAULT_NOW_LAYERS).map((k) => [k, false])) as NowLayers;
    expect(encodeNowLayers(none)).toBe('none');
    expect(decodeNowLayers('none')).toEqual(none);
  });

  it('round-trips', () => {
    const layers: NowLayers = { airports: false, radar: true, satellite: false, lightning: true, cells: true, rain: false };
    expect(decodeNowLayers(encodeNowLayers(layers))).toEqual(layers);
  });

  it('drops unknown names next to known ones, and rejects a list of only unknowns', () => {
    expect(decodeNowLayers('radar,hail')).toEqual({
      airports: false, radar: true, satellite: false, lightning: false, cells: false, rain: false,
    });
    expect(decodeNowLayers('hail,fog')).toBeNull();
    expect(parse('hail,fog')).toBeUndefined();
  });

  it('keeps a link that spells out the default set, so it still beats localStorage', () => {
    expect(parse('cells,radar,airports,lightning,satellite')).toBe('airports,radar,satellite,lightning,cells');
  });
});
