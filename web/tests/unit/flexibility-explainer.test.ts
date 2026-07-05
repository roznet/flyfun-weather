/** Tests for the flexibility-explainer first-time gate predicate (#352). */

import { describe, it, expect } from 'vitest';
import { shouldShowFlexibilityExplainer } from '../../ts/components/flexibility-explainer';

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
