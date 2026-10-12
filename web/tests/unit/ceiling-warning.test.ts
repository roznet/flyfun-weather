/** Flight ceiling vs aircraft service ceiling warning (#777 §4). */

import { describe, it, expect, vi } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'fs';
import { join } from 'path';
import en from '../../ts/i18n/locales/en.json';

vi.mock('../../ts/i18n/i18n', () => ({
  getDateLocale: () => 'en-GB',
  t: (key: string, params?: Record<string, string | number>) => {
    let msg = (en as Record<string, string>)[key] ?? key;
    for (const [k, v] of Object.entries(params ?? {})) msg = msg.split(`{${k}}`).join(String(v));
    return msg;
  },
}));

import {
  aircraftWarningName,
  applyCeilingWarning,
  ceilingWarningText,
  checkCeilingAgainstAircraft,
  parseCeilingInput,
} from '../../ts/utils/ceiling-warning';

const ac = (over: Partial<{ tail_number: string | null; nickname: string | null; type_name: string; ceiling_ft: number | null }> = {}) => ({
  tail_number: 'N123',
  nickname: 'Club SR22',
  type_name: 'Cirrus SR22',
  ceiling_ft: 18000 as number | null,
  ...over,
});

describe('checkCeilingAgainstAircraft', () => {
  it('warns when the ceiling is above the service ceiling', () => {
    expect(checkCeilingAgainstAircraft(25000, ac())).toEqual({
      ceilingFt: 25000, serviceCeilingFt: 18000, aircraftName: 'N123',
    });
  });

  it('does not warn at or below the service ceiling', () => {
    expect(checkCeilingAgainstAircraft(18000, ac())).toBeNull();
    expect(checkCeilingAgainstAircraft(10000, ac())).toBeNull();
  });

  it('does not warn when the aircraft has no service ceiling', () => {
    expect(checkCeilingAgainstAircraft(25000, ac({ ceiling_ft: null }))).toBeNull();
    expect(checkCeilingAgainstAircraft(25000, ac({ ceiling_ft: 0 }))).toBeNull();
  });

  it('does not warn without an aircraft or a usable ceiling', () => {
    expect(checkCeilingAgainstAircraft(25000, null)).toBeNull();
    expect(checkCeilingAgainstAircraft(25000, undefined)).toBeNull();
    expect(checkCeilingAgainstAircraft(null, ac())).toBeNull();
    expect(checkCeilingAgainstAircraft(NaN, ac())).toBeNull();
  });
});

describe('aircraftWarningName', () => {
  it('prefers tail number, then nickname, then type name', () => {
    expect(aircraftWarningName(ac())).toBe('N123');
    expect(aircraftWarningName(ac({ tail_number: null }))).toBe('Club SR22');
    expect(aircraftWarningName(ac({ tail_number: '  ', nickname: null }))).toBe('Cirrus SR22');
  });
});

describe('ceilingWarningText', () => {
  it('formats the English warning', () => {
    expect(ceilingWarningText(25000, ac()))
      .toBe("Ceiling 25,000 ft is above N123's service ceiling (18,000 ft).");
  });

  it('is null when there is nothing to warn about', () => {
    expect(ceilingWarningText(25000, ac({ ceiling_ft: null }))).toBeNull();
  });
});

describe('applyCeilingWarning', () => {
  it('shows the element above the service ceiling and hides it otherwise', () => {
    const el = { textContent: '' as string | null, hidden: true };
    applyCeilingWarning(el, 25000, ac());
    expect(el.hidden).toBe(false);
    expect(el.textContent).toContain('N123');

    applyCeilingWarning(el, 25000, ac({ ceiling_ft: null }));
    expect(el.hidden).toBe(true);
    expect(el.textContent).toBe('');
  });

  it('tolerates a missing element', () => {
    expect(() => applyCeilingWarning(null, 25000, ac())).not.toThrow();
  });
});

describe('parseCeilingInput', () => {
  it('parses numbers and maps empty/garbage to null', () => {
    expect(parseCeilingInput('18000')).toBe(18000);
    expect(parseCeilingInput('')).toBeNull();
    expect(parseCeilingInput('abc')).toBeNull();
    expect(parseCeilingInput(undefined)).toBeNull();
  });
});

/** Source guard: picking an aircraft must never write its service ceiling
 *  (`ceiling_ft`) into a flight/profile ceiling field — the warning is the only
 *  thing the service ceiling drives (#777). The only legitimate writer is the
 *  settings aircraft form's own service-ceiling input (`ac-ceiling`). */
describe('aircraft service ceiling never overwrites the ceiling field', () => {
  const tsRoot = join(__dirname, '../../ts');
  const files: string[] = [];
  const walk = (dir: string): void => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (p.endsWith('.ts')) files.push(p);
    }
  };
  walk(tsRoot);

  it('no `.value = ...ceiling_ft` assignment outside the aircraft form', () => {
    const offenders: string[] = [];
    for (const f of files) {
      readFileSync(f, 'utf8').split('\n').forEach((line, i) => {
        // `ceiling_ft` but not `flight_ceiling_ft` (the profile/flight value).
        if (/\.value\s*=.*(?<!flight_)\bceiling_ft\b/.test(line) && !line.includes("'ac-ceiling'")) {
          offenders.push(`${f}:${i + 1}: ${line.trim()}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });

  it('the flights page aircraft picker only re-estimates and re-checks the warning', () => {
    const src = readFileSync(join(tsRoot, 'flights-main.ts'), 'utf8');
    const start = src.indexOf('select.onchange = () => {');
    expect(start).toBeGreaterThan(-1);
    const body = src.slice(start, src.indexOf('};', start));
    expect(body).not.toMatch(/input-ceiling|ceilInput|ceiling_ft/);
    expect(body).toContain('updateCeilingWarning()');
  });
});
