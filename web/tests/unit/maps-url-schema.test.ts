/** Per-tab URL schemas on the maps page compose without collisions (#796). */

import { describe, it, expect } from 'vitest';
import { composeUrlSchemas, duplicateUrlKeys } from '../../ts/utils/url-state';
import { FORECAST_URL_SCHEMA } from '../../ts/visualization/forecast-url';
import { HISTORICAL_URL_SCHEMA } from '../../ts/visualization/historical-url';
import { NOW_URL_SCHEMA } from '../../ts/visualization/now-url';
import { SYNOPTIC_URL_SCHEMA } from '../../ts/visualization/synoptic-url';
import { CLIMATOLOGY_URL_SCHEMA } from '../../ts/visualization/climatology-url';

const TABS = {
  'fc.': FORECAST_URL_SCHEMA,
  'hist.': HISTORICAL_URL_SCHEMA,
  'now.': NOW_URL_SCHEMA,
  'syn.': SYNOPTIC_URL_SCHEMA,
  'clim.': CLIMATOLOGY_URL_SCHEMA,
};

describe('maps page URL schemas', () => {
  it('claim no key twice', () => {
    expect(duplicateUrlKeys({ tab: { default: '' } }, ...Object.values(TABS))).toEqual([]);
  });

  it('keep every key under the tab prefix', () => {
    for (const [prefix, schema] of Object.entries(TABS)) {
      for (const key of Object.keys(schema)) expect(key.startsWith(prefix)).toBe(true);
    }
  });

  it('give every map-bearing tab a viewport key', () => {
    for (const [prefix, schema] of Object.entries(TABS)) {
      expect(Object.keys(schema)).toContain(`${prefix}view`);
    }
  });

  it('keep the keys the iOS app reads by name', () => {
    for (const k of ['fc.day', 'fc.hour', 'fc.model', 'fc.metric', 'fc.apt']) {
      expect(Object.keys(FORECAST_URL_SCHEMA)).toContain(k);
    }
    for (const k of ['hist.date', 'hist.time', 'hist.lead', 'hist.source', 'hist.metric', 'hist.apt']) {
      expect(Object.keys(HISTORICAL_URL_SCHEMA)).toContain(k);
    }
  });
});

describe('composeUrlSchemas', () => {
  it('merges distinct schemas', () => {
    const s = composeUrlSchemas({ a: { default: 1 } }, { b: { default: 'x' } });
    expect(Object.keys(s)).toEqual(['a', 'b']);
  });

  it('throws on a key two schemas claim', () => {
    expect(() => composeUrlSchemas({ 'x.view': { default: '' } }, { 'x.view': { default: '' } }))
      .toThrow(/x\.view/);
  });
});
