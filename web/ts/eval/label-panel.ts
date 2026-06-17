/** In-view golden-labelling panel for the eval workbench (dev/admin only).
 *
 * Injected into the standard flight briefing view when the flight id is in the
 * ``eval-`` namespace (see flight-main.ts). Lets the SME record the correct
 * GREEN/AMBER/RED per guidance preset + a rationale, written to the corpus
 * ``label.json`` via POST /api/eval/packs/{corpus_id}/label.
 *
 * Blind mode (default ON) hides the model's assessment + synopsis so the SME
 * labels from the data/advisories without anchoring on the model's verdict.
 */

import { apiFetch } from '../utils';

const GUIDANCES = ['conservative', 'balanced', 'tolerant'] as const;
const LETTERS: Record<string, string> = { G: 'GREEN', A: 'AMBER', R: 'RED' };

// Page elements hidden in blind mode (model verdict + AI synopsis).
const BLIND_SELECTORS = '#latest-assessment, [data-section="synopsis"]';

interface Label {
  assessments: Record<string, string>;
  rationale: string;
  notes: string;
  labeled_by: string;
  labeled_at: string;
}

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

  const panel = el('div', { id: 'eval-label-panel' });
  panel.setAttribute(
    'style',
    'position:fixed;right:1rem;bottom:1rem;z-index:9999;width:20rem;' +
      'background:var(--surface,#fff);color:var(--text,#111);' +
      'border:1px solid var(--border,#ccc);border-radius:8px;padding:0.75rem;' +
      'box-shadow:0 4px 16px rgba(0,0,0,0.25);font-size:0.85rem;',
  );

  // Static markup only via innerHTML; the dynamic route/assessment go through
  // textContent so a crafted value can't inject markup (dev-only, but correct).
  const title = el('div');
  title.innerHTML = '<strong>Golden label</strong> — ';
  title.appendChild(document.createTextNode(pack.route));
  panel.appendChild(title);

  const modelLine = el('div');
  modelLine.style.cssText = 'color:#888;font-size:0.75rem';
  modelLine.textContent =
    `model said: ${pack.assessment || '—'}` +
    `${pack.faithful ? '' : ' · reconstructed context'}`;
  panel.appendChild(modelLine);

  // Per-guidance G/A/R selectors.
  const rows: Record<string, HTMLElement[]> = {};
  for (const g of GUIDANCES) {
    const row = el('div', {}, `<span style="display:inline-block;width:6rem">${g}</span>`);
    row.style.margin = '0.35rem 0';
    const btns: HTMLElement[] = [];
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
        btns.forEach((x) => (x as any)._paint && (x as any)._paint());
      });
      (b as any)._paint = paint;
      paint();
      btns.push(b);
      row.appendChild(b);
    }
    rows[g] = btns;
    panel.appendChild(row);
  }

  const rationale = el('textarea', {
    placeholder: 'rationale (one line)',
    value: pack.label?.rationale || '',
  }) as HTMLTextAreaElement;
  rationale.rows = 2;
  rationale.style.cssText = 'width:100%;margin-top:0.35rem;box-sizing:border-box';
  panel.appendChild(rationale);

  const notes = el('textarea', {
    placeholder: 'notes (optional)',
    value: pack.label?.notes || '',
  }) as HTMLTextAreaElement;
  notes.rows = 2;
  notes.style.cssText = 'width:100%;margin-top:0.35rem;box-sizing:border-box';
  panel.appendChild(notes);

  const status = el('div', {});
  status.style.cssText = 'margin-top:0.35rem;min-height:1rem;color:#888';

  // Controls row: blind toggle + save.
  const controls = el('div', {});
  controls.style.cssText =
    'display:flex;align-items:center;justify-content:space-between;margin-top:0.5rem';

  const blindLabel = el('label', {}, '') as HTMLLabelElement;
  const blind = el('input') as HTMLInputElement;
  blind.type = 'checkbox';
  blind.checked = true;
  blind.addEventListener('change', () => {
    document.body.classList.toggle('eval-blind', blind.checked);
  });
  blindLabel.appendChild(blind);
  blindLabel.appendChild(document.createTextNode(' blind'));
  controls.appendChild(blindLabel);

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
          }),
        },
      );
      status.textContent = `Saved ${Object.values(saved.assessments).join('/')}`;
    } catch (err) {
      status.textContent = err instanceof Error ? err.message : 'Save failed';
    } finally {
      save.disabled = false;
    }
  });
  controls.appendChild(save);

  panel.appendChild(controls);
  panel.appendChild(status);
  document.body.appendChild(panel);
}
