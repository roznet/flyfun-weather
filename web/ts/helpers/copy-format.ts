/**
 * Small formatting helpers shared by the two donation surfaces (the briefing
 * chip and the donate page), which live in separate bundles and would
 * otherwise each carry their own copy.
 */

import { escapeHtml } from '../utils';

/**
 * Escape, then honour `**bold**`.
 *
 * The order matters and is the same one `formatDigestBody` uses: everything is
 * escaped first, and only our own tags are injected afterwards — so the
 * emphasis the copy carries survives without opening the markup to whatever
 * came back from the API.
 */
export function boldify(text: string): string {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}

/** "April" in the viewer's locale; `''` when there is no usable date. */
export function formatMonthName(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { month: 'long' });
}
