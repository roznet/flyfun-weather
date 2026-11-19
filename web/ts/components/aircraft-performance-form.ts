/** Climb / descent preset table of the aircraft form (#1005).
 *
 *  A preset dropdown over the values it sets (climb speed, climb rate, descent
 *  rate or angle, turbocharged). Editing a value away from the preset shows
 *  "Custom (based on …)"; picking a preset again restores its values. The
 *  preset table comes from the server (`GET /aircraft/presets`), never copied
 *  here. Saved as the base preset plus the values that differ from it.
 *
 *  SYNC: iOS `Models/Domain/AircraftPerformanceForm.swift` and Android
 *  `model/rules/AircraftPerformanceForm.kt` port these rules. Change the three
 *  together.
 */

import { t } from '../i18n/i18n';
import {
  fetchPerformancePresets,
  type AircraftResponse,
  type PerformancePreset,
} from '../adapters/aircraft-adapter';

/** What the four number inputs and the checkbox hold. null = empty field. */
export interface PerformanceValues {
  climb_ias_kt: number | null;
  climb_rate_fpm: number | null;
  descent_rate_fpm: number | null;
  descent_angle_deg: number | null;
  turbo: boolean;
}

export interface PerformanceOverrides {
  performance_preset: string;
  is_turbo: boolean;
  climb_ias_kt: number;
  climb_rate_fpm: number;
  descent_rate_fpm: number;
  descent_angle_deg: number;
}

const NUMBER_FIELDS = ['climb_ias_kt', 'climb_rate_fpm', 'descent_rate_fpm', 'descent_angle_deg'] as const;

/** A preset's own values, as the form shows them (the unused descent field empty). */
export function presetValues(p: PerformancePreset): PerformanceValues {
  return {
    climb_ias_kt: p.climb_ias_kt,
    climb_rate_fpm: p.climb_rate_fpm,
    descent_rate_fpm: p.descent_mode === 'rate' ? p.descent_rate_fpm : null,
    descent_angle_deg: p.descent_mode === 'angle' ? p.descent_angle_deg : null,
    turbo: p.turbo,
  };
}

/** True when a filled field (or the turbo box) differs from the preset. An
 *  empty field means "the preset's value", so it never makes it custom. */
export function isCustom(values: PerformanceValues, base: PerformancePreset): boolean {
  const own = presetValues(base);
  if (values.turbo !== own.turbo) return true;
  return NUMBER_FIELDS.some(k => values[k] != null && values[k] !== own[k]);
}

/** The save payload: the base preset, and only the values that differ from it
 *  (0 = no override, which also clears an earlier one on update). */
export function overridesFor(values: PerformanceValues, base: PerformancePreset): PerformanceOverrides {
  const own = presetValues(base);
  const diff = (k: typeof NUMBER_FIELDS[number]): number => {
    const v = values[k];
    return v != null && v > 0 && v !== own[k] ? v : 0;
  };
  return {
    performance_preset: base.id,
    is_turbo: values.turbo,
    climb_ias_kt: Math.round(diff('climb_ias_kt')),
    climb_rate_fpm: Math.round(diff('climb_rate_fpm')),
    descent_rate_fpm: Math.round(diff('descent_rate_fpm')),
    descent_angle_deg: diff('descent_angle_deg'),
  };
}

export function presetName(id: string): string {
  const name = t(`page.settings.aircraft.preset.${id}`);
  return name.charAt(0).toUpperCase() + name.slice(1);
}

// --- DOM wiring ----------------------------------------------------------------

const INPUT_IDS: Record<typeof NUMBER_FIELDS[number], string> = {
  climb_ias_kt: 'ac-climb-ias',
  climb_rate_fpm: 'ac-climb-rate',
  descent_rate_fpm: 'ac-descent-rate',
  descent_angle_deg: 'ac-descent-angle',
};
const CUSTOM = 'custom';

let presets: PerformancePreset[] = [];
let suggested = '';
let baseId = '';
/** The selection tracks the ICAO type's suggestion (not picked by hand). */
let followsType = true;
let loadSeq = 0;

const el = <T extends HTMLElement>(id: string) => document.getElementById(id) as T | null;
const base = (): PerformancePreset | undefined => presets.find(p => p.id === baseId);

function readValues(): PerformanceValues {
  const num = (id: string): number | null => {
    const v = parseFloat(el<HTMLInputElement>(id)?.value ?? '');
    return Number.isFinite(v) && v > 0 ? v : null;
  };
  return {
    climb_ias_kt: num(INPUT_IDS.climb_ias_kt),
    climb_rate_fpm: num(INPUT_IDS.climb_rate_fpm),
    descent_rate_fpm: num(INPUT_IDS.descent_rate_fpm),
    descent_angle_deg: num(INPUT_IDS.descent_angle_deg),
    turbo: !!el<HTMLInputElement>('ac-turbo')?.checked,
  };
}

function writeValues(v: PerformanceValues): void {
  for (const k of NUMBER_FIELDS) {
    const input = el<HTMLInputElement>(INPUT_IDS[k]);
    if (input) input.value = v[k] != null ? String(Math.round((v[k] as number) * 10) / 10) : '';
  }
  const turbo = el<HTMLInputElement>('ac-turbo');
  if (turbo) turbo.checked = v.turbo;
}

/** Redraw the dropdown (with "Custom (based on …)" when the values differ) and the hint. */
function render(): void {
  const select = el<HTMLSelectElement>('ac-perf-preset');
  const hint = el('ac-perf-hint');
  const b = base();
  if (!select || !b) return;
  const custom = isCustom(readValues(), b);
  select.replaceChildren(
    ...presets.map(p => new Option(presetName(p.id), p.id)),
    ...(custom ? [new Option(t('page.settings.aircraft.perf.custom', { preset: presetName(b.id) }), CUSTOM)] : []),
  );
  select.value = custom ? CUSTOM : b.id;
  if (hint) {
    hint.textContent = t(
      custom ? 'page.settings.aircraft.perf.customHint' : 'page.settings.aircraft.perf.presetHint',
      { preset: presetName(b.id) },
    );
  }
}

async function loadPresets(icaoType: string, cruiseKt: number | null): Promise<boolean> {
  const seq = ++loadSeq;
  const res = await fetchPerformancePresets(icaoType, cruiseKt);
  if (seq !== loadSeq) return false;  // a newer request superseded this one
  presets = res.presets;
  suggested = res.suggested;
  return true;
}

/** Open the table for an aircraft (or a new one when `ac` is null). */
export async function openPerformanceForm(ac: AircraftResponse | null): Promise<void> {
  try {
    if (!await loadPresets(ac && !ac.is_placeholder ? ac.icao_type : '', ac?.cruise_speed_kt ?? null)) return;
  } catch {
    return;  // the rest of the form still works; the table stays empty
  }
  const perf = ac?.performance ?? null;
  baseId = perf && presets.some(p => p.id === perf.preset) ? perf.preset : suggested;
  followsType = !ac?.performance_preset || ac.performance_preset === suggested;
  const b = base();
  if (perf && b && ac && !ac.is_placeholder) {
    writeValues({
      climb_ias_kt: perf.climb_ias_kt,
      climb_rate_fpm: perf.climb_rate_fpm,
      descent_rate_fpm: perf.descent_mode === 'rate' ? perf.descent_rate_fpm : null,
      descent_angle_deg: perf.descent_mode === 'angle' ? perf.descent_angle_deg : null,
      turbo: !!perf.turbo,
    });
  } else if (b) {
    writeValues(presetValues(b));
  }
  render();
}

/** The type or cruise speed changed: a preset that follows the type moves to
 *  the new suggestion (with its values); a picked or custom one stays. */
export async function refreshSuggestedPreset(icaoType: string, cruiseKt: number | null): Promise<void> {
  const before = base();
  const wasCustom = before ? isCustom(readValues(), before) : false;
  try {
    if (!await loadPresets(icaoType, cruiseKt)) return;
  } catch {
    return;
  }
  if (followsType && !wasCustom && suggested !== baseId) {
    baseId = suggested;
    const b = base();
    if (b) writeValues(presetValues(b));
  }
  render();
}

/** The save payload, or null when the presets never loaded (leave unchanged). */
export function readPerformanceOverrides(): PerformanceOverrides | null {
  const b = base();
  return b ? overridesFor(readValues(), b) : null;
}

/** Wire the dropdown and the value inputs once. */
export function initPerformanceForm(): void {
  el<HTMLSelectElement>('ac-perf-preset')?.addEventListener('change', (e) => {
    const id = (e.target as HTMLSelectElement).value;
    if (id === CUSTOM) return;
    baseId = id;
    followsType = id === suggested;
    const b = base();
    if (b) writeValues(presetValues(b));
    render();
  });
  // A descent is a rate or an angle: typing one clears the other.
  const pair: [string, string][] = [
    [INPUT_IDS.descent_rate_fpm, INPUT_IDS.descent_angle_deg],
    [INPUT_IDS.descent_angle_deg, INPUT_IDS.descent_rate_fpm],
  ];
  for (const [id, other] of pair) {
    el<HTMLInputElement>(id)?.addEventListener('input', (e) => {
      if ((e.target as HTMLInputElement).value !== '') {
        const o = el<HTMLInputElement>(other);
        if (o) o.value = '';
      }
    });
  }
  for (const id of [...Object.values(INPUT_IDS), 'ac-turbo']) {
    el(id)?.addEventListener('input', render);
    el(id)?.addEventListener('change', render);
  }
}
