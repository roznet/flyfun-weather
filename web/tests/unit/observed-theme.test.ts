/** Observed-layer colours across every theme (#574).
 *
 * Cloud tops are coloured by TEMPERATURE, because temperature is what the
 * instrument measures — height is derived from it against a model profile.
 * The ramp follows the enhanced-IR convention pilots already read on satellite
 * imagery, with one deliberate departure: the conventional warm end is
 * grayscale, and gray is indistinguishable from the NWP cloud bands this layer
 * exists to be compared against.
 *
 * Two properties must hold on every theme, and they pull in opposite
 * directions, which is why they are pinned rather than eyeballed:
 *   - the ramp means the same thing everywhere (same hue for the same °C), so
 *     a pilot switching themes does not have to relearn it;
 *   - nothing is gray, on any theme.
 */

import { describe, it, expect, afterEach } from 'vitest';

import { THEMES, setActiveTheme, getActiveTheme, type ThemeId } from '../../ts/visualization/cross-section/theme';
import { tempColor, shareColor } from '../../ts/visualization/cross-section/layers/observed-tops';
import { getLayerLegend } from '../../ts/visualization/layer-legends';

const THEME_IDS = Object.keys(THEMES) as ThemeId[];

/** Parse #rrggbb / rgba() into [r,g,b]. */
function rgb(color: string): [number, number, number] {
  const hex = color.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const m = color.match(/rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/i);
  if (!m) throw new Error(`unparseable colour: ${color}`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

/** Saturation-ish: how far the channels spread. Gray has a spread near zero. */
function chroma(color: string): number {
  const [r, g, b] = rgb(color);
  return Math.max(r, g, b) - Math.min(r, g, b);
}

afterEach(() => setActiveTheme('standard'));

describe('observed cloud-top temperature ramp', () => {
  it('defines an observed block on every theme', () => {
    for (const id of THEME_IDS) {
      const o = THEMES[id].observed;
      expect(o, `theme "${id}" has no observed block`).toBeDefined();
      expect(o.tempStops.length, `theme "${id}" has an empty ramp`).toBeGreaterThan(3);
    }
  });

  it('is never gray on any theme — gray reads as a cloud band', () => {
    // The whole reason the warm end departs from the enhanced-IR convention.
    for (const id of THEME_IDS) {
      setActiveTheme(id);
      for (const celsius of [15, 0, -20, -40, -60, -75]) {
        const c = tempColor(celsius);
        expect(chroma(c), `${id} @ ${celsius}°C is gray (${c})`).toBeGreaterThan(12);
      }
      // …including the fallback for a frame with no temperature at all.
      expect(chroma(getActiveTheme().observed.tempUnknown), `${id} unknown-temp swatch`)
        .toBeGreaterThan(8);
    }
  });

  it('keeps the warm end blue and the cold end warm-hued, everywhere', () => {
    // The convention carries the meaning: cold tops are the dangerous ones and
    // must read hot. Inverting this on one theme would be actively misleading.
    for (const id of THEME_IDS) {
      setActiveTheme(id);
      const [wr, , wb] = rgb(tempColor(10));
      expect(wb, `${id}: warm end should be blue-dominant`).toBeGreaterThan(wr);
      const [cr, , cb] = rgb(tempColor(-70));
      expect(cr, `${id}: cold end should be red-dominant`).toBeGreaterThan(cb);
    }
  });

  it('picks the nearest stop and never interpolates', () => {
    setActiveTheme('standard');
    const stops = getActiveTheme().observed.tempStops;
    const palette = new Set(stops.map((s) => s[1]));
    for (let c = 20; c >= -85; c -= 3) {
      // An invented intermediate colour would imply precision the 2 km
      // retrieval does not have.
      expect(palette.has(tempColor(c)), `${c}°C produced an off-palette colour`).toBe(true);
    }
  });

  it('gains contrast against its own sky as share rises, on every theme', () => {
    // The cross-section's colour channel carries SHARE, because the vertical
    // axis already encodes altitude and temperature is nearly a function of it.
    //
    // The invariant is CONTRAST, not darkness. On the light theme a bigger
    // share is darker; on high-contrast, whose sky is a deep navy, a darker
    // band vanishes into it and the ramp has to brighten instead. Asserting
    // "darkens" would have forced one of them to be wrong — as it did: the
    // first high-contrast ramp written here was non-monotonic and this test
    // caught it.
    const luminance = (c: string) => {
      const [r, g, b] = rgb(c);
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    for (const id of THEME_IDS) {
      setActiveTheme(id);
      const sky = luminance(THEMES[id].sky.background);
      const contrast = [0.02, 0.1, 0.35, 0.8]
        .map((f) => Math.abs(luminance(shareColor(f)) - sky));
      for (let i = 1; i < contrast.length; i++) {
        expect(contrast[i], `${id}: share ramp must gain contrast (step ${i})`)
          .toBeGreaterThan(contrast[i - 1]);
      }
    }
  });

  it('keeps the share ramp distinct from the radar ramp', () => {
    // Radar runs green→yellow→orange→red on the same chart. A shared palette
    // would put two different meanings behind one colour.
    const radar = new Set(['#3cbe5a', '#f0d23c', '#f08c28', '#e13c3c', '#be3cbe']);
    for (const id of THEME_IDS) {
      setActiveTheme(id);
      for (const f of [0.02, 0.1, 0.35, 0.8]) {
        expect(radar.has(shareColor(f).toLowerCase()), `${id} @ ${f}`).toBe(false);
      }
    }
  });

  it('publishes a legend on every theme, including the two non-temperature states', () => {
    for (const id of THEME_IDS) {
      const legend = getLayerLegend('observed-tops', THEMES[id]);
      expect(legend, `no tops legend for "${id}"`).toBeTruthy();
      const meanings = legend!.map((e) => e.meaning).join(' ');
      // "No retrieval" must never be presentable as clear sky, and the layer
      // must say it measures only the top.
      expect(meanings).toMatch(/NOT a clear sky/);
      expect(meanings).toMatch(/no cloud base/);

      const surface = getLayerLegend('observed-surface', THEMES[id]);
      expect(surface, `no surface legend for "${id}"`).toBeTruthy();
      expect(surface!.map((e) => e.meaning).join(' ')).toMatch(/NOT "no rain"/);
    }
  });
});
