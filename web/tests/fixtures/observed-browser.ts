import { buildSync } from 'esbuild';
import { existsSync, readFileSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { expect, type Page, type Route } from '@playwright/test';

const WEB = resolve(__dirname, '../..');
const FIXTURES = join(__dirname, 'egtf_eglf');
const FLIGHT = 'egtf_eglf-2026-02-25-45ed';
export const CLOCK = '2026-02-25T17:00:00Z';
export const IMAGE_TIME = '2026-02-25T16:55:00Z';
const SNAPSHOT_TIME = '2026-02-25T16:40:00Z';
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAHUlEQVR4nGOUXOXdw0ABYKJE86gBowaMGjCYDAAAwowBujKpzqYAAAAASUVORK5CYII=', 'base64');

// Auxiliary features deliberately absent from this focused observation fixture.
// Any other missing request (especially a weather/static asset) fails the test.
const OPTIONAL_APIS = new Set([
  '/api/user/profiles', '/api/user/preferences/advisories/catalog', '/api/messages/status',
  '/api/models', '/api/donations/nudge', '/api/pireps', '/api/events', '/api/maps/forecast/days',
]);
function expectedMissing(path: string) {
  return OPTIONAL_APIS.has(path) || /^\/api\/flights\/[^/]+\/packs\/(?:refresh\/status|[^/]+\/(?:route-fronts|advisories\/altitude-table|sounding-profile\/0\/gfs))$/.test(path);
}

// Compile the real entrypoint only in memory for browser execution. The
// checkout's web/dist remains untouched; this is not the production build.
const assets = buildSync({
  absWorkingDir: WEB,
  entryPoints: ['ts/briefing-main.ts'],
  outdir: 'observed-browser-memory',
  bundle: true,
  write: false,
  target: 'es2020',
}).outputFiles;

function fixture(name: string) {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf8'));
}

function observedFixture(includeTops: boolean) {
  const analyses = fixture('route_analyses.json').analyses;
  const stations = analyses.map((point: { lat: number; lon: number; distance_from_origin_nm: number }, index: number) => ({
    id: `P${index}`, name: null, lat: point.lat, lon: point.lon,
    enroute_distance_nm: point.distance_from_origin_nm, distance_from_route_nm: 0,
  }));
  const counts = {
    total_px: 100, valid_px: 20, nodata_px: 80, detected_px: 10, undetect_px: 10,
    coverage_fraction: 0.2, detected_fraction: 0.5, insufficient_coverage: true,
    max_value: 45, mean_value: 30, p90_value: 40,
  };
  const meta = (source: string, units: string) => ({
    source, quantity: source, units, valid_time: SNAPSHOT_TIME, age_minutes: 0, window_minutes: 10,
    attribution: { text: 'Synthetic snapshot producer', url: 'https://example.invalid/attribution', license: 'fixture', producer: 'Fixture' },
  });
  const field = (source: string, units: string, extra = {}) => ({
    ...meta(source, units),
    stations: stations.map((station: { id: string }) => ({
      station_id: station.id,
      annuli: [5, 10, 20].map(radius_nm => ({ radius_nm, ...counts, ...extra })),
    })),
  });
  return {
    computed_at: '2026-02-25T16:41:00Z', corridor_nm: 20, radii_nm: [5, 10, 20], stations,
    reflectivity: field('opera_dbzh', 'dBZ'),
    rain_rate: field('opera_rate', 'mm/h', { max_value: 6.5 }),
    cloud_tops: includeTops ? field('eumetsat_ctth', 'm', {
      max_value: 10668, highest_fl: 350, coldest_top_k: 223.15,
      highest_cloudiness: 0.35, median_cloudiness: 0.6, highest_aviation_fl: 340,
      fl_bins: { 'FL250-400': 10 }, quality_method: { '1': 10, '0': 10 },
    }) : null,
    lightning: {
      ...meta('eumetsat_li', 'count'),
      stations: stations.map((station: { id: string }) => ({
        station_id: station.id,
        annuli: [5, 10, 20].map(radius_nm => ({ radius_nm, flash_count: 1,
          area_km2: 100, window_minutes: 10, nearest_flash_nm: 2,
          latest_flash_time: '2026-02-25T16:39:00Z', flashes_per_1000km2_per_min: 1,
        })),
      })),
    },
    summary: 'Synthetic observed-weather test data, not operational weather.',
    summary_lines: ['Synthetic radar detection with partial coverage.'],
    summary_entries: [{ kind: 'reflectivity', text: 'Synthetic radar detection with partial coverage.', metric_id: 'observed.radar' }],
    sources: [{ source: 'opera_dbzh', available: true, reason: null, latest_valid_time: SNAPSHOT_TIME }],
    has_any_field: true,
  };
}

export function observedMotionFixture() {
  const coverage = { status: 'available', reason_codes: [], scope: 'feature_contour', known_cells: 20, total_cells: 20, known_fraction: 1 };
  const geolocation = { status: 'validated', reason_codes: [], evidence_id: 'fixture-geolocation', method_version: 'fixture-registration-v1', applicability_id: 'fixture-domain' };
  const geometry = (coordinates: number[][][][]) => ({ status: 'available', reason_codes: [], geometry: { type: 'MultiPolygon', coordinates }, provenance: 'grid_contour', simplification_tolerance_m: 200 });
  const source = (source_id: string, frame_id: string, valid_at: string, attribution: string, previousValidAt?: string) => ({
    source_id, status: 'available', reason_codes: [], attribution, gaps: [], coverage: { ...coverage, scope: 'analysis_domain' }, geolocation,
    frames: [...(previousValidAt ? [{ frame_id: frame_id.replace(/-1$/, '-0'), content_id: `content-${frame_id}-previous`, product_id: source_id,
      decoder_version: 'fixture-v1', grid_id: 'fixture-grid', valid_at: previousValidAt, received_at: previousValidAt, reference_at: previousValidAt,
      acquisition_window: { start_at: previousValidAt, end_at: previousValidAt } }] : []),
      { frame_id, content_id: `content-${frame_id}`, product_id: source_id, decoder_version: 'fixture-v1', grid_id: 'fixture-grid',
        valid_at, received_at: valid_at, reference_at: valid_at, acquisition_window: { start_at: valid_at, end_at: valid_at } }],
  });
  const radarCoordinates = [[
    [[-0.95, 51.15], [-0.55, 51.15], [-0.55, 51.45], [-0.95, 51.45], [-0.95, 51.15]],
    [[-0.82, 51.24], [-0.68, 51.24], [-0.68, 51.34], [-0.82, 51.34], [-0.82, 51.24]],
  ]];
  const cloudCoordinates = [[[[ -0.35, 51.18], [0.05, 51.18], [0.05, 51.48], [-0.35, 51.48], [-0.35, 51.18]]]];
  const baseFeature = {
    coverage, geolocation, projections: [], route_rows: [], observations: [], reason_codes: [],
    lightning_evidence: { status: 'unavailable', reason_codes: ['missing_source'], source_id: null, frame_ids: [], evaluated_window: null,
      reported_detection_count: null, emitted_marker_count: 0, evaluation_complete: false },
    planned_overlap: { status: 'unavailable', reason_codes: ['invalid_planned_timing'], method: 'relative_segment_contour_intersection',
      planned_time_method: 'distance_proportional_planned', evaluated_interval: null, intervals: [], complete: false },
  };
  const radar = {
    ...baseFeature, feature_id: 'radar-feature-1', source_id: 'opera-dbzh', family: 'radar_echo',
    definition: { quantity: 'reflectivity', operator: 'gte', threshold: 5, unit: 'dBZ' },
    reference_at: '2026-02-25T16:50:00Z', reference_frame_id: 'radar-frame-1', frame_ids: ['radar-frame-0', 'radar-frame-1'],
    display_geometry: geometry(radarCoordinates),
    trail: [{ frame_id: 'radar-frame-0', observed_at: '2026-02-25T16:40:00Z', center: [-0.88, 51.27] },
      { frame_id: 'radar-frame-1', observed_at: '2026-02-25T16:50:00Z', center: [-0.75, 51.30] }],
    observations: [{ kind: 'rain_rate_max', status: 'available', reason_codes: [], value: 6.5, unit: 'mm_h', source_id: 'opera-rate', frame_id: 'rate-frame-1',
      observed_at: '2026-02-25T16:47:00Z', comparison_at: '2026-02-25T16:50:00Z', acquisition_window: { start_at: '2026-02-25T16:42:00Z', end_at: '2026-02-25T16:47:00Z' },
      alignment_method: 'in_history_translation', sample_id: 'rate-sample-1', sample_position: [-0.75, 51.30], paired_temperature_k: null, coverage }],
    lightning_evidence: { status: 'available', reason_codes: ['selection_limit'], source_id: 'eumetsat-li', frame_ids: ['li-frame-1'],
      evaluated_window: { start_at: '2026-02-25T16:40:00Z', end_at: '2026-02-25T16:50:00Z' }, reported_detection_count: 7,
      emitted_marker_count: 1, evaluation_complete: false },
    motion: { status: 'accepted', reason_codes: ['grid_discretization'], ground_speed_kt: 22, bearing_deg_true: 85,
      velocity_reference_point: [-0.75, 51.30], velocity_method: 'inverse_aeqd_geodesic_1s', pair_diagnostics: [], fit_rms_residual_cells: 0.3 },
    projection_end_at: '2026-02-25T17:05:00Z',
    projections: [{ at: '2026-02-25T17:05:00Z', status: 'available', reason_codes: ['projected_translation'],
      display_geometry: geometry([[...radarCoordinates[0].map(ring => ring.map(([lon, lat]) => [lon + 0.1, lat]))]]) }],
    route_rows: [{ leg_id: 'fixture-route:0', leg_index: 0, from_label: 'EGTF', to_label: 'EGLF', at: '2026-02-25T17:05:00Z', status: 'available',
      reason_codes: ['grid_discretization'], distance_nm: 3.2, closure_kt: 8.4,
      closure_interval: { start_at: '2026-02-25T17:00:00Z', end_at: '2026-02-25T17:05:00Z' }, relationship: 'approaching',
      planned_time_method: 'distance_proportional_planned', planned_time_status: 'available', planned_time_reason_codes: [], planned_overlap_at_time: false }],
    planned_overlap: { status: 'available', reason_codes: ['grid_discretization'], method: 'relative_segment_contour_intersection',
      planned_time_method: 'distance_proportional_planned', evaluated_interval: { start_at: '2026-02-25T17:00:00Z', end_at: '2026-02-25T17:10:00Z' },
      intervals: [{ leg_id: 'fixture-route:0', leg_index: 0, start_at: '2026-02-25T17:03:00Z', end_at: '2026-02-25T17:05:00Z', contact: 'interval', approximate: true }], complete: true },
  };
  const cloud = {
    ...baseFeature, feature_id: 'cloud-feature-1', source_id: 'eumetsat-ctth', family: 'high_cloud_top',
    definition: { quantity: 'geometric_cloud_top_height', operator: 'gte', threshold: 4572, unit: 'm_msl' },
    reference_at: '2026-02-25T16:48:00Z', reference_frame_id: 'cloud-frame-1', frame_ids: ['cloud-frame-0', 'cloud-frame-1'],
    display_geometry: geometry(cloudCoordinates),
    trail: [{ frame_id: 'cloud-frame-0', observed_at: '2026-02-25T16:38:00Z', center: [-0.30, 51.28] },
      { frame_id: 'cloud-frame-1', observed_at: '2026-02-25T16:48:00Z', center: [-0.15, 51.32] }],
    observations: [{ kind: 'cloud_top_max', status: 'available', reason_codes: [], value: 9000, unit: 'm_msl', source_id: 'eumetsat-ctth', frame_id: 'cloud-frame-1',
      observed_at: '2026-02-25T16:48:00Z', comparison_at: '2026-02-25T16:48:00Z', acquisition_window: { start_at: '2026-02-25T16:38:00Z', end_at: '2026-02-25T16:48:00Z' },
      alignment_method: 'observed', sample_id: 'cloud-sample-1', sample_position: [-0.15, 51.32], paired_temperature_k: 223.15, coverage }],
    motion: { status: 'accepted', reason_codes: [], ground_speed_kt: 14, bearing_deg_true: 265,
      velocity_reference_point: [-0.15, 51.32], velocity_method: 'inverse_aeqd_geodesic_1s', pair_diagnostics: [], fit_rms_residual_cells: 0.2 },
    projection_end_at: '2026-02-25T17:05:00Z', projections: [],
  };
  return {
    schema_version: 1, status: 'available', reason_codes: ['source_window_limit'], revision: 8, run_id: 'fixture-run-8',
    route_geometry_id: 'fixture-route-geometry', planned_timing_id: 'fixture-planned-timing', computed_at: '2026-02-25T16:51:00Z',
    cutoff_at: '2026-02-25T16:50:00Z', expires_at: '2026-02-25T17:05:00Z', method_version: 'masked_contour_translation_v1',
    policy_version: 'observed_motion_policy_v1', analysis_domain: { center: [-0.5, 51.3], crs: '+proj=aeqd +datum=WGS84', cell_size_m: 2000,
      width_cells: 100, height_cells: 100, origin_x_m: -100000, origin_y_m: -100000, bounds: [-2, 50, 1, 52], reason_codes: ['grid_discretization'] },
    sources: [source('opera-dbzh', 'radar-frame-1', '2026-02-25T16:50:00Z', 'Synthetic OPERA', '2026-02-25T16:40:00Z'),
      source('opera-rate', 'rate-frame-1', '2026-02-25T16:47:00Z', 'Synthetic OPERA rate'),
      source('eumetsat-ctth', 'cloud-frame-1', '2026-02-25T16:48:00Z', 'Synthetic EUMETSAT', '2026-02-25T16:38:00Z'),
      source('eumetsat-li', 'li-frame-1', '2026-02-25T16:49:00Z', 'Synthetic EUMETSAT LI')],
    features: [radar, cloud], associations: [{ association_id: 'association-1', radar_feature_id: 'radar-feature-1', cloud_feature_id: 'cloud-feature-1',
      status: 'available', reason_codes: [], relation: 'nearby', comparison_at: '2026-02-25T16:48:00Z', alignment_method: 'in_history_translation',
      radar_frame_ids: ['radar-frame-1'], cloud_frame_ids: ['cloud-frame-1'], radar_window: { start_at: '2026-02-25T16:45:00Z', end_at: '2026-02-25T16:50:00Z' },
      cloud_window: { start_at: '2026-02-25T16:38:00Z', end_at: '2026-02-25T16:48:00Z' }, intersection_area_km2: 0,
      radar_overlap_fraction: 0, cloud_overlap_fraction: 0, edge_distance_nm: 2.1, measurement_basis: 'analysis_grid_contours' }],
    lightning: [
      { detection_id: 'flash-individual', source_id: 'eumetsat-li', frame_id: 'li-frame-1', position: [-0.72, 51.31], time_precision: 'individual_time',
        event_at: '2026-02-25T16:48:30Z', acquisition_window: { start_at: '2026-02-25T16:40:00Z', end_at: '2026-02-25T16:49:00Z' }, reason_codes: [],
        association_status: 'available', association_reason_codes: [], associated_feature_ids: ['radar-feature-1'] },
      { detection_id: 'flash-window', source_id: 'eumetsat-li', frame_id: 'li-frame-1', position: [-0.20, 51.30], time_precision: 'window_only',
        event_at: null, acquisition_window: { start_at: '2026-02-25T16:40:00Z', end_at: '2026-02-25T16:49:00Z' }, reason_codes: ['window_only_time'],
        association_status: 'unavailable', association_reason_codes: ['window_only_time'], associated_feature_ids: null },
    ], projection_times: ['2026-02-25T17:05:00Z'],
    completeness: [{ category: 'features', status: 'complete', reason_codes: [], considered_count: 2, emitted_count: 2, omitted_count: 0 }],
    future_extension: { preserve_me: [1, true] },
  };
}

export function fulfillImage(route: Route, attribution = 'Synthetic image producer', validTime = IMAGE_TIME) {
  return route.fulfill({ body: PNG, contentType: 'image/png', headers: {
    'X-Observed-Valid-Time': validTime,
    'X-Observed-Window-Minutes': '10',
    'X-Observed-Attribution': encodeURIComponent(attribution),
  } });
}

export function fulfillFlashes(route: Route, attribution = 'Synthetic flash producer', time = '2026-02-25T16:01:30Z') {
  return route.fulfill({ json: {
    flashes: [{ lat: 51.32, lon: -0.64, time }], newest_valid_time: IMAGE_TIME,
    window_minutes: 10, attribution: { text: attribution },
  } });
}

export async function openObservedPage(page: Page, options: {
  source?: string;
  includeTops?: boolean;
  image?: (route: Route, source: string, attempt: number) => Promise<void>;
  flashes?: (route: Route, attempt: number) => Promise<void>;
  motion?: Record<string, unknown> | null;
  capability?: boolean | null;
  beforeEntrypoint?: string;
} = {}) {
  const observed = observedFixture(options.includeTops ?? true);
  let currentMotion = options.motion === undefined ? observedMotionFixture() : options.motion;
  let capability = options.capability === undefined ? true : options.capability;
  const snapshot = { ...fixture('snapshot.json'), observed_conditions: observed, observed_motion: currentMotion };
  const state = { images: [] as string[], flashes: 0, refreshes: 0, snapshots: 0, failSnapshots: false, failRefresh: false, omitMotion: false,
    pageErrors: [] as string[], unhandled: [] as string[] };
  page.on('pageerror', error => state.pageErrors.push(error.message));
  await page.clock.install({ time: new Date(CLOCK) });
  await page.clock.pauseAt(new Date(CLOCK));
  await page.addInitScript(source => {
    localStorage.setItem('wb_displayMode', 'full');
    localStorage.setItem('wb_locale', 'en');
    localStorage.setItem('wb_vizSettings', JSON.stringify({ layout: 'map', observedOverlay: source,
      mapForecastOverlayVisible: false, mapFrontsVisible: false }));
  }, options.source ?? 'opera_dbzh');

  // Default-deny network: every request is fulfilled here, including third-party
  // map tiles. Unhandled API routes return 404, never an invented success body.
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const path = decodeURIComponent(url.pathname);
    if (url.host !== 'observed.test') {
      if (route.request().resourceType() === 'image') return route.fulfill({ body: PNG, contentType: 'image/png' });
      state.unhandled.push(url.origin + path);
      return route.fulfill({ status: 404 });
    }
    if (path === '/dist/briefing.js' || path === '/dist/briefing.css') {
      const asset = assets.find(file => file.path.endsWith(path.endsWith('.js') ? '.js' : '.css'));
      if (!asset) throw new Error(`Missing in-memory test asset: ${path}`);
      const body = path.endsWith('.js') && options.beforeEntrypoint
        ? Buffer.concat([Buffer.from(options.beforeEntrypoint + '\n'), Buffer.from(asset.contents)]) : Buffer.from(asset.contents);
      return route.fulfill({ body, contentType: path.endsWith('.js') ? 'text/javascript' : 'text/css' });
    }
    const local = resolve(WEB, `.${path}`);
    if (local.startsWith(WEB + sep) && /\.(html|css|png|svg|ico|woff2?)$/.test(local) && existsSync(local)) {
      return route.fulfill({ path: local });
    }
    if (path === '/auth/me') return route.fulfill({ json: {
      id: 'fixture-user', email: 'fixture@example.invalid', name: 'Fixture', approved: true,
      is_admin: false, setup_completed: true, units_region: 'europe', synoptic_forecast_map_enabled: false,
    } });
    if (path === `/api/flights/${FLIGHT}`) return route.fulfill({ json: { ...fixture('flight.json'), user_id: 'fixture-user', role: 'owner' } });
    if (path === `/api/flights/${FLIGHT}/packs`) return route.fulfill({ json: fixture('packs.json') });
    if (/\/packs\/[^/]+$/.test(path) && !path.endsWith('/freshness')) return route.fulfill({ json: fixture('pack_meta.json') });
    if (path.endsWith('/snapshot')) {
      state.snapshots++;
      if (state.failSnapshots) return route.fulfill({ status: 503, json: { detail: 'Synthetic snapshot failure' } });
      const { observed_motion: _storedMotion, ...snapshotWithoutMotion } = snapshot;
      return route.fulfill({ json: { ...snapshotWithoutMotion, ...(state.omitMotion ? {} : { observed_motion: currentMotion }) }, headers: capability == null ? {} : { 'X-Observed-Motion-Enabled': capability ? '1' : '0' } });
    }
    const payloads: Record<string, string> = { '/route-analyses': 'route_analyses.json', '/advisories': 'advisories.json',
      '/elevation': 'elevation.json', '/digest/json': 'digest.json' };
    for (const [suffix, file] of Object.entries(payloads)) {
      if (path.endsWith(suffix)) return route.fulfill({ json: fixture(file) });
    }
    if (path.endsWith('/observations/refresh')) {
      state.refreshes++;
      if (state.failRefresh) return route.fulfill({ status: 503, json: { detail: 'Synthetic refresh failure' } });
      return route.fulfill({ json: { observations: snapshot.route_observations, observed, sigmets: null, delta: null, observed_motion: currentMotion },
        headers: capability == null ? {} : { 'X-Observed-Motion-Enabled': capability ? '1' : '0' } });
    }
    if (path.startsWith('/api/observed/overlay/')) {
      const source = path.split('/').pop()!.replace('.png', '');
      state.images.push(source);
      return options.image ? options.image(route, source, state.images.length) : fulfillImage(route);
    }
    if (path === '/api/observed/flashes') {
      state.flashes++;
      return options.flashes ? options.flashes(route, state.flashes) : fulfillFlashes(route);
    }
    if (path === '/api/observed/status') return route.fulfill({ json: { sources: [
      { source: 'opera_dbzh', label: 'Radar reflectivity', units: 'dBZ', legend: [{ value: 0, color: '#19aa4b' }, { value: 60, color: '#ff0000' }] },
      { source: 'opera_rate', label: 'Rain rate', units: 'mm/h', legend: [{ value: 0, color: '#19aa4b' }, { value: 20, color: '#ff0000' }] },
      { source: 'eumetsat_ctth_temp', label: 'Cloud-top temperature', units: 'K', legend: [{ value: 220, color: '#ff0000' }, { value: 280, color: '#19aa4b' }] },
    ] } });
    if (path === '/api/user/preferences') return route.fulfill({ json: { notify_scope: 'off', notify_change_only: true } });
    if (path === '/api/help/catalog') return route.fulfill({ path: join(WEB, 'ts/data/metrics-catalog.json') });
    if (path.endsWith('/packs/freshness')) return route.fulfill({ json: { fresh: true, stale_models: [],
      model_init_times: {}, next_expected_update: null, next_expected_model: null } });
    if (path.endsWith('/seen')) return route.fulfill({ status: 204 });
    if (path === '/api/refresh/active') return route.fulfill({ json: [] });
    state.unhandled.push(path);
    return route.fulfill({ status: 404, json: { detail: 'Not supplied by observed browser fixture' } });
  });

  // The default browser suite has a different baseURL. Always navigate to this
  // fixture's intercepted origin, even when discovered by that broader runner.
  await page.goto(`http://observed.test/briefing.html?flight=${FLIGHT}`);
  await expect(page.locator('#observed-section')).toContainText('Synthetic radar detection');
  const section = page.locator('[data-section="cross-section"]');
  if (await section.evaluate(el => el.classList.contains('collapsed'))) await section.locator('h3').first().click();
  await expect(page.locator('#map-observed-overlay')).toBeVisible();
  return Object.assign(state, {
    setCapability(value: boolean | null) { capability = value; },
    setMotion(value: Record<string, unknown> | null) { currentMotion = value; },
    omitMotionFromSnapshots() { state.omitMotion = true; },
    assertHealthy() {
    expect(state.pageErrors).toEqual([]);
    expect([...new Set(state.unhandled)].filter(path => !expectedMissing(path))).toEqual([]);
  } });
}
