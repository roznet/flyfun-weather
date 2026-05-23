/** Tests for render-time NWP→DD layer substitution.
 *
 *  The fallback substitutes same-style DD cloud / Ogimet-DD icing layers when
 *  the selected model lacks native NWP data — purely in a throwaway effective
 *  map, never mutating the stored preference.
 */

import { describe, it, expect } from 'vitest';
import {
  applyNwpFallback,
  getSubstitutedLayers,
  getDdSubstituteId,
} from '../../ts/visualization/cross-section/nwp-fallback';

// What getUnavailableLayers adds when a model has no native NWP cloud data.
const NWP_UNAVAILABLE = new Set([
  'nwp-cloud-bands',
  'icing-ogimet-nwp-bands',
  'ieng-icing-bands',
]);

describe('applyNwpFallback — clouds', () => {
  it('NWP available: no DD injected', () => {
    const enabled = { 'soft-nwp-cloud-bands': true };
    const effective = applyNwpFallback(enabled, new Set());
    expect(effective['soft-cloud-bands']).toBeUndefined();
    expect(getSubstitutedLayers(enabled, effective).size).toBe(0);
  });

  it('NWP unavailable + soft NWP enabled → soft DD injected, NWP variant disabled', () => {
    const enabled = { 'soft-nwp-cloud-bands': true };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(effective['soft-cloud-bands']).toBe(true);
    // The replaced NWP variant must not stay enabled (renderer + panel state).
    expect(effective['soft-nwp-cloud-bands']).toBe(false);
    expect(getSubstitutedLayers(enabled, effective).has('soft-cloud-bands')).toBe(true);
  });

  it('NWP unavailable + natural NWP enabled → natural DD injected', () => {
    const enabled = { 'nwp-cloud-bands': true };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    // The canonical NWP id is disabled by the unavailable loop...
    expect(effective['nwp-cloud-bands']).toBe(false);
    // ...and its DD pair is substituted in.
    expect(effective['cloud-bands']).toBe(true);
  });

  it('NWP unavailable + square NWP enabled → square DD injected, NWP variant disabled', () => {
    const enabled = { 'square-nwp-cloud-bands': true };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(effective['square-cloud-bands']).toBe(true);
    expect(effective['square-nwp-cloud-bands']).toBe(false);
  });

  it('explicit DD choice stays and is not flagged as substituted', () => {
    const enabled = { 'soft-nwp-cloud-bands': true, 'soft-cloud-bands': true };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(effective['soft-cloud-bands']).toBe(true);
    // User already enabled DD — it renders, but isn't an auto-substitute.
    expect(getSubstitutedLayers(enabled, effective).has('soft-cloud-bands')).toBe(false);
  });

  it('user genuinely chose DD only (NWP off): no injection, nothing substituted', () => {
    const enabled = { 'soft-cloud-bands': true, 'soft-nwp-cloud-bands': false };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(effective['soft-cloud-bands']).toBe(true);
    expect(getSubstitutedLayers(enabled, effective).size).toBe(0);
  });

  it('switching back to an NWP-capable model drops the substitute', () => {
    const enabled = { 'soft-nwp-cloud-bands': true };
    // No-NWP model substitutes DD...
    const downgraded = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(downgraded['soft-cloud-bands']).toBe(true);
    // ...NWP-capable model (empty unavailable) restores NWP, no DD substitute.
    const restored = applyNwpFallback(enabled, new Set());
    expect(restored['soft-cloud-bands']).toBeUndefined();
    expect(restored['soft-nwp-cloud-bands']).toBe(true);
  });
});

describe('applyNwpFallback — icing', () => {
  it('Ogimet-NWP unavailable + enabled → Ogimet-DD injected', () => {
    const enabled = { 'icing-ogimet-nwp-bands': true };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(effective['icing-ogimet-nwp-bands']).toBe(false);
    expect(effective['icing-bands']).toBe(true);
    expect(getSubstitutedLayers(enabled, effective).has('icing-bands')).toBe(true);
  });

  it('explicit Ogimet-DD choice stays, not flagged substituted', () => {
    const enabled = { 'icing-ogimet-nwp-bands': true, 'icing-bands': true };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(effective['icing-bands']).toBe(true);
    expect(getSubstitutedLayers(enabled, effective).has('icing-bands')).toBe(false);
  });

  it('Ogimet-NWP disabled by user → no DD injection', () => {
    const enabled = { 'icing-ogimet-nwp-bands': false };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(effective['icing-bands']).toBeUndefined();
  });

  it('IENG has no DD pair: disabled, nothing injected', () => {
    const enabled = { 'ieng-icing-bands': true };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(effective['ieng-icing-bands']).toBe(false);
    // No IENG-DD layer exists to inject.
    expect(getSubstitutedLayers(enabled, effective).size).toBe(0);
  });
});

describe('applyNwpFallback — invariants', () => {
  it('never mutates the stored preference', () => {
    const enabled = { 'soft-nwp-cloud-bands': true, 'icing-ogimet-nwp-bands': true };
    const snapshot = { ...enabled };
    applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(enabled).toEqual(snapshot);
  });

  it('does not touch terrain (force-on lives in the render path, not the helper)', () => {
    const enabled = { terrain: false };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    expect(effective['terrain']).toBe(false);
  });

  it('disables every unavailable layer', () => {
    const enabled = { 'nwp-cloud-bands': true, 'icing-ogimet-nwp-bands': true, 'ieng-icing-bands': true };
    const effective = applyNwpFallback(enabled, NWP_UNAVAILABLE);
    for (const id of NWP_UNAVAILABLE) expect(effective[id]).toBe(false);
  });
});

describe('getDdSubstituteId', () => {
  it('maps each NWP cloud style to its same-style DD layer', () => {
    expect(getDdSubstituteId('soft-nwp-cloud-bands')).toBe('soft-cloud-bands');
    expect(getDdSubstituteId('nwp-cloud-bands')).toBe('cloud-bands');
    expect(getDdSubstituteId('square-nwp-cloud-bands')).toBe('square-cloud-bands');
  });

  it('maps Ogimet-NWP icing to Ogimet-DD', () => {
    expect(getDdSubstituteId('icing-ogimet-nwp-bands')).toBe('icing-bands');
  });

  it('returns null for DD layers, IENG, and non-cloud/icing layers', () => {
    expect(getDdSubstituteId('soft-cloud-bands')).toBeNull();
    expect(getDdSubstituteId('ieng-icing-bands')).toBeNull();
    expect(getDdSubstituteId('cat-bands')).toBeNull();
    expect(getDdSubstituteId('terrain')).toBeNull();
  });
});
