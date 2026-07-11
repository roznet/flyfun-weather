/** Client-side twin of the backend altitude-diff primitive (#259).
 *
 * Compares two altitude-table rows and reports which altitude-dependent
 * advisories improve / worsen between them. Used to:
 *   - overlay per-altitude advisory statuses onto the displayed cards as the
 *     lever moves (instant, no server round-trip), and
 *   - render the deterministic delta note comparing the probed altitude to the
 *     digest's planned altitude.
 *
 * Mirrors `weatherbrief/analysis/advisories/altitude_table.py:diff_altitude_rows`.
 * Pure — no DOM, no store. Kept testable in isolation.
 */

import type {
  AltitudeAdvisoryRow,
  AltitudeTableResult,
  AdvisoryStatus,
  RouteAdvisoriesManifest,
} from '../types/advisories';
import { formatAlt } from '../utils';

/** Severity rank; UNAVAILABLE is excluded (not comparable). */
const SEVERITY: Partial<Record<AdvisoryStatus, number>> = {
  green: 0,
  amber: 1,
  red: 2,
};

export interface AltitudeStatusChange {
  advisory_id: string;
  name: string;
  from: AdvisoryStatus;
  to: AdvisoryStatus;
}

export interface AltitudeDelta {
  improved: AltitudeStatusChange[];
  worsened: AltitudeStatusChange[];
  unchanged: string[];
}

export function diffAltitudeRows(
  baseline: AltitudeAdvisoryRow,
  candidate: AltitudeAdvisoryRow,
  names: Record<string, string> = {},
): AltitudeDelta {
  const improved: AltitudeStatusChange[] = [];
  const worsened: AltitudeStatusChange[] = [];
  const unchanged: string[] = [];

  // Iterate the baseline's advisories: a candidate-only advisory has no
  // baseline status to diff against (mirrors the Python twin).
  for (const [advisoryId, baseStatus] of Object.entries(baseline.statuses)) {
    const candStatus = candidate.statuses[advisoryId];
    if (candStatus === undefined) continue;
    const baseRank = SEVERITY[baseStatus];
    const candRank = SEVERITY[candStatus];
    if (baseRank === undefined || candRank === undefined) continue;
    if (candRank === baseRank) {
      unchanged.push(advisoryId);
      continue;
    }
    const change: AltitudeStatusChange = {
      advisory_id: advisoryId,
      name: names[advisoryId] ?? advisoryId,
      from: baseStatus,
      to: candStatus,
    };
    if (candRank < baseRank) improved.push(change);
    else worsened.push(change);
  }

  return { improved, worsened, unchanged };
}

/** Exact-altitude row lookup. */
export function rowForAltitude(
  table: AltitudeTableResult,
  altitudeFt: number | null,
): AltitudeAdvisoryRow | null {
  if (altitudeFt === null) return null;
  return table.rows.find(r => r.altitude_ft === altitudeFt) ?? null;
}

/** Nearest-altitude row — used when the lever sits between table steps so the
 *  overlay/delta stay instant without a finer-grained server sweep. */
export function nearestRow(
  table: AltitudeTableResult,
  altitudeFt: number | null,
): AltitudeAdvisoryRow | null {
  if (altitudeFt === null || table.rows.length === 0) return null;
  let best = table.rows[0];
  let bestDist = Math.abs(best.altitude_ft - altitudeFt);
  for (const r of table.rows) {
    const dist = Math.abs(r.altitude_ft - altitudeFt);
    if (dist < bestDist) {
      best = r;
      bestDist = dist;
    }
  }
  return best;
}

function phrase(changes: AltitudeStatusChange[]): string {
  return changes
    .map(c => `${c.name} (${c.from.toUpperCase()}→${c.to.toUpperCase()})`)
    .join('; ');
}

/** Deterministic delta note comparing the probed altitude against the digest's
 *  planned altitude. Returns null when the lever is at (or snaps to) the
 *  planned altitude or nothing differs, so the caller renders nothing. */
export function formatAltitudeDeltaNote(
  table: AltitudeTableResult,
  probedAltFt: number,
  plannedAltFt: number,
): string | null {
  if (probedAltFt === plannedAltFt) return null;
  const planned = rowForAltitude(table, plannedAltFt) ?? nearestRow(table, plannedAltFt);
  const probed = rowForAltitude(table, probedAltFt) ?? nearestRow(table, probedAltFt);
  if (!planned || !probed || planned.altitude_ft === probed.altitude_ft) return null;

  const delta = diffAltitudeRows(planned, probed, table.advisory_names);
  if (delta.improved.length === 0 && delta.worsened.length === 0) {
    return `At ${formatAlt(probed.altitude_ft)} vs planned ${formatAlt(planned.altitude_ft)}: same advisory picture.`;
  }
  const parts: string[] = [];
  if (delta.improved.length > 0) parts.push(`improves ${phrase(delta.improved)}`);
  if (delta.worsened.length > 0) parts.push(`worsens ${phrase(delta.worsened)}`);
  return `At ${formatAlt(probed.altitude_ft)} vs planned ${formatAlt(planned.altitude_ft)}: ${parts.join('; ')}.`;
}

/** Overlay a probed altitude's per-advisory statuses onto a manifest's cards.
 *  Only altitude-dependent advisories present in the table row are touched;
 *  everything else (airport, model quality, …) keeps its baseline status. The
 *  detail text is intentionally left at the pack's planned altitude — the lever
 *  conveys the per-altitude picture through the overlaid badges + delta note,
 *  and the base manifest is never mutated, so resetting is instant. */
export function overlayAltitudeStatuses(
  manifest: RouteAdvisoriesManifest,
  table: AltitudeTableResult,
  altitudeFt: number,
): RouteAdvisoriesManifest {
  const row = rowForAltitude(table, altitudeFt) ?? nearestRow(table, altitudeFt);
  if (!row) return manifest;
  return {
    ...manifest,
    advisories: manifest.advisories.map(adv => {
      const status = row.statuses[adv.advisory_id];
      if (status === undefined) return adv;
      return {
        ...adv,
        aggregate_status: status,
        representative_model: null,
        per_model: adv.per_model.map(model => ({
          ...model,
          data_state: null,
          primary_method_id: null,
          evidence_regions: [],
        })),
      };
    }),
  };
}
