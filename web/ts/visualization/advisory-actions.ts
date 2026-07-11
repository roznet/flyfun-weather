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

export declare function planAdvisoryAction(
  advisory: RouteAdvisoryResult,
  context: AdvisoryActionContext,
  requestedModel?: string,
): AdvisoryActionPlan;
