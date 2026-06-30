/** Tests for the flight-duration dropdown helpers. */

import { describe, it, expect } from 'vitest';
import {
  splitDurationCeil, combineDuration, formatDurationHM,
  buildDurationHourOptions, buildDurationMinuteOptions,
  MAX_DURATION_HOURS,
} from '../../ts/utils/duration';

describe('splitDurationCeil', () => {
  it('keeps exact 15-minute multiples unchanged', () => {
    expect(splitDurationCeil(0)).toEqual({ hours: 0, minutes: 0 });
    expect(splitDurationCeil(0.25)).toEqual({ hours: 0, minutes: 15 });
    expect(splitDurationCeil(0.5)).toEqual({ hours: 0, minutes: 30 });
    expect(splitDurationCeil(0.75)).toEqual({ hours: 0, minutes: 45 });
    expect(splitDurationCeil(1)).toEqual({ hours: 1, minutes: 0 });
    expect(splitDurationCeil(2.5)).toEqual({ hours: 2, minutes: 30 });
  });

  it('rounds up to the next 15-minute unit (never shorter)', () => {
    // 1h02m -> 1h15m
    expect(splitDurationCeil(1 + 2 / 60)).toEqual({ hours: 1, minutes: 15 });
    // 1h16m -> 1h30m
    expect(splitDurationCeil(1 + 16 / 60)).toEqual({ hours: 1, minutes: 30 });
    // 59m -> 1h00m
    expect(splitDurationCeil(59 / 60)).toEqual({ hours: 1, minutes: 0 });
    // just over 45m -> 1h00m
    expect(splitDurationCeil(0.76)).toEqual({ hours: 1, minutes: 0 });
  });

  it('clamps to the 12h45m ceiling', () => {
    expect(splitDurationCeil(13)).toEqual({ hours: 12, minutes: 45 });
    expect(splitDurationCeil(100)).toEqual({ hours: 12, minutes: 45 });
    expect(splitDurationCeil(MAX_DURATION_HOURS + 0.75)).toEqual({ hours: 12, minutes: 45 });
  });

  it('treats non-positive / invalid input as zero', () => {
    expect(splitDurationCeil(-1)).toEqual({ hours: 0, minutes: 0 });
    expect(splitDurationCeil(NaN)).toEqual({ hours: 0, minutes: 0 });
    // Non-finite is rejected by the guard rather than clamped.
    expect(splitDurationCeil(Infinity)).toEqual({ hours: 0, minutes: 0 });
  });
});

describe('combineDuration', () => {
  it('combines hours and minutes into decimal hours', () => {
    expect(combineDuration(0, 0)).toBe(0);
    expect(combineDuration(1, 15)).toBe(1.25);
    expect(combineDuration(2, 30)).toBe(2.5);
    expect(combineDuration(12, 45)).toBe(12.75);
  });

  it('round-trips with splitDurationCeil for grid values', () => {
    const { hours, minutes } = splitDurationCeil(3.5);
    expect(combineDuration(hours, minutes)).toBe(3.5);
  });
});

describe('formatDurationHM', () => {
  it('formats whole and partial hours compactly', () => {
    expect(formatDurationHM(0)).toBe('0h');
    expect(formatDurationHM(2)).toBe('2h');
    expect(formatDurationHM(1.25)).toBe('1h15');
    expect(formatDurationHM(2.5)).toBe('2h30');
    expect(formatDurationHM(12.75)).toBe('12h45');
  });

  it('rounds up to the 15-min grid so view matches the edit dropdowns', () => {
    // 2.1h -> 2h15 (ceil), agreeing with splitDurationCeil rather than 2h06.
    expect(formatDurationHM(2.1)).toBe('2h15');
    expect(formatDurationHM(1 + 1 / 60)).toBe('1h15');
  });

  it('handles non-positive / invalid input as 0h', () => {
    expect(formatDurationHM(-1)).toBe('0h');
    expect(formatDurationHM(NaN)).toBe('0h');
  });
});

describe('buildDurationHourOptions', () => {
  it('offers 0..MAX_DURATION_HOURS with the selected one marked', () => {
    const html = buildDurationHourOptions(2);
    expect(html).toContain('<option value="0">0</option>');
    expect(html).toContain('<option value="2" selected>2</option>');
    expect(html).toContain(`<option value="${MAX_DURATION_HOURS}">${MAX_DURATION_HOURS}</option>`);
    // 13 options for 0..12
    expect(html.match(/<option/g)?.length).toBe(MAX_DURATION_HOURS + 1);
  });
});

describe('buildDurationMinuteOptions', () => {
  it('offers the four quarter-hour values, zero-padded', () => {
    const html = buildDurationMinuteOptions(30);
    expect(html).toContain('<option value="0">00</option>');
    expect(html).toContain('<option value="30" selected>30</option>');
    expect(html).toContain('<option value="45">45</option>');
    expect(html.match(/<option/g)?.length).toBe(4);
  });
});
