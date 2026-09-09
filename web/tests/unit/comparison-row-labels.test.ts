/** Model-comparison row labels: units visible, no duplicate concepts.
 *
 *  A user reported two rows both reading "Freezing Level" in the Model
 *  Comparison table — one in metres, one in feet — with no unit anywhere on
 *  the row. Two separate defects made that possible:
 *
 *    1. the catalog holds `name` and `unit` in different fields and the table
 *       rendered `name` alone, so the unit was only visible behind the (i);
 *    2. the model-native freezing level was emitted in metres, its native
 *       unit, while every other altitude in the app is in feet.
 *
 *  These pin both, plus the tier config that keeps the cross-check row out of
 *  the default view.
 */
import { describe, it, expect } from 'vitest';
import catalog from '../../ts/data/metrics-catalog.json';
import display from '../../ts/data/metrics-display.json';
import { metricLabelWithUnit, variableToMetricId } from '../../ts/helpers/metrics-helper';

/** The comparison variables `_analyze_models` emits today.
 *  Mirrors the `comp` accumulator in src/weatherbrief/tasks/analyze.py. */
const EMITTED_VARIABLES = [
  'temperature_c', 'wind_speed_kt', 'wind_direction_deg',
  'cloud_cover_pct', 'precipitation_mm',
  'freezing_level_ft', 'nwp_freezing_level_ft',
  'cape_surface_jkg', 'nwp_cape_jkg', 'lcl_altitude_ft',
  'k_index', 'total_totals', 'precipitable_water_mm',
  'lifted_index', 'bulk_shear_0_6km_kt', 'max_omega_pa_s',
  'snowfall_cm', 'rain_mm', 'pressure_msl_hpa',
];

const CATALOG = catalog as Record<string, { name: string; unit: string }>;
const COMPARISON_METRICS = (display as {
  sections: Record<string, { metrics: Array<{ id: string; tier: string }> }>;
}).sections.comparison.metrics;

describe('model comparison row labels', () => {
  it('never reports an altitude in metres for a current briefing', () => {
    const metric = EMITTED_VARIABLES.map(variableToMetricId).filter((id): id is string => !!id);
    const inMetres = metric.filter((id) => CATALOG[id]?.unit === 'm');
    expect(inMetres).toEqual([]);
  });

  it('labels every row with its unit', () => {
    for (const variable of EMITTED_VARIABLES) {
      const id = variableToMetricId(variable);
      if (!id || !CATALOG[id]) continue; // falls back to a unit-bearing i18n label
      const unit = CATALOG[id].unit;
      if (!unit) continue; // dimensionless index
      expect(metricLabelWithUnit(id), `${variable} label`).toContain(`(${unit})`);
    }
  });

  it('gives the two freezing levels distinct labels, both in feet', () => {
    const derived = metricLabelWithUnit('freezing_level_ft');
    const native = metricLabelWithUnit('nwp_freezing_level_ft');
    expect(derived).toBe('Freezing Level (ft)');
    expect(native).toBe('Freezing Level, model native (ft)');
    expect(derived).not.toBe(native);
  });

  it('leaves no two rows sharing a label', () => {
    const labels = EMITTED_VARIABLES
      .map(variableToMetricId)
      .filter((id): id is string => !!id && !!CATALOG[id])
      .map((id) => metricLabelWithUnit(id)!);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('tiers every emitted metric, so none bypasses the tier filter', () => {
    // isMetricVisible returns true for metrics absent from the display config.
    // That fall-through is how the metres row reached the default view.
    const tiered = new Set(COMPARISON_METRICS.map((m) => m.id));
    const untiered = EMITTED_VARIABLES
      .map(variableToMetricId)
      .filter((id): id is string => !!id)
      .filter((id) => !tiered.has(id));
    expect(untiered).toEqual([]);
  });

  it('keeps the model-native cross-check row out of the default view', () => {
    const row = COMPARISON_METRICS.find((m) => m.id === 'nwp_freezing_level_ft');
    expect(row?.tier).toBe('advanced');
    expect(COMPARISON_METRICS.find((m) => m.id === 'freezing_level_ft')?.tier).toBe('key');
  });

  it('still resolves the legacy metres row on briefings saved before the switch', () => {
    expect(metricLabelWithUnit('freezing_level_m')).toBe('Freezing Level, model native (m)');
  });
});
