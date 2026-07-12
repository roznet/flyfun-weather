import { describe, expect, it } from 'vitest';

import {
  planAdvisoryAction,
  type AdvisoryActionPlan,
} from '../../ts/visualization/advisory-actions';
import {
  actionContext,
  airportAdvisory,
  ddNwpCloudAgreement,
  frontsAdvisory,
  modelAgreement,
} from './fixtures/advisory-focus';

type EvidenceRegion = NonNullable<
  ReturnType<typeof ddNwpCloudAgreement>['per_model'][number]['evidence_regions']
>[number];

function expectPlan(actual: AdvisoryActionPlan, expected: AdvisoryActionPlan): void {
  expect(actual).toEqual(expected);
}

function ddResult(
  model: string,
  reasons: EvidenceRegion['reason_code'][],
): ReturnType<typeof ddNwpCloudAgreement>['per_model'][number] {
  const template = ddNwpCloudAgreement().per_model[0];
  const region = template.evidence_regions![0];
  return {
    ...template,
    model,
    evidence_regions: reasons.map((reason_code) => ({
      ...region,
      reason_code,
      metric_id: reason_code === 'freezing_level_disagreement'
        ? 'freezing_level_ft'
        : 'cloud_coverage',
    })),
  };
}

function ddAgreementWithDistinctResults(
  representativeModel: string | null = 'gfs',
): ReturnType<typeof ddNwpCloudAgreement> {
  return {
    ...ddNwpCloudAgreement(),
    representative_model: representativeModel,
    per_model: [
      ddResult('gfs', ['dd_cloud_disagreement', 'nwp_cloud_disagreement']),
      ddResult('ecmwf', ['freezing_level_disagreement']),
    ],
  };
}

function agreementWithPerModelMetrics(
  representativeMetric: string,
  otherMetric: string,
): ReturnType<typeof modelAgreement> {
  const base = modelAgreement();
  const template = base.per_model[0];
  const region = template.evidence_regions![0];
  const result = (model: string, metric_id: string) => ({
    ...template,
    model,
    evidence_regions: [{ ...region, metric_id }],
  });
  return {
    ...base,
    representative_model: 'ecmwf',
    per_model: [
      result('gfs', otherMetric),
      result('ecmwf', representativeMetric),
    ],
  };
}

describe('planAdvisoryAction', () => {
  it('returns the exact empty-plan fields for a preset action', () => {
    const advisory = {
      ...ddNwpCloudAgreement(),
      advisory_id: 'cloud_top',
    };

    expectPlan(planAdvisoryAction(advisory, actionContext()), {
      kind: 'preset-focus',
      model: 'gfs',
      layout: null,
      enableModels: [],
      compareLayer: null,
      layerOverrides: {},
      airportProfileModel: null,
      noteKey: null,
      noteParams: {},
      disabledReasonKey: null,
    });
  });

  it('uses requested model evidence before representative and selected evidence', () => {
    const advisory = ddAgreementWithDistinctResults('gfs');

    expectPlan(planAdvisoryAction(
      advisory,
      actionContext({ selectedModel: 'gfs' }),
      'ecmwf',
    ), {
      kind: 'method-context',
      model: 'ecmwf',
      layout: 'cross-section',
      enableModels: [],
      compareLayer: null,
      layerOverrides: { 'freezing-level': true },
      airportProfileModel: null,
      noteKey: null,
      noteParams: {},
      disabledReasonKey: null,
    });
  });

  it('rejects a requested model that is not available and uses the valid representative', () => {
    const advisory = ddAgreementWithDistinctResults('gfs');

    const plan = planAdvisoryAction(
      advisory,
      actionContext({ selectedModel: 'gfs', availableModels: ['gfs'] }),
      'ecmwf',
    );

    expect(plan.model).toBe('gfs');
    expect(plan.layerOverrides).toEqual({
      'square-cloud-bands': true,
      'square-nwp-cloud-bands': true,
    });
  });

  it('uses representative model evidence before selected-model evidence', () => {
    const advisory = ddAgreementWithDistinctResults('gfs');

    expectPlan(planAdvisoryAction(
      advisory,
      actionContext({ selectedModel: 'ecmwf' }),
    ), {
      kind: 'method-context',
      model: 'gfs',
      layout: 'cross-section',
      enableModels: [],
      compareLayer: null,
      layerOverrides: {
        'square-cloud-bands': true,
        'square-nwp-cloud-bands': true,
      },
      airportProfileModel: null,
      noteKey: null,
      noteParams: {},
      disabledReasonKey: null,
    });
  });

  it('uses selected-model evidence only when requested and representative attribution are absent', () => {
    const advisory = ddAgreementWithDistinctResults(null);

    expectPlan(planAdvisoryAction(
      advisory,
      actionContext({ selectedModel: 'ecmwf' }),
    ), {
      kind: 'method-context',
      model: 'ecmwf',
      layout: 'cross-section',
      enableModels: [],
      compareLayer: null,
      layerOverrides: { 'freezing-level': true },
      airportProfileModel: null,
      noteKey: null,
      noteParams: {},
      disabledReasonKey: null,
    });
  });

  it('rejects a representative outside the briefing and uses the valid selected model', () => {
    const advisory = ddAgreementWithDistinctResults('ecmwf');

    const plan = planAdvisoryAction(
      advisory,
      actionContext({ selectedModel: 'gfs', availableModels: ['gfs'] }),
    );

    expect(plan.model).toBe('gfs');
    expect(plan.layerOverrides).toEqual({
      'square-cloud-bands': true,
      'square-nwp-cloud-bands': true,
    });
  });

  it('uses the first valid per-model result when requested, representative, and selected are invalid', () => {
    const advisory = ddAgreementWithDistinctResults(null);
    advisory.per_model = [advisory.per_model[1], advisory.per_model[0]];

    const plan = planAdvisoryAction(
      advisory,
      actionContext({ selectedModel: 'icon', availableModels: ['gfs', 'ecmwf'] }),
      'ukmo',
    );

    expect(plan.model).toBe('ecmwf');
    expect(plan.layerOverrides).toEqual({ 'freezing-level': true });
  });

  it('returns no model when the advisory and briefing model sets do not intersect', () => {
    const plan = planAdvisoryAction(
      ddAgreementWithDistinctResults(null),
      actionContext({ selectedModel: 'ukmo', availableModels: ['ukmo'] }),
    );

    expect(plan.model).toBeNull();
    expect(plan.layerOverrides).toEqual({});
  });

  it('returns the exact freezing-only method-context plan', () => {
    const advisory = {
      ...ddNwpCloudAgreement(),
      per_model: [ddResult('gfs', ['freezing_level_disagreement'])],
    };

    expectPlan(planAdvisoryAction(advisory, actionContext()), {
      kind: 'method-context',
      model: 'gfs',
      layout: 'cross-section',
      enableModels: [],
      compareLayer: null,
      layerOverrides: { 'freezing-level': true },
      airportProfileModel: null,
      noteKey: null,
      noteParams: {},
      disabledReasonKey: null,
    });
  });

  it('returns the exact mixed cloud-and-freezing method-context plan', () => {
    const advisory = {
      ...ddNwpCloudAgreement(),
      per_model: [ddResult('gfs', [
        'dd_cloud_disagreement',
        'nwp_cloud_disagreement',
        'freezing_level_disagreement',
      ])],
    };

    expectPlan(planAdvisoryAction(advisory, actionContext()), {
      kind: 'method-context',
      model: 'gfs',
      layout: 'cross-section',
      enableModels: [],
      compareLayer: null,
      layerOverrides: {
        'square-cloud-bands': true,
        'square-nwp-cloud-bands': true,
        'freezing-level': true,
      },
      airportProfileModel: null,
      noteKey: null,
      noteParams: {},
      disabledReasonKey: null,
    });
  });

  it('maps a supported metric from the representative model result', () => {
    const advisory = agreementWithPerModelMetrics(
      'freezing_level_ft',
      'unsupported_metric',
    );

    expectPlan(planAdvisoryAction(advisory, actionContext()), {
      kind: 'compare-models',
      model: null,
      layout: 'compare',
      enableModels: ['gfs', 'ecmwf'],
      compareLayer: 'freezing-level',
      layerOverrides: {},
      airportProfileModel: null,
      noteKey: null,
      noteParams: {},
      disabledReasonKey: null,
    });
  });

  it('does not invent a mapping from a non-representative model result', () => {
    const advisory = agreementWithPerModelMetrics(
      'unsupported_metric',
      'freezing_level_ft',
    );

    expectPlan(planAdvisoryAction(advisory, actionContext()), {
      kind: 'compare-models',
      model: null,
      layout: 'compare',
      enableModels: ['gfs', 'ecmwf'],
      compareLayer: null,
      layerOverrides: {},
      airportProfileModel: null,
      noteKey: 'advisories.noDirectCompareLayer',
      noteParams: {},
      disabledReasonKey: null,
    });
  });

  it('returns the exact absent-fronts plan', () => {
    expectPlan(planAdvisoryAction(
      frontsAdvisory(),
      actionContext({ hasFronts: false }),
    ), {
      kind: 'fronts-map',
      model: 'gfs',
      layout: 'map',
      enableModels: [],
      compareLayer: null,
      layerOverrides: {},
      airportProfileModel: null,
      noteKey: null,
      noteParams: {},
      disabledReasonKey: 'advisories.frontsUnavailable',
    });
  });

  it('disables fronts when every per-model result is unavailable', () => {
    const advisory = frontsAdvisory();
    for (const result of advisory.per_model) {
      result.status = 'unavailable';
      result.data_state = 'unavailable';
      result.evidence_regions = [];
    }

    expect(planAdvisoryAction(
      advisory,
      actionContext({ hasFronts: true }),
    ).disabledReasonKey).toBe('advisories.frontsUnavailable');
  });

  it('disables fronts when the per-model result set is empty', () => {
    const advisory = frontsAdvisory();
    advisory.per_model = [];

    expect(planAdvisoryAction(
      advisory,
      actionContext({ hasFronts: true }),
    ).disabledReasonKey).toBe('advisories.frontsUnavailable');
  });

  it('falls back when the advisory model is unsupported even if it is available', () => {
    expectPlan(planAdvisoryAction(
      airportAdvisory('meteofrance'),
      actionContext({ availableModels: ['meteofrance', 'ecmwf'] }),
    ), {
      kind: 'airport-profile',
      model: 'meteofrance',
      layout: null,
      enableModels: [],
      compareLayer: null,
      layerOverrides: {},
      airportProfileModel: 'ecmwf',
      noteKey: 'advisories.airportProfileFallback',
      noteParams: {
        advisoryModel: 'meteofrance',
        profileModel: 'ecmwf',
      },
      disabledReasonKey: null,
    });
  });

  it('preserves an unavailable advisory model for visible airport-profile fallback', () => {
    expectPlan(planAdvisoryAction(
      airportAdvisory('gfs'),
      actionContext({ availableModels: ['ecmwf'] }),
    ), {
      kind: 'airport-profile',
      model: 'gfs',
      layout: null,
      enableModels: [],
      compareLayer: null,
      layerOverrides: {},
      airportProfileModel: 'ecmwf',
      noteKey: 'advisories.airportProfileFallback',
      noteParams: {
        advisoryModel: 'gfs',
        profileModel: 'ecmwf',
      },
      disabledReasonKey: null,
    });
  });

  it('disables airport profiles when no supported profile model is available', () => {
    expectPlan(planAdvisoryAction(
      airportAdvisory('meteofrance'),
      actionContext({ availableModels: ['ukmo', 'meteofrance'] }),
    ), {
      kind: 'airport-profile',
      model: 'meteofrance',
      layout: null,
      enableModels: [],
      compareLayer: null,
      layerOverrides: {},
      airportProfileModel: null,
      noteKey: null,
      noteParams: {},
      disabledReasonKey: 'advisories.airportProfileUnavailable',
    });
  });

  it('uses the advisory airport model only when it is both supported and available', () => {
    expectPlan(planAdvisoryAction(
      airportAdvisory('gfs'),
      actionContext({ availableModels: ['gfs', 'ecmwf'] }),
    ), {
      kind: 'airport-profile',
      model: 'gfs',
      layout: null,
      enableModels: [],
      compareLayer: null,
      layerOverrides: {},
      airportProfileModel: 'gfs',
      noteKey: null,
      noteParams: {},
      disabledReasonKey: null,
    });
  });

  it('rejects an advisory without a registered action', () => {
    const advisory = {
      ...modelAgreement(),
      advisory_id: 'unregistered_advisory',
    };

    expect(() => planAdvisoryAction(advisory, actionContext()))
      .toThrow('No action registered for unregistered_advisory');
  });
});
