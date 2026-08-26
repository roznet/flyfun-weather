/** Adapter for the public per-source catalog endpoint.
 *
 * Backs the help-page Data Sources table and any other UI that needs the
 * merged static + live state of every (model, source) pair we track.
 * Single source of truth is the server-side SOURCE_REGISTRY plus the
 * in-memory marker store — keep this adapter thin.
 */

import { apiFetch } from '../utils';

/** One per-source entry returned by `GET /api/data-sources`. */
export interface DataSourceEntry {
  /** Stable registry key, e.g. `"ecmwf:direct"`. */
  key: string;
  /** Marker-store model name (e.g. `"icon_eu"`, `"icon"`, `"ecmwf"`). */
  model: string;
  /** Display label for the underlying NWP model, e.g. `"ICON-EU"`. */
  model_label: string;
  /** Human provider name (`"DWD"`, `"NOAA"`, `"ECMWF"`, `"Open-Meteo"`). */
  provider_label: string;
  /** Optional documentation URL for the provider. */
  provider_url: string;
  /** Role: `"primary-sounding" | "cloud-enrichment" | "surface-base" |
   *  "primary" | "observed"`. `"observed"` (#574) is a MEASUREMENT stream —
   *  radar, lightning, satellite — not a forecast model, and has no horizon. */
  role: string;
  /** Spatial resolution for display. */
  resolution: string;
  /** Geographic coverage for display. */
  coverage: string;
  /** Number of pressure levels delivered by this source, or null. */
  pressure_levels: number | null;
  /** One-sentence description of what this source contributes. */
  description: string;

  /** `"cycle"` for an NWP run at fixed hours of the day, `"interval"` for an
   *  observed stream publishing every N minutes (#574). The two answer
   *  different questions — "when is the next run due?" vs "how old is the
   *  current frame?" — so the table renders them differently. */
  schedule_kind: string;
  /** Publication period in minutes for an interval source; null for a cycle one. */
  interval_minutes: number | null;
  /** UTC hours-of-day at which the source publishes runs. Empty for an
   *  interval source, which does not publish at hours of the day. */
  cycles: number[];
  /** Forecast horizon (hours) keyed by cycle hour (string-keyed JSON). */
  horizon_hours: Record<string, number>;
  /** Delivery-offset (hours) keyed by cycle hour. */
  delivery_offset_hours: Record<string, number>;

  /** Latest observed init (ISO UTC) or null if the marker isn't populated yet. */
  latest_init: string | null;
  /** Provider-reported publish wallclock (ISO UTC) or null. */
  published_at: string | null;
  /** Wallclock at which the next run is expected (ISO UTC) or null. */
  next_expected: string | null;
  /** End of the current run's forecast horizon (ISO UTC) or null. */
  horizon_end: string | null;
  /** `"ok" | "suspect" | "unobserved" | "unknown"`.
   *
   * `"unobserved"` means the loop is running but has never successfully
   * reached this source, so the schedule shown is the configured expectation
   * rather than anything measured — distinct from `"ok"`, which requires a
   * confirmed probe. */
  marker_health: string;
}

export interface DataSourcesResponse {
  sources: DataSourceEntry[];
  /** ISO UTC wallclock at which the response was generated. */
  generated_at: string;
}

/** Fetch the full data-source catalog (static + live). */
export async function fetchDataSources(): Promise<DataSourcesResponse> {
  return apiFetch<DataSourcesResponse>('/data-sources');
}
