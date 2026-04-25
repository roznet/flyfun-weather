/** Render a Hewson metric's info popup using the same HTML structure /
 * CSS classes as the briefing-page metric popup (popup-section, popup-vibe,
 * popup-threshold-table) so it inherits the existing modal styling.
 *
 * Consumed by maps-main.ts via showPopupContent(html) from
 * components/info-popup.ts.
 */

import {
  HEWSON_CATALOG,
  type HewsonCatalogEntry,
  type HewsonLevelThresholds,
} from '../data/hewson-metrics-catalog';
import type { HewsonMetric } from '../visualization/hewson-colormaps';

type Risk = 'none' | 'low' | 'moderate' | 'high' | 'severe';

function riskClass(risk: Risk): string {
  // Match the existing CSS classes used by metrics-helper.ts
  switch (risk) {
    case 'none': return 'risk-none';
    case 'low': return 'risk-low';
    case 'moderate': return 'risk-moderate';
    case 'high': return 'risk-high';
    case 'severe': return 'risk-severe';
  }
}

function renderThresholdTable(rows: HewsonLevelThresholds['rows']): string {
  return `<table class="popup-threshold-table">
    <tbody>
      ${rows.map((r) => `<tr class="${riskClass(r.risk)}">
        <td class="popup-thr-range">${r.range}</td>
        <td class="popup-thr-label">${r.label}</td>
        <td>${r.meaning}</td>
      </tr>`).join('')}
    </tbody>
  </table>`;
}

function renderLevelThresholds(levels: HewsonLevelThresholds[]): string {
  return levels.map((lvl) => `
    <div class="popup-section">
      <h4>${lvl.level} hPa <span class="popup-unit">(${lvl.altitude_label})</span></h4>
      ${lvl.note ? `<p style="margin-bottom:0.4rem">${lvl.note}</p>` : ''}
      ${renderThresholdTable(lvl.rows)}
    </div>
  `).join('');
}

export function renderHewsonInfo(metric: HewsonMetric): string {
  const entry: HewsonCatalogEntry | undefined = HEWSON_CATALOG[metric];
  if (!entry) return `<p>No information available for this metric.</p>`;

  const thresholdsHtml = entry.level_thresholds
    ? `<div class="popup-section"><h4>Thresholds by level</h4></div>${renderLevelThresholds(entry.level_thresholds)}`
    : entry.flat_thresholds
      ? `<div class="popup-section"><h4>Thresholds</h4>${renderThresholdTable(entry.flat_thresholds)}</div>`
      : '';

  return `
    <div class="popup-header">
      <h3>${entry.name}${entry.unit ? ` <span class="popup-unit">(${entry.unit})</span>` : ''}</h3>
      <p class="popup-vibe">${entry.vibe}</p>
    </div>
    <div class="popup-body">
      <div class="popup-section">
        <h4>What it is</h4>
        ${entry.what_it_is}
      </div>
      <div class="popup-section">
        <h4>What the map shows</h4>
        <p>${entry.what_map_shows}</p>
      </div>
      ${thresholdsHtml}
      ${entry.multi_level_usage ? `<div class="popup-section">
        <h4>Multi-level usage</h4>
        ${entry.multi_level_usage}
      </div>` : ''}
      ${entry.pilot_notes ? `<div class="popup-section">
        <h4>Pilot notes</h4>
        ${entry.pilot_notes}
      </div>` : ''}
      ${entry.limitations ? `<div class="popup-section popup-limitations">
        <h4>Limitations</h4>
        <p>${entry.limitations}</p>
      </div>` : ''}
      ${entry.wikipedia ? `<div class="popup-section popup-learn-more">
        <a href="${entry.wikipedia}" target="_blank" rel="noopener noreferrer">Learn more on Wikipedia ↗</a>
      </div>` : ''}
      <div class="popup-section popup-discuss-ai" data-metric-name="${escapeAttr(entry.name)}"${entry.llm_prompt ? ` data-llm-prompt="${escapeAttr(entry.llm_prompt)}"` : ''}>
        <div class="popup-discuss-header">
          <span class="popup-discuss-label">Discuss with AI</span>
          <span class="popup-discuss-hint">A prompt is copied to clipboard — just paste it in the new chat</span>
        </div>
        <div class="popup-discuss-buttons">
          <a href="https://claude.ai/new" target="_blank" rel="noopener noreferrer" class="popup-ai-btn popup-ai-claude" data-ai="claude">Claude</a>
          <a href="https://chatgpt.com/" target="_blank" rel="noopener noreferrer" class="popup-ai-btn popup-ai-chatgpt" data-ai="chatgpt">ChatGPT</a>
          <a href="https://gemini.google.com/app" target="_blank" rel="noopener noreferrer" class="popup-ai-btn popup-ai-gemini" data-ai="gemini">Gemini</a>
        </div>
        <div class="popup-discuss-toast" hidden>Prompt copied! Paste it into the chat.</div>
      </div>
    </div>
  `;
}

function escapeAttr(s: string): string {
  // Standard order: & first (so &amp; doesn't double-escape), then quotes.
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}
