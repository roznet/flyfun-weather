const LS_KEY = 'wb_tour_offered';

export function hasBeenOffered(): boolean {
  try {
    return localStorage.getItem(LS_KEY) === '1';
  } catch {
    return false;
  }
}

export function markOffered(): void {
  try {
    localStorage.setItem(LS_KEY, '1');
  } catch {
    // localStorage unavailable (private mode, full quota) — silently no-op.
  }
}
