import { afterEach, expect, it, vi } from 'vitest';
import * as geometry from '../../ts/visualization/route-map/observed-overlay-geometry';
import { ObservedFlashRequests, ObservedImageRequests } from '../../ts/visualization/route-map/observed-request-state';

afterEach(() => vi.unstubAllGlobals());

it('labels exactly the returned PNG, not the old briefing snapshot', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new Uint8Array([1, 2]), {
    headers: { 'X-Observed-Valid-Time': '2026-09-05T10:20:00Z', 'X-Observed-Attribution': encodeURIComponent('Météo-France') },
  })));
  expect(typeof geometry.fetchObservedImage).toBe('function');
  const image = await geometry.fetchObservedImage('/actual-image', {
    source: 'opera_dbzh', label: 'Radar', validTime: '2026-08-25T14:05:00Z', ageMinutes: 0, windowMinutes: 10, attribution: 'old',
  });
  expect(image.field.validTime).toBe('2026-09-05T10:20:00Z');
  expect(image.field.attribution).toBe('Météo-France');
  expect(image.blob.size).toBe(2);
});

it('does not substitute snapshot time when an image has no time header', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new Uint8Array([1]))));
  expect(typeof geometry.fetchObservedImage).toBe('function');
  const image = await geometry.fetchObservedImage('/actual-image', {
    source: 'opera_dbzh', label: 'Radar', validTime: '2026-08-25T14:05:00Z', ageMinutes: 0, windowMinutes: 10, attribution: 'old',
  });
  expect(geometry.formatBadge(image.field)).toContain('age unknown');
  expect(image.field.attribution).not.toBe('old');
});

it.each([
  ['missing', {}, 0],
  ['invalid', { 'X-Observed-Window-Minutes': 'not-a-number' }, 0],
  ['actual', { 'X-Observed-Window-Minutes': '15' }, 15],
] as const)('uses the %s PNG acquisition window instead of the snapshot window', async (_case, headers, expected) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new Uint8Array([1]), { headers })));
  const image = await geometry.fetchObservedImage('/actual-image', {
    source: 'opera_rate', label: 'Rain rate', validTime: '2026-08-25T14:05:00Z', ageMinutes: 0, windowMinutes: 10, attribution: 'old',
  });
  expect(image.field.windowMinutes).toBe(expected);
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const badgeField = (label: string): geometry.ObservedBadgeField => ({
  source: 'opera_dbzh', label, validTime: '2026-09-05T10:20:00Z', ageMinutes: 0, windowMinutes: 10, attribution: '',
});

it.each([
  ['', { reflectivity: true, rainRate: true, cloudTops: true, lightning: true }, ''],
  ['eumetsat_ctth_temp', { reflectivity: true, rainRate: false, cloudTops: true, lightning: false }, 'eumetsat_ctth_temp'],
  ['eumetsat_li', { reflectivity: true, rainRate: false, cloudTops: false, lightning: true }, 'eumetsat_li'],
  ['eumetsat_ctth', { reflectivity: true, rainRate: true, cloudTops: false, lightning: false }, 'opera_dbzh'],
  ['opera_dbzh', { reflectivity: false, rainRate: true, cloudTops: false, lightning: false }, 'opera_rate'],
  ['opera_rate', { reflectivity: false, rainRate: false, cloudTops: false, lightning: false }, ''],
] as const)('honours selected source %s, including explicit None, with fallback only when unavailable', (chosen, available, expected) => {
  expect(typeof geometry.resolveObservedOverlay).toBe('function');
  expect(geometry.resolveObservedOverlay(chosen, available)).toBe(expected);
});

it('accepts only the newest raster generation across an A to B to A reselect', async () => {
  const a1 = deferred<{ blob: Blob; field: geometry.ObservedBadgeField }>();
  const b = deferred<{ blob: Blob; field: geometry.ObservedBadgeField }>();
  const a2 = deferred<{ blob: Blob; field: geometry.ObservedBadgeField }>();
  const loads = [a1, b, a2];
  const requests = new ObservedImageRequests(() => loads.shift()!.promise, (blob) => `blob:${blob.size}`, vi.fn());
  const changed = vi.fn();

  requests.select('A', '/a', badgeField('A old'), changed);
  requests.select('B', '/b', badgeField('B'), changed);
  requests.select('A', '/a', badgeField('A newest'), changed);
  a2.resolve({ blob: new Blob(['new']), field: badgeField('A newest') });
  await vi.waitFor(() => expect(requests.current().field?.label).toBe('A newest'));
  a1.resolve({ blob: new Blob(['old']), field: badgeField('A old') });
  b.resolve({ blob: new Blob(['b']), field: badgeField('B') });
  await Promise.all([a1.promise, b.promise]);

  expect(requests.current().field?.label).toBe('A newest');
  expect(requests.current().url).toBe('blob:3');
});

it('latches a raster failure until explicit retry and revokes replacements and destroy', async () => {
  const first = deferred<{ blob: Blob; field: geometry.ObservedBadgeField }>();
  const second = deferred<{ blob: Blob; field: geometry.ObservedBadgeField }>();
  const third = deferred<{ blob: Blob; field: geometry.ObservedBadgeField }>();
  const load = vi.fn(() => [first, second, third][load.mock.calls.length - 1].promise);
  const revoke = vi.fn();
  const requests = new ObservedImageRequests(load, (blob) => `blob:${blob.size}`, revoke);
  const changed = vi.fn();

  requests.select('A', '/a', badgeField('A'), changed);
  first.reject(new Error('offline'));
  await vi.waitFor(() => expect(requests.current().failed).toBe(true));
  requests.select('A', '/a', badgeField('A'), changed);
  expect(load).toHaveBeenCalledTimes(1);

  requests.retryFailed();
  requests.select('A', '/a', badgeField('A'), changed);
  second.resolve({ blob: new Blob(['one']), field: badgeField('A') });
  await vi.waitFor(() => expect(requests.current().url).toBe('blob:3'));
  requests.select('B', '/b', badgeField('B'), changed);
  third.resolve({ blob: new Blob(['longer']), field: badgeField('B') });
  await vi.waitFor(() => expect(requests.current().url).toBe('blob:6'));
  expect(revoke).toHaveBeenCalledWith('blob:3');
  requests.destroy();
  expect(revoke).toHaveBeenCalledWith('blob:6');
});

it('latches a failed flash request before its rerender and retries only when explicitly reset', async () => {
  const requests = new ObservedFlashRequests<number>();
  const failed = deferred<number>();
  const retried = deferred<number>();
  const load = vi.fn(() => load.mock.calls.length === 1 ? failed.promise : retried.promise);
  const rerender = vi.fn();

  requests.select('same-frame-and-bounds', load, rerender);
  failed.reject(new Error('offline'));
  await vi.waitFor(() => expect(rerender).toHaveBeenCalledTimes(1));
  requests.select('same-frame-and-bounds', load, rerender);
  expect(load).toHaveBeenCalledTimes(1);

  requests.retryFailed();
  requests.select('same-frame-and-bounds', load, rerender);
  retried.resolve(42);
  await vi.waitFor(() => expect(requests.current('same-frame-and-bounds')).toBe(42));
  expect(load).toHaveBeenCalledTimes(2);
});

it('invalidates a pending flash completion when its map is closed and starts fresh on reopen', async () => {
  const requests = new ObservedFlashRequests<number>();
  const old = deferred<number>();
  const current = deferred<number>();
  const oldRerender = vi.fn();
  const currentRerender = vi.fn();
  requests.select('A', () => old.promise, oldRerender);
  requests.clear();
  requests.select('A', () => current.promise, currentRerender);
  old.resolve(1);
  await old.promise;
  await Promise.resolve();
  expect(requests.current('A')).toBeNull();
  expect(oldRerender).not.toHaveBeenCalled();
  current.resolve(2);
  await vi.waitFor(() => expect(currentRerender).toHaveBeenCalledTimes(1));
  expect(requests.current('A')).toBe(2);
  expect(oldRerender).not.toHaveBeenCalled();
});
