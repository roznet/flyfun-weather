/** Extract visualization-ready data from a RouteAnalysesManifest for a given model. */

import type { ElevationProfile, RouteAnalysesManifest, RoutePointAnalysis, SoundingAnalysis, RouteObservations, RouteSigmets } from '../store/types';
import type { RouteWindOverlay } from '../adapters/api-adapter';
import type { TerrainPoint, VizRouteData, VizPoint, WaypointMarker, AltitudeLines, VizCloudLayer, VizIcingZone, VizSfipZone, VizSldZone, VizCATLayer, VizInversionLayer, VizCloudDiag, VizCurrentConditions, VizMetarColumn, VizSigmetZone } from './types';
import { computeSurfaceObscurationFromCloudLayers } from './surface-obscuration';
import { randomOverlapPct } from './scales';

export interface ExtractVizOptions {
  /** Per-model wind components recomputed at an override altitude (from
   *  /advisories/recalculate). Per-point lookup falls back to the
   *  manifest's wind_components when the overlay lacks a value. */
  windOverlay?: RouteWindOverlay | null;
  /** Effective cruise altitude in feet — overrides the manifest's baked
   *  value for the cruise reference line and the Y-axis ceiling. Falls
   *  back to manifest.cruise_altitude_ft when omitted. */
  effectiveCruiseAltitudeFt?: number | null;
  /** D-0 METAR/TAF observations from the snapshot, for the current-
   *  conditions overlay. `null`/absent on D-1+. */
  routeObservations?: RouteObservations | null;
  /** D-0 route SIGMETs from the snapshot, for the current-conditions
   *  overlay. `null`/absent on D-1+. */
  routeSigmets?: RouteSigmets | null;
}

export function extractVizData(
  manifest: RouteAnalysesManifest,
  model: string,
  flightCeilingFt?: number,
  elevationProfile?: ElevationProfile | null,
  opts?: ExtractVizOptions,
): VizRouteData {
  const points: VizPoint[] = [];
  const waypointMarkers: WaypointMarker[] = [];

  const terrainProfile: TerrainPoint[] | null = elevationProfile
    ? elevationProfile.points.map((p) => ({
        distanceNm: p.distance_nm,
        elevationFt: p.elevation_ft,
      }))
    : null;

  // Build a point_index → overlay-wind map once for O(1) lookup per point.
  const windOverlayByIndex: Map<number, { headwind_kt: number; crosswind_kt: number }> = new Map();
  if (opts?.windOverlay) {
    for (const op of opts.windOverlay.points) {
      const wc = op.wind_components[model];
      if (wc) {
        windOverlayByIndex.set(op.point_index, { headwind_kt: wc.headwind_kt, crosswind_kt: wc.crosswind_kt });
      }
    }
  }

  for (const rpa of manifest.analyses) {
    const sounding = rpa.sounding[model] ?? null;
    const wind = windOverlayByIndex.get(rpa.point_index) ?? rpa.wind_components[model] ?? null;
    const terrainFt = interpolateTerrainElevation(terrainProfile, rpa.distance_from_origin_nm);

    points.push(extractPoint(rpa, sounding, wind, model, terrainFt));

    if (rpa.waypoint_icao) {
      waypointMarkers.push({
        distanceNm: rpa.distance_from_origin_nm,
        icao: rpa.waypoint_icao,
        lat: rpa.lat,
        lon: rpa.lon,
      });
    }
  }

  const effectiveCruise = opts?.effectiveCruiseAltitudeFt ?? manifest.cruise_altitude_ft;
  const actualCeiling = flightCeilingFt ?? effectiveCruise;

  return {
    points,
    cruiseAltitudeFt: effectiveCruise,
    ceilingAltitudeFt: actualCeiling,
    flightCeilingFt: Math.max(actualCeiling, effectiveCruise) + 5000,
    totalDistanceNm: manifest.total_distance_nm,
    waypointMarkers,
    departureTime: manifest.departure_time,
    flightDurationHours: manifest.flight_duration_hours,
    terrainProfile,
    currentConditions: buildCurrentConditions(opts?.routeObservations, opts?.routeSigmets, terrainProfile),
  };
}

/**
 * Map snapshot observations + SIGMETs into the route-distance-keyed viz
 * structs the current-conditions layer renders. Returns `null` when neither
 * source carries anything usable (D-1+ snapshots, or D-0 with no reporting
 * airports and no active SIGMETs) so the toggle can gray out.
 */
function buildCurrentConditions(
  obs: RouteObservations | null | undefined,
  sigmets: RouteSigmets | null | undefined,
  terrainProfile: TerrainPoint[] | null,
): VizCurrentConditions | null {
  const airports: VizMetarColumn[] = [];
  for (const a of obs?.airports ?? []) {
    // Need a flight category (drives the color) and an along-route position.
    if (a.metar_flight_category == null || a.enroute_distance_nm == null) continue;
    airports.push({
      icao: a.icao,
      enrouteDistanceNm: a.enroute_distance_nm,
      distanceFromRouteNm: a.distance_from_route_nm,
      flightCategory: a.metar_flight_category.toUpperCase(),
      // Column base at the terrain under the airport's X (field elevation is
      // not serialized on the observation; terrain is the best proxy).
      baseFt: interpolateTerrainElevation(terrainProfile, a.enroute_distance_nm),
      metarRaw: a.metar_raw,
      ceilingFt: a.metar_ceiling_ft,
      visibilityM: a.metar_visibility_m,
      windDir: a.metar_wind_dir,
      windSpeedKt: a.metar_wind_speed_kt,
      windGustKt: a.metar_wind_gust_kt,
    });
  }

  const zones: VizSigmetZone[] = [];
  for (const s of sigmets?.sigmets ?? []) {
    // Without an enroute span there's nothing to place on the X axis — skip.
    if (s.enroute_distance_from_nm == null || s.enroute_distance_to_nm == null) continue;
    zones.push({
      enrouteFromNm: s.enroute_distance_from_nm,
      enrouteToNm: s.enroute_distance_to_nm,
      baseFt: s.base_ft,
      topFt: s.top_ft,
      hazard: s.hazard ?? 'SIGMET',
      qualifier: s.qualifier,
      rawText: s.raw_text,
    });
  }

  if (airports.length === 0 && zones.length === 0) return null;
  return { airports, sigmets: zones };
}

function extractPoint(
  rpa: RoutePointAnalysis,
  sounding: SoundingAnalysis | null,
  wind: { headwind_kt: number; crosswind_kt: number } | null,
  model: string,
  terrainElevationFt: number,
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

  const cloudLayers: VizCloudLayer[] = (sounding?.cloud_layers ?? []).map((cl) => ({
    baseFt: cl.base_ft,
    topFt: cl.top_ft,
    coverage: cl.coverage,
    meanDewpointDepressionC: cl.mean_dewpoint_depression_c ?? undefined,
    meanCloudCoverPct: cl.mean_cloud_cover_pct ?? undefined,
    meanTemperatureC: cl.mean_temperature_c ?? undefined,
  }));

  // nwp_cloud_layers is intentionally nullable from the backend:
  //   null → model has no native NWP cloud envelope (gate toggle off)
  //   []   → native source available, model says clear sky
  //   [...] → render layers
  const rawNwpCloudLayers = sounding?.nwp_cloud_layers ?? null;
  const nwpCloudLayers: VizCloudLayer[] | null = rawNwpCloudLayers === null
    ? null
    : rawNwpCloudLayers.map((cl) => ({
        baseFt: cl.base_ft,
        topFt: cl.top_ft,
        coverage: cl.coverage,
        meanCloudCoverPct: cl.mean_cloud_cover_pct ?? undefined,
        meanTemperatureC: cl.mean_temperature_c ?? undefined,
        source: cl.source ?? 'dd',
      }));

  const mapIcingZone = (iz: any): VizIcingZone => ({
    baseFt: iz.base_ft,
    topFt: iz.top_ft,
    risk: iz.risk,
    type: iz.icing_type,
    meanIcingIndex: iz.mean_icing_index ?? undefined,
    meanTemperatureC: iz.mean_temperature_c ?? undefined,
    sldRisk: iz.sld_risk ?? undefined,
  });

  const icingZones: VizIcingZone[] = (sounding?.icing_zones ?? []).map(mapIcingZone);
  const icingOgimetNwpZones: VizIcingZone[] = (sounding?.icing_ogimet_nwp_zones ?? []).map(mapIcingZone);
  const iengIcingZones: VizIcingZone[] = (sounding?.ieng_icing_zones ?? []).map(mapIcingZone);

  const sfipZones: VizSfipZone[] = (sounding?.sfip_zones ?? []).map((sz) => ({
    baseFt: sz.base_ft,
    topFt: sz.top_ft,
    risk: sz.risk,
    type: sz.icing_type,
    meanSfip100: sz.mean_sfip_100,
    variant: sz.variant,
    meanTemperatureC: sz.mean_temperature_c ?? undefined,
  }));

  const sldZones: VizSldZone[] = (sounding?.sld_zones ?? []).map((sz: any) => ({
    baseFt: sz.base_ft,
    topFt: sz.top_ft,
    risk: sz.risk,
    mechanism: sz.mechanism ?? 'unknown',
  }));

  const catLayers: VizCATLayer[] = (sounding?.vertical_motion?.cat_risk_layers ?? []).map((cl: any) => ({
    baseFt: cl.base_ft,
    topFt: cl.top_ft,
    risk: cl.risk,
    richardsonNumber: cl.richardson_number ?? undefined,
  }));

  const eShearLayers: VizCATLayer[] = (sounding?.vertical_motion?.e_shear_layers ?? []).map((cl: any) => ({
    baseFt: cl.base_ft,
    topFt: cl.top_ft,
    risk: cl.risk,
    richardsonNumber: cl.richardson_number ?? undefined,
  }));

  const inversions: VizInversionLayer[] = (sounding?.inversion_layers ?? []).map((inv) => ({
    baseFt: inv.base_ft,
    topFt: inv.top_ft,
    strengthC: inv.strength_c,
    surfaceBased: (inv as any).surface_based ?? undefined,
  }));

  // Cloud cover total: random-overlap combination of the three bands
  // (see #124 — additive sum double-counts overlapping layers).
  const low = sounding?.cloud_cover_low_pct ?? 0;
  const mid = sounding?.cloud_cover_mid_pct ?? 0;
  const high = sounding?.cloud_cover_high_pct ?? 0;
  const cloudCoverTotalPct = randomOverlapPct(low, mid, high);

  // Worst model agreement
  let worstModelAgreement = 'good';
  for (const d of rpa.model_divergence) {
    if (d.agreement === 'poor') { worstModelAgreement = 'poor'; break; }
    if (d.agreement === 'moderate') { worstModelAgreement = 'moderate'; }
  }

  // GFS cloud diagnostics
  const diag = sounding?.nwp_cloud_diagnostics ?? null;
  const nwpCloudDiag: VizCloudDiag | null = diag ? {
    low: { coverPct: diag.low.cover_pct, baseFt: diag.low.base_ft, topFt: diag.low.top_ft },
    mid: { coverPct: diag.mid.cover_pct, baseFt: diag.mid.base_ft, topFt: diag.mid.top_ft },
    high: { coverPct: diag.high.cover_pct, baseFt: diag.high.base_ft, topFt: diag.high.top_ft },
    ceilingFt: diag.ceiling_ft,
  } : null;

  // Extract surface values from model_divergence (per-model values)
  const temperatureC = divergenceValue(rpa, 'temperature_c', model);
  const precipitationMm = divergenceValue(rpa, 'precipitation_mm', model);

  // Prefer NWP cloud layers when available so the obscuration band top
  // matches the cloud method drawn directly above it. `??` falls back
  // to DD only when NWP is unavailable (`null` — model has no native
  // NWP cloud envelope, e.g. ECMWF without GRIB enrichment); an empty
  // NWP array (model says clear sky) is treated as "available, no
  // clouds" and the band top falls through to the 1500 ft cap rather
  // than reading from DD layers — that's intentional, since the user
  // has selected the NWP cloud method.
  const layersForObscuration = nwpCloudLayers ?? cloudLayers;
  const surfaceObscuration = computeSurfaceObscurationFromCloudLayers(
    {
      visibilityM: sounding?.visibility_m ?? null,
      temperature2mC: sounding?.temperature_2m_c ?? null,
      dewpoint2mC: sounding?.dewpoint_2m_c ?? null,
      cloudCoverLowPct: sounding?.cloud_cover_low_pct ?? null,
    },
    layersForObscuration,
    terrainElevationFt,
  );

  return {
    distanceNm: rpa.distance_from_origin_nm,
    lat: rpa.lat,
    lon: rpa.lon,
    time: rpa.interpolated_time,
    altitudeLines,
    cloudLayers,
    nwpCloudLayers,
    icingZones,
    icingOgimetNwpZones,
    sfipZones,
    iengIcingZones,
    sldZones,
    catLayers,
    eShearLayers,
    inversions,
    convectiveRisk: sounding?.convective?.risk_level ?? 'none',
    convectiveBaseFt: sounding?.convective?.base_ft ?? null,
    convectiveTopFt: sounding?.convective?.top_ft ?? null,
    cinSurfaceJkg: indices?.cin_surface_jkg ?? sounding?.convective?.cin_jkg ?? 0,
    nwpConvectiveRisk: sounding?.convective_nwp?.risk_level ?? 'none',
    nwpConvectiveBaseFt: sounding?.convective_nwp?.base_ft ?? null,
    nwpConvectiveTopFt: sounding?.convective_nwp?.top_ft ?? null,
    nwpConvectiveCoverPct: sounding?.convective_nwp?.cover_pct ?? null,
    nwpConvectiveMethod: sounding?.convective_nwp?.method ?? null,
    hasNwpConvective: sounding?.convective_nwp != null,
    cloudCoverTotalPct,
    cloudCoverLowPct: sounding?.cloud_cover_low_pct ?? 0,
    cloudCoverMidPct: sounding?.cloud_cover_mid_pct ?? 0,
    headwindKt: wind?.headwind_kt ?? 0,
    crosswindKt: wind?.crosswind_kt ?? 0,
    capeSurfaceJkg: indices?.cape_surface_jkg ?? 0,
    worstModelAgreement,
    nwpCloudDiag,
    soundingCeilingFt: indices?.sounding_ceiling_ft ?? null,
    terrainElevationFt,
    temperatureC,
    precipitationMm,
    surfaceObscuration,
  };
}

/**
 * Determine which cross-section layers lack data for the current model.
 * Returns layer IDs that should be disabled in the UI.
 */
export function getUnavailableLayers(data: VizRouteData): Set<string> {
  const unavailable = new Set<string>();

  // "NWP Natural / Soft / Square" cloud toggle: enabled when ANY point has
  // native NWP cloud info (even if []). Distinguishes "model says clear sky"
  // (toggle on, empty) from "no NWP enrichment" (toggle disabled).
  const hasNwpCloudData = data.points.some((p) => p.nwpCloudLayers !== null);
  // Ogimet-NWP / IENG: their backends now return [] when no native NWP
  // layers exist (no fabricated zones). So gating on "any native NWP
  // cloud data" is the right signal — same as the cloud toggle.
  const hasNwpConvective = data.points.some((p) => p.hasNwpConvective);
  const hasSfip = data.points.some((p) => p.sfipZones.length > 0);
  const hasOgimetNwp = data.points.some((p) => p.icingOgimetNwpZones.length > 0);
  const hasIeng = data.points.some((p) => p.iengIcingZones.length > 0);
  const hasSld = data.points.some((p) => p.sldZones.length > 0);
  const hasEShear = data.points.some((p) => p.eShearLayers.length > 0);

  if (!hasNwpCloudData) {
    // Use the natural-style id as the canonical "NWP source unavailable"
    // signal — the compound clouds control reads this one id and grays
    // out the entire NWP source toggle (covering soft/square variants too).
    unavailable.add('nwp-cloud-bands');
    unavailable.add('icing-ogimet-nwp-bands');
    unavailable.add('ieng-icing-bands');
  }
  if (!hasSfip) unavailable.add('sfip-bands');
  if (!hasSld) unavailable.add('sld-bands');
  if (!hasEShear) unavailable.add('e-shear-bands');
  if (!hasNwpConvective) unavailable.add('nwp-convective-bg');
  // Suppress unused-var warnings — kept for symmetry / future re-use
  void hasOgimetNwp; void hasIeng;

  // Current conditions (D-0 METAR columns + SIGMET zones): unavailable when
  // the snapshot carried no observations and no SIGMETs. `buildCurrentConditions`
  // returns null (never an empty object) in that case, so a null check suffices.
  if (!data.currentConditions) unavailable.add('current-conditions');

  return unavailable;
}

/** Interpolate terrain elevation at a given route distance from the terrain profile. */
function interpolateTerrainElevation(profile: TerrainPoint[] | null, distanceNm: number): number {
  if (!profile || profile.length === 0) return 0;
  if (distanceNm <= profile[0].distanceNm) return profile[0].elevationFt;
  if (distanceNm >= profile[profile.length - 1].distanceNm) return profile[profile.length - 1].elevationFt;
  for (let i = 0; i < profile.length - 1; i++) {
    if (distanceNm >= profile[i].distanceNm && distanceNm <= profile[i + 1].distanceNm) {
      const t = (distanceNm - profile[i].distanceNm) / (profile[i + 1].distanceNm - profile[i].distanceNm);
      return profile[i].elevationFt + t * (profile[i + 1].elevationFt - profile[i].elevationFt);
    }
  }
  return 0;
}

/** Look up a per-model value from the model_divergence comparison data. */
function divergenceValue(rpa: RoutePointAnalysis, variable: string, model: string): number | null {
  for (const d of rpa.model_divergence) {
    if (d.variable === variable) {
      return d.model_values[model] ?? null;
    }
  }
  return null;
}
