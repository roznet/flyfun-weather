/** Tests for the shared forecast-map color/format helpers — the pilot-facing
 *  color thresholds and model-agreement/alternate aggregation that drive both
 *  the marker layer and the airport summary card. */

import { describe, it, expect } from 'vitest';
import {
  getForecastColor, aggAltRequired, getAgreementForMetric, CAT_COLORS,
} from '../../ts/visualization/weather-map-format';
import type {
  ForecastAirport, ModelForecast, ConsensusForecast,
} from '../../ts/adapters/maps-adapter';

function makeModelForecast(overrides: Partial<ModelForecast> = {}): ModelForecast {
  return {
    ceiling_ft: null,
    visibility_m: null,
    wind_speed_kt: null,
    wind_dir_deg: null,
    wind_gust_kt: null,
    crosswind_kt: null,
    headwind_kt: null,
    best_runway_id: null,
    gust_crosswind_kt: null,
    gust_headwind_kt: null,
    cloud_cover_pct: null,
    cape_jkg: null,
    convective_risk: 'none',
    temperature_c: null,
    flight_category: 'VFR',
    ...overrides,
  };
}

function makeAirport(models: Record<string, Partial<ModelForecast>>): ForecastAirport {
  const built: Record<string, ModelForecast> = {};
  for (const [k, v] of Object.entries(models)) built[k] = makeModelForecast(v);
  return {
    icao: 'EGLF', lat: 51.276, lon: -0.776,
    models: built,
    consensus: { flight_category: 'VFR', agreement: {} },
  };
}

describe('getForecastColor', () => {
  it('colors flight category from the shared CAT_COLORS scale (per model)', () => {
    const apt = makeAirport({ gfs: { flight_category: 'IFR' } });
    expect(getForecastColor(apt, 'flight_category', 'gfs')).toBe(CAT_COLORS.IFR);
  });

  it('applies ceiling thresholds (IFR band 500–1000 ft → red)', () => {
    const apt = makeAirport({ gfs: { ceiling_ft: 800 } });
    expect(getForecastColor(apt, 'ceiling_ft', 'gfs')).toBe('#ef4444');
  });

  it('applies wind-speed thresholds (25–35 kt → red)', () => {
    const apt = makeAirport({ gfs: { wind_speed_kt: 30 } });
    expect(getForecastColor(apt, 'wind_speed_kt', 'gfs')).toBe('#ef4444');
  });

  it('returns the muted color for a missing model', () => {
    const apt = makeAirport({ gfs: {} });
    expect(getForecastColor(apt, 'ceiling_ft', 'icon')).toBe('#888');
  });

  it('consensus modes read the server-baked block, not a client recompute (#419)', () => {
    // Both blocks are baked server-side; the client just picks by mode. The
    // per-model categories are deliberately inconsistent with the baked blocks
    // to prove the colour comes from the baked value, not a recomputation.
    const apt = makeAirport({
      gfs: { flight_category: 'VFR' },
      icon: { flight_category: 'VFR' },
      ecmwf: { flight_category: 'VFR' },
    });
    apt.consensus = { flight_category: 'IFR', agreement: {} };
    apt.consensus_majority = { flight_category: 'MVFR', agreement: {} };
    expect(getForecastColor(apt, 'flight_category', 'worst')).toBe(CAT_COLORS.IFR);
    expect(getForecastColor(apt, 'flight_category', 'majority')).toBe(CAT_COLORS.MVFR);
  });

  it('majority falls back to the worst block when no majority block is baked', () => {
    const apt = makeAirport({ gfs: { flight_category: 'VFR' } });
    apt.consensus = { flight_category: 'IFR', agreement: {} };
    // No consensus_majority (older cached payload) → falls back to consensus.
    expect(getForecastColor(apt, 'flight_category', 'majority')).toBe(CAT_COLORS.IFR);
  });
});

describe('aggAltRequired', () => {
  it('worst mode flags a requirement if any model requires it', () => {
    const apt = makeAirport({
      gfs: { alt_required: { faa: false, easa: false } },
      ecmwf: { alt_required: { faa: true, easa: false } },
    });
    expect(aggAltRequired(apt, 'worst')).toEqual({ faa: true, easa: false });
  });

  it('majority mode uses the modal value with worst tiebreak', () => {
    const apt = makeAirport({
      gfs: { alt_required: { faa: true, easa: false } },
      icon: { alt_required: { faa: true, easa: false } },
      ecmwf: { alt_required: { faa: false, easa: true } },
    });
    // FAA: two "yes" vs one "no" → yes. EASA: two "no" vs one "yes" → no.
    expect(aggAltRequired(apt, 'majority')).toEqual({ faa: true, easa: false });
  });

  it('returns undefined when no model carries alternate flags', () => {
    const apt = makeAirport({ gfs: {}, ecmwf: {} });
    expect(aggAltRequired(apt, 'worst')).toBeUndefined();
  });
});

describe('getAgreementForMetric', () => {
  const consensus: ConsensusForecast = {
    flight_category: 'IFR',
    agreement: {
      flight_category: 'divergent',
      wind_speed_kt: 'mixed',
      ceiling_ft: 'consistent',
    },
  };

  it('reads the direct agreement key', () => {
    expect(getAgreementForMetric(consensus, 'ceiling_ft')).toBe('consistent');
    expect(getAgreementForMetric(consensus, 'flight_category')).toBe('divergent');
  });

  it('maps crosswind/headwind to the wind-speed agreement proxy', () => {
    expect(getAgreementForMetric(consensus, 'crosswind_kt')).toBe('mixed');
    expect(getAgreementForMetric(consensus, 'headwind_kt')).toBe('mixed');
  });

  it('returns null when the mapped key is absent', () => {
    // convective_risk proxies to cape_jkg, which isn't in this consensus.
    expect(getAgreementForMetric(consensus, 'convective_risk')).toBeNull();
  });
});
