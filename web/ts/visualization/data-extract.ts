/** Extract visualization-ready data from a RouteAnalysesManifest for a given model. */

import type { ElevationProfile, RouteAnalysesManifest, RoutePointAnalysis, SoundingAnalysis } from '../store/types';
import type { TerrainPoint, VizRouteData, VizPoint, WaypointMarker, AltitudeLines, VizCloudLayer, VizIcingZone, VizSfipZone, VizCATLayer, VizInversionLayer, VizCloudDiag } from './types';

export function extractVizData(
  manifest: RouteAnalysesManifest,
  model: string,
  flightCeilingFt?: number,
  elevationProfile?: ElevationProfile | null,
): VizRouteData {
  const points: VizPoint[] = [];
  const waypointMarkers: WaypointMarker[] = [];

  for (const rpa of manifest.analyses) {
    const sounding = rpa.sounding[model] ?? null;
    const wind = rpa.wind_components[model] ?? null;

    points.push(extractPoint(rpa, sounding, wind, model));

    if (rpa.waypoint_icao) {
      waypointMarkers.push({
        distanceNm: rpa.distance_from_origin_nm,
        icao: rpa.waypoint_icao,
        lat: rpa.lat,
        lon: rpa.lon,
      });
    }
  }

  const actualCeiling = flightCeilingFt ?? manifest.cruise_altitude_ft;

  const terrainProfile: TerrainPoint[] | null = elevationProfile
    ? elevationProfile.points.map((p) => ({
        distanceNm: p.distance_nm,
        elevationFt: p.elevation_ft,
      }))
    : null;

  return {
    points,
    cruiseAltitudeFt: manifest.cruise_altitude_ft,
    ceilingAltitudeFt: actualCeiling,
    flightCeilingFt: Math.max(actualCeiling, manifest.cruise_altitude_ft) + 5000,
    totalDistanceNm: manifest.total_distance_nm,
    waypointMarkers,
    departureTime: manifest.departure_time,
    flightDurationHours: manifest.flight_duration_hours,
    terrainProfile,
  };
}

function extractPoint(
  rpa: RoutePointAnalysis,
  sounding: SoundingAnalysis | null,
  wind: { headwind_kt: number; crosswind_kt: number } | null,
  model: string,
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
  }));

  const icingZones: VizIcingZone[] = (sounding?.icing_zones ?? []).map((iz) => ({
    baseFt: iz.base_ft,
    topFt: iz.top_ft,
    risk: iz.risk,
    type: iz.icing_type,
  }));

  const sfipZones: VizSfipZone[] = (sounding?.sfip_zones ?? []).map((sz) => ({
    baseFt: sz.base_ft,
    topFt: sz.top_ft,
    risk: sz.risk,
    type: sz.icing_type,
    meanSfip100: sz.mean_sfip_100,
    variant: sz.variant,
  }));

  const catLayers: VizCATLayer[] = (sounding?.vertical_motion?.cat_risk_layers ?? []).map((cl) => ({
    baseFt: cl.base_ft,
    topFt: cl.top_ft,
    risk: cl.risk,
  }));

  const inversions: VizInversionLayer[] = (sounding?.inversion_layers ?? []).map((inv) => ({
    baseFt: inv.base_ft,
    topFt: inv.top_ft,
    strengthC: inv.strength_c,
  }));

  // Cloud cover total: sum low+mid+high, cap at 100
  const low = sounding?.cloud_cover_low_pct ?? 0;
  const mid = sounding?.cloud_cover_mid_pct ?? 0;
  const high = sounding?.cloud_cover_high_pct ?? 0;
  const cloudCoverTotalPct = Math.min(100, low + mid + high);

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

  return {
    distanceNm: rpa.distance_from_origin_nm,
    lat: rpa.lat,
    lon: rpa.lon,
    time: rpa.interpolated_time,
    altitudeLines,
    cloudLayers,
    icingZones,
    sfipZones,
    catLayers,
    inversions,
    convectiveRisk: sounding?.convective?.risk_level ?? 'none',
    cloudCoverTotalPct,
    cloudCoverLowPct: sounding?.cloud_cover_low_pct ?? 0,
    cloudCoverMidPct: sounding?.cloud_cover_mid_pct ?? 0,
    headwindKt: wind?.headwind_kt ?? 0,
    crosswindKt: wind?.crosswind_kt ?? 0,
    capeSurfaceJkg: indices?.cape_surface_jkg ?? 0,
    worstModelAgreement,
    nwpCloudDiag,
    temperatureC,
    precipitationMm,
  };
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
