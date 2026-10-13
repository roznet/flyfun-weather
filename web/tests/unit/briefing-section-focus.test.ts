import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  FOCUSABLE_SECTIONS,
  NAV_GROUPS,
  isFocusableSection,
  parseChromeParam,
  parseSectionMessage,
  parseSectionParam,
} from '../../ts/managers/sidebar-layout';

// `?section=<key>` and the framing page's `wb-frame:section` message focus the
// briefing on one section. Both must accept exactly the known section keys.
describe('focusable sections', () => {
  const html = readFileSync(resolve(__dirname, '../../briefing.html'), 'utf8');
  const pageSections = Array.from(html.matchAll(/data-section="([^"]+)"/g), (m) => m[1]);

  it('every focusable key is a section on the page', () => {
    for (const key of FOCUSABLE_SECTIONS) expect(pageSections).toContain(key);
  });

  it('covers every nav section plus the advisories', () => {
    for (const key of NAV_GROUPS.flatMap((g) => g.keys)) expect(FOCUSABLE_SECTIONS).toContain(key);
    expect(FOCUSABLE_SECTIONS).toContain('advisories');
  });

  it('rejects unknown, empty and non-string keys', () => {
    expect(isFocusableSection('observations')).toBe(true);
    expect(isFocusableSection('nope')).toBe(false);
    expect(isFocusableSection('')).toBe(false);
    expect(isFocusableSection(null)).toBe(false);
    expect(isFocusableSection(undefined)).toBe(false);
    expect(isFocusableSection(3)).toBe(false);
    // An attribute-selector injection is just an unknown key.
    expect(isFocusableSection('skewt"],[data-x="')).toBe(false);
    // Case matters, as in data-section.
    expect(isFocusableSection('SkewT')).toBe(false);
  });
});

describe('parseSectionParam', () => {
  const p = (q: string) => parseSectionParam(new URLSearchParams(q));

  it('returns a known key', () => {
    expect(p('flight=x&section=skewt')).toBe('skewt');
    expect(p('section=sigmets')).toBe('sigmets');
    expect(p('section=advisories')).toBe('advisories');
  });

  it('trims whitespace', () => {
    expect(p('section=%20alternates%20')).toBe('alternates');
  });

  it('is null when absent, empty or unknown', () => {
    expect(p('flight=x')).toBeNull();
    expect(p('section=')).toBeNull();
    expect(p('section=bogus')).toBeNull();
  });

  it('ignores view= (a separate, scroll-only deep link)', () => {
    expect(p('view=skewt')).toBeNull();
    expect(p('view=skewt-compare&section=skewt')).toBe('skewt');
  });
});

describe('parseSectionMessage', () => {
  it('focuses a known section', () => {
    expect(parseSectionMessage({ type: 'wb-frame:section', section: 'observations' })).toEqual({ section: 'observations' });
  });

  it('exits focus on section: null', () => {
    expect(parseSectionMessage({ type: 'wb-frame:section', section: null })).toEqual({ section: null });
  });

  it('ignores unknown keys and malformed messages', () => {
    expect(parseSectionMessage({ type: 'wb-frame:section', section: 'bogus' })).toBeNull();
    expect(parseSectionMessage({ type: 'wb-frame:section' })).toBeNull();
    expect(parseSectionMessage({ type: 'wb-frame:section', section: 42 })).toBeNull();
    expect(parseSectionMessage({ type: 'wb-embed:point', section: 'skewt' })).toBeNull();
    expect(parseSectionMessage({ section: 'skewt' })).toBeNull();
    expect(parseSectionMessage('wb-frame:section')).toBeNull();
    expect(parseSectionMessage(null)).toBeNull();
    expect(parseSectionMessage(undefined)).toBeNull();
  });
});

describe('parseChromeParam', () => {
  it('is on only for chrome=none', () => {
    expect(parseChromeParam(new URLSearchParams('chrome=none'))).toBe(true);
    expect(parseChromeParam(new URLSearchParams('chrome= none '))).toBe(true);
    expect(parseChromeParam(new URLSearchParams(''))).toBe(false);
    expect(parseChromeParam(new URLSearchParams('chrome=full'))).toBe(false);
    expect(parseChromeParam(new URLSearchParams('chrome=None'))).toBe(false);
  });
});
