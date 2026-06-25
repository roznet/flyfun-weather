/** Eval labelling workbench — corpus list + coverage grid (dev/admin only). */

import { fetchCurrentUser } from '../adapters/auth-adapter';
import { apiFetch, redirectToLogin } from '../utils';
import { initTheme } from '../theme';

type Area = 'staging' | 'corpus';

interface Label {
  assessments: Record<string, string>;
  rationale: string;
  notes: string;
  priority?: number | null;
  labeled_by: string;
  labeled_at: string;
}

interface CorpusPackSummary {
  corpus_id: string;
  flight_id: string;
  area: Area;
  route: string;
  target_date: string;
  fetch_timestamp: string;
  days_out: number;
  assessment: string | null;
  situations: string[];
  faithful: boolean;
  is_labeled: boolean;
  full_fidelity: boolean;
  debriefed: boolean;
  debrief_decision: string | null;
  debrief_graded: boolean;
  label: Label | null;
}

const DEBRIEF_BADGE: Record<string, string> = {
  flown: '✈ flown',
  cancelled: '✖ cancelled',
  monitoring: '👁 monitoring',
};

interface CoverageRow {
  situation: string;
  total: number;
  labeled: number;
  unlabeled: number;
  corpus_ids: string[];
}

let currentArea: Area = 'staging';

function badge(letter: string | null | undefined): string {
  if (!letter) return '<span class="badge unl">—</span>';
  const c = letter[0].toUpperCase();
  return `<span class="badge ${c}">${c}</span>`;
}

/** HTML-escape a dynamic value before interpolating into an innerHTML string. */
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

function renderPacks(packs: CorpusPackSummary[], area: Area): string {
  if (packs.length === 0) {
    const where = area === 'staging' ? 'staging' : 'corpus';
    return `<p>No packs in ${where}. ${
      area === 'staging'
        ? 'Pull some with <code>scripts/pull_eval_corpus.py --area staging</code>.'
        : 'Promote labelled packs from staging.'
    }</p>`;
  }
  // Triage order: by priority (1 first, untriaged last), then unlabelled first.
  const prio = (p: CorpusPackSummary): number => p.label?.priority ?? 9;
  const sorted = [...packs].sort(
    (a, b) => prio(a) - prio(b) || Number(a.is_labeled) - Number(b.is_labeled),
  );
  const rows = sorted
    .map((p) => {
      const g = p.label?.assessments || {};
      const recon = p.faithful ? '' : ' <span class="recon">reconstructed</span>';
      const labelOnly = p.full_fidelity
        ? ''
        : ' <span class="diff-note" title="No route_analyses.json — captured after T1 retention. Label from advisories + digest; no cross-section.">label-only</span>';
      const pr = p.label?.priority;
      const prCell = pr ? `<span class="badge prio p${pr}">P${pr}</span>` : '<span class="sit">—</span>';
      const deb = p.debriefed
        ? `<span class="badge deb ${p.debrief_decision || ''}" title="Pilot debrief${
            p.debrief_graded ? ' — graded (reasons/outcomes)' : ''
          }">${DEBRIEF_BADGE[p.debrief_decision || ''] || 'debriefed'}${
            p.debrief_graded ? ' ★' : ''
          }</span>`
        : '<span class="sit">—</span>';
      const open =
        `/briefing.html?flight=${encodeURIComponent(p.flight_id)}` +
        `&pack=${encodeURIComponent(p.fetch_timestamp)}`;
      // Promote only from staging, only when labelled (server enforces too).
      const action =
        area === 'staging'
          ? `<button class="btn-promote" data-promote="${encodeURIComponent(p.corpus_id)}"${
              p.is_labeled ? '' : ' disabled title="label before promoting"'
            }>Promote →</button>`
          : '';
      return `<tr>
        <td>${prCell}</td>
        <td><a href="${open}">${esc(p.route)}</a> <span class="sit">d${p.days_out}</span>${recon}${labelOnly}</td>
        <td class="sit">${esc(p.target_date)}</td>
        <td>${deb}</td>
        <td>${badge(p.assessment)}</td>
        <td>${badge(g.conservative)} ${badge(g.balanced)} ${badge(g.tolerant)}</td>
        <td class="sit">${esc(p.situations.join(', '))}</td>
        <td>${action}</td>
      </tr>`;
    })
    .join('');
  return `<table class="eval-table">
    <thead><tr>
      <th>Pri</th>
      <th>Route — open to label</th>
      <th>Flight date</th>
      <th>Debrief</th>
      <th>Model</th>
      <th>Golden (cons / bal / tol)</th>
      <th>Situations</th>
      <th></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function renderToggle(area: Area): string {
  const btn = (a: Area, label: string) =>
    `<button data-area="${a}" class="${a === area ? 'active' : ''}">${label}</button>`;
  return btn('staging', 'Staging') + btn('corpus', 'Corpus');
}

async function promote(corpusId: string): Promise<void> {
  try {
    await apiFetch(`/eval/packs/${encodeURIComponent(corpusId)}/promote`, { method: 'POST' });
  } catch (err) {
    alert(`Promote failed: ${err instanceof Error ? err.message : String(err)}`);
    return;
  }
  await loadArea(currentArea);
}

async function loadArea(area: Area): Promise<void> {
  currentArea = area;
  const covEl = document.getElementById('coverage');
  const packsEl = document.getElementById('packs');
  const toggleEl = document.getElementById('area-toggle');
  if (toggleEl) toggleEl.innerHTML = renderToggle(area);
  if (covEl) covEl.textContent = 'Loading…';
  if (packsEl) packsEl.textContent = 'Loading…';
  try {
    const [coverage, packs] = await Promise.all([
      apiFetch<CoverageRow[]>(`/eval/coverage?area=${area}`),
      apiFetch<CorpusPackSummary[]>(`/eval/packs?area=${area}`),
    ]);
    if (covEl) covEl.innerHTML = renderCoverage(coverage);
    if (packsEl) packsEl.innerHTML = renderPacks(packs, area);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (covEl) covEl.textContent = '';
    if (packsEl) {
      packsEl.textContent =
        `Workbench unavailable: ${msg}. Is WEATHERBRIEF_EVAL_WORKBENCH=1 set ` +
        `and are you an admin?`;
    }
  }
}

async function init(): Promise<void> {
  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    return;
  }
  initTheme();

  // Event delegation: area toggle + per-row promote.
  document.addEventListener('click', (e) => {
    const t = e.target as HTMLElement;
    const area = t.getAttribute('data-area') as Area | null;
    if (area) {
      void loadArea(area);
      return;
    }
    const promoteId = t.getAttribute('data-promote');
    if (promoteId) void promote(decodeURIComponent(promoteId));
  });

  await loadArea(currentArea);
}

init();
