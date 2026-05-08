/** Tests for the surface-obscuration heuristic.
 *
 * Cases mirror the spec in issue #125: severity tiers from visibility,
 * secondary low-cloud + DD trigger, band geometry (floor / cap / DD
 * dry-out top).
 */

import { describe, it, expect } from 'vitest';
import {
  computeSurfaceObscuration,
  computeSurfaceObscurationFromCloudLayers,
  type ObscurationSurface,
  type ObscurationLevel,
} from '../../ts/visualization/surface-obscuration';

const CLEAR: ObscurationSurface = {
  visibilityM: 30000,
  temperature2mC: 15,
  dewpoint2mC: 5,
  cloudCoverLowPct: 10,
};

describe('computeSurfaceObscuration — visibility trigger', () => {
  it('severe fog (<1 km) → red', () => {
    const out = computeSurfaceObscuration(
      { ...CLEAR, visibilityM: 60, temperature2mC: 6, dewpoint2mC: 6, cloudCoverLowPct: 100 },
      [],
      364,
    );
    expect(out).not.toBeNull();
    expect(out!.severity).toBe('lifr');
    expect(out!.reason).toBe('visibility');
  });

  it('moderate mist (1–3 km) → amber', () => {
    const out = computeSurfaceObscuration(
      { ...CLEAR, visibilityM: 2400 },
      [],
      0,
    );
    expect(out!.severity).toBe('ifr');
    expect(out!.reason).toBe('visibility');
  });

  it('haze (3–5 km) → yellow', () => {
    const out = computeSurfaceObscuration(
      { ...CLEAR, visibilityM: 4000 },
      [],
      0,
    );
    expect(out!.severity).toBe('mvfr');
  });

  it('no trigger when visibility good and low_cc small', () => {
    const out = computeSurfaceObscuration(CLEAR, [], 0);
    expect(out).toBeNull();
  });

  it('boundary at 5000 m — exactly 5000 does not fire', () => {
    const out = computeSurfaceObscuration(
      { ...CLEAR, visibilityM: 5000 },
      [],
      0,
    );
    expect(out).toBeNull();
  });
});

describe('computeSurfaceObscuration — secondary low-cloud + DD trigger', () => {
  it('low_cc=100% and DD=0.5 → amber, reason=low_cloud_dd', () => {
    const out = computeSurfaceObscuration(
      {
        visibilityM: null,
        temperature2mC: 6,
        dewpoint2mC: 5.5,
        cloudCoverLowPct: 100,
      },
      [],
      500,
    );
    expect(out).not.toBeNull();
    expect(out!.severity).toBe('ifr');
    expect(out!.reason).toBe('low_cloud_dd');
  });

  it('low_cc=70% does not fire (below threshold)', () => {
    const out = computeSurfaceObscuration(
      {
        visibilityM: null,
        temperature2mC: 6,
        dewpoint2mC: 5.5,
        cloudCoverLowPct: 70,
      },
      [],
      0,
    );
    expect(out).toBeNull();
  });

  it('DD=2.5°C does not fire (above threshold)', () => {
    const out = computeSurfaceObscuration(
      {
        visibilityM: null,
        temperature2mC: 6,
        dewpoint2mC: 3.5,
        cloudCoverLowPct: 100,
      },
      [],
      0,
    );
    expect(out).toBeNull();
  });

  it('visibility wins when both triggers fire (primary)', () => {
    const out = computeSurfaceObscuration(
      {
        visibilityM: 600,
        temperature2mC: 6,
        dewpoint2mC: 5.9,
        cloudCoverLowPct: 100,
      },
      [],
      0,
    );
    expect(out!.severity).toBe('lifr');
    expect(out!.reason).toBe('visibility');
  });

  it('does NOT fire when vis is good (>=5km) even with saturated low cloud', () => {
    // Models that report visibility (GFS / ICON / UKMO): if vis is good,
    // the secondary trigger must NOT fire — a 100% low-cloud forecast
    // with near-zero DD at 8 km vis is "low stratus, not fog".
    const out = computeSurfaceObscuration(
      {
        visibilityM: 8000,
        temperature2mC: 6,
        dewpoint2mC: 5.9,
        cloudCoverLowPct: 100,
      },
      [],
      0,
    );
    expect(out).toBeNull();
  });
});

describe('computeSurfaceObscuration — band geometry', () => {
  it('top floors at terrain + 500 ft when no usable levels', () => {
    const out = computeSurfaceObscuration(
      { ...CLEAR, visibilityM: 60 },
      [],
      364,
    );
    expect(out!.baseFt).toBe(364);
    // No levels passed → cap=terrain+1500, no level constraint, but floor
    // is also terrain+500. Cap wins over floor in this scenario.
    expect(out!.topFt).toBe(364 + 1500);
  });

  it('top clamps at floor when DD-dries-out is below floor', () => {
    // Level just above terrain with DD=5 — would clamp top to that level
    // (terrain+200), but floor enforces terrain+500.
    const levels: ObscurationLevel[] = [
      { altitudeFt: 564, ddC: 5 },
    ];
    const out = computeSurfaceObscuration(
      { ...CLEAR, visibilityM: 60 },
      levels,
      364,
    );
    expect(out!.topFt).toBe(864); // 364 + 500 floor
  });

  it('top clamps to first DD-dries-out level when above floor', () => {
    // First level at 1200ft AGL with DD=5 — should clamp top there.
    const levels: ObscurationLevel[] = [
      { altitudeFt: 1564, ddC: 5 },
    ];
    const out = computeSurfaceObscuration(
      { ...CLEAR, visibilityM: 60 },
      levels,
      364,
    );
    expect(out!.topFt).toBe(1564);
  });

  it('top capped at terrain + 1500 ft when no constraint kicks in', () => {
    // No DD>4 in the levels and lowest level is high → cap = terrain+1500
    // wins over both DD-dries-out (no level fires) and lowest-level (3000).
    const levels: ObscurationLevel[] = [
      { altitudeFt: 3000, ddC: 1 },
      { altitudeFt: 5000, ddC: 2 },
    ];
    const out = computeSurfaceObscuration(
      { ...CLEAR, visibilityM: 60 },
      levels,
      364,
    );
    // Cap = 364 + 1500 = 1864; lowest level = 3000; min = 1864.
    expect(out!.topFt).toBe(1864);
  });

  it('lowest-level constraint clamps top below cap when level is above floor', () => {
    // Level at 1200ft (well above floor=864) and no DD>4 → top clamps to
    // lowest level's altitude.
    const levels: ObscurationLevel[] = [
      { altitudeFt: 1200, ddC: 1 },
    ];
    const out = computeSurfaceObscuration(
      { ...CLEAR, visibilityM: 60 },
      levels,
      364,
    );
    expect(out!.topFt).toBe(1200);
  });

  it('floor wins when lowest level sits below terrain+500 (fog under grid)', () => {
    // Level at 800ft (just above 364ft terrain) is below floor=864 →
    // floor enforces minimum visible thickness.
    const levels: ObscurationLevel[] = [
      { altitudeFt: 800, ddC: 1 },
    ];
    const out = computeSurfaceObscuration(
      { ...CLEAR, visibilityM: 60 },
      levels,
      364,
    );
    expect(out!.topFt).toBe(864);
  });
});

describe('computeSurfaceObscuration — surface RH derivation', () => {
  it('computes RH from T/Td via Magnus formula', () => {
    const out = computeSurfaceObscuration(
      { visibilityM: 100, temperature2mC: 10, dewpoint2mC: 10, cloudCoverLowPct: null },
      [],
      0,
    );
    expect(out!.surfaceRhPct).not.toBeNull();
    expect(out!.surfaceRhPct).toBeCloseTo(100, 0);
  });

  it('null RH when surface T/Td absent', () => {
    const out = computeSurfaceObscuration(
      { visibilityM: 100, temperature2mC: null, dewpoint2mC: null, cloudCoverLowPct: null },
      [],
      0,
    );
    expect(out!.surfaceRhPct).toBeNull();
  });
});

describe('computeSurfaceObscurationFromCloudLayers — cloudLayer adapter', () => {
  it('treats meanDewpointDepressionC as the per-level DD', () => {
    const out = computeSurfaceObscurationFromCloudLayers(
      { ...CLEAR, visibilityM: 60 },
      [
        { baseFt: 1000, topFt: 2000, coverage: 'ovc', meanDewpointDepressionC: 5 },
      ],
      364,
    );
    // Level above terrain at 1000 with DD=5 → clamps top there.
    expect(out!.topFt).toBe(1000);
  });

  it('ignores cloud layers below terrain', () => {
    const out = computeSurfaceObscurationFromCloudLayers(
      { ...CLEAR, visibilityM: 60 },
      [
        { baseFt: 100, topFt: 200, coverage: 'ovc', meanDewpointDepressionC: 5 },
      ],
      500,
    );
    // Below-terrain layer ignored → falls through to cap.
    expect(out!.topFt).toBe(500 + 1500);
  });
});

describe('LFAQ canonical repro from issue #125', () => {
  it('vis=560m, low_cc=100%, DD=0.1°C, terrain=364 → red band, base=364', () => {
    const out = computeSurfaceObscuration(
      {
        visibilityM: 560,
        temperature2mC: 6.3,
        dewpoint2mC: 6.2,
        cloudCoverLowPct: 100,
      },
      [],
      364,
    );
    expect(out).not.toBeNull();
    expect(out!.severity).toBe('lifr');
    expect(out!.reason).toBe('visibility');
    expect(out!.baseFt).toBe(364);
    expect(out!.topFt).toBeGreaterThanOrEqual(364 + 500);
    expect(out!.visM).toBe(560);
    expect(out!.surfaceTC).toBe(6.3);
    expect(out!.surfaceTdC).toBe(6.2);
  });
});
