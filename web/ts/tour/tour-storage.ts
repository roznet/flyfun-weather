/** Per-tour "already offered" tracking in localStorage.
 *
 * Originally a single hard-coded `wb_tour_offered` key for the briefing tour.
 * Now there is a second tour (the flight-creation tour, #400), so the key is
 * per-tour. The briefing tour keeps the bare legacy key so existing users who
 * already saw it aren't re-offered; new tours get a suffixed key.
 */

export type TourId = 'briefing' | 'flights';

const LS_PREFIX = 'wb_tour_offered';

/** localStorage key for a given tour. Briefing uses the legacy bare key for
 *  backward compatibility; other tours are suffixed with their id. */
export function tourStorageKey(tour: TourId): string {
  return tour === 'briefing' ? LS_PREFIX : `${LS_PREFIX}_${tour}`;
}

export function hasBeenOffered(tour: TourId): boolean {
  try {
    return localStorage.getItem(tourStorageKey(tour)) === '1';
  } catch {
    return false;
  }
}

export function markOffered(tour: TourId): void {
  try {
    localStorage.setItem(tourStorageKey(tour), '1');
  } catch {
    // localStorage unavailable (private mode, full quota) — silently no-op.
  }
}

/** Clear the "offered" flag for a tour (or all tours) — used by the admin
 *  "reset first-time experience" tools. */
export function clearOffered(tour?: TourId): void {
  try {
    if (tour) {
      localStorage.removeItem(tourStorageKey(tour));
    } else {
      localStorage.removeItem(tourStorageKey('briefing'));
      localStorage.removeItem(tourStorageKey('flights'));
    }
  } catch {
    // localStorage unavailable — silently no-op.
  }
}
