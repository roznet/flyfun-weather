import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseObservedMotion } from '../../ts/observed-motion/types';
import strictV1Fixture from '../fixtures/observed-motion-v1.json';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const motionFixture = {
  schema_version: 1,
  status: 'available',
  reason_codes: [],
  revision: 8,
  run_id: 'run-8',
  route_geometry_id: 'route-geometry-1',
  planned_timing_id: 'planned-timing-1',
  computed_at: '2026-09-05T11:59:00Z',
  cutoff_at: '2026-09-05T12:00:00Z',
  expires_at: '2026-09-05T12:15:00Z',
  method_version: 'masked_contour_translation_v1',
  policy_version: 'observed_motion_policy_v1',
  analysis_domain: {
    center: [-0.95, 50.05],
    crs: '+proj=aeqd +lat_0=50.05 +lon_0=-0.95 +datum=WGS84 +units=m +no_defs',
    cell_size_m: 2000,
    width_cells: 100,
    height_cells: 100,
    origin_x_m: -100000,
    origin_y_m: -100000,
    bounds: [-2, 49, 1, 51],
    reason_codes: [],
  },
  sources: [
    {
      source_id: 'opera-dbzh', status: 'available', reason_codes: [], attribution: 'OPERA', gaps: [],
      coverage: { status: 'available', reason_codes: [], scope: 'analysis_domain', known_cells: 100, total_cells: 100, known_fraction: 1 },
      geolocation: { status: 'validated', reason_codes: [], evidence_id: 'radar-geo', method_version: 'registration-v1', applicability_id: 'opera-grid' },
      frames: [{
        frame_id: 'radar-frame-1', content_id: 'radar-content-1', product_id: 'opera-dbzh', decoder_version: 'odim-v1', grid_id: 'opera-grid',
        valid_at: '2026-09-05T12:00:00Z', received_at: '2026-09-05T12:00:00Z', reference_at: '2026-09-05T12:00:00Z',
        acquisition_window: { start_at: '2026-09-05T11:55:00Z', end_at: '2026-09-05T12:00:00Z' },
      }],
    },
    {
      source_id: 'eumetsat-ctth', status: 'available', reason_codes: [], attribution: 'EUMETSAT', gaps: [],
      coverage: { status: 'available', reason_codes: [], scope: 'analysis_domain', known_cells: 100, total_cells: 100, known_fraction: 1 },
      geolocation: { status: 'validated', reason_codes: [], evidence_id: 'cloud-geo', method_version: 'registration-v1', applicability_id: 'fci-grid' },
      frames: [{
        frame_id: 'cloud-frame-1', content_id: 'cloud-content-1', product_id: 'mtg-fci-ctth', decoder_version: 'fci-v1', grid_id: 'fci-grid',
        valid_at: '2026-09-05T11:58:00Z', received_at: '2026-09-05T11:59:00Z', reference_at: '2026-09-05T11:58:00Z',
        acquisition_window: { start_at: '2026-09-05T11:48:00Z', end_at: '2026-09-05T11:58:00Z' },
      }],
    },
    {
      source_id: 'opera-rate', status: 'available', reason_codes: [], attribution: 'OPERA', gaps: [], coverage: {},
      geolocation: { status: 'validated', reason_codes: [] }, frames: [{
        frame_id: 'rate-frame-1', content_id: 'rate-content-1', product_id: 'opera-rate', decoder_version: 'odim-v1', grid_id: 'opera-grid',
        valid_at: '2026-09-05T11:57:00Z', received_at: '2026-09-05T11:58:00Z', reference_at: '2026-09-05T11:57:00Z',
        acquisition_window: { start_at: '2026-09-05T11:52:00Z', end_at: '2026-09-05T11:57:00Z' },
      }],
    },
    {
      source_id: 'eumetsat-li', status: 'available', reason_codes: [], attribution: 'EUMETSAT', gaps: [], coverage: {},
      geolocation: { status: 'validated', reason_codes: [] }, frames: [{
        frame_id: 'li-frame-1', content_id: 'li-content-1', product_id: 'mtg-li', decoder_version: 'li-v1', grid_id: 'li-grid',
        valid_at: '2026-09-05T12:00:00Z', received_at: '2026-09-05T12:00:00Z', reference_at: '2026-09-05T12:00:00Z',
        acquisition_window: { start_at: '2026-09-05T11:50:00Z', end_at: '2026-09-05T12:00:00Z' },
      }],
    },
  ],
  features: [
    {
      feature_id: 'radar-feature-1', source_id: 'opera-dbzh', family: 'radar_echo',
      definition: { quantity: 'reflectivity', operator: 'gte', threshold: 5, unit: 'dBZ' },
      reference_at: '2026-09-05T12:00:00Z', reference_frame_id: 'radar-frame-1', frame_ids: ['radar-frame-1'],
      display_geometry: { status: 'available', reason_codes: [], provenance: 'grid_contour', simplification_tolerance_m: 200, geometry: {
        type: 'MultiPolygon', coordinates: [[
          [[-1, 50], [-0.7, 50], [-0.7, 50.3], [-1, 50.3], [-1, 50]],
          [[-0.92, 50.08], [-0.82, 50.08], [-0.82, 50.18], [-0.92, 50.18], [-0.92, 50.08]],
        ]],
      } },
      trail: [{ frame_id: 'radar-frame-1', observed_at: '2026-09-05T12:00:00Z', center: [-0.85, 50.15] }],
      observations: [{
        kind: 'rain_rate_max', status: 'available', reason_codes: [], value: 6.5, unit: 'mm_h', source_id: 'opera-rate', frame_id: 'rate-frame-1',
        observed_at: '2026-09-05T11:57:00Z', comparison_at: '2026-09-05T12:00:00Z', acquisition_window: { start_at: '2026-09-05T11:52:00Z', end_at: '2026-09-05T11:57:00Z' },
        alignment_method: 'in_history_translation', sample_id: 'rate-sample-1', sample_position: [-0.84, 50.14], paired_temperature_k: null,
        coverage: { status: 'available', reason_codes: [], scope: 'feature_contour', known_cells: 25, total_cells: 25, known_fraction: 1 },
      }],
      lightning_evidence: { status: 'available', reason_codes: [], source_id: 'eumetsat-li', frame_ids: ['li-frame-1'], evaluated_window: { start_at: '2026-09-05T11:50:00Z', end_at: '2026-09-05T12:00:00Z' }, reported_detection_count: 3, emitted_marker_count: 0, evaluation_complete: true },
      coverage: { status: 'available', reason_codes: [], scope: 'feature_contour', known_cells: 25, total_cells: 25, known_fraction: 1 },
      geolocation: { status: 'validated', reason_codes: [], evidence_id: 'radar-geo', method_version: 'registration-v1', applicability_id: 'opera-grid' },
      motion: { status: 'accepted', reason_codes: [], ground_speed_kt: 20, bearing_deg_true: 90, velocity_reference_point: [-0.85, 50.15], velocity_method: 'inverse_aeqd_geodesic_1s', pair_diagnostics: [], fit_rms_residual_cells: 0.2 },
      projection_end_at: '2026-09-05T12:15:00Z',
      projections: [{ at: '2026-09-05T12:05:00Z', status: 'available', reason_codes: [], display_geometry: { status: 'available', reason_codes: [], provenance: 'grid_contour', simplification_tolerance_m: 200, geometry: { type: 'MultiPolygon', coordinates: [[[[ -0.95, 50], [-0.65, 50], [-0.65, 50.3], [-0.95, 50.3], [-0.95, 50]]]] } } }],
      route_rows: [{ leg_id: 'route-1:0', leg_index: 0, from_label: 'EGTF', to_label: 'EGLF', at: '2026-09-05T12:05:00Z', status: 'available', reason_codes: [], distance_nm: 4.2, closure_kt: 7.5, closure_interval: { start_at: '2026-09-05T12:00:00Z', end_at: '2026-09-05T12:05:00Z' }, relationship: 'approaching', planned_time_method: 'distance_proportional_planned', planned_time_status: 'available', planned_time_reason_codes: [], planned_overlap_at_time: false }],
      planned_overlap: { status: 'available', reason_codes: [], method: 'relative_segment_contour_intersection', planned_time_method: 'distance_proportional_planned', evaluated_interval: { start_at: '2026-09-05T12:00:00Z', end_at: '2026-09-05T12:15:00Z' }, intervals: [{ leg_id: 'route-1:0', leg_index: 0, start_at: '2026-09-05T12:06:00Z', end_at: '2026-09-05T12:08:00Z', contact: 'interval', approximate: true }], complete: true },
      reason_codes: ['grid_discretization'],
    },
    {
      feature_id: 'cloud-feature-1', source_id: 'eumetsat-ctth', family: 'high_cloud_top',
      definition: { quantity: 'geometric_cloud_top_height', operator: 'gte', threshold: 4572, unit: 'm_msl' },
      reference_at: '2026-09-05T11:58:00Z', reference_frame_id: 'cloud-frame-1', frame_ids: ['cloud-frame-1'],
      display_geometry: { status: 'available', reason_codes: [], provenance: 'grid_contour', simplification_tolerance_m: 200, geometry: { type: 'MultiPolygon', coordinates: [[[[ -0.6, 50.05], [-0.4, 50.05], [-0.4, 50.25], [-0.6, 50.25], [-0.6, 50.05]]]] } },
      trail: [{ frame_id: 'cloud-frame-1', observed_at: '2026-09-05T11:58:00Z', center: [-0.5, 50.15] }],
      observations: [{ kind: 'cloud_top_max', status: 'available', reason_codes: [], value: 9000, unit: 'm_msl', source_id: 'eumetsat-ctth', frame_id: 'cloud-frame-1', observed_at: '2026-09-05T11:58:00Z', comparison_at: '2026-09-05T11:58:00Z', acquisition_window: { start_at: '2026-09-05T11:48:00Z', end_at: '2026-09-05T11:58:00Z' }, alignment_method: 'observed', sample_id: 'cloud-sample-1', sample_position: [-0.5, 50.15], paired_temperature_k: 223.15, coverage: { status: 'available', reason_codes: [], scope: 'feature_contour', known_cells: 20, total_cells: 20, known_fraction: 1 } }],
      lightning_evidence: { status: 'unavailable', reason_codes: ['missing_source'], source_id: null, frame_ids: [], evaluated_window: null, reported_detection_count: null, emitted_marker_count: 0, evaluation_complete: false },
      coverage: { status: 'available', reason_codes: [], scope: 'feature_contour', known_cells: 20, total_cells: 20, known_fraction: 1 },
      geolocation: { status: 'validated', reason_codes: [], evidence_id: 'cloud-geo', method_version: 'registration-v1', applicability_id: 'fci-grid' },
      motion: { status: 'accepted', reason_codes: [], ground_speed_kt: 12, bearing_deg_true: 270, velocity_reference_point: [-0.5, 50.15], velocity_method: 'inverse_aeqd_geodesic_1s', pair_diagnostics: [], fit_rms_residual_cells: 0.3 },
      projection_end_at: '2026-09-05T12:15:00Z', projections: [], route_rows: [],
      planned_overlap: { status: 'unavailable', reason_codes: ['invalid_planned_timing'], method: 'relative_segment_contour_intersection', planned_time_method: 'distance_proportional_planned', evaluated_interval: null, intervals: [], complete: false },
      reason_codes: [],
    },
  ],
  associations: [{ association_id: 'association-1', radar_feature_id: 'radar-feature-1', cloud_feature_id: 'cloud-feature-1', status: 'available', reason_codes: [], relation: 'nearby', comparison_at: '2026-09-05T11:58:00Z', alignment_method: 'in_history_translation', radar_frame_ids: ['radar-frame-1'], cloud_frame_ids: ['cloud-frame-1'], radar_window: { start_at: '2026-09-05T11:55:00Z', end_at: '2026-09-05T12:00:00Z' }, cloud_window: { start_at: '2026-09-05T11:48:00Z', end_at: '2026-09-05T11:58:00Z' }, intersection_area_km2: 0, radar_overlap_fraction: 0, cloud_overlap_fraction: 0, edge_distance_nm: 2.3, measurement_basis: 'analysis_grid_contours' }],
  lightning: [],
  projection_times: ['2026-09-05T12:05:00Z'],
  completeness: [{ category: 'features', status: 'complete', reason_codes: [], considered_count: 2, emitted_count: 2, omitted_count: 0 }],
  future_extension: { untouched: [1, true, 'raw'] },
};

describe('parseObservedMotion', () => {
  it('accepts the full strict producer fixture including reciprocal pair and patch diagnostics', () => {
    const parsed = parseObservedMotion(strictV1Fixture);
    expect(parsed.status).toBe('available');
    expect(parsed.motion?.features[0].motion.pair_diagnostics).toHaveLength(2);
    expect((parsed.motion?.features[0].motion.pair_diagnostics as Array<{ patches: unknown[] }>)[0].patches).toHaveLength(4);
  });

  it('cannot retain accepted motion when a nested patch diagnostic is malformed', () => {
    const malformed = structuredClone(strictV1Fixture) as any;
    malformed.features[0].motion.pair_diagnostics[0].patches[0].ncc = 'high';
    const parsed = parseObservedMotion(malformed);
    expect(parsed.status).toBe('unavailable');
    expect(parsed.revision).toBe(1);
    expect(parsed.motion?.features[0].motion).toMatchObject({ status: 'unavailable', ground_speed_kt: null });
    expect(parsed.motion?.features[0].display_geometry.status).toBe('available');
    expect(parsed.validationReasons).toContain('invalid_motion');
  });

  it('retains observed geometry but refuses active motion when the analysis domain is malformed', () => {
    const malformed = structuredClone(strictV1Fixture) as any;
    malformed.analysis_domain.bounds = [1, 49, -2, 51];
    const parsed = parseObservedMotion(malformed);
    expect(parsed.status).toBe('unavailable');
    expect(parsed.motion?.analysis_domain).toBeNull();
    expect(parsed.motion?.features[0].display_geometry.status).toBe('available');
    expect(parsed.motion?.features[0].motion.status).toBe('unavailable');
    expect(parsed.validationReasons).toContain('invalid_analysis_domain');
  });

  it('retains unknown raw JSON and validates two families, holes and independent evidence', () => {
    const parsed = parseObservedMotion(motionFixture);
    expect(parsed.status).toBe('available');
    expect(parsed.raw).toBe(motionFixture);
    expect((parsed.raw as typeof motionFixture).future_extension).toEqual({ untouched: [1, true, 'raw'] });
    expect(parsed.motion?.features.map(feature => feature.family)).toEqual(['radar_echo', 'high_cloud_top']);
    expect(parsed.motion?.features[0].display_geometry.geometry?.coordinates[0]).toHaveLength(2);
    expect(parsed.motion?.features[0].lightning_evidence.reported_detection_count).toBe(3);
    expect(parsed.motion?.features[0].route_rows[0]).toMatchObject({ distance_nm: 4.2, closure_kt: 7.5, planned_overlap_at_time: false });
  });

  it('uses a readable newer revision to reject unsupported schema rendering', () => {
    const parsed = parseObservedMotion({ ...motionFixture, schema_version: 2, revision: 9 });
    expect(parsed).toMatchObject({ status: 'unavailable', revision: 9, unavailableReason: 'unsupported_schema' });
    expect(parsed.motion).toBeNull();
  });

  it('does not order an unsupported envelope whose identity fields are missing', () => {
    expect(parseObservedMotion({ schema_version: 2, revision: 10 })).toMatchObject({
      status: 'unavailable', revision: null, motion: null, unavailableReason: 'invalid_motion',
    });
  });

  it('degrades malformed geometry and drops records with broken dependent references', () => {
    const malformed = structuredClone(motionFixture);
    malformed.features[0].display_geometry.geometry!.coordinates[0][0][2] = [Number.NaN, 50.25];
    malformed.features[1].source_id = 'missing-source';
    const parsed = parseObservedMotion(malformed);
    expect(parsed.status).toBe('available');
    expect(parsed.motion?.features.map(feature => feature.feature_id)).toEqual(['radar-feature-1']);
    expect(parsed.motion?.features[0].display_geometry).toMatchObject({ status: 'unavailable', geometry: null });
    expect(parsed.motion?.associations).toEqual([]);
    expect(parsed.validationReasons).toContain('invalid_geometry');
    expect(parsed.validationReasons).toContain('dangling_reference');
  });

  it('degrades a projection beyond its owning feature expiry even when the envelope advertises that time', () => {
    const malformed = structuredClone(motionFixture);
    malformed.projection_times = ['2026-09-05T12:05:00Z', '2026-09-05T12:10:00Z'];
    malformed.features[1].projection_end_at = '2026-09-05T12:06:00Z';
    malformed.features[1].projections = [{
      ...structuredClone(motionFixture.features[0].projections[0]),
      at: '2026-09-05T12:10:00Z',
    }];

    const parsed = parseObservedMotion(malformed);

    expect(parsed.motion?.features[1].projections[0]).toMatchObject({
      at: '2026-09-05T12:10:00Z',
      status: 'unavailable',
      display_geometry: { status: 'unavailable', geometry: null },
    });
  });

  it('degrades a projection that is not an advertised future time', () => {
    const malformed = structuredClone(motionFixture);
    malformed.features[0].projections[0].at = '2026-09-05T12:06:00Z';

    const parsed = parseObservedMotion(malformed);

    expect(parsed.motion?.features[0].projections[0]).toMatchObject({
      at: '2026-09-05T12:06:00Z',
      status: 'unavailable',
      display_geometry: { status: 'unavailable', geometry: null },
    });
  });

  it('degrades a projection at the envelope cutoff even when the envelope advertises it', () => {
    const malformed = structuredClone(motionFixture);
    malformed.projection_times = ['2026-09-05T12:00:00Z'];
    malformed.features[0].projections[0].at = '2026-09-05T12:00:00Z';

    const parsed = parseObservedMotion(malformed);

    expect(parsed.motion?.features[0].projections[0]).toMatchObject({
      at: '2026-09-05T12:00:00Z',
      status: 'unavailable',
      display_geometry: { status: 'unavailable', geometry: null },
    });
  });

  it('degrades a projection whose feature lacks validated geolocation or accepted motion', () => {
    const malformed = structuredClone(motionFixture);
    malformed.features[0].geolocation.status = 'unverified';

    const parsed = parseObservedMotion(malformed);

    expect(parsed.motion?.features[0].projections[0]).toMatchObject({
      status: 'unavailable',
      display_geometry: { status: 'unavailable', geometry: null },
    });
  });

  it('does not retain geometry from an unavailable projection record', () => {
    const malformed = structuredClone(motionFixture);
    malformed.features[0].projections[0].status = 'unavailable';
    malformed.features[0].projections[0].reason_codes = ['motion_unavailable'];

    const parsed = parseObservedMotion(malformed);

    expect(parsed.motion?.features[0].projections[0]).toMatchObject({
      status: 'unavailable',
      display_geometry: { status: 'unavailable', geometry: null },
    });
  });

  it('resolves context frames through their named source and association endpoints through the correct families', () => {
    const malformed = structuredClone(motionFixture) as any;
    malformed.features[0].trail[0].frame_id = 'cloud-frame-1';
    malformed.features[0].observations[0].source_id = 'eumetsat-ctth';
    malformed.features[0].lightning_evidence.source_id = 'opera-dbzh';
    malformed.associations[0].radar_feature_id = 'cloud-feature-1';
    malformed.associations[0].cloud_feature_id = 'radar-feature-1';
    malformed.lightning = [{
      detection_id: 'li-detection-1', source_id: 'opera-dbzh', frame_id: 'cloud-frame-1', position: [-0.8, 50.1],
      time_precision: 'individual_time', event_at: '2026-09-05T11:57:00Z',
      acquisition_window: { start_at: '2026-09-05T11:56:00Z', end_at: '2026-09-05T11:58:00Z' },
      associated_feature_ids: ['radar-feature-1'],
    }];

    const parsed = parseObservedMotion(malformed);
    expect(parsed.motion?.features[0].trail).toEqual([]);
    expect(parsed.motion?.features[0].observations).toEqual([]);
    expect(parsed.motion?.features[0].lightning_evidence).toMatchObject({ status: 'unavailable', reported_detection_count: null });
    expect(parsed.motion?.associations).toEqual([]);
    expect(parsed.motion?.lightning).toEqual([]);
    expect(parsed.validationReasons).toContain('dangling_reference');
  });

  it('drops a feature whose reference frame belongs to another source', () => {
    const malformed = structuredClone(motionFixture);
    malformed.features[0].reference_frame_id = 'cloud-frame-1';
    const parsed = parseObservedMotion(malformed);
    expect(parsed.motion?.features.map(feature => feature.feature_id)).toEqual(['cloud-feature-1']);
    expect(parsed.motion?.associations).toEqual([]);
    expect(parsed.validationReasons).toContain('dangling_reference');
  });

  it('does not retain accepted motion or projections inside an unavailable envelope', () => {
    const malformed = structuredClone(motionFixture);
    malformed.status = 'unavailable';
    malformed.reason_codes = ['compute_failed'];
    malformed.expires_at = null;
    const parsed = parseObservedMotion(malformed);
    expect(parsed.status).toBe('unavailable');
    expect(parsed.motion?.features[0]).toMatchObject({
      motion: { status: 'unavailable', ground_speed_kt: null }, projection_end_at: null, projections: [],
    });
    expect(parsed.validationReasons).toContain('invalid_motion');
  });

  it('treats a missing legacy block as unavailable without inventing ordering', () => {
    expect(parseObservedMotion(undefined)).toMatchObject({
      status: 'unavailable', revision: null, motion: null, unavailableReason: 'missing_legacy_data',
    });
  });
});

describe('existing snapshot motion authority transport', () => {
  it('coalesces by request generation, bypasses cache and aborts at ten seconds', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('deadline', 'AbortError')));
    }));
    vi.stubGlobal('fetch', fetcher);
    const { fetchExistingSnapshotForMotionAuthority } = await import('../../ts/adapters/api-adapter');
    const first = fetchExistingSnapshotForMotionAuthority('flight-1', 'pack-1', 7);
    const coalesced = fetchExistingSnapshotForMotionAuthority('flight-1', 'pack-1', 7);
    expect(coalesced).toBe(first);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][1]).toMatchObject({ cache: 'no-store', headers: { 'Cache-Control': 'no-cache' } });
    const rejected = expect(first).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(10_000);
    await rejected;
    fetchExistingSnapshotForMotionAuthority('flight-1', 'pack-1', 8).catch(() => undefined);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
