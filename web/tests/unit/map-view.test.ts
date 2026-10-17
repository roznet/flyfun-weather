/** Viewport codec + binding for the maps page's `*.view` keys (#796). */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  bindMapView, formatMapView, mapViewCodec, parseMapView, type ViewMap,
} from '../../ts/utils/map-view';

describe('map view codec', () => {
  it('round-trips lat,lon,zoom at 2 dp and integer zoom', () => {
    expect(parseMapView('45.76,4.84,8')).toEqual({ lat: 45.76, lon: 4.84, zoom: 8 });
    expect(formatMapView({ lat: 45.7612, lon: 4.8357, zoom: 8 })).toBe('45.76,4.84,8');
    expect(formatMapView({ lat: -0.001, lon: 0, zoom: 5 })).toBe('0.00,0.00,5');
  });

  it('canonicalises on read', () => {
    expect(mapViewCodec.parse('45.7612,4.8357,7.6')).toBe('45.76,4.84,8');
  });

  it.each([
    'garbage', '', '1,2', '1,2,3,4', '91,0,5', '0,181,5', '45,4,0', '45,4,30', 'a,b,c', ',,5',
  ])('rejects %j so the page falls back to the default view', (raw) => {
    expect(parseMapView(raw)).toBeNull();
    expect(mapViewCodec.parse(raw)).toBeUndefined();
  });
});

/** A stub Leaflet map: center/zoom plus a single moveend listener. */
function stubMap(lat = 48, lng = 10, zoom = 5) {
  const listeners: Array<() => void> = [];
  const state = { lat, lng, zoom };
  const map = {
    getCenter: () => ({ lat: state.lat, lng: state.lng }),
    getZoom: () => state.zoom,
    setView: vi.fn((c: [number, number], z: number) => { state.lat = c[0]; state.lng = c[1]; state.zoom = z; return map; }),
    on: vi.fn((_e: string, fn: () => void) => { listeners.push(fn); return map; }),
    off: vi.fn(() => map),
  };
  const move = (la: number, lo: number, z: number) => {
    state.lat = la; state.lng = lo; state.zoom = z;
    listeners.forEach((fn) => fn());
  };
  return { map: map as unknown as ViewMap, move, setView: map.setView };
}

describe('bindMapView', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('leaves no value while the map sits at its initial view', () => {
    const { map } = stubMap();
    const b = bindMapView(map, { initial: '', onChange: vi.fn() });
    expect(b.current()).toBe('');
  });

  it('applies a linked view and reports it', () => {
    const { map, setView } = stubMap();
    const b = bindMapView(map, { initial: '45.76,4.84,8', onChange: vi.fn() });
    expect(setView).toHaveBeenCalledWith([45.76, 4.84], 8, { animate: false });
    expect(b.current()).toBe('45.76,4.84,8');
  });

  it('ignores a garbage linked view', () => {
    const { map, setView } = stubMap();
    const b = bindMapView(map, { initial: 'nope', onChange: vi.fn() });
    expect(setView).not.toHaveBeenCalled();
    expect(b.current()).toBe('');
  });

  it('notifies once per settled move, and not for a move that keeps the view', () => {
    const { map, move } = stubMap();
    const onChange = vi.fn();
    const b = bindMapView(map, { onChange });
    move(46, 5, 6);
    move(45.76, 4.84, 8);
    vi.advanceTimersByTime(300);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(b.current()).toBe('45.76,4.84,8');
    // invalidateSize() style moveend: same centre, nothing to write.
    move(45.761, 4.839, 8);
    vi.advanceTimersByTime(300);
    expect(onChange).toHaveBeenCalledTimes(1);
    // Back to the start: the key goes away, which is a change.
    move(48, 10, 5);
    vi.advanceTimersByTime(300);
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(b.current()).toBe('');
  });

  it('elides against an explicit default rather than the bind-time view', () => {
    const { map } = stubMap(50, 2, 7);
    const b = bindMapView(map, { onChange: vi.fn(), defaultView: { lat: 48, lon: 10, zoom: 5 } });
    expect(b.current()).toBe('50.00,2.00,7');
  });
});
