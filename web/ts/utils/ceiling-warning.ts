/**
 * Flight ceiling vs aircraft service ceiling (#777 §4).
 *
 * The flight/profile ceiling is what the pilot *intends* (the top of the
 * analysed altitude band); the aircraft's service ceiling is what the machine
 * *can* do. They are deliberately separate values and we never clamp one to the
 * other — the pilot may want the analysis above the plane's ceiling. We only
 * warn when the intent exceeds the capability.
 *
 * One pure comparison + one formatter, shared by the three surfaces that show
 * the warning: the new-flight pane, the flight edit form, and settings
 * "Flight Defaults" (each profile against the default aircraft).
 */

import { getDateLocale, t } from '../i18n/i18n';

/** The aircraft fields the warning needs — a subset of AircraftResponse. */
export interface CeilingWarningAircraft {
  tail_number: string | null;
  nickname: string | null;
  type_name: string;
  ceiling_ft: number | null;
}

export interface CeilingWarning {
  ceilingFt: number;
  serviceCeilingFt: number;
  aircraftName: string;
}

/** How an aircraft is named in the warning: tail number, else nickname,
 *  else the type name. */
export function aircraftWarningName(ac: CeilingWarningAircraft): string {
  return ac.tail_number?.trim() || ac.nickname?.trim() || ac.type_name;
}

/** Compare a flight/profile ceiling with an aircraft's service ceiling.
 *  Returns null (no warning) when either value is missing / not a positive
 *  number, or when the ceiling is at or below the service ceiling. */
export function checkCeilingAgainstAircraft(
  ceilingFt: number | null | undefined,
  aircraft: CeilingWarningAircraft | null | undefined,
): CeilingWarning | null {
  if (!aircraft) return null;
  const service = aircraft.ceiling_ft;
  if (service == null || !Number.isFinite(service) || service <= 0) return null;
  if (ceilingFt == null || !Number.isFinite(ceilingFt) || ceilingFt <= 0) return null;
  if (ceilingFt <= service) return null;
  return { ceilingFt, serviceCeilingFt: service, aircraftName: aircraftWarningName(aircraft) };
}

/** Localised one-line warning text, e.g.
 *  "Ceiling 25,000 ft is above N123's service ceiling (18,000 ft)." */
export function formatCeilingWarning(w: CeilingWarning): string {
  const locale = getDateLocale();
  return t('aircraft.ceilingWarning', {
    ceiling: w.ceilingFt.toLocaleString(locale),
    aircraft: w.aircraftName,
    service: w.serviceCeilingFt.toLocaleString(locale),
  });
}

/** Text for the warning, or null when there is nothing to warn about. */
export function ceilingWarningText(
  ceilingFt: number | null | undefined,
  aircraft: CeilingWarningAircraft | null | undefined,
): string | null {
  const w = checkCeilingAgainstAircraft(ceilingFt, aircraft);
  return w ? formatCeilingWarning(w) : null;
}

/** Show/hide a warning element for the given ceiling and aircraft. The element
 *  is only ever toggled — never used to change the ceiling value. */
export function applyCeilingWarning(
  el: { textContent: string | null; hidden: boolean } | null | undefined,
  ceilingFt: number | null | undefined,
  aircraft: CeilingWarningAircraft | null | undefined,
): void {
  if (!el) return;
  const text = ceilingWarningText(ceilingFt, aircraft);
  el.textContent = text ?? '';
  el.hidden = text == null;
}

/** Parse a ceiling <input> value; NaN/empty → null. */
export function parseCeilingInput(value: string | null | undefined): number | null {
  const n = parseInt(value ?? '', 10);
  return Number.isFinite(n) ? n : null;
}
