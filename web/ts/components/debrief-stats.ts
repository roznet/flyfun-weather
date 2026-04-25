/** Stats panel — activity + cancellation reasons + per-category accuracy. */

import type { ConditionTagId, DebriefStats } from '../store/types';
import { TAG_LABELS } from './debrief-taxonomy';
import { escapeHtml } from '../utils';

export function renderDebriefStats(s: DebriefStats): string {
  if (s.total_flights_in_window === 0) {
    return `
      <div class="debrief-stats">
        <div class="debrief-stats-empty">No flights in the last ${s.window_days} days.</div>
      </div>
    `;
  }

  const monitoringPart = s.monitoring_count > 0
    ? ` · <strong>${s.monitoring_count}</strong> monitor only`
    : '';
  const activity = `
    <div class="debrief-stats-row">
      <div class="debrief-stats-label">Activity</div>
      <div class="debrief-stats-value">
        <strong>${s.flown_count}</strong> flown ·
        <strong>${s.cancelled_count}</strong> cancelled ·
        <strong>${s.pending_debrief_count}</strong> pending${monitoringPart}
      </div>
    </div>
  `;

  const reasonItems = Object.entries(s.cancellation_reasons)
    .sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0))
    .map(([tag, count]) => `<span class="debrief-stats-reason">${escapeHtml(tag)} ×${count}</span>`)
    .join(' ');
  const reasons = s.cancelled_count > 0
    ? `
      <div class="debrief-stats-row">
        <div class="debrief-stats-label">Cancelled</div>
        <div class="debrief-stats-value">${reasonItems || '<em>no tags</em>'}</div>
      </div>
    `
    : '';

  const accuracyEntries = Object.entries(s.category_accuracy);
  const accuracy = accuracyEntries.length > 0
    ? `
      <div class="debrief-stats-row">
        <div class="debrief-stats-label">Forecast accuracy</div>
        <div class="debrief-stats-value">
          ${accuracyEntries.map(([tag, cat]) => accuracyBar(tag as ConditionTagId, cat!)).join('')}
        </div>
      </div>
      <div class="debrief-stats-caveat">Based on flagged exceptions only — categories not flagged are assumed consistent.</div>
    `
    : '';

  return `
    <div class="debrief-stats">
      <div class="debrief-stats-header">Last ${s.window_days} days</div>
      ${activity}
      ${reasons}
      ${accuracy}
    </div>
  `;
}

function accuracyBar(tag: ConditionTagId, c: { queried_count: number; consistent: number; better: number; worse: number }): string {
  const total = Math.max(c.queried_count, 1);
  const pct = (n: number) => (n / total) * 100;
  return `
    <div class="debrief-acc-row">
      <div class="debrief-acc-label">${escapeHtml(TAG_LABELS[tag])}</div>
      <div class="debrief-acc-bar" title="${c.queried_count} flights">
        <span class="debrief-acc-seg debrief-acc-consistent" style="width:${pct(c.consistent)}%"></span>
        <span class="debrief-acc-seg debrief-acc-better" style="width:${pct(c.better)}%"></span>
        <span class="debrief-acc-seg debrief-acc-worse" style="width:${pct(c.worse)}%"></span>
      </div>
      <div class="debrief-acc-counts">
        <span class="debrief-acc-count-consistent">${c.consistent}</span> ·
        <span class="debrief-acc-count-better">${c.better}</span> ·
        <span class="debrief-acc-count-worse">${c.worse}</span>
      </div>
    </div>
  `;
}
