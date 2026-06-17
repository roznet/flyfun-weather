/** Eval labelling workbench — corpus list + coverage grid (dev/admin only). */

import { fetchCurrentUser } from '../adapters/auth-adapter';
import { apiFetch, redirectToLogin } from '../utils';
import { initTheme } from '../theme';

interface Label {
  assessments: Record<string, string>;
  rationale: string;
  notes: string;
  labeled_by: string;
  labeled_at: string;
}

interface CorpusPackSummary {
  corpus_id: string;
  flight_id: string;
  route: string;
  target_date: string;
  fetch_timestamp: string;
  days_out: number;
  assessment: string | null;
  situations: string[];
  faithful: boolean;
  is_labeled: boolean;
  label: Label | null;
}

interface CoverageRow {
  situation: string;
  total: number;
  labeled: number;
  unlabeled: number;
  corpus_ids: string[];
}

function badge(letter: string | null | undefined): string {
  if (!letter) return '<span class="badge unl">—</span>';
  const c = letter[0].toUpperCase();
  return `<span class="badge ${c}">${c}</span>`;
}

/** HTML-escape a dynamic value before interpolating into an innerHTML string.
 *  Corpus values are developer-committed (ICAO routes, vocab tags) so the risk
 *  is negligible, but we keep the same no-raw-interpolation discipline as
 *  label-panel.ts even on this dev-only page. */
function esc(s: string): string {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function renderCoverage(rows: CoverageRow[]): string {
  return rows
    .map((r) => {
      const gap = r.labeled === 0 ? ' gap' : '';
      return `<div class="eval-cell${gap}">
        <div>${r.situation}</div>
        <div class="c-count">${r.labeled}/${r.total} labelled</div>
      </div>`;
    })
    .join('');
}

function renderPacks(packs: CorpusPackSummary[]): string {
  if (packs.length === 0) {
    return '<p>No corpus packs yet. Run <code>scripts/pull_eval_corpus.py</code>.</p>';
  }
  const rows = packs
    .map((p) => {
      const g = p.label?.assessments || {};
      const recon = p.faithful ? '' : ' <span class="recon">reconstructed</span>';
      const open = `/flight.html?id=${encodeURIComponent(p.flight_id)}`;
      return `<tr>
        <td><a href="${open}">${esc(p.route)}</a> <span class="sit">d${p.days_out}</span>${recon}</td>
        <td>${badge(p.assessment)}</td>
        <td>${badge(g.conservative)} ${badge(g.balanced)} ${badge(g.tolerant)}</td>
        <td class="sit">${esc(p.situations.join(', '))}</td>
      </tr>`;
    })
    .join('');
  return `<table class="eval-table">
    <thead><tr>
      <th>Route — open to label</th>
      <th>Model</th>
      <th>Golden (cons / bal / tol)</th>
      <th>Situations</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function init(): Promise<void> {
  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    return;
  }
  initTheme();

  const covEl = document.getElementById('coverage');
  const packsEl = document.getElementById('packs');
  try {
    const [coverage, packs] = await Promise.all([
      apiFetch<CoverageRow[]>('/eval/coverage'),
      apiFetch<CorpusPackSummary[]>('/eval/packs'),
    ]);
    if (covEl) covEl.innerHTML = renderCoverage(coverage);
    if (packsEl) packsEl.innerHTML = renderPacks(packs);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (covEl) covEl.textContent = '';
    if (packsEl) {
      // textContent (not innerHTML): the error string is server/network-derived
      // and must not be parsed as HTML even on this dev-only admin page.
      packsEl.textContent =
        `Workbench unavailable: ${msg}. Is WEATHERBRIEF_EVAL_WORKBENCH=1 set ` +
        `and are you an admin?`;
    }
  }
}

init();
