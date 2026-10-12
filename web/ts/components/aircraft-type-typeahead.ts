/**
 * ICAO aircraft-type typeahead on `GET /aircraft/types` — shared by the
 * settings aircraft form and the onboarding wizard's aircraft step (#777).
 *
 * The dropdown element should carry the `.autocomplete-dropdown` class and sit
 * right after the input inside a positioned container.
 */

import { searchAircraftTypes, type AircraftType } from '../adapters/aircraft-adapter';
import { escapeHtml } from '../utils';

const DEBOUNCE_MS = 200;
const MAX_SUGGESTIONS = 10;

export function attachAircraftTypeTypeahead(
  input: HTMLInputElement,
  dropdown: HTMLElement,
  onPick: (type: AircraftType) => void,
): void {
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;

  const hide = (): void => { dropdown.style.display = 'none'; };

  const show = (types: AircraftType[]): void => {
    if (types.length === 0) { hide(); return; }
    dropdown.innerHTML = types.slice(0, MAX_SUGGESTIONS).map(ty =>
      `<div class="autocomplete-item" data-icao="${escapeHtml(ty.icao)}">
        <strong>${escapeHtml(ty.icao)}</strong> — ${escapeHtml(ty.manufacturer)} ${escapeHtml(ty.model)}
        ${ty.category ? `<span class="muted">(${escapeHtml(ty.category)})</span>` : ''}
      </div>`,
    ).join('');
    dropdown.style.display = 'block';

    for (const item of dropdown.querySelectorAll<HTMLElement>('.autocomplete-item')) {
      item.addEventListener('mousedown', (e) => {
        e.preventDefault(); // keep focus so blur doesn't hide before the pick
        const type = types.find(ty => ty.icao === item.dataset.icao);
        if (!type) return;
        input.value = type.icao;
        onPick(type);
        hide();
      });
    }
  };

  input.addEventListener('input', () => {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      const q = input.value.trim();
      if (q.length >= 1) {
        searchAircraftTypes(q).then(show).catch(hide);
      } else {
        hide();
      }
    }, DEBOUNCE_MS);
  });
  input.addEventListener('blur', () => {
    // Delay so a click on a suggestion lands first.
    setTimeout(hide, DEBOUNCE_MS);
  });
}
