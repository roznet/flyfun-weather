import { describe, expect, it } from 'vitest';
import {
  TREND_COLOURS,
  cellPopupHtml,
  cellsBadge,
  cellsChip,
  cellsLegendHtml,
  frameKey,
  hasArrow,
  matchCellFrame,
  motionText,
  trendColour,
  trendText,
  type CellFramesInfo,
  type DisplayCell,
} from '../../ts/visualization/cells-overlay-core';
import { flashBox } from '../../ts/visualization/route-map/observed-overlay-geometry';

const NOW = new Date('2026-10-04T12:00:00Z');

function frame(hhmm: string) {
  const [h, m] = hhmm.split(':');
  const valid = `2026-10-04T${h}:${m}:00+00:00`;
  return { stamp: `20261004T${h}${m}`, valid_time: valid, received_at: valid, age_minutes: 0 };
}

function info(stamps: string[], extra: Partial<CellFramesInfo> = {}): CellFramesInfo {
  const frames = stamps.map(frame);
  return {
    enabled: true, frames, newest: frames[0] ?? null, stale: false, stale_after_minutes: 25,
    unavailable_since: null, url_template: '/api/observed/cells/{stamp}.json', ...extra,
  };
}

function cell(over: Partial<DisplayCell> = {}): DisplayCell {
  return {
    id: 'core41-x', tier: 'core41', lat: 50, lon: 2, area_km2: 40, peak_dbz: 52, rate_peak_mm_h: 30,
    flashes: 3, top_fl: null, truncated: false, age_min: 25, event: 'continued',
    trend: { state: 'developing', window_min: 30, d_peak_db: 6, area_ratio: 1.6, d_flashes: 2 },
    motion: { status: 'available', reason: null, speed_kt: 9, toward_deg: 45 },
    arrow: [50.1, 2.1],
    ...over,
  };
}

describe('trend colours', () => {
  it('match the prototype exactly', () => {
    expect(TREND_COLOURS).toEqual({
      developing: '#d7263d', decaying: '#1b6ca8', steady: '#7a7a7a', mixed: '#f18f01', new: '#ffffff',
    });
  });
  it('fall back to neutral grey for an unknown state, never a red/green', () => {
    expect(trendColour('mature')).toBe('#cccccc');
    expect(trendColour(null)).toBe('#cccccc');
  });
});

describe('motion arrow', () => {
  it('is drawn only when motion is available', () => {
    expect(hasArrow(cell())).toBe(true);
    for (const status of ['withheld', 'unsupported', 'no_pair']) {
      expect(hasArrow(cell({ motion: { status, reason: 'x', speed_kt: 9, toward_deg: 45 } }))).toBe(false);
    }
    expect(hasArrow(cell({ arrow: null }))).toBe(false);
  });
});

describe('matchCellFrame (time alignment)', () => {
  it('takes the overlay with the same stamp as the drawn radar frame', () => {
    const m = matchCellFrame(info(['11:50', '11:45', '11:40']), '20261004T1145', NOW);
    expect(m).toEqual({ state: 'ok', frame: frame('11:45') });
  });
  it('else the newest at or before the radar frame', () => {
    const m = matchCellFrame(info(['11:45', '11:40']), '20261004T1150', NOW);
    expect(m.state === 'ok' && m.frame.stamp).toBe('20261004T1145');
  });
  it('never an overlay newer than the radar frame', () => {
    const m = matchCellFrame(info(['11:55', '11:40']), '20261004T1150', NOW);
    expect(m.state === 'ok' && m.frame.stamp).toBe('20261004T1140');
  });
  it('takes the newest when no radar is drawn', () => {
    const m = matchCellFrame(info(['11:50', '11:45']), null, NOW);
    expect(m.state === 'ok' && m.frame.stamp).toBe('20261004T1150');
  });
  it('draws nothing past the stale threshold and says since when', () => {
    const m = matchCellFrame(info(['11:30', '11:25']), null, NOW);
    expect(m).toEqual({ state: 'unavailable', since: '2026-10-04T11:30:00+00:00' });
    expect(cellsBadge(m, null, NOW)).toBe('Cell analysis unavailable since 11:30Z');
  });
  it('a matched but old overlay is unavailable too', () => {
    const m = matchCellFrame(info(['11:50', '11:20']), '20261004T1145', NOW);
    expect(m.state).toBe('unavailable');
  });
  it('reports disabled and missing feeds in words', () => {
    const disabled = matchCellFrame(info([], { enabled: false }), null, NOW);
    expect(disabled).toEqual({ state: 'disabled' });
    expect(cellsBadge(disabled, null, NOW)).toBe('Cell analysis: not available on this server');
    const empty = matchCellFrame(info([]), null, NOW);
    expect(cellsBadge(empty, null, NOW)).toBe('Cell analysis unavailable (nothing received yet)');
    expect(matchCellFrame(null, null, NOW).state).toBe('unavailable');
  });
});

describe('revisions (#666)', () => {
  it('the display URL key is the newest revision, the bare stamp on an older server', () => {
    const base = { stamp: '20261004T1145', valid_time: '2026-10-04T11:45:00+00:00', received_at: '', age_minutes: 15 };
    expect(frameKey({ ...base, key: '20261004T1145.r1', revision: 1 })).toBe('20261004T1145.r1');
    expect(frameKey(base)).toBe('20261004T1145');
  });
  it('lightning on its way is "pending", never "no lightning"', () => {
    const m = matchCellFrame(info(['11:45']), '20261004T1150', NOW);
    const display = { cells: [], pending: ['lightning'], unavailable: [] } as unknown as Parameters<typeof cellsBadge>[1];
    const text = cellsBadge(m, display, NOW);
    expect(text).toContain('lightning pending');
    expect(text).not.toContain('no lightning');
    const gone = { cells: [], pending: [], unavailable: [{ what: 'lightning', reason: 'unreadable' }] } as unknown as Parameters<typeof cellsBadge>[1];
    expect(cellsBadge(m, gone, NOW)).toContain('lightning unavailable');
    expect(cellPopupHtml(cell({ flashes: null, flashes_pending: true }))).toContain('lightning pending');
    expect(cellPopupHtml(cell({ flashes: null }))).toContain('lightning –');
  });
});

describe('badge', () => {
  it('carries the overlay\'s own time and says experimental', () => {
    const m = matchCellFrame(info(['11:45']), '20261004T1150', NOW);
    const text = cellsBadge(m, null, NOW);
    expect(text).toContain('11:45Z');
    expect(text).toContain('15 min old');
    expect(text).toContain('experimental');
  });
});

describe('words', () => {
  it('motion is plain language', () => {
    expect(motionText(cell().motion)).toBe('moving NE at 9 kt');
    expect(motionText({ status: 'withheld', reason: 'split on this frame', speed_kt: null, toward_deg: null }))
      .toBe('motion not yet measured');
    expect(motionText({ status: 'unsupported', reason: 'only 30%', speed_kt: null, toward_deg: null }))
      .toBe('motion withheld: too little of the cell in matched tiles');
    expect(motionText({ status: 'available', reason: null, speed_kt: 0.5, toward_deg: null })).toBe('nearly stationary');
  });
  it('trend carries its numbers', () => {
    expect(trendText(cell().trend)).toBe('developing over 30 min (peak +6 dB, area ×1.6, flashes +2)');
    expect(trendText({ state: 'new', window_min: null })).toContain('new');
  });
  it('popup: the rain rate says its own time only when it is not the radar\'s', () => {
    expect(cellPopupHtml(cell())).not.toContain('as of');
    expect(cellPopupHtml(cell({ rate_peak_mm_h: 12, rate_as_of: '2026-10-04T11:30:00+00:00' }))).toContain('12 mm/h (as of 11:30Z)');
    expect(cellPopupHtml(cell({ rate_peak_mm_h: null, rate_as_of: '2026-10-04T11:30:00+00:00' }))).not.toContain('as of');
  });
  it('popup: coverage edge and cloud top only when present', () => {
    expect(cellPopupHtml(cell())).not.toContain('outside radar coverage');
    expect(cellPopupHtml(cell({ truncated: true }))).toContain('partly outside radar coverage');
    expect(cellPopupHtml(cell())).not.toContain('cloud top');
    expect(cellPopupHtml(cell({ top_fl: 310 }))).toContain('cloud top FL310');
    expect(cellPopupHtml(cell({ event: '<script>' }))).not.toContain('<script>');
  });
  it('popup: whole-number dBZ (#689)', () => {
    expect(cellPopupHtml(cell({ peak_dbz: 75.5 }))).toContain('peak <b>76 dBZ</b>');
    expect(cellPopupHtml(cell({ peak_dbz: null }))).toContain('peak <b>–</b>');
  });
  it('legend explains colours, tiers and the arrow, and is labelled experimental', () => {
    const html = cellsLegendHtml();
    for (const word of ['developing', 'decaying', 'steady', 'mixed', 'new', 'experimental', 'position in 30 min']) {
      expect(html).toContain(word);
    }
  });
});

describe('flashBox', () => {
  it('passes a small view through and clamps a wide one around its centre', () => {
    const small = { south: 45, west: 0, north: 50, east: 10 };
    expect(flashBox(small)).toEqual(small);
    const wide = flashBox({ south: 20, west: -60, north: 75, east: 60 });
    expect(wide.east - wide.west).toBe(80);
    expect((wide.east + wide.west) / 2).toBe(0);
  });
});

describe('cells summary chip', () => {
  it("carries the overlay's own age; anything but ok is a warning", () => {
    const ok = cellsChip({ state: 'ok', frame: { stamp: '20261004T1150', valid_time: '2026-10-04T11:50:00+00:00' } } as any, new Date('2026-10-04T12:00:00Z'));
    expect(ok).toEqual({ name: 'Cells', status: '10 min', warning: false });
    expect(cellsChip({ state: 'disabled' }, NOW)).toEqual({ name: 'Cells', status: 'n/a', warning: true });
    expect(cellsChip({ state: 'unavailable', since: null }, NOW).warning).toBe(true);
  });
});
