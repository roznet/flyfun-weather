/** Tests for the generic URL state helper. */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { createUrlState } from '../../ts/utils/url-state';

// jsdom-light: vitest's `node` env doesn't provide window/history, so we
// stub the bits the helper touches and reset between tests.
function setLocation(search: string): void {
  (globalThis as any).window = {
    location: { pathname: '/maps.html', search, hash: '' },
    history: {
      replaceState: vi.fn((_state: any, _title: string, url: string) => {
        const qsIdx = url.indexOf('?');
        const hashIdx = url.indexOf('#');
        const end = hashIdx === -1 ? url.length : hashIdx;
        (globalThis as any).window.location.pathname = url.slice(0, qsIdx === -1 ? end : qsIdx);
        (globalThis as any).window.location.search = qsIdx === -1 ? '' : url.slice(qsIdx, end);
        (globalThis as any).window.location.hash = hashIdx === -1 ? '' : url.slice(hashIdx);
      }),
    },
  };
}

beforeEach(() => setLocation(''));

describe('createUrlState', () => {
  it('returns defaults when URL has no params', () => {
    const s = createUrlState({
      tab: { default: 'forecast' },
      day: { default: 0 },
    });
    expect(s.read()).toEqual({ tab: 'forecast', day: 0 });
  });

  it('reads explicit values from the URL', () => {
    setLocation('?tab=verification&day=2');
    const s = createUrlState({
      tab: { default: 'forecast' },
      day: { default: 0 },
    });
    expect(s.read()).toEqual({ tab: 'verification', day: 2 });
  });

  it('parses numbers via the inferred int codec', () => {
    setLocation('?hour=15');
    const s = createUrlState({ hour: { default: 12 } });
    expect(s.read()).toEqual({ hour: 15 });
  });

  it('falls back to default when value is not in the whitelist', () => {
    setLocation('?model=garbage');
    const s = createUrlState({
      model: { default: 'worst', values: ['worst', 'majority'] },
    });
    expect(s.read()).toEqual({ model: 'worst' });
  });

  it('falls back to default when value fails to parse as a number', () => {
    setLocation('?day=foo');
    const s = createUrlState({ day: { default: 0 } });
    expect(s.read()).toEqual({ day: 0 });
  });

  it('elides defaults when writing', () => {
    const s = createUrlState({
      tab: { default: 'forecast' },
      day: { default: 0 },
      hour: { default: 12 },
    });
    s.write({ tab: 'forecast', day: 2, hour: 12 });
    expect(window.location.search).toBe('?day=2');
  });

  it('writes a bare path when every field is at its default', () => {
    setLocation('?day=2');  // start with a non-default value already in URL
    const s = createUrlState({
      tab: { default: 'forecast' },
      day: { default: 0 },
    });
    s.write({ tab: 'forecast', day: 0 });
    expect(window.location.search).toBe('');
  });

  it('preserves params not declared in the schema', () => {
    setLocation('?tracking=campaign-x');
    const s = createUrlState({ day: { default: 0 } });
    s.write({ day: 1 });
    // Order isn't guaranteed by URLSearchParams across all engines, but
    // both keys must appear and tracking must be untouched.
    const params = new URLSearchParams(window.location.search);
    expect(params.get('tracking')).toBe('campaign-x');
    expect(params.get('day')).toBe('1');
  });

  it('roundtrips through write/read', () => {
    const s = createUrlState({
      tab: { default: 'forecast' },
      day: { default: 0 },
      model: { default: 'worst', values: ['worst', 'majority', 'gfs'] },
    });
    s.write({ tab: 'verification', day: 3, model: 'gfs' });
    expect(s.read()).toEqual({ tab: 'verification', day: 3, model: 'gfs' });
  });
});
