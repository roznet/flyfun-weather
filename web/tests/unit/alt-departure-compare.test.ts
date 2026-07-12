/** Tests for the alternate-departure-time comparison chip (#392).
 *
 * The chip tells a pilot whether an alternate departure time is Better / Same /
 * Worse than the planned one. It used to tally red/amber over each manifest as a
 * whole, counting UNAVAILABLE as neither — so an alt time where *nothing graded*
 * scored {red: 0, amber: 0}, beat a planned time carrying real hazards, and
 * rendered a green "Better" chip. A slot we have no data for presented as the
 * better time to fly: the exact defect #392 exists to remove, one hop below the
 * assessment itself.
 *
 * The rule now mirrors the backend's own candidate diff (`tasks/time_scan.py`
 * `_compare`): only advisories that BOTH sides graded are comparable, and if
 * nothing is comparable there is no verdict to give.
 */

import { describe, it, expect } from 'vitest';
import { compareAdvisories } from '../../ts/helpers/alt-departure-compare';
import type { RouteAdvisoriesManifest } from '../../ts/types/advisories';

type Status = 'green' | 'amber' | 'red' | 'unavailable';

function manifest(statuses: Record<string, Status>): RouteAdvisoriesManifest {
  return {
    advisories: Object.entries(statuses).map(([advisory_id, aggregate_status]) => ({
      advisory_id,
      aggregate_status,
      per_model: [],
    })),
    catalog: [],
  } as unknown as RouteAdvisoriesManifest;
}

describe('compareAdvisories', () => {
  it('does not call a no-data alt time "Better" than a hazardous planned time', () => {
    // The regression. Before the fix this returned 'better'.
    const alt = manifest({ icing: 'unavailable', convective: 'unavailable' });
    const planned = manifest({ icing: 'red', convective: 'amber' });

    expect(compareAdvisories(alt, planned)).toBeNull();
    expect(compareAdvisories(alt, planned)).not.toBe('better');
  });

  it('withholds a verdict when the two sides share nothing gradeable', () => {
    const alt = manifest({ icing: 'green' });
    const planned = manifest({ convective: 'red' }); // no overlapping id
    expect(compareAdvisories(alt, planned)).toBeNull();
  });

  it('still calls a genuinely calmer alt time Better', () => {
    // The fix must not blank a real improvement — that would trade one false
    // reading for another.
    const alt = manifest({ icing: 'green', convective: 'green' });
    const planned = manifest({ icing: 'red', convective: 'amber' });
    expect(compareAdvisories(alt, planned)).toBe('better');
  });

  it('still calls a genuinely rougher alt time Worse', () => {
    const alt = manifest({ icing: 'red' });
    const planned = manifest({ icing: 'green' });
    expect(compareAdvisories(alt, planned)).toBe('worse');
  });

  it('reports Same for identical grades', () => {
    const alt = manifest({ icing: 'amber', convective: 'green' });
    const planned = manifest({ icing: 'amber', convective: 'green' });
    expect(compareAdvisories(alt, planned)).toBe('same');
  });

  it('judges only on what both sides graded, ignoring one-sided gaps', () => {
    // icing is comparable (green vs red -> better). convective is UNAVAILABLE on
    // the alt side, so it must not count as "no hazard there" and must not be
    // allowed to swing the verdict.
    const alt = manifest({ icing: 'green', convective: 'unavailable' });
    const planned = manifest({ icing: 'red', convective: 'red' });
    expect(compareAdvisories(alt, planned)).toBe('better');
  });

  it('a gap cannot manufacture an improvement on its own', () => {
    // Both sides grade icing identically; the only "difference" is the alt's gap.
    // That is not an improvement — it is an unknown.
    const alt = manifest({ icing: 'amber', convective: 'unavailable' });
    const planned = manifest({ icing: 'amber', convective: 'red' });
    expect(compareAdvisories(alt, planned)).toBe('same');
  });

  it('returns null when either manifest is absent', () => {
    expect(compareAdvisories(null, manifest({ icing: 'green' }))).toBeNull();
    expect(compareAdvisories(manifest({ icing: 'green' }), null)).toBeNull();
  });
});
