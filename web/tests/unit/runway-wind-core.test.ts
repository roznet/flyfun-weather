/** Runway + wind widget rules (#758).
 *
 * SYNC — these cases mirror
 * app/flyfun-weather/flyfun-weatherTests/RunwayWindRulesTests.swift one for
 * one, with the same inputs and the same expected strings and coordinates.
 * The pair is the guarantee that `runway-wind-core.ts` and
 * `RunwayWindRules.swift` still agree.
 */

import { describe, it, expect } from 'vitest';
import {
  RIM_R,
  arrowLength,
  componentsLine,
  crosswindText,
  crosswindTone,
  designator,
  endLine,
  ghostLine,
  missingWindLabel,
  runwayBars,
  runwayWindPair,
  sampleTimeLabel,
  variableArc,
  visibleLabels,
  windArrow,
  windLabel,
  windMark,
  type Pt,
} from '../../ts/visualization/runway-wind/runway-wind-core';
import { runwayWindPairHtml, runwayWindSvg } from '../../ts/visualization/runway-wind/runway-wind-view';
import type {
  AirportObservation,
  EndComponents,
  RunwayInfo,
  RunwayWindPicture,
  WindAtAirport,
  WindSample,
} from '../../ts/store/types';

function rwy(a: string, ha: number, b: string, hb: number, length: number | null = 6000): RunwayInfo {
  return {
    id: `${a}/${b}`, length_ft: length, surface: 'ASPH', hard: true,
    ends: [{ ident: a, heading_true: ha }, { ident: b, heading_true: hb }],
  };
}

function sample(over: Partial<WindSample> = {}): WindSample {
  return {
    source: 'metar', time: '2026-10-02T09:20:00+00:00', direction_true: 270, speed_kt: 12,
    gust_kt: null, variable: false, variable_from: null, variable_to: null, calm: false, ...over,
  };
}

function end(over: Partial<EndComponents> = {}): EndComponents {
  return {
    ident: '27', headwind_kt: 12, crosswind_kt: 0, side: '', gust_headwind_kt: null,
    gust_crosswind_kt: null, max_crosswind_kt: 0, ...over,
  };
}

function wind(s: WindSample, ends: EndComponents[], best: string | null = ends[0]?.ident ?? null, advisory: string | null = 'green'): WindAtAirport {
  return { wind: s, ends, best_end: best, advisory };
}

function expectPt(p: Pt | null | undefined, x: number, y: number): void {
  expect(p).toBeTruthy();
  expect(p!.x).toBeCloseTo(x, 4);
  expect(p!.y).toBeCloseTo(y, 4);
}

describe('runway bars', () => {
  it('draws 09/27 east-west through the centre, idents on the approach side', () => {
    const [bar] = runwayBars([rwy('09', 90, '27', 270)]);
    expectPt(bar.a, 0.2, 0.5); // threshold of 09: you land 09 arriving from the west
    expectPt(bar.b, 0.8, 0.5);
    expect(bar.labels.map((l) => l.ident)).toEqual(['09', '27']);
    expectPt(bar.labels[0].at, 0.13, 0.5);
    expectPt(bar.labels[1].at, 0.87, 0.5);
  });

  it('spreads 05L/23R and 05R/23L side by side, L to the left of 050', () => {
    const [l, r] = runwayBars([rwy('05L', 50, '23R', 230), rwy('05R', 50, '23L', 230)]);
    // Midpoints: offset ±0.04 along bearing 140 (right of 050).
    const mid = (b: { a: Pt; b: Pt }) => ({ x: (b.a.x + b.b.x) / 2, y: (b.a.y + b.b.y) / 2 });
    expectPt(mid(l), 0.474288, 0.469358);
    expectPt(mid(r), 0.525712, 0.530642);
    // Their labels spread 2.5× as far, so "05L" and "05R" don't overprint.
    expectPt(l.labels[0].at, 0.152285, 0.661227);
    expectPt(r.labels[0].at, 0.280842, 0.814436);
  });

  it('crosses 18/36 and 09/27, the shorter one shorter, both centred', () => {
    const [ns, ew] = runwayBars([rwy('18', 180, '36', 360, 3000), rwy('09', 90, '27', 270)]);
    expectPt(ns.a, 0.5, 0.35); // half-length 0.15: half as long as 09/27
    expectPt(ns.b, 0.5, 0.65);
    expectPt(ns.labels[0].at, 0.5, 0.28); // "18" north: you land 18 arriving from the north
    expectPt(ew.a, 0.2, 0.5);
  });

  it('floors very short strips and draws unknown lengths full length', () => {
    const [long, short, unknown] = runwayBars([
      rwy('09', 90, '27', 270, 10000), rwy('18', 180, '36', 360, 1000), rwy('04', 40, '22', 220, null),
    ]);
    expectPt(long.a, 0.2, 0.5);
    expectPt(short.a, 0.5, 0.38); // 0.4 × 0.3 — the floor
    expect(Math.hypot(unknown.a.x - 0.5, unknown.a.y - 0.5)).toBeCloseTo(0.3, 4);
  });

  it('reads the L/C/R designator', () => {
    expect(designator('05L')).toBe('L');
    expect(designator('23C')).toBe('C');
    expect(designator('27')).toBeNull();
  });

  it('shows all labels in regular, the best end only in compact, none inline', () => {
    const bars = runwayBars([rwy('09', 90, '27', 270)]);
    expect(visibleLabels(bars, 'regular', '27').map((l) => l.ident)).toEqual(['09', '27']);
    expect(visibleLabels(bars, 'compact', '27').map((l) => l.ident)).toEqual(['27']);
    expect(visibleLabels(bars, 'compact', null).map((l) => l.ident)).toEqual(['09', '27']);
    expect(visibleLabels(bars, 'inline', '27')).toEqual([]);
  });
});

describe('wind arrow', () => {
  it('draws a 270 wind from the left edge pointing right, with its gust', () => {
    const a = windArrow(sample({ direction_true: 270, speed_kt: 12, gust_kt: 22 }))!;
    expectPt(a.from, 0.04, 0.5);
    expectPt(a.to, 0.16, 0.5); // 12 kt is under the floor
    expectPt(a.gustTo, 0.228571, 0.5);
  });

  it('caps a strong 045 wind at full scale, from the north-east rim', () => {
    const a = windArrow(sample({ direction_true: 45, speed_kt: 40 }))!;
    expectPt(a.from, 0.825269, 0.174731);
    expectPt(a.to, 0.613137, 0.386863);
    expect(a.gustTo).toBeNull();
  });

  it('keeps a light 180 wind visible, from the bottom rim pointing up', () => {
    const a = windArrow(sample({ direction_true: 180, speed_kt: 2 }))!;
    expectPt(a.from, 0.5, 0.96);
    expectPt(a.to, 0.5, 0.84);
    expect(arrowLength(2)).toBeCloseTo(0.12, 6);
  });

  it('drops a gust within the display threshold', () => {
    expect(windArrow(sample({ speed_kt: 12, gust_kt: 15 }))!.gustTo).toBeNull();
  });

  it('has no arrow for calm or VRB', () => {
    expect(windArrow(sample({ calm: true, speed_kt: 0, direction_true: 0 }))).toBeNull();
    expect(windArrow(sample({ variable: true, direction_true: null, speed_kt: 4 }))).toBeNull();
  });

  it('marks VRB as a dashed circle, calm as a dot, missing as missing — never calm', () => {
    expect(windMark(wind(sample({ variable: true, direction_true: null, speed_kt: 4 }), []), 'No METAR'))
      .toEqual({ kind: 'vrb', r: 0.38, label: 'VRB 04' });
    expect(windMark(wind(sample({ calm: true, speed_kt: 0, direction_true: 0 }), []), 'No METAR'))
      .toEqual({ kind: 'calm', label: 'Calm' });
    expect(windMark(null, 'No METAR')).toEqual({ kind: 'missing', label: 'No METAR' });
  });

  it('draws a dddVddd range as a clockwise rim arc, across north too', () => {
    const arc = variableArc(sample({ variable_from: 240, variable_to: 300 }))!;
    expect(arc.sweepDeg).toBe(60);
    expect(arc.r).toBe(RIM_R);
    expect(variableArc(sample({ variable_from: 350, variable_to: 20 }))!.sweepDeg).toBe(30);
    expect(variableArc(sample())).toBeNull();
  });
});

describe('words', () => {
  it('labels the wind as reported', () => {
    expect(windLabel(sample({ direction_true: 270, speed_kt: 12, gust_kt: 22 }))).toBe('270@12G22');
    expect(windLabel(sample({ direction_true: 5, speed_kt: 8 }))).toBe('005@8');
    expect(windLabel(sample({ direction_true: 270, speed_kt: 12, variable_from: 240, variable_to: 300 })))
      .toBe('270@12 240V300');
    expect(windLabel(sample({ variable: true, direction_true: null, speed_kt: 4, gust_kt: 15 }))).toBe('VRB 04G15');
    expect(windLabel(sample({ calm: true, speed_kt: 0, direction_true: 0 }))).toBe('Calm');
  });

  it('says head, crosswind side and gust for the best end', () => {
    const e = end({ headwind_kt: 6.2, crosswind_kt: -10.6, side: 'left', gust_crosswind_kt: -17.3, max_crosswind_kt: 17.3 });
    expect(componentsLine(wind(sample({ gust_kt: 22 }), [e]), 'No METAR'))
      .toBe('27 · 6 kt head · 11 kt X-wind from left (G 17)');
  });

  it('says tail for a negative headwind', () => {
    const e = end({ ident: '09', headwind_kt: -3.4, crosswind_kt: 2.0, side: 'right', max_crosswind_kt: 2 });
    expect(endLine(e, sample())).toBe('09 · 3 kt tail · 2 kt X-wind from right');
  });

  it('names no side for a zero crosswind and hides a close gust', () => {
    expect(endLine(end(), sample())).toBe('27 · 12 kt head · 0 kt X-wind');
    const e = end({ crosswind_kt: 10, side: 'right', gust_crosswind_kt: 12.4, max_crosswind_kt: 12.4 });
    expect(endLine(e, sample())).toBe('27 · 12 kt head · 10 kt X-wind from right');
  });

  it('adds the variable range worst case when it says more', () => {
    const e = end({ crosswind_kt: 10, side: 'right', max_crosswind_kt: 17.3 });
    expect(endLine(e, sample({ variable_from: 270, variable_to: 330 })))
      .toBe('27 · 12 kt head · 10 kt X-wind from right · up to 17 kt');
  });

  it('rounds ties away from zero', () => {
    const e = end({ ident: '09', headwind_kt: -2.5, crosswind_kt: 0.5, side: 'right', max_crosswind_kt: 0.5 });
    expect(endLine(e, sample())).toBe('09 · 3 kt tail · 1 kt X-wind from right');
  });

  it('gives VRB its worst case, calm and missing their words', () => {
    const vrb = sample({ variable: true, direction_true: null, speed_kt: 4 });
    expect(componentsLine(wind(vrb, [end({ max_crosswind_kt: 4 })], null, null), 'No METAR'))
      .toBe('VRB 04 · up to 4 kt X-wind');
    expect(componentsLine(wind(vrb, [], null, null), 'No METAR')).toBe('VRB 04 · up to 4 kt X-wind');
    expect(componentsLine(wind(sample({ calm: true, speed_kt: 0, direction_true: 0 }), [end()]), 'No METAR')).toBe('Calm');
    expect(componentsLine(null, 'No METAR')).toBe('No METAR');
    expect(componentsLine(wind(sample(), [], null, null), 'No METAR')).toBe('No runway data');
  });

  it('writes the ghost line with source, time and the best end crosswind', () => {
    const taf = sample({ source: 'taf', time: '2026-10-02T14:00:00+00:00', direction_true: 250, speed_kt: 18, gust_kt: 28 });
    const e = end({ headwind_kt: 16.9, crosswind_kt: -6.2, side: 'left', max_crosswind_kt: 6.2 });
    expect(ghostLine(wind(taf, [e]))).toBe('TAF 14Z 250@18G28 · 27 · 6 kt X-wind from left');
    expect(ghostLine(wind(sample({ source: 'taf', time: null, calm: true, speed_kt: 0, direction_true: 0 }), [])))
      .toBe('TAF Calm');
  });

  it('labels sample times in UTC', () => {
    expect(sampleTimeLabel('2026-10-02T14:37:00Z')).toBe('1437Z');
    expect(sampleTimeLabel('2026-10-02T14:00:00')).toBe('14Z');
    expect(sampleTimeLabel(null)).toBe('');
  });

  it('tones only amber and red, and only the crosswind text', () => {
    expect(crosswindTone('amber')).toBe('amber');
    expect(crosswindTone('red')).toBe('red');
    expect(crosswindTone('green')).toBeNull();
    expect(crosswindTone(null)).toBeNull();
    const e = end({ headwind_kt: 6.2, crosswind_kt: -10.6, side: 'left', max_crosswind_kt: 10.6 });
    expect(crosswindText(wind(sample(), [e]))).toBe('11 kt X-wind from left');
    expect(crosswindText(wind(sample({ calm: true, speed_kt: 0 }), [e]))).toBeNull();
  });
});

describe('the departure / destination pair', () => {
  const picture = (icao: string, winds: WindAtAirport[]): RunwayWindPicture => ({
    icao, runways: [rwy('09', 90, '27', 270)], winds,
  });
  const obs = (icao: string, rw: RunwayWindPicture | null | undefined): AirportObservation =>
    ({ icao, runway_wind: rw } as unknown as AirportObservation);
  const metar = wind(sample(), [end()]);
  const taf = wind(sample({ source: 'taf' }), [end()]);

  it('gives the destination its TAF ghost and the departure none', () => {
    const dials = runwayWindPair(
      [obs('ZZAA', picture('ZZAA', [metar, taf])), obs('ZZBB', picture('ZZBB', [metar, taf]))], 'ZZAA', 'ZZBB',
    );
    expect(dials.map((d) => d.role)).toEqual(['DEP', 'ARR']);
    expect(dials[0].ghost).toBeNull();
    expect(dials[1].ghost).toBe(taf);
  });

  it('keeps a side without a picture, saying so', () => {
    const dials = runwayWindPair([obs('ZZBB', picture('ZZBB', []))], 'ZZAA', 'ZZBB');
    expect(dials).toHaveLength(2);
    expect(missingWindLabel(dials[0])).toBe('No runway data');
    expect(missingWindLabel(dials[1])).toBe('No METAR');
  });

  it('is empty when no airport has a picture (older server)', () => {
    expect(runwayWindPair([obs('ZZAA', undefined)], 'ZZAA', 'ZZBB')).toEqual([]);
    expect(runwayWindPair(null, 'ZZAA', 'ZZBB')).toEqual([]);
  });

  it('renders every size with no unresolved values', () => {
    const dials = runwayWindPair(
      [obs('ZZAA', picture('ZZAA', [metar])), obs('ZZBB', picture('ZZBB', [metar, taf]))], 'ZZAA', 'ZZBB',
    );
    for (const size of ['compact', 'regular', 'inline'] as const) {
      const svg = runwayWindSvg(dials[1], size);
      expect(svg).toMatch(/^<svg /);
      expect(svg).not.toMatch(/NaN|undefined/);
    }
    const html = runwayWindPairHtml(dials);
    expect(html).toContain('data-rw-role="DEP"');
    expect(html).toContain('data-rw-role="ARR"');
    expect(html).toContain('data-rw-line="ghost"');
    expect(html).not.toMatch(/NaN|undefined|\[object/);
    expect(runwayWindPairHtml([])).toBe('');
  });
});
