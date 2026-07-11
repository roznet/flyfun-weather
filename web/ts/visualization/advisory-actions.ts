import type { RouteAdvisoryResult } from '../types/advisories';
import { ADVISORY_TO_PRESET } from './cross-section/advisory-presets';
import type { VizLayout } from './types';

export type AdvisoryAction =
  | { kind: 'preset-focus' }
  | { kind: 'compare-models' }
  | { kind: 'method-context' }
  | { kind: 'airport-profile' }
  | { kind: 'fronts-map' };

const SPECIAL_ACTIONS: Readonly<Record<string, AdvisoryAction>> = {
  model_agreement: { kind: 'compare-models' },
  dd_nwp_agreement: { kind: 'method-context' },
  airport_wind: { kind: 'airport-profile' },
  flight_category: { kind: 'airport-profile' },
  fronts: { kind: 'fronts-map' },
};

function hasOwn(record: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

export function actionForAdvisory(advisoryId: string): AdvisoryAction | null {
  if (hasOwn(ADVISORY_TO_PRESET, advisoryId)) return { kind: 'preset-focus' };
  return hasOwn(SPECIAL_ACTIONS, advisoryId) ? SPECIAL_ACTIONS[advisoryId] : null;
}

export const COMPARE_LAYER_BY_METRIC: Readonly<Record<string, string>> = Object.freeze(
  Object.assign(Object.create(null) as Record<string, string>, {
    freezing_level_ft: 'freezing-level',
    cloud_coverage: 'square-nwp-cloud-bands',
    cloud_cover_pct: 'square-nwp-cloud-bands',
    icing_risk: 'icing-bands',
    icing_ogimet_nwp_risk: 'icing-ogimet-nwp-bands',
    sfip_risk: 'sfip-bands',
    cat_risk: 'cat-bands',
    convective_risk: 'thermo-convective-bg',
    nwp_convective_risk: 'nwp-convective-bg',
  }),
);

export interface AdvisoryActionContext {
  selectedModel: string;
  availableModels: string[];
  layout: VizLayout;
  compareLayer: string;
  hasFronts: boolean;
  supportedAirportProfileModels: string[];
}

export interface AdvisoryActionPlan {
  kind: AdvisoryAction['kind'];
  model: string | null;
  layout: VizLayout | null;
  enableModels: string[];
  compareLayer: string | null;
  layerOverrides: Record<string, boolean>;
  airportProfileModel: string | null;
  noteKey: string | null;
  noteParams: Record<string, string>;
  disabledReasonKey: string | null;
}

function emptyPlan(
  kind: AdvisoryAction['kind'],
  model: string | null,
): AdvisoryActionPlan {
  return {
    kind,
    model,
    layout: null,
    enableModels: [],
    compareLayer: null,
    layerOverrides: {},
    airportProfileModel: null,
    noteKey: null,
    noteParams: {},
    disabledReasonKey: null,
  };
}

export function planAdvisoryAction(
  advisory: RouteAdvisoryResult,
  context: AdvisoryActionContext,
  requestedModel?: string,
): AdvisoryActionPlan {
  const action = actionForAdvisory(advisory.advisory_id);
  if (!action) throw new Error(`No action registered for ${advisory.advisory_id}`);
  const kind = action.kind;
  const attributedModel = requestedModel
    ?? advisory.representative_model
    ?? context.selectedModel;
  const attributedResult = advisory.per_model.find(
    result => result.model === attributedModel,
  ) ?? null;
  const plan = emptyPlan(kind, attributedModel);

  if (kind === 'compare-models') {
    const representativeResult = advisory.per_model.find(
      result => result.model === advisory.representative_model,
    ) ?? advisory.per_model[0];
    const compareLayer = representativeResult?.evidence_regions
      ?.map(region => region.metric_id)
      .find((metricId): metricId is string => (
        typeof metricId === 'string'
        && hasOwn(COMPARE_LAYER_BY_METRIC, metricId)
      ));

    return {
      ...emptyPlan(kind, null),
      layout: 'compare',
      enableModels: [...context.availableModels],
      compareLayer: compareLayer ? COMPARE_LAYER_BY_METRIC[compareLayer] : null,
      noteKey: compareLayer ? null : 'advisories.noDirectCompareLayer',
    };
  }

  if (kind === 'method-context') {
    const reasons = new Set(
      attributedResult?.evidence_regions?.map(region => region.reason_code) ?? [],
    );
    const hasCloudDisagreement = reasons.has('dd_cloud_disagreement')
      || reasons.has('nwp_cloud_disagreement');

    return {
      ...plan,
      layout: 'cross-section',
      layerOverrides: {
        ...(hasCloudDisagreement
          ? {
              'square-cloud-bands': true,
              'square-nwp-cloud-bands': true,
            }
          : {}),
        ...(reasons.has('freezing_level_disagreement')
          ? { 'freezing-level': true }
          : {}),
      },
    };
  }

  if (kind === 'airport-profile') {
    const exactModelSupported = context.supportedAirportProfileModels.includes(
      attributedModel,
    ) && context.availableModels.includes(attributedModel);
    const airportProfileModel = exactModelSupported
      ? attributedModel
      : context.supportedAirportProfileModels.find(model => (
          context.availableModels.includes(model)
        )) ?? null;
    const isFallback = airportProfileModel !== null
      && airportProfileModel !== attributedModel;

    return {
      ...plan,
      airportProfileModel,
      noteKey: isFallback ? 'advisories.airportProfileFallback' : null,
      noteParams: isFallback
        ? {
            advisoryModel: attributedModel,
            profileModel: airportProfileModel,
          }
        : {},
      disabledReasonKey: airportProfileModel === null
        ? 'advisories.airportProfileUnavailable'
        : null,
    };
  }

  if (kind === 'fronts-map') {
    return {
      ...plan,
      layout: 'map',
      disabledReasonKey: context.hasFronts
        ? null
        : 'advisories.frontsUnavailable',
    };
  }

  return plan;
}
