/** Tests for the new-flight departure defaults and timezone round-trips. */

import { describe, it, expect } from 'vitest';
import {
  defaultDepartureInstant, instantToLocalFields, localFieldsToInstant, getUtcOffsetMinutes,
} from '../../ts/utils/timezone';

const iso = (d: Date) => d.toISOString();

describe('defaultDepartureInstant', () => {
  it('adds the lead and rounds to the nearest 15 minutes', () => {
    expect(iso(defaultDepartureInstant(new Date('2026-09-27T10:08:00Z')))).toBe('2026-09-27T12:15:00.000Z');
    expect(iso(defaultDepartureInstant(new Date('2026-09-27T10:05:00Z')))).toBe('2026-09-27T12:00:00.000Z');
    expect(iso(defaultDepartureInstant(new Date('2026-09-27T10:53:00Z'), 0))).toBe('2026-09-27T11:00:00.000Z');
    expect(iso(defaultDepartureInstant(new Date('2026-09-27T10:05:00Z'), 1))).toBe('2026-09-27T11:00:00.000Z');
  });

  it('rolls the date over past midnight UTC', () => {
    expect(iso(defaultDepartureInstant(new Date('2026-09-27T22:30:00Z')))).toBe('2026-09-28T00:30:00.000Z');
  });
});

describe('instantToLocalFields / localFieldsToInstant', () => {
  it('shows a UTC evening as the next local day east of UTC', () => {
    const t = new Date('2026-09-27T23:15:00Z');
    expect(instantToLocalFields(t, 'Europe/Paris')).toEqual({ date: '2026-09-28', hour: 1, minute: 15 });
    expect(instantToLocalFields(t, 'UTC')).toEqual({ date: '2026-09-27', hour: 23, minute: 15 });
  });

  it('converts a local time just after midnight to the previous UTC day', () => {
    expect(iso(localFieldsToInstant('2026-09-28', 0, 30, 'Europe/London'))).toBe('2026-09-27T23:30:00.000Z');
  });

  it('round-trips west of UTC and across DST', () => {
    for (const [date, h, m, tz] of [
      ['2026-07-04', 18, 45, 'America/Los_Angeles'],
      ['2026-01-15', 9, 0, 'Europe/Berlin'],
      ['2026-03-29', 12, 0, 'Europe/London'], // DST starts that morning
      ['2026-10-25', 12, 0, 'Europe/London'], // DST ends that morning
    ] as const) {
      expect(instantToLocalFields(localFieldsToInstant(date, h, m, tz), tz)).toEqual({ date, hour: h, minute: m });
    }
  });

  it('gets the offset right across a 30-day month end', () => {
    // 30 Apr 23:30Z is 1 May 01:30 in Paris — a naive day-count across months used to add a day.
    expect(getUtcOffsetMinutes('Europe/Paris', new Date('2026-04-30T23:30:00Z'))).toBe(120);
  });
});
