/** SSE adapter + VizRouteData synthesis for the airport profile panel.
 *
 * Right-clicking an airport on the forecast map fires a phased SSE stream
 * (`/api/maps/airport-profile`). This module:
 *   1. Wraps the EventSource lifecycle (start, abort, error)
 *   2. Translates phase events into a synthetic `VizRouteData` whose
 *      X-axis is hours-since-start (collapsing the route's spatial
 *      extent into a single airport), so the existing
 *      `CrossSectionRenderer` can render without modification.
 */

import type { VizRouteData, VizPoint, AltitudeLines, VizCloudLayer, VizIcingZone, VizSfipZone, VizCATLayer, VizInversionLayer } from '../visualization/types';
import type { SoundingProfileData, SoundingProfileLevel, ParcelPathPoint, CloudLayer, IcingZone, InversionLayer } from '../visualization/skewt/types';
import { computeSurfaceObscuration, type ObscurationLevel } from '../visualization/surface-obscuration';
import { randomOverlapPct } from '../visualization/scales';
import { API_BASE } from '../utils';

export interface AirportProfileMeta {
  icao: string;
  lat: number;
  lon: number;
  elevation_ft: number | null;
  model: string;
  start_hour: string;
  window_h: number;
  hours: string[];
}

export interface AirportProfileSurfaceHour {
  time: string;
  temperature_2m_c: number | null;
  dewpoint_2m_c: number | null;
  visibility_m: number | null;
  wind_speed_kt: number | null;
  wind_direction_deg: number | null;
  wind_gusts_kt: number | null;
  precipitation_mm: number | null;
  snowfall_cm: number | null;
  cape_jkg: number | null;
  cloud_cover_pct: number | null;
  cloud_cover_low_pct: number | null;
  ceiling_ft: number | null;
  freezing_level_ft: number | null;
}

export interface AirportProfileLevelsHour {
  time: string;
  temperature_2m_c: number | null;
  dewpoint_2m_c: number | null;
  wind_speed_10m_kt: number | null;
  wind_direction_10m_deg: number | null;
  wind_gusts_10m_kt: number | null;
  cape_jkg: number | null;
  cloud_cover_pct: number | null;
  cloud_cover_low_pct: number | null;
  freezing_level_m: number | null;
  visibility_m: number | null;
  pressure_levels: Array<{
    pressure_hpa: number;
    altitude_ft: number | null;
    temperature_c: number | null;
    dewpoint_c: number | null;
    wind_speed_kt: number | null;
    wind_direction_deg: number | null;
    relative_humidity_pct: number | null;
    cloud_area_fraction_pct: number | null;
  }>;
}

/** Shallow shape of the server-side `SoundingAnalysis.model_dump()`
 *  payload. Only the fields the adapter actually projects are listed —
 *  the full Python model is much larger. Field types use `any` for
 *  nested zone/layer arrays where the structural assumptions are too
 *  varied to express here without duplicating server contracts; the
 *  point of this interface is to surface the top-level keys so
 *  refactors can't silently drop one without breaking compilation. */
export interface SoundingPayload {
  indices: Record<string, number | null> | null;
  parcel_path?: Array<{ pressure_hpa: number; temperature_c: number }>;
  cloud_layers?: any[];
  nwp_cloud_layers?: any[] | null;
  icing_zones?: any[];
  icing_ogimet_nwp_zones?: any[];
  ieng_icing_zones?: any[];
  sfip_zones?: any[];
  sld_zones?: any[];
  inversion_layers?: any[];
  vertical_motion?: { cat_risk_layers?: any[]; e_shear_layers?: any[] } | null;
  convective?: { risk_level?: string; base_ft?: number | null; top_ft?: number | null; cin_jkg?: number } | null;
  convective_nwp?: { risk_level?: string; base_ft?: number | null; top_ft?: number | null; cover_pct?: number | null; convective_precip_mm_h?: number | null; method?: string | null } | null;
  cloud_cover_low_pct?: number | null;
  cloud_cover_mid_pct?: number | null;
  cloud_cover_high_pct?: number | null;
}

export interface AirportProfileDerivedPoint {
  point_index: number;
  time: string;
  /** Server-side `SoundingAnalysis.model_dump()` payload (snake_case keys). */
  sounding: SoundingPayload | null;
}

/** Result of the local-GRIB enrichment phase. `sources[m]` is present
 *  when the model `m` actually contributed (init_time_unix is the run
 *  timestamp). `skipped[m]` is set when a model couldn't enrich
 *  (out_of_domain / out_of_range / no_data). */
export interface AirportProfileEnriched {
  sources: Record<string, { init_time_unix: number }>;
  skipped: Record<string, string>;
}

export interface AirportProfileSnapshot {
  meta: AirportProfileMeta | null;
  surface: AirportProfileSurfaceHour[];
  levels: AirportProfileLevelsHour[];
  enriched: AirportProfileEnriched | null;
  derived: AirportProfileDerivedPoint[];
}

export type AirportProfilePhase = 'meta' | 'surface' | 'levels' | 'enriched' | 'derived' | 'complete' | 'error';

export interface AirportProfileStreamHandle {
  abort(): void;
}

/** Open the SSE stream and call `onPhase` after each phase update. */
export function streamAirportProfile(
  params: { icao: string; model: string; startHour: string; windowH?: number },
  onPhase: (phase: AirportProfilePhase, snapshot: AirportProfileSnapshot, raw: any) => void,
): AirportProfileStreamHandle {
  const qs = new URLSearchParams({
    icao: params.icao,
    model: params.model,
    start_hour: params.startHour,
  });
  if (params.windowH !== undefined) qs.set('window_h', String(params.windowH));

  const url = `${API_BASE}/maps/airport-profile?${qs.toString()}`;
  const es = new EventSource(url, { withCredentials: true });
  const snapshot: AirportProfileSnapshot = {
    meta: null, surface: [], levels: [], enriched: null, derived: [],
  };

  const dispatch = (phase: AirportProfilePhase, raw: any) => onPhase(phase, snapshot, raw);

  es.addEventListener('meta', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      snapshot.meta = data;
      dispatch('meta', data);
    } catch (err) { console.warn('airport-profile: bad meta event', err); }
  });
  es.addEventListener('surface', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      snapshot.surface = data.hours ?? [];
      dispatch('surface', data);
    } catch (err) { console.warn('airport-profile: bad surface event', err); }
  });
  es.addEventListener('levels', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      snapshot.levels = data.hours ?? [];
      dispatch('levels', data);
    } catch (err) { console.warn('airport-profile: bad levels event', err); }
  });
  es.addEventListener('enriched', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      snapshot.enriched = { sources: data.sources ?? {}, skipped: data.skipped ?? {} };
      dispatch('enriched', data);
    } catch (err) { console.warn('airport-profile: bad enriched event', err); }
  });
  es.addEventListener('derived', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      snapshot.derived = data.points ?? [];
      dispatch('derived', data);
    } catch (err) { console.warn('airport-profile: bad derived event', err); }
  });
  es.addEventListener('complete', () => {
    dispatch('complete', null);
    es.close();
  });
  // Server-emitted structured error event (matches /refresh/stream pattern).
  // We piggyback on the same 'error' phase the EventSource onerror uses;
  // the raw payload tells the host whether it was a clean server error
  // (has phase + message) or a connection drop (no payload).
  es.addEventListener('error', (e: Event) => {
    let payload: any = null;
    if (e instanceof MessageEvent && typeof e.data === 'string') {
      try { payload = JSON.parse(e.data); } catch { /* not JSON, treat as connection error */ }
    }
    dispatch('error', payload ?? e);
    es.close();
  });

  return { abort: () => es.close() };
}

// ---------------------------------------------------------------------------
// Adapter: AirportProfileSnapshot → VizRouteData (time-axis mode)
// ---------------------------------------------------------------------------

const HOURS_TO_NM_SCALE = 1; // 1 hour = 1 "nm" on the synthetic distance axis

/** Build a synthetic `VizRouteData` whose X-axis is hours-since-start.
 *
 * `points[i].distanceNm` carries the hour offset (i in 0..window_h). The
 * cross-section's tick formatter checks `timeAxisMode` and renders the
 * point's `time` as the X label instead of nautical miles.
 *
 * Surface-only (no levels/derived yet) still produces points with empty
 * weather layers — the cross-section axes paint immediately, and the
 * derived phase fills in clouds/icing/CAT/etc.
 */
export function snapshotToVizData(
  snapshot: AirportProfileSnapshot,
  options: { defaultCeilingFt?: number } = {},
): VizRouteData | null {
  if (!snapshot.meta) return null;
  const { meta } = snapshot;
  const elevationFt = meta.elevation_ft ?? 0;

  // Build per-hour points. Ordering follows meta.hours so we render even
  // when surface/derived have gaps.
  const derivedByTime = new Map<string, AirportProfileDerivedPoint>();
  for (const d of snapshot.derived) derivedByTime.set(d.time, d);
  const surfaceByTime = new Map<string, AirportProfileSurfaceHour>();
  for (const s of snapshot.surface) surfaceByTime.set(s.time, s);
  const levelsByTime = new Map<string, AirportProfileLevelsHour>();
  for (const l of snapshot.levels) levelsByTime.set(l.time, l);

  const points: VizPoint[] = meta.hours.map((time, idx) => {
    const derived = derivedByTime.get(time);
    const sounding = derived?.sounding ?? null;
    const surface = surfaceByTime.get(time);
    const levelsHour = levelsByTime.get(time);
    return synthesizeVizPoint(time, idx, meta.lat, meta.lon, elevationFt, sounding, surface ?? null, levelsHour ?? null);
  });

  // Cap the Y axis using the highest cruise-altitude target we'd reasonably
  // care about for an airport detail view (default 18000 ft, matching most
  // GA briefings). The real altitude data drives the layers — this just
  // sizes the canvas.
  const ceilingFt = options.defaultCeilingFt ?? 18000;

  return {
    points,
    cruiseAltitudeFt: ceilingFt,
    ceilingAltitudeFt: ceilingFt,
    flightCeilingFt: ceilingFt + 5000,
    totalDistanceNm: Math.max(1, (meta.hours.length - 1) * HOURS_TO_NM_SCALE),
    waypointMarkers: [],
    departureTime: meta.start_hour,
    flightDurationHours: meta.window_h,
    terrainProfile: [
      { distanceNm: 0, elevationFt },
      { distanceNm: Math.max(1, (meta.hours.length - 1) * HOURS_TO_NM_SCALE), elevationFt },
    ],
    timeAxisMode: true,
    // No route-distance overlay in the single-airport, time-axis view.
    currentConditions: null,
    fronts: null,
    nightIntervals: [],
    sunSide: null,
  };
}

function synthesizeVizPoint(
  time: string,
  idx: number,
  lat: number,
  lon: number,
  elevationFt: number,
  sounding: SoundingPayload | null,
  surface: AirportProfileSurfaceHour | null,
  levelsHour: AirportProfileLevelsHour | null,
): VizPoint {
  const indices = sounding?.indices ?? null;

  const altitudeLines: AltitudeLines = {
    freezingLevelFt: indices?.freezing_level_ft ?? null,
    minus10cLevelFt: indices?.minus10c_level_ft ?? null,
    minus20cLevelFt: indices?.minus20c_level_ft ?? null,
    lclAltitudeFt: indices?.lcl_altitude_ft ?? null,
    lfcAltitudeFt: indices?.lfc_altitude_ft ?? null,
    elAltitudeFt: indices?.el_altitude_ft ?? null,
  };

  const cloudLayers: VizCloudLayer[] = (sounding?.cloud_layers ?? []).map((cl: any) => ({
    baseFt: cl.base_ft, topFt: cl.top_ft, coverage: cl.coverage,
    meanDewpointDepressionC: cl.mean_dewpoint_depression_c ?? undefined,
    meanCloudCoverPct: cl.mean_cloud_cover_pct ?? undefined,
    meanTemperatureC: cl.mean_temperature_c ?? undefined,
  }));
  const rawNwpCloudLayers = sounding?.nwp_cloud_layers ?? null;
  const nwpCloudLayers: VizCloudLayer[] | null = rawNwpCloudLayers === null
    ? null
    : rawNwpCloudLayers.map((cl: any) => ({
        baseFt: cl.base_ft, topFt: cl.top_ft, coverage: cl.coverage,
        meanCloudCoverPct: cl.mean_cloud_cover_pct ?? undefined,
        meanTemperatureC: cl.mean_temperature_c ?? undefined,
        source: cl.source ?? 'dd',
      }));

  const mapIcingZone = (iz: any): VizIcingZone => ({
    baseFt: iz.base_ft, topFt: iz.top_ft, risk: iz.risk, type: iz.icing_type,
    meanIcingIndex: iz.mean_icing_index ?? undefined,
    meanTemperatureC: iz.mean_temperature_c ?? undefined,
    sldRisk: iz.sld_risk ?? undefined,
  });

  const icingZones: VizIcingZone[] = (sounding?.icing_zones ?? []).map(mapIcingZone);
  const icingOgimetNwpZones: VizIcingZone[] = (sounding?.icing_ogimet_nwp_zones ?? []).map(mapIcingZone);
  const iengIcingZones: VizIcingZone[] = (sounding?.ieng_icing_zones ?? []).map(mapIcingZone);

  const sfipZones: VizSfipZone[] = (sounding?.sfip_zones ?? []).map((sz: any) => ({
    baseFt: sz.base_ft, topFt: sz.top_ft, risk: sz.risk, type: sz.icing_type,
    meanSfip100: sz.mean_sfip_100 ?? null, variant: sz.variant ?? 'full',
    meanTemperatureC: sz.mean_temperature_c ?? undefined,
  }));
  const sldZones = (sounding?.sld_zones ?? []).map((sz: any) => ({
    baseFt: sz.base_ft, topFt: sz.top_ft, risk: sz.risk,
    mechanism: sz.mechanism ?? 'unknown',
  }));
  const catLayers: VizCATLayer[] = (sounding?.vertical_motion?.cat_risk_layers ?? []).map((cl: any) => ({
    baseFt: cl.base_ft, topFt: cl.top_ft, risk: cl.risk,
    richardsonNumber: cl.richardson_number ?? undefined,
  }));
  const eShearLayers: VizCATLayer[] = (sounding?.vertical_motion?.e_shear_layers ?? []).map((cl: any) => ({
    baseFt: cl.base_ft, topFt: cl.top_ft, risk: cl.risk,
    richardsonNumber: cl.richardson_number ?? undefined,
  }));
  const inversions: VizInversionLayer[] = (sounding?.inversion_layers ?? []).map((inv: any) => ({
    baseFt: inv.base_ft, topFt: inv.top_ft, strengthC: inv.strength_c,
    surfaceBased: inv.surface_based ?? undefined,
  }));

  // Cloud cover: prefer the sounding (derived phase) values, but fall back
  // to the surface cache so the cross-section's cloud-cover-derived
  // metrics aren't pinned at zero during the ~2s window between levels
  // arriving and derived completing.
  const low = sounding?.cloud_cover_low_pct ?? surface?.cloud_cover_low_pct ?? 0;
  const mid = sounding?.cloud_cover_mid_pct ?? 0;
  const high = sounding?.cloud_cover_high_pct ?? 0;

  // Surface obscuration uses the levels phase when available (real per-
  // level DD for an accurate band top), and falls back to surface-only
  // when levels haven't arrived — the floor/cap still produce a useful
  // visual band.
  const obscurationLevels: ObscurationLevel[] = (levelsHour?.pressure_levels ?? [])
    .filter((pl) => pl.altitude_ft !== null && pl.altitude_ft > elevationFt)
    .map((pl) => {
      const dd = pl.temperature_c !== null && pl.dewpoint_c !== null
        ? pl.temperature_c - pl.dewpoint_c
        : null;
      return { altitudeFt: pl.altitude_ft as number, ddC: dd };
    })
    .sort((a, b) => a.altitudeFt - b.altitudeFt);
  const surfaceObscuration = computeSurfaceObscuration(
    {
      visibilityM: surface?.visibility_m ?? levelsHour?.visibility_m ?? null,
      temperature2mC: surface?.temperature_2m_c ?? levelsHour?.temperature_2m_c ?? null,
      dewpoint2mC: surface?.dewpoint_2m_c ?? levelsHour?.dewpoint_2m_c ?? null,
      cloudCoverLowPct: surface?.cloud_cover_low_pct ?? levelsHour?.cloud_cover_low_pct ?? null,
    },
    obscurationLevels,
    elevationFt,
  );

  return {
    distanceNm: idx * HOURS_TO_NM_SCALE,
    lat, lon, time,
    altitudeLines,
    cloudLayers, nwpCloudLayers,
    icingZones, icingOgimetNwpZones, iengIcingZones,
    sfipZones, sldZones,
    catLayers, eShearLayers,
    inversions,
    convectiveRisk: sounding?.convective?.risk_level ?? 'none',
    convectiveBaseFt: sounding?.convective?.base_ft ?? null,
    convectiveTopFt: sounding?.convective?.top_ft ?? null,
    cinSurfaceJkg: indices?.cin_surface_jkg ?? sounding?.convective?.cin_jkg ?? 0,
    nwpConvectiveRisk: sounding?.convective_nwp?.risk_level ?? 'none',
    nwpConvectiveBaseFt: sounding?.convective_nwp?.base_ft ?? null,
    nwpConvectiveTopFt: sounding?.convective_nwp?.top_ft ?? null,
    nwpConvectiveCoverPct: sounding?.convective_nwp?.cover_pct ?? null,
    nwpConvectivePrecipMmH: sounding?.convective_nwp?.convective_precip_mm_h ?? null,
    nwpConvectiveMethod: sounding?.convective_nwp?.method ?? null,
    hasNwpConvective: sounding?.convective_nwp != null,
    cloudCoverTotalPct: randomOverlapPct(low, mid, high),
    cloudCoverLowPct: low,
    cloudCoverMidPct: mid,
    headwindKt: 0,
    crosswindKt: 0,
    capeSurfaceJkg: indices?.cape_surface_jkg ?? surface?.cape_jkg ?? 0,
    worstModelAgreement: 'good',
    nwpCloudDiag: null,
    soundingCeilingFt: indices?.sounding_ceiling_ft ?? surface?.ceiling_ft ?? null,
    terrainElevationFt: elevationFt,
    temperatureC: surface?.temperature_2m_c ?? null,
    // Single-airport profile has no elected cruise level, so ISA deviation /
    // cruise temperature have no source here — the metric renders empty.
    temperatureCruiseC: null,
    isaDevC: null,
    precipitationMm: surface?.precipitation_mm ?? null,
    // MSL pressure is not part of the airport-profile surface payload, so QNH
    // has no source here — the route-graph metric renders empty for profiles.
    qnhHpa: null,
    surfaceObscuration,
    // Single-airport time-axis view has no along-route sun geometry.
    sun: null,
  };
}

// ---------------------------------------------------------------------------
// Skew-T data: pick a single hour's derived sounding + raw levels
// ---------------------------------------------------------------------------

/** Build a `SoundingProfileData` for the Skew-T renderer from the snapshot,
 *  selecting the hour at `hourIndex` (0 = start_hour). Returns null when
 *  the levels phase hasn't arrived yet (caller can show a skeleton). */
export function snapshotToSkewtData(
  snapshot: AirportProfileSnapshot,
  hourIndex: number = 0,
): SoundingProfileData | null {
  if (!snapshot.meta) return null;
  const time = snapshot.meta.hours[hourIndex];
  if (!time) return null;

  const levelsHour = snapshot.levels.find((h) => h.time === time);
  const derived = snapshot.derived.find((d) => d.time === time);
  if (!levelsHour && !derived) return null;

  const levels: SoundingProfileLevel[] = (levelsHour?.pressure_levels ?? [])
    .filter((pl) => pl.temperature_c !== null)
    .map((pl) => {
      // DD inline (server hasn't run analyze_sounding yet for this hour).
      let dd: number | null = null;
      if (pl.temperature_c !== null && pl.dewpoint_c !== null) {
        dd = +(pl.temperature_c - pl.dewpoint_c).toFixed(1);
      }
      return {
        pressure_hpa: pl.pressure_hpa,
        altitude_ft: pl.altitude_ft,
        temperature_c: pl.temperature_c as number,
        dewpoint_c: pl.dewpoint_c,
        wind_speed_kt: pl.wind_speed_kt,
        wind_direction_deg: pl.wind_direction_deg,
        relative_humidity_pct: pl.relative_humidity_pct,
        dewpoint_depression_c: dd,
        wet_bulb_c: null,
        theta_e_k: null,
        lapse_rate_c_per_km: null,
        icing_index: null,
        icing_index_nwp: null,
        sfip_100: null,
        cloud_liquid_water_g_m3: null,
        ice_mixing_ratio_g_kg: null,
        cloud_area_fraction_pct: pl.cloud_area_fraction_pct,
        richardson_number: null,
        omega_pa_s: null,
        w_fpm: null,
      };
    })
    .sort((a, b) => b.pressure_hpa - a.pressure_hpa);

  const sa = derived?.sounding ?? null;
  const indices = sa?.indices ?? null;
  const parcel_path: ParcelPathPoint[] = (sa?.parcel_path ?? []).map((p: any) => ({
    pressure_hpa: p.pressure_hpa, temperature_c: p.temperature_c,
  }));
  const cloud_layers: CloudLayer[] = (sa?.cloud_layers ?? []).map((cl: any) => ({
    base_ft: cl.base_ft, top_ft: cl.top_ft, coverage: cl.coverage,
  }));
  const nwp_cloud_layers: CloudLayer[] = (sa?.nwp_cloud_layers ?? []).map((cl: any) => ({
    base_ft: cl.base_ft, top_ft: cl.top_ft, coverage: cl.coverage,
  }));
  const icing_zones: IcingZone[] = (sa?.icing_zones ?? []).map((iz: any) => ({
    base_ft: iz.base_ft, top_ft: iz.top_ft, risk: iz.risk, icing_type: iz.icing_type,
  }));
  const icing_ogimet_nwp_zones: IcingZone[] = (sa?.icing_ogimet_nwp_zones ?? []).map((iz: any) => ({
    base_ft: iz.base_ft, top_ft: iz.top_ft, risk: iz.risk, icing_type: iz.icing_type,
  }));
  const inversion_layers: InversionLayer[] = (sa?.inversion_layers ?? []).map((inv: any) => ({
    base_ft: inv.base_ft, top_ft: inv.top_ft, strength_c: inv.strength_c,
  }));

  return {
    point_index: hourIndex,
    lat: snapshot.meta.lat,
    lon: snapshot.meta.lon,
    distance_from_origin_nm: hourIndex,
    waypoint_icao: snapshot.meta.icao,
    model: snapshot.meta.model,
    time,
    levels,
    cruise_altitude_ft: null,
    track_deg: null,
    label: snapshot.meta.icao,
    indices,
    parcel_path,
    cloud_layers,
    nwp_cloud_layers,
    icing_zones,
    icing_ogimet_nwp_zones,
    sfip_zones: sa?.sfip_zones ?? [],
    inversion_layers,
    convective: sa?.convective ?? null,
  };
}
