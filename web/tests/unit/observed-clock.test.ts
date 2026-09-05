import { afterEach, describe, expect, it, vi } from 'vitest';
import * as clock from '../../ts/visualization/observed-time';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('observed display clock lifecycle', () => {
  it('floors elapsed minutes and marks observations stale from 30 minutes', () => {
    expect(clock.observationTimeText('2026-08-25T14:00:30Z', new Date('2026-08-25T14:30:29Z')))
      .toBe('14:00Z · 29 min old');
    expect(clock.observationTimeText('2026-08-25T14:00:00Z', new Date('2026-08-25T14:30:00Z')))
      .toBe('14:00Z · 30 min old · stale');
  });

  it('updates elapsed labels while visible and on foreground, without changing the source or fetching', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-25T14:05:00Z'));
    const document = Object.assign(new EventTarget(), { visibilityState: 'visible' });
    const window = new EventTarget();
    vi.stubGlobal('document', document);
    vi.stubGlobal('window', window);
    const fetch = vi.fn(() => { throw new Error('clock must not fetch'); });
    vi.stubGlobal('fetch', fetch);
    const source = Object.freeze({ validTime: '2026-08-25T14:05:00Z' });
    const labels: string[] = [];
    expect(typeof clock.observeDisplayClock).toBe('function');
    const stop = clock.observeDisplayClock(() => labels.push(clock.observationTimeText(source.validTime)));
    vi.advanceTimersByTime(60000);
    expect(labels.at(-1)).toBe('14:05Z · 1 min old');
    document.visibilityState = 'hidden';
    const count = labels.length;
    vi.advanceTimersByTime(120000);
    expect(labels).toHaveLength(count);
    document.visibilityState = 'visible';
    document.dispatchEvent(new Event('visibilitychange'));
    expect(labels.at(-1)).toBe('14:05Z · 3 min old');
    vi.setSystemTime(new Date('2026-08-26T14:05:00Z'));
    window.dispatchEvent(new Event('pageshow'));
    expect(labels.at(-1)).toBe('2026-08-25 14:05Z · 1440 min old · stale');
    stop();
    const stopped = labels.length;
    vi.advanceTimersByTime(60000);
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('pageshow'));
    expect(labels).toHaveLength(stopped);
    expect(fetch).not.toHaveBeenCalled();
  });
});
