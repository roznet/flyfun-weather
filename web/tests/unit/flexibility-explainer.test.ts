/** Tests for the flexibility-explainer gate + first-time trigger (#352). */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock the network + popup dependencies so the trigger can be exercised in the
// node test env (no DOM / no real fetch).
vi.mock('../../ts/adapters/preferences-adapter', () => ({
  fetchUsageSummary: vi.fn(),
}));
vi.mock('../../ts/components/info-popup', () => ({
  showPopupContent: vi.fn(),
  hideMetricInfo: vi.fn(),
}));

import { fetchUsageSummary } from '../../ts/adapters/preferences-adapter';
import { showPopupContent } from '../../ts/components/info-popup';
import {
  shouldShowFlexibilityExplainer,
  maybeShowFlexibilityExplainer,
  flexExplainerAcked,
} from '../../ts/components/flexibility-explainer';

const mockedFetch = vi.mocked(fetchUsageSummary);
const mockedShow = vi.mocked(showPopupContent);

describe('shouldShowFlexibilityExplainer', () => {
  it('shows when never used and not acked this session', () => {
    expect(shouldShowFlexibilityExplainer({ timeScanUsed: false, sessionAcked: false })).toBe(true);
  });

  it('hides when acked this session even if never used', () => {
    expect(shouldShowFlexibilityExplainer({ timeScanUsed: false, sessionAcked: true })).toBe(false);
  });

  it('hides once the user has genuinely run a scan (durable)', () => {
    expect(shouldShowFlexibilityExplainer({ timeScanUsed: true, sessionAcked: false })).toBe(false);
    expect(shouldShowFlexibilityExplainer({ timeScanUsed: true, sessionAcked: true })).toBe(false);
  });
});

describe('maybeShowFlexibilityExplainer', () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => { store.set(k, String(v)); },
      removeItem: (k: string) => { store.delete(k); },
    });
    // openFlexibilityExplainer looks up the "Got it" button; null is fine here.
    vi.stubGlobal('document', { querySelector: () => null });
    mockedFetch.mockReset();
    mockedShow.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('opens the modal once when the user has never run a scan', async () => {
    mockedFetch.mockResolvedValue({ time_scan_used: false } as never);
    await maybeShowFlexibilityExplainer();
    expect(mockedShow).toHaveBeenCalledTimes(1);
    expect(flexExplainerAcked()).toBe(true);
  });

  it('does not open the modal when the user has already used timing scan', async () => {
    mockedFetch.mockResolvedValue({ time_scan_used: true } as never);
    await maybeShowFlexibilityExplainer();
    expect(mockedShow).not.toHaveBeenCalled();
    // Still cached for the session so later dropdown toggles skip the fetch.
    expect(flexExplainerAcked()).toBe(true);
  });

  it('short-circuits without fetching when already acked this session', async () => {
    sessionStorage.setItem('wb_flex_explainer_ack', '1');
    await maybeShowFlexibilityExplainer();
    expect(mockedFetch).not.toHaveBeenCalled();
    expect(mockedShow).not.toHaveBeenCalled();
  });

  it('sets the ack flag before awaiting, so a concurrent second call bails (no double-fire)', async () => {
    let resolveFetch!: (v: { time_scan_used: boolean }) => void;
    mockedFetch.mockReturnValue(
      new Promise<{ time_scan_used: boolean }>((r) => { resolveFetch = r; }) as never,
    );

    const p1 = maybeShowFlexibilityExplainer();
    // The flag is set synchronously (before the fetch resolves), so a second
    // near-simultaneous trigger sees it and returns immediately.
    expect(flexExplainerAcked()).toBe(true);
    const p2 = maybeShowFlexibilityExplainer();

    resolveFetch({ time_scan_used: false });
    await Promise.all([p1, p2]);

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    expect(mockedShow).toHaveBeenCalledTimes(1);
  });
});
