import type {
  AdvisoryEvidenceRegion,
  ModelAdvisoryResult,
  RouteAdvisoriesManifest,
  RouteAdvisoryResult,
} from '../../../ts/types/advisories';
import type { AdvisoryActionContext } from '../../../ts/visualization/advisory-actions';
import type {
  ActiveAdvisoryFocus,
  ResolvedFocusRegion,
} from '../../../ts/visualization/advisory-focus';
import type { VizRouteData } from '../../../ts/visualization/types';
import { makeVizPoint } from './viz-point';

function evidenceRegion(
  start: number,
  end: number,
  overrides: Partial<AdvisoryEvidenceRegion> = {},
): AdvisoryEvidenceRegion {
  return {
    start_point_index: start,
    end_point_index: end,
    lower_altitude_ft: 5000,
    upper_altitude_ft: 9000,
    severity: 'amber',
    reason_code: 'cloud_top_exceeds_ceiling',
    metric_id: 'cloud_coverage',
    method_id: 'nwp',
    ...overrides,
  };
}

function modelResult(
  model: string,
  overrides: Partial<ModelAdvisoryResult> = {},
): ModelAdvisoryResult {
  return {
    model,
    status: 'amber',
    detail: `${model} detail`,
    affected_points: 1,
    total_points: 3,
    affected_pct: 33.3,
    affected_nm: 15,
    total_nm: 60,
    ...overrides,
  };
}

function advisory(
  advisoryId: string,
  perModel: ModelAdvisoryResult[],
  overrides: Partial<RouteAdvisoryResult> = {},
): RouteAdvisoryResult {
  return {
    advisory_id: advisoryId,
    aggregate_status: 'amber',
    aggregate_detail: 'aggregate detail',
    per_model: perModel,
    parameters_used: {},
    ...overrides,
  };
}

function manifest(
  advisories: RouteAdvisoryResult[],
  models: string[] = ['gfs', 'ecmwf'],
): RouteAdvisoriesManifest {
  return {
    advisories,
    catalog: [],
    route_name: 'EGTF EGLF',
    cruise_altitude_ft: 8000,
    flight_ceiling_ft: 18000,
    total_distance_nm: 60,
    models,
    aggregation: 'majority',
    airport_conditions: null,
  };
}

export function routeData(distances: number[] = [0, 30, 60]): VizRouteData {
  return {
    points: distances.map((distanceNm, pointIndex) => makeVizPoint({
      pointIndex,
      distanceNm,
      lat: 50 + pointIndex,
      lon: -1 + pointIndex,
    })),
    cruiseAltitudeFt: 8000,
    ceilingAltitudeFt: 18000,
    flightCeilingFt: 23000,
    totalDistanceNm: distances.length > 0 ? distances[distances.length - 1] : 0,
    waypointMarkers: [],
    departureTime: '2026-07-10T10:00:00Z',
    flightDurationHours: 2,
    terrainProfile: null,
    currentConditions: null,
    fronts: null,
    nightIntervals: [],
    sunSide: null,
  };
}

export function activeFocus(
  model = 'gfs',
  advisoryId = 'cloud_top',
): ActiveAdvisoryFocus {
  return {
    advisoryId,
    model,
    highlightSurfaces: ['cross-section', 'route-graph', 'route-map'],
    emphasizeLayers: ['square-nwp-cloud-bands', 'terrain', 'cruise-altitude'],
  };
}

export function manifestWithTwoModels(): RouteAdvisoriesManifest {
  return manifest([advisory('cloud_top', [
    modelResult('gfs', {
      data_state: 'complete',
      primary_method_id: 'nwp',
      evidence_regions: [evidenceRegion(0, 0)],
    }),
    modelResult('ecmwf', {
      data_state: 'complete',
      primary_method_id: 'nwp',
      evidence_regions: [evidenceRegion(1, 1)],
    }),
  ], { representative_model: 'ecmwf' })]);
}

export function manifestWithDisjointGfsAndEcmwfRegions(): RouteAdvisoriesManifest {
  return manifest([advisory('cloud_top', [
    modelResult('gfs', {
      data_state: 'complete',
      evidence_regions: [evidenceRegion(0, 0)],
    }),
    modelResult('ecmwf', {
      data_state: 'complete',
      evidence_regions: [evidenceRegion(2, 2)],
    }),
  ], { representative_model: 'gfs' })]);
}

export function manifestWithOneValidAndOneInvalidRegion(): RouteAdvisoriesManifest {
  return manifest([advisory('cloud_top', [modelResult('gfs', {
    data_state: 'complete',
    evidence_regions: [evidenceRegion(0, 0), evidenceRegion(99, 99)],
  })], { representative_model: 'gfs' })], ['gfs']);
}

export function legacyManifestWithoutEvidenceMetadata(): RouteAdvisoriesManifest {
  return manifest([advisory('cloud_top', [modelResult('gfs')], {
    representative_model: 'gfs',
  })], ['gfs']);
}

export function refreshedManifest(): RouteAdvisoriesManifest {
  return manifestWithDisjointGfsAndEcmwfRegions();
}

export function manifestWithoutFocusedModel(): RouteAdvisoriesManifest {
  return manifest([advisory('cloud_top', [modelResult('ecmwf', {
    data_state: 'complete',
    evidence_regions: [evidenceRegion(2, 2)],
  })], { representative_model: 'ecmwf' })], ['ecmwf']);
}

export function focusRegion(
  overrides: Partial<ResolvedFocusRegion> = {},
): ResolvedFocusRegion {
  return {
    model: 'gfs',
    startPointIndex: 0,
    endPointIndex: 0,
    startNm: 0,
    endNm: 15,
    lowerAltitudeFt: 5000,
    upperAltitudeFt: 9000,
    severity: 'amber',
    reasonCode: 'cloud_top_exceeds_ceiling',
    metricId: 'cloud_coverage',
    methodId: 'nwp',
    mapPath: [],
    ...overrides,
  };
}

export function modelAgreement(metricId = 'unsupported_metric'): RouteAdvisoryResult {
  return advisory('model_agreement', [modelResult('all', {
    data_state: 'complete',
    primary_method_id: 'model_divergence',
    evidence_regions: [evidenceRegion(0, 0, {
      reason_code: 'poor_model_agreement',
      metric_id: metricId,
      method_id: 'model_divergence',
    })],
  })], { representative_model: 'all' });
}

export function ddNwpCloudAgreement(): RouteAdvisoryResult {
  return advisory('dd_nwp_agreement', [modelResult('gfs', {
    data_state: 'complete',
    primary_method_id: 'dd_vs_nwp',
    evidence_regions: [
      evidenceRegion(0, 0, {
        reason_code: 'dd_cloud_disagreement',
        method_id: 'dewpoint_depression',
      }),
      evidenceRegion(0, 0, {
        reason_code: 'nwp_cloud_disagreement',
        method_id: 'nwp',
      }),
    ],
  })], { representative_model: 'gfs' });
}

export function frontsAdvisory(): RouteAdvisoryResult {
  return advisory('fronts', [modelResult('gfs', {
    data_state: 'complete',
    primary_method_id: 'hewson',
    evidence_regions: [],
  })], { representative_model: 'gfs' });
}

export function airportAdvisory(model = 'gfs'): RouteAdvisoryResult {
  return advisory('airport_wind', [modelResult(model, {
    data_state: 'complete',
    primary_method_id: 'runway_components',
    evidence_regions: [],
  })], { representative_model: model });
}

export function actionContext(
  overrides: Partial<AdvisoryActionContext> = {},
): AdvisoryActionContext {
  return {
    selectedModel: 'gfs',
    availableModels: ['gfs', 'ecmwf'],
    layout: 'cross-section',
    compareLayer: 'freezing-level',
    hasFronts: true,
    supportedAirportProfileModels: ['ecmwf', 'gfs', 'icon'],
    ...overrides,
  };
}
