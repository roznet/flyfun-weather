import { describe, expect, it } from 'vitest';
import { MotionState } from '../../ts/observed-motion/state';

function fixture(revision: number, status: 'available' | 'unavailable' = 'available') {
  const source = {
    source_id: 'opera-dbzh', status: 'available', reason_codes: [], attribution: 'OPERA', gaps: [],
    coverage: {}, geolocation: { status: 'validated', reason_codes: [] },
    frames: [{
      frame_id: 'frame-1', content_id: 'content-1', product_id: 'opera-dbzh', decoder_version: 'odim-v1', grid_id: 'opera-grid',
      valid_at: '2026-09-05T12:00:00Z', received_at: '2026-09-05T12:00:00Z', reference_at: '2026-09-05T12:00:00Z',
      acquisition_window: { start_at: '2026-09-05T11:55:00Z', end_at: '2026-09-05T12:00:00Z' },
    }],
  };
  const geometry = { status: 'available', reason_codes: [], provenance: 'grid_contour', simplification_tolerance_m: 0,
    geometry: { type: 'MultiPolygon', coordinates: [[[[0, 50], [0.1, 50], [0.1, 50.1], [0, 50.1], [0, 50]]]] } };
  const feature = {
    feature_id: 'feature-1', source_id: 'opera-dbzh', family: 'radar_echo',
    definition: { quantity: 'reflectivity', operator: 'gte', threshold: 5, unit: 'dBZ' },
    reference_at: '2026-09-05T12:00:00Z', reference_frame_id: 'frame-1', frame_ids: ['frame-1'],
    display_geometry: geometry, trail: [], observations: [],
    lightning_evidence: { status: 'unavailable', reason_codes: ['missing_source'], reported_detection_count: null, emitted_marker_count: 0, evaluation_complete: false, evaluated_window: null },
    coverage: {}, geolocation: {}, motion: { status: 'accepted', reason_codes: [], ground_speed_kt: 10, bearing_deg_true: 90,
      velocity_reference_point: [0.05, 50.05], velocity_method: 'inverse_aeqd_geodesic_1s', pair_diagnostics: [], fit_rms_residual_cells: 0.1 },
    projection_end_at: '2026-09-05T12:15:00Z',
    projections: [{ at: '2026-09-05T12:05:00Z', status: 'available', reason_codes: [], display_geometry: geometry }],
    route_rows: [], planned_overlap: { status: 'unavailable', reason_codes: ['invalid_planned_timing'], evaluated_interval: null, intervals: [], complete: false }, reason_codes: [],
  };
  return {
    schema_version: 1, status, reason_codes: status === 'available' ? [] : ['compute_failed'], revision,
    run_id: status === 'available' ? `run-${revision}` : null,
    route_geometry_id: 'route-geometry-1', planned_timing_id: null,
    computed_at: '2026-09-05T12:00:01Z', cutoff_at: '2026-09-05T12:00:00Z', expires_at: status === 'available' ? '2026-09-05T12:15:00Z' : null,
    method_version: 'masked_contour_translation_v1', policy_version: 'observed_motion_policy_v1',
    analysis_domain: status === 'available' ? { center: [0, 50], crs: '+proj=aeqd +datum=WGS84', cell_size_m: 2000, width_cells: 1, height_cells: 1, origin_x_m: 0, origin_y_m: 0, bounds: [-1, 49, 1, 51], reason_codes: [] } : null,
    sources: status === 'available' ? [source] : [], features: status === 'available' ? [feature] : [], associations: [], lightning: [], projection_times: status === 'available' ? ['2026-09-05T12:05:00Z'] : [],
    completeness: [{ category: 'features', status: status === 'available' ? 'complete' : 'not_evaluated', reason_codes: status === 'available' ? [] : ['not_evaluated'], considered_count: status === 'available' ? 0 : null, emitted_count: 0, omitted_count: status === 'available' ? 0 : null }],
  };
}

describe('MotionState', () => {
  it('does not resurrect ready data after a failed newer run', () => {
    const state = new MotionState();
    state.accept(fixture(8));
    state.accept(fixture(9, 'unavailable'));
    state.accept(fixture(8));
    expect(state.current?.status).toBe('unavailable');
    expect(state.current?.revision).toBe(9);
  });

  it('does not treat missing legacy data or a failed request as a refreshed result', () => {
    const state = new MotionState();
    state.accept(fixture(8));
    state.accept(undefined);
    state.noteRefreshFailure('network');
    expect(state.current?.revision).toBe(8);
    expect(state.presentationReasons).toContain('refresh_failed');
    expect(state.presentationReasons).toContain('stored_analysis');
  });

  it('does not let a capability header authorize retained data when the same response lacks motion', () => {
    const state = new MotionState();
    state.enterMotionMode();
    state.accept(fixture(8));
    state.selectTime('2026-09-05T12:05:00Z');
    state.accept(undefined);
    state.acceptCapability(true, state.beginRequest(state.requestGeneration));
    state.updateClock(new Date('2026-09-05T12:01:00Z'));
    expect(state.current?.revision).toBe(8);
    expect(state.canPresentActivePrediction).toBe(false);
    expect(state.presentationReasons).toContain('missing_legacy_data');
  });

  it('reports invalid current ordering without replacing historical valid evidence', () => {
    const state = new MotionState();
    state.enterMotionMode();
    state.accept(fixture(8));
    state.accept({ ...fixture(9), revision: 'nine' });
    state.acceptCapability(true, state.beginRequest(state.requestGeneration));
    expect(state.current?.revision).toBe(8);
    expect(state.canPresentActivePrediction).toBe(false);
    expect(state.presentationReasons).toContain('invalid_motion');
  });

  it('rejects late results from a previous flight or pack generation', () => {
    const state = new MotionState();
    const oldGeneration = state.enterContext('flight-1', 'pack-1');
    const newGeneration = state.enterContext('flight-1', 'pack-2');
    expect(newGeneration).toBeGreaterThan(oldGeneration);
    expect(state.accept(fixture(9), oldGeneration)).toBe(false);
    expect(state.current).toBeNull();
  });

  it('starts capability unknown on entry and ignores older capability observations', () => {
    const state = new MotionState();
    const generation = state.enterContext('flight-1', 'pack-1');
    const older = state.beginRequest(generation);
    const newer = state.beginRequest(generation);
    state.acceptCapability(false, newer);
    state.acceptCapability(true, older);
    expect(state.capability).toBe('disabled');
    state.enterMotionMode();
    expect(state.capability).toBe('unknown');
    expect(state.selectedTime).toBe('observed');
  });

  it('keeps an expired focused time selected but removes active prediction authority', () => {
    const state = new MotionState();
    state.accept(fixture(8));
    state.acceptCapability(true, state.beginRequest(state.requestGeneration));
    state.selectTime('2026-09-05T12:05:00Z');
    state.updateClock(new Date('2026-09-05T12:06:00Z'));
    expect(state.selectedTime).toBe('2026-09-05T12:05:00Z');
    expect(state.canPresentActivePrediction).toBe(false);
    expect(state.presentationReasons).toContain('expired');
  });

  it('turns same-revision conflicting JSON into a visible contract error', () => {
    const state = new MotionState();
    state.accept({ ...fixture(8), future_extension: { value: 1 } });
    state.accept({ ...fixture(8), future_extension: { value: true } });
    expect(state.current).toMatchObject({ status: 'unavailable', revision: 8, unavailableReason: 'same_revision_conflict' });
  });

  it('clears a transport failure after a successful idempotent snapshot reload', () => {
    const state = new MotionState();
    const raw = fixture(8);
    state.accept(raw);
    state.noteRefreshFailure('network');
    state.accept(structuredClone(raw));
    expect(state.presentationReasons).not.toContain('refresh_failed');
  });

  it('cannot select or authorize a projection from an unavailable envelope', () => {
    const state = new MotionState();
    state.enterMotionMode();
    const unavailableWithRetainedContent = { ...fixture(8), status: 'unavailable', reason_codes: ['compute_failed'], expires_at: null };
    state.accept(unavailableWithRetainedContent);
    state.acceptCapability(true, state.beginRequest(state.requestGeneration));
    state.selectTime('2026-09-05T12:05:00Z');
    state.updateClock(new Date('2026-09-05T12:01:00Z'));
    expect(state.selectedTime).toBe('observed');
    expect(state.canPresentActivePrediction).toBe(false);
  });
});
