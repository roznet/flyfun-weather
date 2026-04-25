/** TS mirror of src/weatherbrief/debriefs/taxonomy.py.
 *
 * Keep in sync — both define the canonical condition tags and keyword
 * map. Drift is caught at code review (small file, no obvious harm
 * mechanism) since runtime cross-validation would mean an extra fetch.
 */

import type { ConditionTagId, OutcomeValue } from '../store/types';

export const ALL_TAGS: ConditionTagId[] = [
  'IMC', 'ICE', 'WIND', 'TS', 'TURB', 'FRZ', 'VIS', 'OPS',
];

/** Categories that can be graded as outcomes (everything except OPS). */
export const OUTCOME_CATEGORIES: ConditionTagId[] = ALL_TAGS.filter((t) => t !== 'OPS');

export const TAG_LABELS: Record<ConditionTagId, string> = {
  IMC:  'IMC',
  ICE:  'Icing',
  WIND: 'Wind',
  TS:   'Thunderstorm',
  TURB: 'Turbulence',
  FRZ:  'Freezing precip',
  VIS:  'Visibility',
  OPS:  'Operational',
};

export const TAG_DESCRIPTIONS: Record<ConditionTagId, string> = {
  IMC:  'Low ceilings / IFR conditions',
  ICE:  'Airframe icing',
  WIND: 'Strong / gusty / crosswind',
  TS:   'Thunderstorms or convective build-up',
  TURB: 'Turbulence (any intensity)',
  FRZ:  'Freezing rain / sleet',
  VIS:  'Reduced visibility, fog, mist',
  OPS:  'Non-weather (aircraft, pilot, NOTAM, fuel, …)',
};

export const OUTCOME_LABELS: Record<OutcomeValue, string> = {
  consistent: 'As forecast',
  better: 'Better than forecast',
  worse: 'Worse than forecast',
};

/** Phrase → tag map. Lowercase substrings; matched with a word-boundary
 *  check by ``matchTagsInText`` below. Mirrors KEYWORD_MAP in Python. */
const KEYWORD_MAP: Record<ConditionTagId, string[]> = {
  IMC:  ['imc', 'ifr', 'overcast', 'low ceiling', 'ceiling'],
  ICE:  ['icing', 'ice', 'rime', 'sld'],
  WIND: ['wind', 'gust', 'crosswind'],
  TS:   ['thunder', 'thunderstorm', 'cb', 'storm', 'lightning', 'convect'],
  TURB: ['turb', 'bumpy', 'rough', 'shear'],
  FRZ:  ['freezing rain', 'fzra', 'sleet', 'freezing precip'],
  VIS:  ['visib', 'fog', 'haze', 'mist', 'smoke'],
  OPS:  ['fuel', 'notam', 'currency', 'aircraft', 'personal', 'passenger'],
};

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const TAG_PATTERNS: Record<ConditionTagId, RegExp> = (() => {
  const out: Partial<Record<ConditionTagId, RegExp>> = {};
  for (const tag of ALL_TAGS) {
    const phrases = [...KEYWORD_MAP[tag]].sort((a, b) => b.length - a.length);
    const alt = phrases.map(escapeRegex).join('|');
    out[tag] = new RegExp(`(?<!\\w)(?:${alt})`, 'i');
  }
  return out as Record<ConditionTagId, RegExp>;
})();

/** Scan ``text`` for keyword hits and return matching tags. */
export function matchTagsInText(text: string): Set<ConditionTagId> {
  const out = new Set<ConditionTagId>();
  if (!text) return out;
  for (const tag of ALL_TAGS) {
    if (TAG_PATTERNS[tag].test(text)) out.add(tag);
  }
  return out;
}

/** Advisory id → debrief tag. Per-id rather than per-category because some
 *  categories (e.g. ``airport``) cover both wind and visibility-style
 *  concerns. ``model`` advisories aren't weather phenomena → no mapping. */
const ADVISORY_TAG_MAP: Record<string, ConditionTagId> = {
  icing_escape:      'ICE',
  fiki_icing:        'ICE',
  vmc_cruise:        'IMC',
  cloud_top:         'IMC',
  vfr_feasibility:   'IMC',
  ifr_feasibility:   'IMC',
  flight_category:   'IMC',
  turbulence:        'TURB',
  mountain_wind:     'TURB',
  convective:        'TS',
  airport_wind:      'WIND',
};

/** From an advisory manifest, return the set of tags whose advisory came
 *  back AMBER or RED (anything worse than green). GREEN/UNAVAILABLE don't
 *  count — there's nothing for the pilot to grade. */
export function flaggedTagsFromAdvisories(manifest: {
  advisories: Array<{ advisory_id: string; aggregate_status: string }>;
} | null | undefined): ConditionTagId[] {
  if (!manifest?.advisories) return [];
  const out = new Set<ConditionTagId>();
  for (const adv of manifest.advisories) {
    if (adv.aggregate_status !== 'amber' && adv.aggregate_status !== 'red') continue;
    const tag = ADVISORY_TAG_MAP[adv.advisory_id];
    if (tag) out.add(tag);
  }
  return [...out];
}
