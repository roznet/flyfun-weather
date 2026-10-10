import { describe, expect, it } from 'vitest';
import { defaultHop, fmtLatency, renderLiveLatency } from '../../ts/admin-latency-view';
import type { LiveLatencyReport } from '../../ts/adapters/admin-adapter';

const report: LiveLatencyReport = {
  days: 30,
  generated_at: '2026-10-10T12:00:00+00:00',
  hops: [
    { key: 'report_to_fetched:metar', label: 'Report time → fetched (metar)', n: 40, p50: 300, p95: 900, max: 1500 },
    { key: 'end_to_end:metar:web', label: 'Report time → delivered (metar, web)', n: 3, p50: 700, p95: 800, max: 800 },
    { key: 'end_to_end:metar:ios', label: 'Report time → delivered (metar, ios)', n: 5, p50: 780, p95: 1200, max: 1300 },
  ],
  daily: [
    { day: '2026-10-09', hops: { 'end_to_end:metar:ios': { n: 2, p50: 600, p95: 700, max: 700 } } },
    { day: '2026-10-08', hops: {} },
  ],
  counts: { tick_rows: 10, ticks: 4, deliveries: 7, dropped: { 'available_to_delivered:untracked': 1 } },
};

describe('observed latency view (#751)', () => {
  it('formats seconds, minutes and hours', () => {
    expect(fmtLatency(null)).toBe('-');
    expect(fmtLatency(42)).toBe('42.0s');
    expect(fmtLatency(185)).toBe('3m05s');
    expect(fmtLatency(3720)).toBe('1h02m');
  });

  it('leads with METAR end to end on iOS', () => {
    expect(defaultHop(report)).toBe('end_to_end:metar:ios');
    expect(defaultHop({ ...report, hops: report.hops.slice(0, 2) })).toBe('end_to_end:metar:web');
  });

  it('renders every hop, the counts, and the selected hop per day', () => {
    const html = renderLiveLatency(report, null);
    expect(html).toContain('Report time → fetched (metar)');
    expect(html).toContain('4 ticks');
    expect(html).toContain('available_to_delivered:untracked: 1');
    // The daily trend shows the selected hop; a day without samples shows n=0.
    expect(html).toContain('<td>2026-10-09</td><td class="num">2</td><td class="num">10m00s</td>');
    expect(html).toContain('<td>2026-10-08</td><td class="num">0</td>');
  });

  it('says so when nothing was recorded', () => {
    expect(renderLiveLatency({ ...report, hops: [], daily: [] }, null)).toContain('No live ticks recorded');
  });
});
