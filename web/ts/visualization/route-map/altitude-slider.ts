/** Altitude slider for level-dependent map metrics. */

export interface AltitudeSliderCallbacks {
  onChange: (altitudeFt: number) => void;
}

/**
 * Render the altitude slider into the container.
 * The slider steps in 500ft increments from surface to flightCeiling.
 */
export function renderAltitudeSlider(
  container: HTMLElement,
  currentAltFt: number,
  flightCeilingFt: number,
  callbacks: AltitudeSliderCallbacks,
): void {
  const min = 0;
  const max = flightCeilingFt;
  const step = 500;

  const flLabel = formatFL(currentAltFt);

  let html = '';
  html += `<span class="viz-toggle-label">Altitude:</span>`;
  html += `<input type="range" id="map-alt-range" min="${min}" max="${max}" step="${step}" value="${currentAltFt}">`;
  html += `<span class="altitude-label" id="map-alt-label">${flLabel}</span>`;

  container.innerHTML = html;

  const rangeInput = container.querySelector('#map-alt-range') as HTMLInputElement;
  const labelEl = container.querySelector('#map-alt-label') as HTMLElement;

  let debounceTimer: ReturnType<typeof setTimeout> | null = null;

  rangeInput.addEventListener('input', () => {
    const val = parseInt(rangeInput.value, 10);
    labelEl.textContent = formatFL(val);

    // Debounce the callback to avoid excessive re-renders
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      callbacks.onChange(val);
    }, 50);
  });
}

function formatFL(ft: number): string {
  if (ft < 100) return 'SFC';
  const fl = Math.round(ft / 100);
  return `FL${String(fl).padStart(3, '0')}`;
}
