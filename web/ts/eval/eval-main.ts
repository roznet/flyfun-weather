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
/** Situation tags selected in the coverage grid; a pack must carry ALL of them. */
const selected = new Set<string>();
let currentCoverage: CoverageRow[] = [];
let currentPacks: CorpusPackSummary[] = [];

function badge(letter: string | null | undefined): string {
  if (!letter) return '<span class="badge unl">—</span>';
  const c = letter[0].toUpperCase();
  // Only G/A/R have a CSS rule; anything else (notably UNAVAILABLE -> 'U') would
  // render unstyled. Fall back to the neutral "no verdict" pill (#392).
  const cls = c === 'G' || c === 'A' || c === 'R' ? c : 'unl';
  return `<span class="badge ${cls}">${c}</span>`;
}

/** HTML-escape a dynamic value before interpolating into an innerHTML string. */
function esc(s: string): string {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

/** A pack matches when it carries every selected situation tag (AND, not OR). */
function matchesFilter(p: CorpusPackSummary): boolean {
  for (const sit of selected) {
    if (!p.situations.includes(sit)) return false;
  }
  return true;
}

function renderCoverage(rows: CoverageRow[], packs: CorpusPackSummary[]): string {
  const shown = packs.filter(matchesFilter);
  return rows
    .map((r) => {
      const isSel = selected.has(r.situation);
      // How many of the currently-shown packs would survive adding this tag —
      // 0 means the cell is a dead end, so dim it rather than let the user
      // click into an empty list.
      const reach = isSel
        ? shown.length
        : shown.filter((p) => p.situations.includes(r.situation)).length;
      const cls = [
        'eval-cell',
        r.labeled === 0 ? 'gap' : '',
        isSel ? 'sel' : '',
        !isSel && reach === 0 ? 'dead' : '',
      ]
        .filter(Boolean)
        .join(' ');
      // Narrowed count only differs from the corpus-wide total when a filter is
      // active; showing both keeps the coverage checklist readable.
      const narrowed =
        selected.size > 0 && !isSel ? ` <span class="c-narrow">(${reach} shown)</span>` : '';
      return `<div class="${cls}" data-sit="${esc(r.situation)}" role="button" tabindex="0"
        title="${isSel ? 'Click to remove from filter' : 'Click to filter packs by this tag'}">
        <div>${esc(r.situation)}</div>
        <div class="c-count">${r.labeled}/${r.total} labelled${narrowed}</div>
      </div>`;
    })
    .join('');
}

function renderFilterBar(packs: CorpusPackSummary[]): string {
  if (selected.size === 0) return '';
  const chips = [...selected]
    .map(
      (s) =>
        `<button class="chip" data-unsit="${esc(s)}" title="Remove this tag">${esc(s)} ✕</button>`,
    )
    .join('');
  const shown = packs.filter(matchesFilter).length;
  return `<div class="filter-bar">
    <span class="sit">Filter (all of):</span>${chips}
    <button class="btn-clear" data-clear="1">Clear all</button>
    <span class="sit">${shown} of ${packs.length} packs</span>
  </div>`;
}

function renderPacks(allPacks: CorpusPackSummary[], area: Area): string {
  if (allPacks.length === 0) {
    const where = area === 'staging' ? 'staging' : 'corpus';
    return `<p>No packs in ${where}. ${
      area === 'staging'
        ? 'Pull some with <code>scripts/pull_eval_corpus.py --area staging</code>.'
        : 'Promote labelled packs from staging.'
    }</p>`;
  }
  const packs = allPacks.filter(matchesFilter);
  if (packs.length === 0) {
    return `<p>No packs carry all of: ${esc([...selected].join(', '))}. ` +
      `<button class="btn-clear" data-clear="1">Clear all</button></p>`;
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
        <td class="sit">${p.situations
          .map(
            (s) =>
              `<button class="sit-tag${selected.has(s) ? ' sel' : ''}" data-sit="${esc(s)}"
                 title="Filter by this tag">${esc(s)}</button>`,
          )
          .join(' ')}</td>
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

/** Re-render coverage + filter bar + table from the cached data (no refetch). */
function renderAll(): void {
  const covEl = document.getElementById('coverage');
  const barEl = document.getElementById('filter-bar');
  const packsEl = document.getElementById('packs');
  if (covEl) covEl.innerHTML = renderCoverage(currentCoverage, currentPacks);
  if (barEl) barEl.innerHTML = renderFilterBar(currentPacks);
  if (packsEl) packsEl.innerHTML = renderPacks(currentPacks, currentArea);
}

function toggleSituation(sit: string): void {
  if (selected.has(sit)) selected.delete(sit);
  else selected.add(sit);
  renderAll();
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
    currentCoverage = coverage;
    currentPacks = packs;
    // Staging and corpus share the situation vocab, so a filter set on one area
    // is still meaningful on the other — but drop tags no area-local pack has,
    // otherwise the switch silently lands on an empty table.
    for (const s of [...selected]) {
      if (!packs.some((p) => p.situations.includes(s))) selected.delete(s);
    }
    renderAll();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (covEl) covEl.textContent = '';
    const barEl = document.getElementById('filter-bar');
    if (barEl) barEl.textContent = '';
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

  // Event delegation: area toggle + situation filter + per-row promote.
  document.addEventListener('click', (e) => {
    const t = e.target as HTMLElement;
    const area = t.getAttribute('data-area') as Area | null;
    if (area) {
      void loadArea(area);
      return;
    }
    if (t.closest('[data-clear]')) {
      selected.clear();
      renderAll();
      return;
    }
    // Coverage cells wrap their label in child divs, so match on the ancestor.
    const sitEl = t.closest('[data-sit]') as HTMLElement | null;
    if (sitEl) {
      toggleSituation(sitEl.getAttribute('data-sit') || '');
      return;
    }
    const unsit = t.closest('[data-unsit]') as HTMLElement | null;
    if (unsit) {
      toggleSituation(unsit.getAttribute('data-unsit') || '');
      return;
    }
    const promoteId = t.getAttribute('data-promote');
    if (promoteId) void promote(decodeURIComponent(promoteId));
  });

  // Coverage cells are divs with role=button — keep them keyboard-operable.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const sitEl = (e.target as HTMLElement).closest('.eval-cell[data-sit]') as HTMLElement | null;
    if (!sitEl) return;
    e.preventDefault();
    toggleSituation(sitEl.getAttribute('data-sit') || '');
  });

  await loadArea(currentArea);
}

init();
