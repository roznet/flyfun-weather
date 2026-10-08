import { describe, it, expect } from 'vitest';
import {
  ARROW_MIN_GAP,
  INSET,
  TRACK_Y,
  ZONE_BOTTOM,
  ZONE_TOP,
  bandAnchor,
  bandRects,
  dbzColour,
  motionArrowDir,
  phaseLabel,
  radarFill,
  relativeMotionText,
  ribbonArrows,
  roundHalfAway,
  ribbonCaption,
  segmentFocusAt,
  segmentLabel,
  sigmetText,
  STATION_HIT_RADIUS,
  STORM_HIT_RADIUS,
  stationLabel,
  stationMarkSize,
  stormMarkSize,
  stormMotionText,
  stormPositionText,
  stormTargets,
  weatherAvailable,
  weatherSummary,
  xForNm,
  yForCross,
} from '../../ts/visualization/observed/ribbon-core';
import type {
  LiveRibbon,
  LiveRibbonSegment,
  LiveStorm,
  RibbonStation,
  RibbonWeather,
} from '../../ts/store/types';

const WIDTH = 520;

function storm(over: Partial<LiveStorm> = {}): LiveStorm {
  return {
    id: 'c1', cell_ids: ['c1'], lat: 50, lon: 0, peak_dbz: 47,
    along_nm: 60, offtrack_nm: 25, cross_nm: -25, side: 'left',
    relative_motion: 'moving_away', closing_kt: -15, ...over,
  };
}

function band(over: Partial<RibbonWeather> = {}): RibbonWeather {
  return {
    id: 'b1', tier: 'core', from_nm: 10, to_nm: 30, side: 'left',
    near_nm: 5, far_nm: 20, peak_dbz: 47, ...over,
  };
}

function ribbon(over: Partial<LiveRibbon> = {}): LiveRibbon {
  return {
    route_nm: 100, segment_nm: 25, radar_radius_nm: 10,
    weather_status: 'available', weather_corridor_nm: 30, weather_bin_nm: 5, ...over,
  };
}

describe('ribbon geometry', () => {
  it('maps along-route distance across the drawing, clamped at both ends', () => {
    expect(xForNm(0, 100, WIDTH)).toBe(INSET);
    expect(xForNm(100, 100, WIDTH)).toBe(WIDTH - INSET);
    expect(xForNm(50, 100, WIDTH)).toBeCloseTo((INSET + WIDTH - INSET) / 2);
    // A cell past the route's end sits ON the end, never off the drawing.
    expect(xForNm(180, 100, WIDTH)).toBe(WIDTH - INSET);
    expect(xForNm(-20, 100, WIDTH)).toBe(INSET);
  });

  it('never divides by a zero route length', () => {
    expect(Number.isFinite(xForNm(10, 0, WIDTH))).toBe(true);
  });

  it('puts left of course above the line and right below', () => {
    expect(yForCross(0, 30)).toBe(TRACK_Y);
    expect(yForCross(-30, 30)).toBe(ZONE_TOP);
    expect(yForCross(30, 30)).toBe(ZONE_BOTTOM);
    expect(yForCross(-15, 30)).toBeCloseTo(TRACK_Y - (TRACK_Y - ZONE_TOP) / 2);
    // Beyond the corridor clamps to its edge rather than drawing outside.
    expect(yForCross(-90, 30)).toBe(ZONE_TOP);
    expect(yForCross(90, 30)).toBe(ZONE_BOTTOM);
  });
});

describe('colour ladders', () => {
  // The boundaries are the cell tiers and are shared with iOS exactly.
  it('steps at 35, 41 and 50 dBZ', () => {
    expect(dbzColour(34.9)).toBe('#3cbe5a');
    expect(dbzColour(35)).toBe('#f0d23c');
    expect(dbzColour(41)).toBe('#f08c28');
    expect(dbzColour(50)).toBe('#e13c3c');
    expect(dbzColour(75.5)).toBe('#e13c3c');
  });

  it('distinguishes "the radar could not see here" from "nothing there"', () => {
    const base: LiveRibbonSegment = { index: 0, from_nm: 0, to_nm: 25 };
    expect(radarFill({ ...base, radar_status: 'no_coverage' }))
      .toBe('rgba(136, 136, 136, 0.35)');
    // Measured, nothing detected: faint, but not the no-coverage grey.
    expect(radarFill({ ...base, radar_status: 'measured', radar_max_dbz: null }))
      .toBe('rgba(60, 190, 90, 0.08)');
    expect(radarFill({ ...base, radar_status: 'no_sample' })).toBe('transparent');
    expect(radarFill({ ...base, radar_status: 'measured', radar_max_dbz: 47 })).toBe('#f08c28');
  });
});

describe('motion marks', () => {
  it('points the arrow at the track when closing and away when leaving', () => {
    // cross < 0 is left of course, drawn ABOVE the line: closing is downward.
    expect(motionArrowDir(storm({ relative_motion: 'closing' }), -25)).toBe('down');
    expect(motionArrowDir(storm({ relative_motion: 'moving_away' }), -25)).toBe('up');
    expect(motionArrowDir(storm({ relative_motion: 'closing' }), 25)).toBe('up');
    expect(motionArrowDir(storm({ relative_motion: 'moving_away' }), 25)).toBe('down');
  });

  it('draws no arrow when the motion was not measured', () => {
    expect(motionArrowDir(storm({ relative_motion: 'unknown' }), 10)).toBeNull();
    expect(motionArrowDir(storm({ relative_motion: 'parallel' }), 10)).toBeNull();
  });

  it('sizes the marker by strength', () => {
    expect(stormMarkSize(20)).toBe(10);
    expect(stormMarkSize(41)).toBe(14);
    expect(stormMarkSize(50)).toBe(18);
    expect(stormMarkSize(null)).toBe(10);
  });

  it('draws the route\'s ends larger than the airports in the rows', () => {
    expect(stationMarkSize({ icao: 'EGTF', role: 'departure' })).toBe(16);
    expect(stationMarkSize({ icao: 'EGLF', role: 'destination' })).toBe(16);
    expect(stationMarkSize({ icao: 'EGLK', role: 'route' })).toBe(10);
    expect(stationMarkSize({ icao: 'EGLK', role: 'alternate' })).toBe(10);
  });

  it('gives every mark a target well above its drawn size', () => {
    // A 10 px disc is far under the 44 px guidance on its own.
    expect(STORM_HIT_RADIUS * 2).toBeGreaterThanOrEqual(stormMarkSize(20) * 2);
    expect(STATION_HIT_RADIUS * 2).toBeGreaterThanOrEqual(stationMarkSize({ icao: 'X', role: 'route' }));
  });
});

describe('rounding parity with Swift', () => {
  // The whole point of the mirrored pair is that the label strings match.
  // JS `Math.round` breaks ties toward +∞; Swift's `.rounded()` breaks them
  // away from zero. Verified against a real Swift run: (-2.5).rounded() = -3.
  it('breaks ties away from zero, as Swift does', () => {
    expect(roundHalfAway(2.5)).toBe(3);
    expect(roundHalfAway(-2.5)).toBe(-3);
    expect(roundHalfAway(0.5)).toBe(1);
    expect(roundHalfAway(-0.5)).toBe(-1);
    expect(roundHalfAway(2.4)).toBe(2);
    expect(roundHalfAway(-2.4)).toBe(-2);
    // This is where plain Math.round would have disagreed.
    expect(roundHalfAway(-2.5)).not.toBe(Math.round(-2.5));
  });

  it('never prints a negative zero', () => {
    expect(`${roundHalfAway(-0.2)}`).toBe('0');
    expect(`${roundHalfAway(-0)}`).toBe('0');
  });

  it('rounds a negative reflectivity in a segment label the Swift way', () => {
    // Reflectivity is the one mirrored input that can go negative.
    expect(segmentLabel({ index: 0, from_nm: 0, to_nm: 25,
      radar_status: 'measured', radar_max_dbz: -2.5 }))
      .toBe('0–25 NM: radar peak -3 dBZ');
  });
});

describe('words', () => {
  it('names a stretch of route by what the radar did there', () => {
    const base: LiveRibbonSegment = { index: 0, from_nm: 0, to_nm: 25 };
    expect(segmentLabel({ ...base, radar_status: 'measured', radar_max_dbz: 47 }))
      .toBe('0–25 NM: radar peak 47 dBZ');
    expect(segmentLabel({ ...base, radar_status: 'measured' })).toBe('0–25 NM: no radar echo');
    expect(segmentLabel({ ...base, radar_status: 'no_coverage' }))
      .toBe('0–25 NM: radar coverage insufficient');
    expect(segmentLabel({ ...base, radar_status: 'no_sample' })).toBe('0–25 NM: no radar sample');
  });

  it('says "METAR unavailable" rather than implying it is clear', () => {
    const st: RibbonStation = { icao: 'LPPR', role: 'departure' };
    expect(stationLabel(st)).toBe('LPPR METAR unavailable');
  });

  it('reads an en-route station with its convective groups and side of course', () => {
    const st: RibbonStation = {
      icao: 'LFMT', role: 'route', metar_category: 'VFR', convective: ['CB', 'TS'],
      taf_category_at_eta: 'MVFR', taf_temporary_type: 'PROB30',
      taf_temporary_category: 'IFR', cross_nm: -12,
    };
    expect(stationLabel(st))
      .toBe('LFMT VFR CB TS TAF at ETA MVFR PROB30 IFR 12 NM left of course');
  });

  it('places a cell against the track, or off an end from its airport', () => {
    expect(stormPositionText(storm())).toBe('25 NM left of track at 60 NM');
    expect(stormPositionText(storm({ side: null }))).toBe('on track at 60 NM');
    expect(stormPositionText(storm({ end: 'departure', end_icao: 'LPPR', end_bearing: 'NE' })))
      .toBe('25 NM NE of LPPR');
  });

  it('states observed motion, and says so when there is none', () => {
    expect(stormMotionText(storm())).toBe('moving away 15 kt');
    expect(stormMotionText(storm({ relative_motion: 'closing', closing_kt: 8 })))
      .toBe('closing 8 kt');
    expect(stormMotionText(storm({ relative_motion: 'closing', closing_kt: null })))
      .toBe('closing');
    expect(stormMotionText(storm({ relative_motion: 'parallel' }))).toBe('moving along the track');
    expect(stormMotionText(storm({ relative_motion: 'stationary' }))).toBe('nearly stationary');
    expect(stormMotionText(storm({ relative_motion: 'unknown' }))).toBe('motion not yet measured');
  });

  it('turns motion relative to the course into words at 30° and 150°', () => {
    expect(relativeMotionText(0)).toBe('moving along the course');
    expect(relativeMotionText(30)).toBe('moving along the course');
    expect(relativeMotionText(31)).toBe('drifting toward the right of course');
    expect(relativeMotionText(-31)).toBe('drifting toward the left of course');
    expect(relativeMotionText(150)).toBe('moving against the course');
    expect(relativeMotionText(-179)).toBe('moving against the course');
  });

  it('summarises the zones, and says nothing is there without claiming "clear"', () => {
    expect(weatherSummary([])).toBe('No rain or cells within the corridor');
    expect(weatherSummary([band({ tier: 'rain', peak_dbz: 22 }), band({ peak_dbz: 47 })]))
      .toBe('1 rain areas, 1 cells along the route, strongest 47 dBZ');
  });

  it('names a SIGMET by its FIR and hazard, falling back to "SIGMET"', () => {
    expect(sigmetText({ id: 'sigmet:LECM|6', label: 'LECM 6: EMBD TS', hazard: 'TS', qualifier: 'EMBD' }))
      .toBe('LECM 6 EMBD TS');
    expect(sigmetText({ id: 'sigmet:LECM|6', label: 'LECM 6: ???' })).toBe('LECM 6 SIGMET');
  });

  it('drops an empty hazard rather than leaving a stray separator', () => {
    // Swift `compactMap` keeps "" where the web's `filter(Boolean)` drops it;
    // both now drop it, so neither emits a double space.
    expect(sigmetText({ id: 'sigmet:LECM|6', label: 'LECM 6: x', qualifier: 'EMBD', hazard: '' }))
      .toBe('LECM 6 EMBD');
    expect(sigmetText({ id: 'sigmet:LECM|6', label: 'LECM 6: x', qualifier: '', hazard: '' }))
      .toBe('LECM 6 SIGMET');
  });

  it('captions the ribbon with the scale it is actually drawn to', () => {
    const z = () => '14:29Z';
    expect(ribbonCaption(ribbon({ weather: [], radar_time: '2026-10-02T14:29:00Z' }), 30, z))
      .toBe('±30 NM of course · 14:29Z');
    // No cell bands: the caption must describe the radar strip instead.
    expect(ribbonCaption(ribbon({ weather_status: 'stale', radar_time: null }), 30, z))
      .toBe('radar ≤10 NM');
  });

  it('labels the phases the way the nutshell reads them', () => {
    // Title case: the CSS shouts it, so the accessible name stays readable.
    expect(phaseLabel('departure')).toBe('Departure');
    expect(phaseLabel('enroute')).toBe('En route');
    expect(phaseLabel('arrival')).toBe('Arrival');
  });
});

describe('weather bands', () => {
  const profiled = ribbon({
    weather: [
      band({ id: 'rain', tier: 'rain', profile: [[15, -20, -5]] }),
      band({ id: 'core', tier: 'core', profile: [[15, -12, -8]] }),
    ],
  });

  it('is only drawable when the cells feed says so', () => {
    expect(weatherAvailable(profiled)).toBe(true);
    expect(weatherAvailable(ribbon({ weather_status: 'stale', weather: [] }))).toBe(false);
    expect(weatherAvailable(ribbon({ weather_status: 'available', weather: undefined }))).toBe(false);
  });

  it('paints rain first so cores stay visible on top', () => {
    const rects = bandRects(profiled, WIDTH);
    expect(rects).toHaveLength(2);
    expect(rects[0].fill).toBe('rgba(60, 190, 90, 0.28)');
    expect(rects[1].fill).toBe('#f08c28');
  });

  it('gives a hairline bin a visible minimum size', () => {
    const [rect] = bandRects(
      ribbon({ weather: [band({ profile: [[15, -10, -10]] })], weather_bin_nm: 0 }),
      WIDTH,
    );
    expect(rect.width).toBeGreaterThanOrEqual(1.5);
    expect(rect.height).toBeGreaterThanOrEqual(3);
  });

  it('skips a malformed profile bin instead of drawing a NaN rect', () => {
    const rects = bandRects(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ribbon({ weather: [band({ profile: [[15, -10] as any, [20, -10, -5]] })] }),
      WIDTH,
    );
    expect(rects).toHaveLength(1);
    expect(rects.every((r) => Number.isFinite(r.x) && Number.isFinite(r.y))).toBe(true);
  });

  it('anchors a band on its widest bin', () => {
    const at = bandAnchor(band({ profile: [[10, -6, -5], [40, -25, -5]] }), ribbon(), WIDTH);
    expect(at).not.toBeNull();
    expect(at!.x).toBeCloseTo(xForNm(40, 100, WIDTH));
  });

  it('has no anchor for a band with no profile', () => {
    expect(bandAnchor(band({ profile: [] }), ribbon(), WIDTH)).toBeNull();
  });
});

describe('arrows', () => {
  it('draws cores before rain, strongest first', () => {
    const arrows = ribbonArrows(ribbon({
      weather: [
        band({ id: 'weak', peak_dbz: 36, motion_rel_deg: 90, profile: [[10, -10, -5]] }),
        band({ id: 'strong', peak_dbz: 55, motion_rel_deg: 90, profile: [[90, -10, -5]] }),
      ],
    }), WIDTH);
    expect(arrows.map((a) => a.id)).toEqual(['strong', 'weak']);
  });

  it('drops an arrow that would sit on top of another', () => {
    const arrows = ribbonArrows(ribbon({
      weather: [
        band({ id: 'a', peak_dbz: 55, motion_rel_deg: 90, profile: [[50, -10, -5]] }),
        band({ id: 'b', peak_dbz: 50, motion_rel_deg: 90, profile: [[50, -10, -5]] }),
      ],
    }), WIDTH);
    expect(arrows).toHaveLength(1);
    expect(arrows[0].id).toBe('a');
    expect(ARROW_MIN_GAP).toBe(16);
  });

  it('leaves a short rain area without one, but arrows a long band', () => {
    const short = ribbonArrows(ribbon({
      weather: [band({ id: 'r', tier: 'rain', from_nm: 0, to_nm: 9, motion_rel_deg: 45, profile: [[5, -8, -2]] })],
    }), WIDTH);
    expect(short).toHaveLength(0);
    const long = ribbonArrows(ribbon({
      weather: [band({ id: 'r', tier: 'rain', from_nm: 0, to_nm: 40, motion_rel_deg: 45, profile: [[20, -8, -2]] })],
    }), WIDTH);
    expect(long).toHaveLength(1);
    expect(long[0].core).toBe(false);
  });

  it('never invents an arrow for an unmeasured motion', () => {
    expect(ribbonArrows(ribbon({
      weather: [band({ motion_rel_deg: null, profile: [[15, -10, -5]] })],
    }), WIDTH)).toHaveLength(0);
  });
});

describe('tap targets', () => {
  it('offers one target per storm, however many bands it has', () => {
    const targets = stormTargets(
      ribbon({
        weather: [
          band({ id: 'b1', storm_id: 's1', profile: [[10, -10, -5]] }),
          band({ id: 'b2', storm_id: 's1', profile: [[20, -10, -5]] }),
        ],
      }),
      [storm({ id: 's1' })],
      WIDTH,
    );
    expect(targets).toHaveLength(1);
    expect(targets[0].storm.id).toBe('s1');
  });

  it('ignores a band whose storm is not in the list', () => {
    const targets = stormTargets(
      ribbon({ weather: [band({ storm_id: 'gone', profile: [[10, -10, -5]] })] }),
      [storm({ id: 's1' })],
      WIDTH,
    );
    expect(targets).toHaveLength(0);
  });

  it('frames the stretch of route under the tap', () => {
    const focus = (i: number) => ({
      kind: 'segment' as const, id: `seg:${i}`,
      bbox: [0, 50, 1, 51] as [number, number, number, number], layers: ['route'],
    });
    const r = ribbon({
      segments: [
        { index: 0, from_nm: 0, to_nm: 50, focus: focus(0) },
        { index: 1, from_nm: 50, to_nm: 100, focus: focus(1) },
      ],
    });
    expect(segmentFocusAt(r, xForNm(10, 100, WIDTH), WIDTH)?.id).toBe('seg:0');
    expect(segmentFocusAt(r, xForNm(80, 100, WIDTH), WIDTH)?.id).toBe('seg:1');
    // Past the last segment: the nearest stretch, not nothing.
    expect(segmentFocusAt(r, WIDTH + 100, WIDTH)?.id).toBe('seg:1');
  });

  it('has no segment focus when the ribbon carries no segments', () => {
    expect(segmentFocusAt(ribbon({ segments: [] }), 100, WIDTH)).toBeNull();
  });
});
