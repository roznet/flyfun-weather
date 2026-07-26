/** Every cross-section line must stay identifiable — including for a pilot
 *  who cannot separate the hues.
 *
 *  Two rules, both learned from real collisions:
 *
 *   1. No two lines may share BOTH colour and dash. Sharing a colour is fine
 *      when something else separates them — GRAMET deliberately draws −10°C
 *      and −20°C in the same green because they are always stacked in that
 *      order and the dash differs. Sharing colour *and* dash is not.
 *
 *   2. The LCL / LFC / EL triplet must have three distinct dash patterns.
 *      Their hues are green / orange / red — the classic red-green confusion
 *      set — so under simulated protanopia LCL and EL land at ΔE≈9 in the
 *      Light theme. Dash is the only cue that survives, so it must carry the
 *      identity rather than merely decorate it.
 */

import { describe, it, expect } from 'vitest';
import { THEMES, type CrossSectionTheme, type LineStyle } from '../../ts/visualization/cross-section/theme';
import { referenceLineStyles } from '../../ts/visualization/cross-section/layers/reference-lines';

function allLines(theme: CrossSectionTheme): Record<string, LineStyle> {
  const ref = referenceLineStyles(theme);
  return {
    'freezing-level': theme.temperature.freezingLevel,
    'minus-10c': theme.temperature.minus10c,
    'minus-20c': theme.temperature.minus20c,
    lcl: theme.stability.lcl,
    lfc: theme.stability.lfc,
    el: theme.stability.el,
    cruise: ref.cruise,
    ceiling: ref.ceiling,
  };
}

/** Solid strokes normalise to the same signature regardless of how the theme
 *  spells "no dash" (missing key vs empty array). */
const dashKey = (l: LineStyle) => (l.dash?.length ? l.dash.join(',') : 'solid');

const themeIds = Object.keys(THEMES) as Array<keyof typeof THEMES>;

describe('cross-section line distinctness', () => {
  for (const id of themeIds) {
    const theme = THEMES[id];

    it(`${id}: no two lines share both colour and dash`, () => {
      const lines = allLines(theme);
      const names = Object.keys(lines);
      const collisions: string[] = [];

      for (let i = 0; i < names.length; i++) {
        for (let j = i + 1; j < names.length; j++) {
          const a = lines[names[i]];
          const b = lines[names[j]];
          if (a.color.toLowerCase() === b.color.toLowerCase() && dashKey(a) === dashKey(b)) {
            collisions.push(`${names[i]} / ${names[j]} (${a.color} ${dashKey(a)})`);
          }
        }
      }

      expect(collisions).toEqual([]);
    });

    it(`${id}: LCL / LFC / EL have three distinct dash patterns`, () => {
      const { lcl, lfc, el } = theme.stability;
      const keys = [dashKey(lcl), dashKey(lfc), dashKey(el)];
      expect(new Set(keys).size).toBe(3);
    });
  }

  it('every registered theme is covered', () => {
    // Guards against a new theme being added without picking up these rules.
    expect(themeIds.sort()).toEqual(['gramet', 'high-contrast', 'light', 'standard']);
  });
});
