/** Extract visualization-ready data from a RouteAnalysesManifest for a given model. */

import type { ElevationProfile, RouteAnalysesManifest, RoutePointAnalysis, SoundingAnalysis } from '../store/types';
import type { TerrainPoint, VizRouteData, VizPoint, WaypointMarker, AltitudeLines, VizCloudLayer, VizIcingZone, VizCATLayer, VizInversionLayer } from './types';

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

    points.push(extractPoint(rpa, sounding, wind));

    if (rpa.waypoint_icao) {
      waypointMarkers.push({
        distanceNm: rpa.distance_from_origin_nm,
        icao: rpa.waypoint_icao,
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
  }));

  const icingZones: VizIcingZone[] = (sounding?.icing_zones ?? []).map((iz) => ({
    baseFt: iz.base_ft,
    topFt: iz.top_ft,
    risk: iz.risk,
    type: iz.icing_type,
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

  return {
    distanceNm: rpa.distance_from_origin_nm,
    time: rpa.interpolated_time,
    altitudeLines,
    cloudLayers,
    icingZones,
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
  };
}
