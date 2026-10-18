/**
 * The compact text the MCP App widget hands the model (`ui/update-model-context`)
 * so "this point" / "why is this red" refers to what the pilot sees. Plain
 * text, no arrays or soundings: each update replaces the previous one.
 */

import type { RouteAnalysesManifest } from '../store/types';

export interface SelectedPoint {
  index: number;
  /** Waypoint ICAO (and name) when the point is a waypoint, else null. */
  waypoint: string | null;
  distanceNm: number;
  /** When the aircraft is planned there (route_analyses `interpolated_time`). */
  validTime: string;
  /** The model hour the point reads (`forecast_hour`). */
  forecastHour: string;
}

export interface ModelContextInput {
  route: string;
  packTimestamp: string;
  model: string;
  models: readonly string[];
  /** Display names of the layers switched on, in panel order. */
  layers: readonly string[];
  point: SelectedPoint | null;
}

/** The selected point as the model should read it, or null for none/out of range. */
export function describePoint(manifest: RouteAnalysesManifest, index: number): SelectedPoint | null {
  const p = manifest.analyses.find((a) => a.point_index === index) ?? manifest.analyses[index];
  if (index < 0 || !p) return null;
  const waypoint = p.waypoint_icao
    ? (p.waypoint_name ? `${p.waypoint_icao} (${p.waypoint_name})` : p.waypoint_icao)
    : null;
  return {
    index: p.point_index,
    waypoint,
    distanceNm: p.distance_from_origin_nm,
    validTime: p.interpolated_time,
    forecastHour: p.forecast_hour,
  };
}

/** `2026-02-25T17:12:00` / `…+00:00` / `…Z` → `2026-02-25 17:12Z` (times are UTC). */
export function utc(ts: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(ts);
  return m ? `${m[1]} ${m[2]}Z` : ts;
}

export function buildModelContext(c: ModelContextInput): string {
  const lines = [
    'The pilot is looking at the FlyFun Weather route cross-section (interactive, in this chat).',
    `Route: ${c.route}. Briefing of ${utc(c.packTimestamp)}.`,
    `Model shown: ${c.model.toUpperCase()} (pack also has: ${c.models.filter((m) => m !== c.model).join(', ') || 'none'}).`,
    `Layers on: ${c.layers.join(', ') || 'none'}.`,
  ];
  const p = c.point;
  if (p) {
    const where = p.waypoint
      ? `waypoint ${p.waypoint}, ${Math.round(p.distanceNm)} NM along the route`
      : `${Math.round(p.distanceNm)} NM along the route`;
    lines.push(
      `Selected point: index ${p.index}, ${where}; planned overhead ${utc(p.validTime)}, `
      + `model forecast hour ${utc(p.forecastHour)}.`,
    );
  } else {
    lines.push('No route point selected.');
  }
  lines.push('What the widget shows is the briefing data; it does not re-grade any advisory.');
  return lines.join('\n');
}
