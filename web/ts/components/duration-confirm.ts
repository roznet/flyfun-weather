/** Zero-duration confirmation popup — shown when the create-flight form is
 *  submitted with duration = 0, which is almost always an accidental empty
 *  field rather than a deliberate snapshot-in-time briefing. Lets the pilot
 *  enter a value, optionally pre-filled from distance / cruise-speed
 *  suggestions, or proceed with 0 if intentional. */

import { fetchRouteDistance } from '../adapters/api-adapter';
import { escapeHtml } from '../utils';
import { t } from '../i18n/i18n';

export interface DurationConfirmResult {
  confirmed: boolean;
  duration: number;
}

/** Show the popup. Resolves with confirmed=false when the pilot cancels
 *  (Go back / ESC / backdrop click); confirmed=true with whatever value
 *  is in the input (which may still be 0 if they really meant 0). */
export async function confirmZeroDuration(waypoints: string[]): Promise<DurationConfirmResult> {
  let distanceNm: number | null = null;
  try {
    if (waypoints.length >= 2) {
      const resp = await fetchRouteDistance(waypoints);
      distanceNm = resp.total_distance_nm;
    }
  } catch {
    // Distance is optional — popup still works without suggestions.
  }

  return new Promise<DurationConfirmResult>((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'metric-popup-backdrop active';

    const modal = document.createElement('div');
    modal.className = 'metric-popup';
    modal.style.maxWidth = '460px';

    modal.innerHTML = `
      <button class="metric-popup-close" aria-label="${escapeHtml(t('popup.close'))}">×</button>
      <h3>${escapeHtml(t('flights.form.zeroDurationTitle'))}</h3>
      <p style="margin-top:0.5rem;">${escapeHtml(t('flights.form.zeroDurationMessage'))}</p>
      <div style="margin-top:0.75rem;display:flex;align-items:center;gap:0.5rem;">
        <label for="zero-duration-input" style="font-weight:600;">
          ${escapeHtml(t('flights.form.zeroDurationLabel'))}
        </label>
        <input id="zero-duration-input" type="number" min="0" max="24" step="0.5" value="0"
               style="width:5rem;padding:0.35rem;font-size:1rem;" />
        <span style="color:var(--text-muted,#6b7280);">${escapeHtml(t('flights.form.zeroDurationHoursUnit'))}</span>
      </div>
      ${buildSuggestionsHtml(distanceNm)}
      <div style="margin-top:1rem;display:flex;gap:0.5rem;justify-content:flex-end;">
        <button type="button" id="zero-duration-back-btn" class="btn btn-outline btn-sm">
          ${escapeHtml(t('flights.form.zeroDurationGoBack'))}
        </button>
        <button type="button" id="zero-duration-continue-btn" class="btn btn-primary btn-sm">
          ${escapeHtml(t('flights.form.zeroDurationContinue'))}
        </button>
      </div>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    modal.addEventListener('click', (e) => e.stopPropagation());

    const input = modal.querySelector('#zero-duration-input') as HTMLInputElement | null;
    input?.focus();
    input?.select();

    const cleanup = () => {
      document.removeEventListener('keydown', onEsc);
      backdrop.remove();
    };

    const cancel = () => {
      cleanup();
      resolve({ confirmed: false, duration: 0 });
    };

    const accept = () => {
      const raw = parseFloat(input?.value || '0');
      const duration = isNaN(raw) ? 0 : Math.max(0, raw);
      cleanup();
      resolve({ confirmed: true, duration });
    };

    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cancel();
    };

    // Enter inside the field commits the value (matches form-submit muscle memory)
    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        accept();
      }
    });

    // Suggestion chips fill the input but don't auto-submit — the pilot
    // still sees the value before clicking Continue.
    modal.querySelectorAll<HTMLButtonElement>('.zero-duration-suggestion').forEach((btn) => {
      btn.addEventListener('click', () => {
        const hours = btn.dataset.hours;
        if (hours && input) {
          input.value = hours;
          input.focus();
        }
      });
    });

    backdrop.addEventListener('click', cancel);
    modal.querySelector('.metric-popup-close')?.addEventListener('click', cancel);
    modal.querySelector('#zero-duration-back-btn')?.addEventListener('click', cancel);
    modal.querySelector('#zero-duration-continue-btn')?.addEventListener('click', accept);
    document.addEventListener('keydown', onEsc);
  });
}

function buildSuggestionsHtml(distanceNm: number | null): string {
  if (distanceNm == null || distanceNm <= 0) return '';
  // Whole-hour suggestions — conservative (rounded up, never shorter) and they
  // land on valid 15-min dropdown values. The caller snaps the chosen value to
  // the duration dropdowns via setDurationControls/splitDurationCeil.
  const at120 = Math.ceil(distanceNm / 120);
  const at150 = Math.ceil(distanceNm / 150);
  const distLabel = t('flights.form.zeroDurationDistance', { dist: distanceNm.toFixed(0) });
  const hint = t('flights.form.zeroDurationSuggestionsHint');
  return `
    <div style="margin-top:0.75rem;padding:0.6rem;background:var(--bg-soft,#f3f4f6);border-radius:4px;font-size:0.9rem;">
      <div style="margin-bottom:0.3rem;">${escapeHtml(distLabel)}</div>
      <div style="margin-bottom:0.5rem;color:var(--text-muted,#6b7280);">${escapeHtml(hint)}</div>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
        <button type="button" class="zero-duration-suggestion btn btn-outline btn-sm" data-hours="${at120}">
          120 kt &rarr; ${at120} h
        </button>
        <button type="button" class="zero-duration-suggestion btn btn-outline btn-sm" data-hours="${at150}">
          150 kt &rarr; ${at150} h
        </button>
      </div>
    </div>
  `;
}
