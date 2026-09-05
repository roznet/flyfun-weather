/** Display clocks never alter the scientific source timestamp or fetch weather. */
export function observationTimeText(validTime: string, now = new Date()): string {
  const stamp = new Date(validTime);
  const elapsed = (now.getTime() - stamp.getTime()) / 60000;
  if (!Number.isFinite(stamp.getTime())) return 'time unknown · age unknown';
  const iso = stamp.toISOString();
  const dated = !Number.isFinite(now.getTime()) || iso.slice(0, 10) !== now.toISOString().slice(0, 10);
  const time = `${dated ? iso.slice(0, 10) + ' ' : ''}${iso.slice(11, 16)}Z`;
  const age = !Number.isFinite(elapsed) || elapsed < 0
    ? 'age unknown (clock mismatch)'
    : elapsed < 1 ? 'just now'
      : `${Math.floor(elapsed)} min old${elapsed >= 30 ? ' · stale' : ''}`;
  return `${time} · ${age}`;
}

export function observationWindowText(source: string, minutes: number): string {
  if (!(minutes > 0)) return '';
  const kind = source === 'eumetsat_li' ? 'accumulation' : 'acquisition';
  return ` · ${Math.round(minutes)} min ${kind} window`;
}

/** Minute-level label updates only; the owner disposes this with its view. */
export function observeDisplayClock(update: () => void): () => void {
  const tick = () => { if (document.visibilityState !== 'hidden') update(); };
  const timer = setInterval(tick, 60000);
  document.addEventListener('visibilitychange', tick);
  window.addEventListener('pageshow', tick);
  return () => {
    clearInterval(timer);
    document.removeEventListener('visibilitychange', tick);
    window.removeEventListener('pageshow', tick);
  };
}
