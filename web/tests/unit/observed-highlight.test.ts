/** The Observed highlight (#697) in the web nutshell: reading order, caption,
 *  thumbs, and the headline fallback. Against the exported real-morning
 *  fixtures (`LiveScenarios/*_highlight.json` carries a fixed, grounded line;
 *  the others carry `highlight: null`, as the suite-less server produces). */

import { describe, it, expect, vi } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import en from '../../ts/i18n/locales/en.json';

vi.mock('../../ts/i18n/i18n', () => ({
  t: (key: string, params?: Record<string, string | number>) => {
    let msg = (en as Record<string, string>)[key] ?? key;
    for (const [k, v] of Object.entries(params ?? {})) msg = msg.split(`{${k}}`).join(String(v));
    return msg;
  },
}));

import type { LiveLayer } from '../../ts/store/types';
import {
  highlightCaption,
  highlightHtml,
  highlightRatingKey,
  nutshellHtml,
} from '../../ts/visualization/observed/nutshell-view';

const DIR = resolve(__dirname, '../../../app/flyfun-weather/flyfun-weatherUITests/LiveScenarios');
const load = (name: string): LiveLayer => JSON.parse(readFileSync(resolve(DIR, name), 'utf8')) as LiveLayer;
const highlightFiles = readdirSync(DIR).filter((f) => f.endsWith('_highlight.json')).sort();

const plain = (html: string): string =>
  html.replace(/&amp;/g, '&').replace(/&#39;/g, "'").replace(/&quot;/g, '"');

describe('observed highlight', () => {
  it('has at least one exported highlight fixture', () => {
    expect(highlightFiles.length).toBeGreaterThan(0);
  });

  it.each(highlightFiles)('%s: the highlight, then the alert lines, then the rest', (name) => {
    const glance = load(name).glance!;
    const hl = glance.highlight!;
    expect(hl.text).toBeTruthy();
    const html = nutshellHtml(glance, false, { rateable: true });

    const at = (needle: string): number => plain(html).indexOf(needle);
    const highlightAt = at(hl.text);
    expect(highlightAt).toBeGreaterThan(-1);
    for (const line of glance.lines) {
      const lineAt = at(line.text);
      expect(lineAt).toBeGreaterThan(-1);
      expect(lineAt).toBeGreaterThan(highlightAt);
    }
    // The headline stays (it carries the observation time), under the
    // highlight and the alert lines.
    expect(at(glance.headline)).toBeGreaterThan(highlightAt);
    for (const line of glance.lines.filter((l) => l.alert)) {
      expect(at(glance.headline)).toBeGreaterThan(at(line.text));
    }
    // Caption with the written time, and the thumbs.
    expect(html).toContain('Experimental, still being calibrated. Thanks for flagging issues.');
    expect(html).toContain('written 08:20Z');
    expect(html).toContain('data-hl-thumb="up"');
    expect(html).toContain('data-hl-thumb="down"');
    expect(html).not.toMatch(/undefined|NaN|\[object/);
  });

  it.each(highlightFiles)('%s: the highlight is never styled as an alert', (name) => {
    const block = highlightHtml(load(name).glance!.highlight!);
    expect(block).not.toContain('alert');
  });

  it('without a highlight: the headline, no caption, no thumbs', () => {
    const name = readdirSync(DIR).find((f) => f.endsWith('.json') && !f.endsWith('_highlight.json'))!;
    const glance = load(name).glance!;
    expect(glance.highlight ?? null).toBeNull();
    const html = nutshellHtml(glance, false, { rateable: true });
    expect(html).toContain('data-testid="glance-headline"');
    expect(html).not.toContain('data-testid="glance-highlight"');
    expect(html).not.toContain('Experimental');
    expect(html).not.toContain('data-hl-thumb');
  });

  it('a rated highlight shows thanks instead of the thumbs', () => {
    const glance = load(highlightFiles[0]).glance!;
    const html = nutshellHtml(glance, false, { rateable: true, highlightRated: true });
    expect(html).toContain('Thanks 🙏');
    expect(html).not.toContain('data-hl-thumb');
  });

  it('draws no thumbs when nothing can post the rating', () => {
    const html = nutshellHtml(load(highlightFiles[0]).glance!, false);
    expect(html).toContain('data-testid="glance-highlight"');
    expect(html).not.toContain('data-hl-thumb');
  });

  it('escapes the server text', () => {
    const html = highlightHtml({
      text: 'ZZAA <b>LIFR</b> & gusts', model: 'fixture', facts_hash: 'h', generated_at: '2026-10-09T09:05:00Z',
    });
    expect(html).toContain('ZZAA &lt;b&gt;LIFR&lt;/b&gt; &amp; gusts');
  });

  it('caption drops the written time when it is unparseable', () => {
    const cap = highlightCaption({ text: 'x', model: 'm', facts_hash: 'h', generated_at: 'nope' });
    expect(cap).toBe('Experimental, still being calibrated. Thanks for flagging issues.');
  });

  it('dedups a rating on flight and rated line', () => {
    const hl = { text: 'x', model: 'm', facts_hash: 'abc', generated_at: '2026-10-09T09:05:00Z' };
    expect(highlightRatingKey('zz-flight', hl)).toBe('zz-flight|abc');
  });
});
