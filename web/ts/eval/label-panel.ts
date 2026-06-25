/** In-view golden-labelling panel for the eval workbench (dev/admin only).
 *
 * Injected into the standard flight briefing view when the flight id is in the
 * ``eval-`` namespace (see briefing-main.ts). Lets the SME record the correct
 * GREEN/AMBER/RED per guidance preset + a rationale, written to the corpus
 * ``label.json`` via POST /api/eval/packs/{corpus_id}/label.
 *
 * Blind mode (default ON) hides the model's assessment + synopsis so the SME
 * labels from the data/advisories without anchoring on the model's verdict.
 */

import { apiFetch } from '../utils';

const GUIDANCES = ['conservative', 'balanced', 'tolerant'] as const;
const LETTERS: Record<string, string> = { G: 'GREEN', A: 'AMBER', R: 'RED' };

// Page elements hidden in blind mode: the model's verdict + AI summary so the
// SME judges from the data, not the model. Covers the top assessment banner
// (the colored GREEN/AMBER/RED digest summary), the alternate-time banner, the
// legacy #latest-assessment node, and the synopsis section.
const BLIND_SELECTORS =
  '#assessment-banner, #alt-assessment-banner, #latest-assessment, ' +
  '[data-section="synopsis"]';

interface Label {
  assessments: Record<string, string>;
  rationale: string;
  notes: string;
  priority?: number | null;
  labeled_by: string;
  labeled_at: string;
}

// Curation priority (set during triage, independent of the G/A/R label).
const PRIORITIES: Array<[number, string]> = [
  [1, '1 · very interesting — revalidate first'],
  [2, '2 · good'],
  [3, '3 · normal / simple'],
  [4, '4 · skip / not interesting'],
];

interface CorpusPackSummary {
  corpus_id: string;
  route: string;
  assessment: string | null;
  faithful: boolean;
  label: Label | null;
}

function injectBlindStyle(): void {
  if (document.getElementById('eval-blind-style')) return;
  const style = document.createElement('style');
  style.id = 'eval-blind-style';
  style.textContent =
    `body.eval-blind ${BLIND_SELECTORS.split(',').join(', body.eval-blind ')}` +
    ` { display: none !important; }`;
  document.head.appendChild(style);
}

function el(tag: string, props: Partial<HTMLElement> = {}, html = ''): HTMLElement {
  const e = document.createElement(tag);
  Object.assign(e, props);
  if (html) e.innerHTML = html;
  return e;
}

export async function initLabelPanel(flightId: string): Promise<void> {
  const corpusId = flightId.replace(/^eval-/, '');
  injectBlindStyle();
  document.body.classList.add('eval-blind'); // blind-first

  let pack: CorpusPackSummary | null = null;
  try {
    pack = await apiFetch<CorpusPackSummary>(`/eval/packs/${encodeURIComponent(corpusId)}`);
  } catch {
    // Workbench off / not admin — render nothing.
    document.body.classList.remove('eval-blind');
    return;
  }

  const chosen: Record<string, string> = { ...(pack.label?.assessments || {}) };

  // The panel is draggable + resizable; its geometry is persisted so it
  // survives the page reload that happens when navigating between packs.
  const GEOM_KEY = 'eval-panel-geom';
  let geom: { left?: number; top?: number; width?: number; height?: number } = {};
  try {
    geom = JSON.parse(localStorage.getItem(GEOM_KEY) || '{}') || {};
  } catch {
    geom = {};
  }

  const panel = el('div', { id: 'eval-label-panel' });
  panel.setAttribute(
    'style',
    'position:fixed;z-index:9999;display:flex;flex-direction:column;' +
      'background:var(--surface,#fff);color:var(--text,#111);' +
      'border:1px solid var(--border,#ccc);border-radius:8px;' +
      'box-shadow:0 4px 16px rgba(0,0,0,0.25);font-size:0.85rem;' +
      'resize:both;overflow:hidden;min-width:15rem;min-height:14rem;' +
      'max-width:95vw;max-height:92vh;',
  );
  // Restore saved position (clamped to the viewport) or default bottom-right.
  if (typeof geom.left === 'number' && typeof geom.top === 'number') {
    panel.style.left = `${Math.max(0, Math.min(geom.left, window.innerWidth - 60))}px`;
    panel.style.top = `${Math.max(0, Math.min(geom.top, window.innerHeight - 40))}px`;
  } else {
    panel.style.right = '1rem';
    panel.style.bottom = '1rem';
  }
  panel.style.width = geom.width ? `${geom.width}px` : '22rem';
  panel.style.height = geom.height ? `${geom.height}px` : '24rem';

  // Drag handle / title bar.
  const header = el('div');
  header.style.cssText =
    'flex:0 0 auto;cursor:move;user-select:none;touch-action:none;' +
    'display:flex;align-items:center;gap:0.4rem;padding:0.45rem 0.7rem;' +
    'border-bottom:1px solid var(--border,#ccc);background:rgba(127,127,127,0.08)';
  const grip = el('span', { textContent: '⠿' });
  grip.style.cssText = 'opacity:0.5';
  header.appendChild(grip);
  header.appendChild(el('strong', { textContent: 'Golden label' }));
  panel.appendChild(header);

  // Scrollable body — grows/shrinks as the panel is resized; the text fields
  // flex to fill the spare vertical space.
  const body = el('div');
  body.style.cssText =
    'flex:1 1 auto;overflow:auto;display:flex;flex-direction:column;' +
    'gap:0.35rem;padding:0.7rem;box-sizing:border-box';
  panel.appendChild(body);

  // Dynamic route/assessment go through textContent so a crafted value can't
  // inject markup (dev-only, but correct).
  const routeLine = el('div');
  routeLine.style.cssText = 'font-weight:600;word-break:break-word';
  routeLine.textContent = pack.route;
  body.appendChild(routeLine);

  // The model's verdict is itself blinded: shown only when blind is toggled off
  // so the panel doesn't leak what the page is hiding.
  const modelLine = el('div');
  modelLine.style.cssText = 'color:#888;font-size:0.75rem';
  const modelText =
    `model said: ${pack.assessment || '—'}` +
    `${pack.faithful ? '' : ' · reconstructed context'}`;
  const applyModelVisibility = (blindOn: boolean) => {
    modelLine.textContent = blindOn ? 'model verdict hidden (blind)' : modelText;
  };
  body.appendChild(modelLine);

  // Per-guidance G/A/R selectors. Each button has a paint() closure; clicking
  // any button repaints the whole row so only the chosen one is highlighted.
  for (const g of GUIDANCES) {
    const row = el('div', {}, `<span style="display:inline-block;width:6rem">${g}</span>`);
    row.style.cssText = 'flex:0 0 auto;margin:0.15rem 0';
    const paintFns: Array<() => void> = [];
    for (const L of ['G', 'A', 'R']) {
      const b = el('button', { textContent: L }) as HTMLButtonElement;
      b.type = 'button';
      b.style.cssText =
        'margin-right:0.25rem;width:2rem;border-radius:4px;cursor:pointer;' +
        'border:1px solid var(--border,#ccc);background:transparent;color:inherit';
      const paint = () => {
        b.style.background = chosen[g] === LETTERS[L] ? '#3367d6' : 'transparent';
        b.style.color = chosen[g] === LETTERS[L] ? '#fff' : 'inherit';
      };
      b.addEventListener('click', () => {
        chosen[g] = LETTERS[L];
        paintFns.forEach((f) => f());
      });
      paint();
      paintFns.push(paint);
      row.appendChild(b);
    }
    body.appendChild(row);
  }

  // Curation priority row (1–4). Independent of G/A/R so a pack can be triaged
  // (or marked skip) before it's labelled. Hover for what each level means.
  let chosenPriority: number | null = pack.label?.priority ?? null;
  const prow = el('div', {}, '<span style="display:inline-block;width:6rem">priority</span>');
  prow.style.cssText = 'flex:0 0 auto;margin:0.15rem 0';
  const priorityPaint: Array<() => void> = [];
  for (const [val, title] of PRIORITIES) {
    const b = el('button', { textContent: String(val), title }) as HTMLButtonElement;
    b.type = 'button';
    b.style.cssText =
      'margin-right:0.25rem;width:2rem;border-radius:4px;cursor:pointer;' +
      'border:1px solid var(--border,#ccc);background:transparent;color:inherit';
    const paint = () => {
      b.style.background = chosenPriority === val ? '#3367d6' : 'transparent';
      b.style.color = chosenPriority === val ? '#fff' : 'inherit';
    };
    b.addEventListener('click', () => {
      // Click the active one again to clear back to untriaged.
      chosenPriority = chosenPriority === val ? null : val;
      priorityPaint.forEach((f) => f());
    });
    paint();
    priorityPaint.push(paint);
    prow.appendChild(b);
  }
  body.appendChild(prow);

  // Text fields flex to fill spare vertical space and are independently
  // resizable; rationale gets the larger share.
  const rationale = el('textarea', {
    placeholder: 'rationale (why this assessment)',
    value: pack.label?.rationale || '',
  }) as HTMLTextAreaElement;
  rationale.style.cssText =
    'width:100%;box-sizing:border-box;resize:vertical;' +
    'flex:2 1 4rem;min-height:3rem';
  body.appendChild(rationale);

  const notes = el('textarea', {
    placeholder: 'notes (optional)',
    value: pack.label?.notes || '',
  }) as HTMLTextAreaElement;
  notes.style.cssText =
    'width:100%;box-sizing:border-box;resize:vertical;' +
    'flex:1 1 3rem;min-height:2.5rem';
  body.appendChild(notes);

  const status = el('div', {});
  status.style.cssText = 'flex:0 0 auto;min-height:1rem;color:#888';

  // Controls row: blind toggle + save.
  const controls = el('div', {});
  controls.style.cssText =
    'flex:0 0 auto;display:flex;align-items:center;justify-content:space-between';

  const blindLabel = el('label', {}, '') as HTMLLabelElement;
  blindLabel.style.cssText = 'cursor:pointer';
  const blind = el('input') as HTMLInputElement;
  blind.type = 'checkbox';
  blind.checked = true;
  blind.addEventListener('change', () => {
    document.body.classList.toggle('eval-blind', blind.checked);
    applyModelVisibility(blind.checked);
  });
  blindLabel.appendChild(blind);
  blindLabel.appendChild(document.createTextNode(' blind'));
  controls.appendChild(blindLabel);

  // Right-aligned button group: back to the eval set, then save.
  const btnGroup = el('div');
  btnGroup.style.cssText = 'display:flex;align-items:center;gap:0.4rem';

  // Quick return to the corpus list to pick the next pack — works whether or
  // not the current label was saved.
  const back = el('button', { textContent: '← Eval set' }) as HTMLButtonElement;
  back.type = 'button';
  back.title = 'Back to the eval set (does not save)';
  back.style.cssText =
    'background:transparent;color:inherit;border:1px solid var(--border,#ccc);' +
    'border-radius:4px;padding:0.35rem 0.6rem;cursor:pointer';
  back.addEventListener('click', () => {
    window.location.href = '/eval.html';
  });
  btnGroup.appendChild(back);

  const save = el('button', { textContent: 'Save label' }) as HTMLButtonElement;
  save.type = 'button';
  save.style.cssText =
    'background:#3367d6;color:#fff;border:none;border-radius:4px;' +
    'padding:0.35rem 0.7rem;cursor:pointer';
  save.addEventListener('click', async () => {
    save.disabled = true;
    status.textContent = 'Saving…';
    try {
      const saved = await apiFetch<Label>(
        `/eval/packs/${encodeURIComponent(corpusId)}/label`,
        {
          method: 'POST',
          body: JSON.stringify({
            assessments: chosen,
            rationale: rationale.value,
            notes: notes.value,
            priority: chosenPriority,
          }),
        },
      );
      const av = Object.values(saved.assessments).join('/');
      const pv = saved.priority ? `P${saved.priority}` : '';
      status.textContent = `Saved ${[av, pv].filter(Boolean).join(' · ') || '(empty)'}`;
    } catch (err) {
      status.textContent = err instanceof Error ? err.message : 'Save failed';
    } finally {
      save.disabled = false;
    }
  });
  btnGroup.appendChild(save);
  controls.appendChild(btnGroup);

  body.appendChild(controls);
  body.appendChild(status);
  document.body.appendChild(panel);

  // Blind starts ON (set on body above); reflect it in the model line.
  applyModelVisibility(blind.checked);

  // Persist position + size so the panel stays put across per-pack reloads.
  function saveGeom(): void {
    const r = panel.getBoundingClientRect();
    try {
      localStorage.setItem(
        GEOM_KEY,
        JSON.stringify({
          left: Math.round(r.left),
          top: Math.round(r.top),
          width: Math.round(r.width),
          height: Math.round(r.height),
        }),
      );
    } catch {
      /* storage unavailable — geometry just won't persist */
    }
  }

  // Drag via the header. Switch to left/top on first drag so the panel detaches
  // from its right/bottom anchor and tracks the pointer.
  let startX = 0;
  let startY = 0;
  let startLeft = 0;
  let startTop = 0;
  let dragging = false;
  header.addEventListener('pointerdown', (e) => {
    dragging = true;
    const r = panel.getBoundingClientRect();
    panel.style.left = `${r.left}px`;
    panel.style.top = `${r.top}px`;
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    startX = e.clientX;
    startY = e.clientY;
    startLeft = r.left;
    startTop = r.top;
    header.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  header.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const left = Math.max(0, Math.min(window.innerWidth - 60, startLeft + (e.clientX - startX)));
    const top = Math.max(0, Math.min(window.innerHeight - 40, startTop + (e.clientY - startY)));
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  });
  const endDrag = (e: PointerEvent) => {
    if (!dragging) return;
    dragging = false;
    try {
      header.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer already released */
    }
    saveGeom();
  };
  header.addEventListener('pointerup', endDrag);
  header.addEventListener('pointercancel', endDrag);

  // Persist user resizes (debounced to an animation frame).
  let rafId = 0;
  const ro = new ResizeObserver(() => {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(saveGeom);
  });
  ro.observe(panel);
}
