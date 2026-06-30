/** Shared helpers for the flight-duration hour/minute dropdown controls used on
 *  the flight setup form (index.html) and the flight-detail edit form. The model
 *  stores ``flight_duration_hours`` as decimal hours; the UI presents it as a
 *  whole-hour select (0–12) plus a 15-minute select. */

/** Selectable minute values — quarter-hour granularity. */
export const DURATION_MINUTE_OPTIONS = [0, 15, 30, 45] as const;

/** Largest whole-hour the hour dropdown offers. */
export const MAX_DURATION_HOURS = 12;

/** Largest duration the dropdowns can represent, in decimal hours (12h45m). */
const MAX_DURATION_DECIMAL = MAX_DURATION_HOURS + 45 / 60;

export interface DurationParts {
  hours: number;
  minutes: number;
}

/** Split decimal hours into {hours, minutes}, rounding UP to the next 15-minute
 *  unit (never shorter) and clamping to the 12h45m dropdown ceiling. A still-air
 *  estimate of 1h02m therefore becomes 1h15m — we never advertise a flight
 *  window shorter than the computed time. Non-positive / invalid input → 0h00. */
export function splitDurationCeil(decimalHours: number): DurationParts {
  if (!Number.isFinite(decimalHours) || decimalHours <= 0) return { hours: 0, minutes: 0 };
  const capped = Math.min(decimalHours, MAX_DURATION_DECIMAL);
  // Count 15-minute units, rounding up. The epsilon absorbs float error so exact
  // multiples (e.g. 0.75h → 3.0 quarters) don't tip into the next unit.
  const quarters = Math.ceil(capped * 4 - 1e-9);
  const totalMinutes = quarters * 15;
  return { hours: Math.floor(totalMinutes / 60), minutes: totalMinutes % 60 };
}

/** Combine whole hours + minutes back into decimal hours for the model. */
export function combineDuration(hours: number, minutes: number): number {
  return hours + minutes / 60;
}

/** Format decimal hours as a compact "1h15" / "2h" label for read-only display.
 *  Rounds UP to the next 15-minute unit (via splitDurationCeil) so the label
 *  agrees with what the edit dropdowns show for the same stored value. "0h" for
 *  non-positive / invalid input. */
export function formatDurationHM(decimalHours: number): string {
  const { hours, minutes } = splitDurationCeil(decimalHours);
  return minutes ? `${hours}h${minutes.toString().padStart(2, '0')}` : `${hours}h`;
}

/** Build ``<option>`` markup for the hour select (0…MAX_DURATION_HOURS). */
export function buildDurationHourOptions(selectedHours: number): string {
  let html = '';
  for (let h = 0; h <= MAX_DURATION_HOURS; h++) {
    const sel = h === selectedHours ? ' selected' : '';
    html += `<option value="${h}"${sel}>${h}</option>`;
  }
  return html;
}

/** Build ``<option>`` markup for the 15-minute select. */
export function buildDurationMinuteOptions(selectedMinutes: number): string {
  return DURATION_MINUTE_OPTIONS.map(m => {
    const sel = m === selectedMinutes ? ' selected' : '';
    return `<option value="${m}"${sel}>${m.toString().padStart(2, '0')}</option>`;
  }).join('');
}
