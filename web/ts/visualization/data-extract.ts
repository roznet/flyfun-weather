/** Extract visualization-ready data from a RouteAnalysesManifest for a given model. */

import type { ElevationProfile, RouteAnalysesManifest, RoutePointAnalysis, SoundingAnalysis, RouteObservations, RouteSigmets, ObservedConditions, ObservedAnnulus, ObservedTopsAnnulus, ObservedFlashAnnulus } from '../store/types';
import type { RouteWindOverlay } from '../adapters/api-adapter';
import type { TerrainPoint, VizRouteData, VizPoint, WaypointMarker, AltitudeLines, VizCloudLayer, VizIcingZone, VizSfipZone, VizSldZone, VizCATLayer, VizInversionLayer, VizCloudDiag, VizCurrentConditions, VizMetarColumn, VizSigmetZone, VizFronts, VizNightInterval, VizSunSide, VizSunAtPoint, VizObserved, VizObservedPoint, VizObservedSource, VizObservedTopBin } from './types';
import type { FrontCrossing, FrontProximity, RouteFrontsManifest } from '../types/fronts';
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
  /** Experimental Hewson front artifact (#196). `null`/absent unless the
   *  "Auto Front Detection" pref was on. Resolved per-model below. */
  routeFronts?: RouteFrontsManifest | null;
  /** D-0 observed conditions from the snapshot (#574). `null`/absent on D-1+
   *  and wherever the observed collector is not enabled. */
  observedConditions?: ObservedConditions | null;
  /** Which sampled corridor width to resolve `observed.points` at. All radii
   *  ship in the payload, so changing this needs no re-fetch — the caller
   *  re-extracts and the graph/layers follow. Defaults to the widest. */
  observedRadiusNm?: number | null;
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

  // Per-point sun geometry (#227) keyed by along-route distance — model-
  // independent, so it is the same for every model selection.
  const sunByDistance = new Map<number, VizSunAtPoint>();
  for (const sp of manifest.sun?.points ?? []) {
    sunByDistance.set(Math.round(sp.distance_nm * 10), {
      elevationDeg: sp.elevation_deg,
      azimuthDeg: sp.azimuth_deg,
      relativeBearingDeg: sp.relative_bearing_deg,
    });
  }

  for (const rpa of manifest.analyses) {
    const sounding = rpa.sounding[model] ?? null;
    const wind = windOverlayByIndex.get(rpa.point_index) ?? rpa.wind_components[model] ?? null;
    const terrainFt = interpolateTerrainElevation(terrainProfile, rpa.distance_from_origin_nm);
    const sun = sunByDistance.get(Math.round(rpa.distance_from_origin_nm * 10)) ?? null;

    points.push(extractPoint(rpa, sounding, wind, model, terrainFt, sun, manifest.cruise_altitude_ft));

    if (rpa.waypoint_icao) {
      waypointMarkers.push({
        distanceNm: rpa.distance_from_origin_nm,
        icao: rpa.waypoint_icao,
        lat: rpa.lat,
        lon: rpa.lon,
      });
    }
  }

  // Observed values live in their own artifact keyed by route distance; fold
  // them onto the points so the route-graph metric registry can read them the
  // same way it reads everything else.
  const observed = buildObserved(opts?.observedConditions, opts?.observedRadiusNm);
  mergeObserved(points, observed);

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
    observed,
    fronts: buildFronts(opts?.routeFronts, model),
    nightIntervals: buildNightIntervals(manifest),
    sunSide: buildSunSide(manifest),
    // Advisory highlight geometry is derived from the advisories manifest (a
    // separate artifact), so briefing-main attaches it after extraction —
    // extractVizData only sees route analyses. Default to null here.
    advisoryHighlights: null,
  };
}

/** Map the manifest's solar night intervals to distance-based shading bands. */
function buildNightIntervals(manifest: RouteAnalysesManifest): VizNightInterval[] {
  const sun = manifest.sun;
  if (!sun || !sun.night_intervals) return [];
  return sun.night_intervals.map((n) => ({
    startNm: n.start_distance_nm,
    endNm: n.end_distance_nm,
    startTime: n.start_time,
    endTime: n.end_time,
    phase: n.phase,
  }));
}

/** Extract the sun-side summary for the seating note. */
function buildSunSide(manifest: RouteAnalysesManifest): VizSunSide | null {
  const sun = manifest.sun;
  if (!sun) return null;
  return {
    dominantSide: sun.sun_side.dominant_side,
    dominantSidePct: sun.sun_side.dominant_side_pct,
  };
}

/**
 * Resolve the front manifest to the rendered model at its primary
 * (nearest-cruise) level. Returns `null` when there is no artifact or the
 * model carries no front data (only ecmwf/gfs/icon are precomputed). Matches
 * the analysis by `level_hPa`, not position (manifest contract).
 */
function buildFronts(
  manifest: RouteFrontsManifest | null | undefined,
  model: string,
): VizFronts | null {
  if (!manifest) return null;
  const analyses = manifest.per_model[model];
  if (!analyses || analyses.length === 0) return null;

  // Merge crossings across ALL stored levels, not just the nearest-cruise one:
  // a front can sit below cruise (so the primary level has none) yet still
  // matter — its cloud/convection lofts through the flight (the Dijon case).
  // Dedupe the same front seen at multiple levels into ~25 km bins, keeping the
  // most relevant version (convective > wet > partly > dry, then by intensity)
  // so the marker reflects the worst the column carries.
  const RELEVANCE: Record<string, number> = { convective: 3, wet: 2, partly: 1, dry: 0 };
  const INTENSITY: Record<string, number> = { sharp: 3, classical: 2, significant: 1 };
  const score = (c: FrontCrossing): number =>
    (RELEVANCE[c.co_location ?? ''] ?? 0) * 10 + (INTENSITY[c.intensity] ?? 0);
  // 50 km bins — same width the advisory uses to count distinct crossings
  // (analysis/advisories/fronts.py), so marker count and "N fronts" text agree.
  const byBin = new Map<number, FrontCrossing>();
  for (const a of analyses) {
    for (const c of a.crossings) {
      const bin = Math.round(c.distance_km / 50);
      const cur = byBin.get(bin);
      if (!cur || score(c) > score(cur)) byBin.set(bin, c);
    }
  }
  const crossings = [...byBin.values()].sort((a, b) => a.distance_km - b.distance_km);

  // Nearest: prefer an on-track gated front, else the closest off-track one,
  // across levels.
  let nearest: FrontProximity | null = null;
  for (const a of analyses) {
    const nf = a.nearest;
    if (!nf) continue;
    const better = !nearest
      || (nf.on_track && !nearest.on_track)
      || (nf.on_track === nearest.on_track && nf.distance_km < nearest.distance_km);
    if (better) nearest = nf;
  }

  const chains = manifest.front_chains?.[model] ?? [];

  if (crossings.length === 0 && !nearest && chains.length === 0) return null;
  return { crossings, nearest, primaryLevelHpa: manifest.primary_level_hPa, chains };
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

/** Legacy wire bin widths in hundreds of geometric ft MSL, NOT pressure FL.
 *  Must match `CLOUD_TOP_FINE_FL_STEP`. */
const OBSERVED_FINE_FL_STEP = 10;

/** Coarse bands, kept only as a fallback for packs built before `fl_fine`. */
const OBSERVED_TOP_BANDS: Array<{ label: string; loFt: number; hiFt: number }> = [
  { label: 'FL000-050', loFt: 0, hiFt: 5000 },
  { label: 'FL050-150', loFt: 5000, hiFt: 15000 },
  { label: 'FL150-250', loFt: 15000, hiFt: 25000 },
  { label: 'FL250-400', loFt: 25000, hiFt: 40000 },
  { label: 'FL400+', loFt: 40000, hiFt: 60000 },
];

function pickAnnulus<A extends { radius_nm: number }>(annuli: A[], radiusNm: number): A | null {
  if (annuli.length === 0) return null;
  // Exact match, else the closest sampled radius — never interpolate: the
  // discs are pixel counts over real areas, and a value between two of them
  // was not measured.
  let best = annuli[0];
  for (const a of annuli) {
    if (a.radius_nm === radiusNm) return a;
    if (Math.abs(a.radius_nm - radiusNm) < Math.abs(best.radius_nm - radiusNm)) best = a;
  }
  return best;
}

function observedSource(
  field: { source: string; valid_time: string; age_minutes: number; window_minutes: number; attribution: { text: string } } | null,
  label: string,
): VizObservedSource | null {
  if (!field) return null;
  return {
    source: field.source,
    label,
    validTime: field.valid_time,
    ageMinutes: field.age_minutes,
    windowMinutes: field.window_minutes,
    attribution: field.attribution?.text ?? '',
  };
}

/**
 * Resolve the observed payload to one value per route point at one corridor
 * width.
 *
 * The three-state split survives the trip: `dbz === null` with
 * `radarNoCoverage === false` means the radar looked and found no echo, while
 * `radarNoCoverage === true` means it does not see there at all. Collapsing
 * the two would paint about half the OPERA grid as clear sky.
 */
function buildObserved(
  observed: ObservedConditions | null | undefined,
  radiusOverrideNm: number | null | undefined,
): VizObserved | null {
  if (!observed) return null;
  const radii = observed.radii_nm ?? [];
  if (radii.length === 0) return null;
  const radiusNm = radiusOverrideNm != null && radii.includes(radiusOverrideNm)
    ? radiusOverrideNm
    : Math.max(...radii);

  const distanceById = new Map<string, number>();
  for (const station of observed.stations) {
    if (station.enroute_distance_nm != null) distanceById.set(station.id, station.enroute_distance_nm);
  }

  const byId = new Map<string, VizObservedPoint>();
  const pointFor = (stationId: string): VizObservedPoint | null => {
    const distanceNm = distanceById.get(stationId);
    if (distanceNm == null) return null;
    let point = byId.get(stationId);
    if (!point) {
      point = {
        distanceNm,
        radarPresent: false,
        ratePresent: false,
        lightningPresent: false,
        dbz: null,
        radarNoCoverage: false,
        rateMmH: null,
        rateNoCoverage: false,
        flashCount: 0,
        flashRate: null,
        topsHighestFt: null,
        topsBins: [],
        topsNoCoverage: false,
        topsColdestC: null,
        topsHighestCloudiness: null,
        topsMedianCloudiness: null,
        topsHighestAviationFl: null,
      };
      byId.set(stationId, point);
    }
    return point;
  };

  for (const station of observed.reflectivity?.stations ?? []) {
    const point = pointFor(station.station_id);
    const annulus = pickAnnulus<ObservedAnnulus>(station.annuli, radiusNm);
    if (!point || !annulus) continue;
    point.radarNoCoverage = annulus.insufficient_coverage;
    point.radarPresent = true;
    point.dbz = annulus.max_value;
  }

  for (const station of observed.rain_rate?.stations ?? []) {
    const point = pointFor(station.station_id);
    const annulus = pickAnnulus<ObservedAnnulus>(station.annuli, radiusNm);
    if (!point || !annulus) continue;
    point.rateNoCoverage = annulus.insufficient_coverage;
    point.ratePresent = true;
    point.rateMmH = annulus.max_value;
  }

  for (const station of observed.lightning?.stations ?? []) {
    const point = pointFor(station.station_id);
    const annulus = pickAnnulus<ObservedFlashAnnulus>(station.annuli, radiusNm);
    if (!point || !annulus) continue;
    point.flashCount = annulus.flash_count;
    point.lightningPresent = true;
    point.flashRate = annulus.flashes_per_1000km2_per_min;
  }

  for (const station of observed.cloud_tops?.stations ?? []) {
    const point = pointFor(station.station_id);
    const annulus = pickAnnulus<ObservedTopsAnnulus>(station.annuli, radiusNm);
    if (!point || !annulus) continue;
    point.topsNoCoverage = annulus.insufficient_coverage;
    point.topsHighestFt = annulus.highest_fl != null ? annulus.highest_fl * 100 : null;
    // Denominator is valid retrieval samples (cloudy and clear), not cloudy
    // pixels alone. Parallax overlap/unequal footprints prevent an area claim.
    const lookedAt = annulus.valid_px || 0;
    // Prefer the sparse fine histogram; fall back to the coarse bands for a
    // pack built before it existed. Fine bins avoid implying the full coarse
    // interval contains detected tops; missing bins still do not prove clear air.
    const fine = annulus.fl_fine ?? {};
    const fineKeys = Object.keys(fine);
    point.topsBins = fineKeys.length > 0
      ? fineKeys
          .map((k) => Number(k))
          .filter((fl) => Number.isFinite(fl))
          .sort((a, b) => a - b)
          .map((fl): VizObservedTopBin => {
            const count = fine[String(fl)] ?? 0;
            return {
              label: `${fl * 100}–${(fl + OBSERVED_FINE_FL_STEP) * 100} ft MSL`,
              loFt: fl * 100,
              hiFt: (fl + OBSERVED_FINE_FL_STEP) * 100,
              fraction: lookedAt > 0 ? count / lookedAt : 0,
              count,
            };
          })
      : OBSERVED_TOP_BANDS.map((band): VizObservedTopBin => {
          const count = annulus.fl_bins?.[band.label] ?? 0;
          return {
            label: band.label === 'FL400+' ? '40000+ ft MSL' : `${band.loFt}–${band.hiFt} ft MSL`,
            loFt: band.loFt,
            hiFt: band.hiFt,
            fraction: lookedAt > 0 ? count / lookedAt : 0,
            count,
          };
        });
    // QM9 describes Opaque + RTM + inversion; no multilayer/confidence inference.
    // Kelvin on the wire (the granule's own unit); °C for anything a pilot reads.
    point.topsColdestC = annulus.coldest_top_k != null ? annulus.coldest_top_k - 273.15 : null;
    point.topsHighestCloudiness = annulus.highest_cloudiness ?? null;
    point.topsMedianCloudiness = annulus.median_cloudiness ?? null;
    point.topsHighestAviationFl = annulus.highest_aviation_fl ?? null;
  }

  const points = [...byId.values()].sort((a, b) => a.distanceNm - b.distanceNm);
  if (points.length === 0) return null;

  return {
    radiiNm: [...radii].sort((a, b) => a - b),
    radiusNm,
    points,
    reflectivity: observedSource(observed.reflectivity, 'Radar reflectivity'),
    rainRate: observedSource(observed.rain_rate, 'Radar rain rate'),
    cloudTops: observedSource(observed.cloud_tops, 'Satellite cloud tops'),
    lightning: observedSource(observed.lightning, 'Lightning'),
    summaryLines: observed.summary_lines ?? [],
  };
}

/** Largest gap (nm) between an analysis point and an observed station that
 *  still counts as the same place. The observed sampler walks the same route
 *  at the same spacing, so matches are normally exact; this tolerates a
 *  rounding difference, not a genuinely different point. */
const OBSERVED_MATCH_TOLERANCE_NM = 6;

/** Fold observed values onto the route points, matched by along-route distance. */
function mergeObserved(points: VizPoint[], observed: VizObserved | null): void {
  if (!observed || observed.points.length === 0) return;
  for (const point of points) {
    let best: VizObservedPoint | null = null;
    let bestGap = Infinity;
    for (const candidate of observed.points) {
      const gap = Math.abs(candidate.distanceNm - point.distanceNm);
      if (gap < bestGap) {
        bestGap = gap;
        best = candidate;
      }
    }
    if (!best || bestGap > OBSERVED_MATCH_TOLERANCE_NM) continue;
    // The whole matched sample, so the hover rows can report everything this
    // point measured without re-deriving the match. The flattened fields below
    // stay for the route-graph metric registry, which reads scalars off the
    // point directly.
    point.observed = best;
    point.observedRateMmH = best.rateMmH;
    point.observedFlashRate = best.flashRate;
    // This flag belongs to the rain-rate graph, not the reflectivity stream.
    point.observedRadarNoCoverage = best.rateNoCoverage;
  }
}

/**
 * ISA standard-atmosphere temperature (°C) at a geopotential altitude (ft).
 * Troposphere lapse rate ≈1.9812 °C per 1000 ft to the 36,089 ft tropopause,
 * isothermal −56.5 °C above. Reference for ISA deviation at cruise.
 */
export function isaTemperatureC(altitudeFt: number): number {
  return altitudeFt <= 36089 ? 15 - 1.9812 * (altitudeFt / 1000) : -56.5;
}

function extractPoint(
  rpa: RoutePointAnalysis,
  sounding: SoundingAnalysis | null,
  wind: { headwind_kt: number; crosswind_kt: number } | null,
  model: string,
  terrainElevationFt: number,
  sun: VizSunAtPoint | null,
  cruiseAltitudeFt: number,
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
  const qnhHpa = divergenceValue(rpa, 'pressure_msl_hpa', model);

  // Temperature at the elected cruise level (per-model dict; absent on old
  // packs) and its ISA deviation. The backend interpolated cruise temp at
  // `cruiseAltitudeFt`, so the ISA reference must use the same altitude.
  const temperatureCruiseC = rpa.cruise_temperature_c?.[model] ?? null;
  const isaDevC =
    temperatureCruiseC == null
      ? null
      : temperatureCruiseC - isaTemperatureC(cruiseAltitudeFt);

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
    nwpConvectivePrecipMmH: sounding?.convective_nwp?.convective_precip_mm_h ?? null,
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
    temperatureCruiseC,
    isaDevC,
    precipitationMm,
    // Observed values are merged in after extraction (see mergeObserved):
    // they come from a different artifact keyed by route distance, not from
    // this point's model analysis.
    observedRateMmH: null,
    observedFlashRate: null,
    observedRadarNoCoverage: false,
    qnhHpa,
    surfaceObscuration,
    sun,
  };
}

/**
 * Determine which cross-section layers lack data for the current model.
 * Returns layer IDs that should be disabled in the UI.
 *
 * SYNC: the iOS app mirrors this in
 * app/flyfun-weather/flyfun-weather/Views/CrossSection/NwpFallback.swift
 * (NwpFallback.unavailableLayers). The substitution half lives in
 * ./cross-section/nwp-fallback.ts. Keep all three in lockstep.
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

  // Experimental front markers (#196): unavailable unless the rendered model
  // carries on-track front crossings (only ecmwf/gfs/icon do, and only when
  // the "Auto Front Detection" pref was on).
  if (!data.fronts || data.fronts.crossings.length === 0) unavailable.add('fronts-markers');

  // Observed layers (#574): each greys out when ITS OWN source is absent, not
  // when the payload is. Radar without EUMETSAT credentials is half the
  // feature working, and the surface layer should stay usable in that case.
  if (!data.observed?.cloudTops) unavailable.add('observed-tops');
  if (!data.observed?.reflectivity && !data.observed?.lightning) {
    unavailable.add('observed-surface');
  }

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
