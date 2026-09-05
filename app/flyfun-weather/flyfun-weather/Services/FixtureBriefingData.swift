#if DEBUG
import Foundation

/// Canned, internally-consistent briefing fixtures for `fixture-1`
/// (LFMD → LFML, D-1, AMBER overall with one RED convective advisory).
///
/// Serves the whole briefing surface for the XCUITest journeys (#318): the
/// advisory dashboard + drill-down, the cross-section, the digest, and the
/// airport-conditions strip. Every blob is decoded through the production
/// `JSONDecoder.weatherBrief` (snake_case → camelCase, the same path the real
/// network responses take), so a model change that breaks the contract fails
/// loudly here — `try!` on purpose (see `FixtureBriefingRepository.decodeFlights`).
///
/// Coherence rules the blobs keep so nothing renders as a contradiction:
///  - Models are exactly `["ecmwf", "gfs"]` everywhere (pack init-times, route
///    analyses, advisory per-model splits, airport conditions).
///  - The route is 5 points over 78 nm; the convective RED peaks near LFMD on
///    GFS while ECMWF stays AMBER — the "RED under blue sky" cross-check story.
///  - `assessment` is AMBER on the pack + digest, matching the worst *non-red*
///    aggregate the dashboard collapses to after the single RED convective card.
enum FixtureBriefingData {

    static let flightId = "fixture-1"
    static let timestamp = "2099-06-30T06:00:00Z"

    private static func decode<T: Decodable>(_ type: T.Type, _ json: String) -> T {
        try! JSONDecoder.weatherBrief.decode(T.self, from: Data(json.utf8))
    }

    // MARK: - Pack metadata

    static let pack: PackMetaResponse = decode(PackMetaResponse.self, """
    {
      "flight_id": "fixture-1",
      "fetch_timestamp": "2099-06-30T06:00:00Z",
      "days_out": 1,
      "is_historical": false,
      "has_gramet": false,
      "has_skewt": true,
      "has_digest": true,
      "has_advisories": true,
      "assessment": "amber",
      "assessment_reason": "Isolated convective cell risk near Cannes on GFS; marginal cruise icing on the coast. VFR feasible with care and a timing eye on the afternoon build-up.",
      "model_init_times": { "ecmwf": 0, "gfs": 6 },
      "grib_init_times": { "ecmwf": 0, "gfs": 6 },
      "models_skipped_region": [],
      "data_status": {
        "fresh": true,
        "stale_models": [],
        "model_init_times": { "ecmwf": 0, "gfs": 6 },
        "next_expected_update": null,
        "next_expected_model": null
      }
    }
    """)

    static let packs: [PackMetaResponse] = [pack]

    // MARK: - Advisories

    static let advisories: AdvisoriesResponse = decode(AdvisoriesResponse.self, """
    {
      "advisories": [
        {
          "advisory_id": "convective",
          "aggregate_status": "red",
          "aggregate_detail": "Isolated convective cell near LFMD — GFS builds CAPE to ~1400 J/kg by mid-morning.",
          "parameters_used": { "cape_red_jkg": 1000.0, "affected_pct_red": 25.0 },
          "per_model": [
            {
              "model": "gfs",
              "status": "red",
              "detail": "CAPE ~1400 J/kg near LFMD, tops to FL340.",
              "affected_points": 2,
              "total_points": 5,
              "affected_pct": 40.0,
              "affected_nm": 20.0,
              "total_nm": 78.0,
              "cross_check": "High CAPE (~1400 J/kg) while the model's own convective cover is ~2% — a classic quiet-sky thermodynamic RED, not a wall of cloud."
            },
            {
              "model": "ecmwf",
              "status": "amber",
              "detail": "Marginal instability; CAPE ~600 J/kg.",
              "affected_points": 1,
              "total_points": 5,
              "affected_pct": 20.0,
              "affected_nm": 8.0,
              "total_nm": 78.0
            }
          ]
        },
        {
          "advisory_id": "icing",
          "aggregate_status": "amber",
          "aggregate_detail": "Light rime possible in coastal cloud between 6,000 and 9,000 ft.",
          "parameters_used": { "freezing_level_ft": 9000.0 },
          "per_model": [
            {
              "model": "gfs",
              "status": "amber",
              "detail": "Light rime 6–9 kft in stratiform cloud.",
              "affected_points": 2,
              "total_points": 5,
              "affected_pct": 40.0,
              "affected_nm": 18.0,
              "total_nm": 78.0
            },
            {
              "model": "ecmwf",
              "status": "green",
              "detail": "Freezing level above cloud tops.",
              "affected_points": 0,
              "total_points": 5,
              "affected_pct": 0.0,
              "affected_nm": 0.0,
              "total_nm": 78.0
            }
          ]
        },
        {
          "advisory_id": "turbulence",
          "aggregate_status": "green",
          "aggregate_detail": "No significant turbulence expected en route.",
          "parameters_used": {},
          "per_model": [
            {
              "model": "gfs", "status": "green", "detail": "Smooth.",
              "affected_points": 0, "total_points": 5, "affected_pct": 0.0,
              "affected_nm": 0.0, "total_nm": 78.0
            },
            {
              "model": "ecmwf", "status": "green", "detail": "Smooth.",
              "affected_points": 0, "total_points": 5, "affected_pct": 0.0,
              "affected_nm": 0.0, "total_nm": 78.0
            }
          ]
        },
        {
          "advisory_id": "winds_aloft",
          "aggregate_status": "green",
          "aggregate_detail": "Light westerly component, no trip-impacting headwind.",
          "parameters_used": {},
          "per_model": [
            {
              "model": "gfs", "status": "green", "detail": "10 kt headwind.",
              "affected_points": 0, "total_points": 5, "affected_pct": 0.0,
              "affected_nm": 0.0, "total_nm": 78.0
            },
            {
              "model": "ecmwf", "status": "green", "detail": "8 kt headwind.",
              "affected_points": 0, "total_points": 5, "affected_pct": 0.0,
              "affected_nm": 0.0, "total_nm": 78.0
            }
          ]
        }
      ],
      "catalog": [
        {
          "id": "convective", "name": "Convective", "short_description": "Thunderstorm / instability risk",
          "description": "Flags convective potential from model CAPE and native convective cloud fields, graded by the worst realizable scenario along the route.",
          "category": "convective", "default_enabled": true, "altitude_dependent": false, "parameters": []
        },
        {
          "id": "icing", "name": "Icing", "short_description": "Airframe icing risk",
          "description": "Airframe icing risk from freezing level, cloud layers, and Ogimet/NWP icing indices, gated to in-cloud layers.",
          "category": "icing", "default_enabled": true, "altitude_dependent": true, "parameters": []
        },
        {
          "id": "turbulence", "name": "Turbulence", "short_description": "En-route turbulence",
          "description": "Clear-air and mechanical turbulence risk from vertical motion and terrain-driven wind.",
          "category": "turbulence", "default_enabled": true, "altitude_dependent": true, "parameters": []
        },
        {
          "id": "winds_aloft", "name": "Winds Aloft", "short_description": "Trip-impacting wind",
          "description": "Winds-aloft trip impact from the headwind/tailwind component along the planned route and altitude.",
          "category": "winds", "default_enabled": true, "altitude_dependent": true, "parameters": []
        }
      ],
      "route_name": "LFMD LFML",
      "cruise_altitude_ft": 8000,
      "flight_ceiling_ft": 13000,
      "total_distance_nm": 78.0,
      "models": ["ecmwf", "gfs"],
      "aggregation": "worst",
      "airport_conditions": {
        "departure": {
          "icao": "LFMD", "name": "Cannes-Mandelieu",
          "runway_ends": [ { "id": "17", "heading_deg": 165.0 }, { "id": "35", "heading_deg": 345.0 } ],
          "conditions": [
            {
              "model": "ecmwf", "flight_category": "VFR", "ceiling_ft": 4500, "visibility_sm": 9.0,
              "wind_speed_kt": 8.0, "wind_direction_deg": 200.0, "wind_gust_kt": null,
              "best_runway": { "runway_id": "17", "heading_deg": 165.0, "crosswind_kt": 4.0, "headwind_kt": 7.0 },
              "all_runways": [
                { "runway_id": "17", "heading_deg": 165.0, "crosswind_kt": 4.0, "headwind_kt": 7.0 },
                { "runway_id": "35", "heading_deg": 345.0, "crosswind_kt": 4.0, "headwind_kt": -7.0 }
              ]
            }
          ]
        },
        "arrival": {
          "icao": "LFML", "name": "Marseille Provence",
          "runway_ends": [ { "id": "13L", "heading_deg": 130.0 }, { "id": "31R", "heading_deg": 310.0 } ],
          "conditions": [
            {
              "model": "ecmwf", "flight_category": "VFR", "ceiling_ft": 3800, "visibility_sm": 8.0,
              "wind_speed_kt": 12.0, "wind_direction_deg": 300.0, "wind_gust_kt": 18.0,
              "best_runway": { "runway_id": "31R", "heading_deg": 310.0, "crosswind_kt": 6.0, "headwind_kt": 10.0 },
              "all_runways": [
                { "runway_id": "31R", "heading_deg": 310.0, "crosswind_kt": 6.0, "headwind_kt": 10.0 },
                { "runway_id": "13L", "heading_deg": 130.0, "crosswind_kt": 6.0, "headwind_kt": -10.0 }
              ]
            }
          ]
        }
      }
    }
    """)

    // MARK: - Advisory drill-down (convective "why it's RED")

    static let convectiveDetail: AdvisoryDetailResponse = decode(AdvisoryDetailResponse.self, """
    {
      "advisory_id": "convective",
      "aggregate_status": "red",
      "aggregate_detail": "Isolated convective cell near LFMD — GFS builds CAPE to ~1400 J/kg by mid-morning.",
      "parameters_used": { "cape_red_jkg": 1000.0, "affected_pct_red": 25.0, "el_top_ft": 34000.0 },
      "cross_check_note": "This is an explainer, not an alert: the RED is driven by realizable CAPE, even though the model paints a nearly clear sky. Watch the timing — instability grows through the morning.",
      "name": "Convective",
      "category": "convective",
      "description": "Convective potential from model CAPE and native convective cloud fields, graded by the worst realizable scenario along the route.",
      "convective_note": "GFS and ECMWF disagree on magnitude; treat the coast near Cannes as the focus area.",
      "per_model": [
        {
          "model": "gfs",
          "status": "red",
          "detail": "CAPE ~1400 J/kg near LFMD, parcel equilibrium level ~FL340.",
          "affected_pct": 40.0,
          "affected_nm": 20.0,
          "total_nm": 78.0,
          "cross_check": "High CAPE (~1400 J/kg) while the model's own convective cover is ~2% — a quiet-sky thermodynamic RED."
        },
        {
          "model": "ecmwf",
          "status": "amber",
          "detail": "Marginal instability; CAPE ~600 J/kg, tops near FL240.",
          "affected_pct": 20.0,
          "affected_nm": 8.0,
          "total_nm": 78.0
        }
      ],
      "convective": {
        "gfs": {
          "assessment_method": "thermo",
          "method_counts": { "thermo": 2, "nwp": 0 },
          "thermo": {
            "cape_range_jkg": [300.0, 1400.0],
            "peak": {
              "cape_jkg": 1400.0,
              "el_top_ft": 34000.0,
              "risk_level": "high",
              "distance_nm": 8.0,
              "waypoint_icao": "LFMD",
              "valid_time": "2099-07-01T10:00:00Z",
              "eta": "2099-07-01T09:15:00Z"
            }
          },
          "nwp": { "max_cover_pct": 2.0, "peak_top_ft": null, "note": "Native convective cover ~2% — blue sky." }
        },
        "ecmwf": {
          "assessment_method": "thermo",
          "method_counts": { "thermo": 1, "nwp": 0 },
          "thermo": {
            "cape_range_jkg": [150.0, 600.0],
            "peak": {
              "cape_jkg": 600.0,
              "el_top_ft": 24000.0,
              "risk_level": "moderate",
              "distance_nm": 12.0,
              "waypoint_icao": "LFMD",
              "valid_time": "2099-07-01T10:00:00Z",
              "eta": "2099-07-01T09:18:00Z"
            }
          },
          "nwp": { "max_cover_pct": 1.0, "peak_top_ft": null, "note": "Native convective cover ~1%." }
        }
      }
    }
    """)

    // MARK: - Digest

    static let digest: DigestResponse = decode(DigestResponse.self, """
    {
      "assessment": "amber",
      "assessment_reason": "VFR feasible with attention to the afternoon convective build-up near the coast.",
      "synoptic": "A weak upper trough drifts across the Côte d'Azur, steepening lapse rates through the morning. Surface flow is a light onshore westerly with a shallow marine layer along the Marseille approach.",
      "specific_concerns": "Isolated convective cells could pop near Cannes after 10Z. Keep a diversion plan for the coastal leg.",
      "winds": "Light westerly aloft, 8–12 kt at cruise. No trip-impacting headwind on the direct route.",
      "icing": "A thin band of light rime is possible where the cruise level clips coastal stratiform cloud between 6,000 and 9,000 ft — GFS flags it, ECMWF keeps the freezing level above the tops.",
      "turbulence": "Smooth away from any convective cell. Expect light mechanical turbulence on the Marseille approach in the westerly.",
      "precipitation": "Dry for most of the route; a brief shower is possible under any cell that develops near LFMD.",
      "visibility": "Good, 8–10 SM, reducing only briefly in a shower.",
      "trend": "Instability increases through the afternoon — an earlier departure keeps the convective risk lower.",
      "model_agreement": "Good on winds and visibility; GFS is more bullish than ECMWF on convective CAPE near the coast.",
      "recommendations": "Depart on the earlier side, brief a coastal diversion, and re-check the D-0 pack for cell initiation.",
      "watch_items": [
        "Convective initiation near LFMD after 10Z",
        "Marine layer ceiling on the LFML approach",
        "Light rime in coastal cloud at cruise"
      ]
    }
    """)

    // MARK: - Snapshot (route + waypoint analyses + observations)

    /// A complete version-1 raw envelope used only by the DEBUG/UI-test
    /// repository. It deliberately crosses the same raw boundary as production
    /// rather than constructing a lossy typed DTO, and retains an unknown key to
    /// keep that boundary visible in fixture-backed journeys.
    static let observedMotion = RawObservedMotion(rawJSON: Data(#"""
    {
      "schema_version":1,"status":"available","reason_codes":[],"revision":1,
      "run_id":"fixture-motion-run-1","route_geometry_id":"fixture-route-geometry-v1","planned_timing_id":"fixture-timing-v1",
      "computed_at":"2099-06-30T05:56:00Z","cutoff_at":"2099-06-30T05:55:00Z","expires_at":"2099-06-30T06:10:00Z",
      "method_version":"masked_contour_translation_v1","policy_version":"observed_motion_policy_v1",
      "analysis_domain":{"center":[6.0,43.5],"crs":"+proj=aeqd +lat_0=43.5 +lon_0=6.0 +datum=WGS84 +units=m +no_defs","cell_size_m":2000,"width_cells":100,"height_cells":100,"origin_x_m":-100000,"origin_y_m":-100000,"bounds":[5.5,43.0,6.5,44.0],"reason_codes":[]},
      "sources":[
        {"source_id":"radar","status":"available","reason_codes":[],"frames":[
          {"frame_id":"radar-frame-1","content_id":"radar-content-1","product_id":"DBZH","decoder_version":"fixture-1","grid_id":"fixture-grid","valid_at":"2099-06-30T05:35:00Z","received_at":"2099-06-30T05:35:00Z","acquisition_window":{"start_at":"2099-06-30T05:35:00Z","end_at":"2099-06-30T05:35:00Z"},"reference_at":"2099-06-30T05:35:00Z"},
          {"frame_id":"radar-frame-2","content_id":"radar-content-2","product_id":"DBZH","decoder_version":"fixture-1","grid_id":"fixture-grid","valid_at":"2099-06-30T05:45:00Z","received_at":"2099-06-30T05:45:00Z","acquisition_window":{"start_at":"2099-06-30T05:45:00Z","end_at":"2099-06-30T05:45:00Z"},"reference_at":"2099-06-30T05:45:00Z"},
          {"frame_id":"radar-frame-3","content_id":"radar-content-3","product_id":"DBZH","decoder_version":"fixture-1","grid_id":"fixture-grid","valid_at":"2099-06-30T05:55:00Z","received_at":"2099-06-30T05:55:00Z","acquisition_window":{"start_at":"2099-06-30T05:55:00Z","end_at":"2099-06-30T05:55:00Z"},"reference_at":"2099-06-30T05:55:00Z"}
        ],"gaps":[],"attribution":"Fixture radar","coverage":{"status":"available","reason_codes":[],"scope":"analysis_domain","known_cells":100,"total_cells":100,"known_fraction":1},"geolocation":{"status":"validated","reason_codes":[],"evidence_id":"fixture-geo","method_version":"fixture-registration-v1","applicability_id":"fixture-domain"}},
        {"source_id":"rate","status":"available","reason_codes":[],"frames":[
          {"frame_id":"rate-frame-1","content_id":"rate-content-1","product_id":"RATE","decoder_version":"fixture-1","grid_id":"fixture-grid","valid_at":"2099-06-30T05:54:00Z","received_at":"2099-06-30T05:54:30Z","acquisition_window":{"start_at":"2099-06-30T05:53:00Z","end_at":"2099-06-30T05:54:00Z"},"reference_at":"2099-06-30T05:54:00Z"}
        ],"gaps":[],"attribution":"Fixture rate","coverage":{"status":"available","reason_codes":[],"scope":"analysis_domain","known_cells":100,"total_cells":100,"known_fraction":1},"geolocation":{"status":"validated","reason_codes":[],"evidence_id":"fixture-geo","method_version":"fixture-registration-v1","applicability_id":"fixture-domain"}}
      ],
      "features":[{
        "feature_id":"fixture-radar-1","source_id":"radar","family":"radar_echo","definition":{"quantity":"reflectivity","operator":"gte","threshold":5,"unit":"dBZ"},
        "reference_at":"2099-06-30T05:55:00Z","reference_frame_id":"radar-frame-3","frame_ids":["radar-frame-1","radar-frame-2","radar-frame-3"],
        "display_geometry":{"status":"available","reason_codes":[],"geometry":{"type":"MultiPolygon","coordinates":[[[[5.8,43.3],[6.2,43.3],[6.2,43.7],[5.8,43.7],[5.8,43.3]],[[5.9,43.4],[6.1,43.4],[6.1,43.6],[5.9,43.6],[5.9,43.4]]]]},"provenance":"grid_contour","simplification_tolerance_m":500},
        "trail":[{"frame_id":"radar-frame-1","observed_at":"2099-06-30T05:35:00Z","center":[5.8,43.5]},{"frame_id":"radar-frame-2","observed_at":"2099-06-30T05:45:00Z","center":[5.9,43.5]},{"frame_id":"radar-frame-3","observed_at":"2099-06-30T05:55:00Z","center":[6.0,43.5]}],
        "observations":[{"kind":"rain_rate_max","status":"available","reason_codes":[],"value":3.5,"unit":"mm_h","source_id":"rate","frame_id":"rate-frame-1","observed_at":"2099-06-30T05:54:00Z","comparison_at":"2099-06-30T05:54:00Z","acquisition_window":{"start_at":"2099-06-30T05:53:00Z","end_at":"2099-06-30T05:54:00Z"},"alignment_method":"observed","sample_id":"fixture-rate-sample","sample_position":[6.0,43.5],"paired_temperature_k":null,"coverage":{"status":"available","reason_codes":[],"scope":"feature_contour","known_cells":20,"total_cells":20,"known_fraction":1}}],
        "lightning_evidence":{"status":"unavailable","reason_codes":["fixture_no_lightning"],"source_id":null,"frame_ids":[],"evaluated_window":null,"reported_detection_count":null,"emitted_marker_count":0,"evaluation_complete":false},
        "coverage":{"status":"available","reason_codes":[],"scope":"feature_contour","known_cells":20,"total_cells":20,"known_fraction":1},"geolocation":{"status":"validated","reason_codes":[],"evidence_id":"fixture-geo","method_version":"fixture-registration-v1","applicability_id":"fixture-domain"},
        "motion":{"status":"accepted","reason_codes":[],"ground_speed_kt":18,"bearing_deg_true":90,"velocity_reference_point":[6.0,43.5],"velocity_method":"inverse_aeqd_geodesic_1s","pair_diagnostics":[{"from_frame_id":"radar-frame-1","to_frame_id":"radar-frame-2","elapsed_seconds":600,"status":"available","reason_codes":[],"patches":[
          {"direction":"forward","center_column":10,"center_row":20,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":1,"dy_cells":0,"refinement":"quadratic"},
          {"direction":"forward","center_column":13,"center_row":23,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":1,"dy_cells":0,"refinement":"quadratic"},
          {"direction":"reverse","center_column":10,"center_row":20,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":-1,"dy_cells":0,"refinement":"quadratic"},
          {"direction":"reverse","center_column":13,"center_row":23,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":-1,"dy_cells":0,"refinement":"quadratic"}
        ],"forward_dx_cells":1,"forward_dy_cells":0,"patch_disagreement_cells":0,"reverse_residual_cells":0,"next_observation_residual_cells":0.2,"common_support_iou":0.8,"area_ratio":1,"plausible_parent_count":1,"plausible_child_count":1,"lineage_complete":true},{"from_frame_id":"radar-frame-2","to_frame_id":"radar-frame-3","elapsed_seconds":600,"status":"available","reason_codes":[],"patches":[
          {"direction":"forward","center_column":10,"center_row":20,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":1,"dy_cells":0,"refinement":"quadratic"},
          {"direction":"forward","center_column":13,"center_row":23,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":1,"dy_cells":0,"refinement":"quadratic"},
          {"direction":"reverse","center_column":10,"center_row":20,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":-1,"dy_cells":0,"refinement":"quadratic"},
          {"direction":"reverse","center_column":13,"center_row":23,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":-1,"dy_cells":0,"refinement":"quadratic"}
        ],"forward_dx_cells":1,"forward_dy_cells":0,"patch_disagreement_cells":0,"reverse_residual_cells":0,"next_observation_residual_cells":null,"common_support_iou":0.8,"area_ratio":1,"plausible_parent_count":1,"plausible_child_count":1,"lineage_complete":true}],"fit_rms_residual_cells":0.2},
        "projection_end_at":"2099-06-30T06:10:00Z","projections":[{"at":"2099-06-30T06:00:00Z","status":"available","reason_codes":[],"display_geometry":{"status":"available","reason_codes":["projected_translation"],"geometry":{"type":"MultiPolygon","coordinates":[[[[5.85,43.3],[6.25,43.3],[6.25,43.7],[5.85,43.7],[5.85,43.3]],[[5.95,43.4],[6.15,43.4],[6.15,43.6],[5.95,43.6],[5.95,43.4]]]]},"provenance":"grid_contour","simplification_tolerance_m":500}},{"at":"2099-06-30T06:05:00Z","status":"available","reason_codes":[],"display_geometry":{"status":"available","reason_codes":["projected_translation"],"geometry":{"type":"MultiPolygon","coordinates":[[[[5.9,43.3],[6.3,43.3],[6.3,43.7],[5.9,43.7],[5.9,43.3]],[[6.0,43.4],[6.2,43.4],[6.2,43.6],[6.0,43.6],[6.0,43.4]]]]},"provenance":"grid_contour","simplification_tolerance_m":500}},{"at":"2099-06-30T06:10:00Z","status":"available","reason_codes":[],"display_geometry":{"status":"available","reason_codes":["projected_translation"],"geometry":{"type":"MultiPolygon","coordinates":[[[[5.95,43.3],[6.35,43.3],[6.35,43.7],[5.95,43.7],[5.95,43.3]],[[6.05,43.4],[6.25,43.4],[6.25,43.6],[6.05,43.6],[6.05,43.4]]]]},"provenance":"grid_contour","simplification_tolerance_m":500}}],
        "route_rows":[{"leg_id":"fixture-route:0","leg_index":0,"from_label":"LFMD","to_label":"LFML","at":"2099-06-30T06:00:00Z","status":"available","reason_codes":[],"distance_nm":4.2,"closure_kt":7,"closure_interval":{"start_at":"2099-06-30T05:59:30Z","end_at":"2099-06-30T06:00:30Z"},"relationship":"approaching","planned_time_method":"distance_proportional_planned","planned_time_status":"available","planned_time_reason_codes":[],"planned_overlap_at_time":false}],
        "planned_overlap":{"status":"available","reason_codes":[],"method":"relative_segment_contour_intersection","planned_time_method":"distance_proportional_planned","evaluated_interval":{"start_at":"2099-06-30T05:55:00Z","end_at":"2099-06-30T06:10:00Z"},"intervals":[{"leg_id":"fixture-route:0","leg_index":0,"start_at":"2099-06-30T06:02:00Z","end_at":"2099-06-30T06:04:00Z","contact":"interval","approximate":true}],"complete":true},"reason_codes":[]
      }],
      "associations":[],"lightning":[],"projection_times":["2099-06-30T06:00:00Z","2099-06-30T06:05:00Z","2099-06-30T06:10:00Z"],
      "completeness":[
        {"category":"regions","status":"complete","reason_codes":[],"considered_count":1,"emitted_count":1,"omitted_count":0},
        {"category":"input_frames","status":"complete","reason_codes":[],"considered_count":3,"emitted_count":3,"omitted_count":0},
        {"category":"small_detections","status":"complete","reason_codes":[],"considered_count":0,"emitted_count":0,"omitted_count":0},
        {"category":"candidates","status":"complete","reason_codes":[],"considered_count":1,"emitted_count":1,"omitted_count":0},
        {"category":"features","status":"complete","reason_codes":[],"considered_count":1,"emitted_count":1,"omitted_count":0},
        {"category":"geometry","status":"complete","reason_codes":[],"considered_count":4,"emitted_count":4,"omitted_count":0},
        {"category":"associations","status":"complete","reason_codes":[],"considered_count":0,"emitted_count":0,"omitted_count":0},
        {"category":"lightning","status":"complete","reason_codes":[],"considered_count":0,"emitted_count":0,"omitted_count":0},
        {"category":"legs","status":"complete","reason_codes":[],"considered_count":1,"emitted_count":1,"omitted_count":0},
        {"category":"route_rows","status":"complete","reason_codes":[],"considered_count":1,"emitted_count":1,"omitted_count":0},
        {"category":"overlap_intervals","status":"complete","reason_codes":[],"considered_count":1,"emitted_count":1,"omitted_count":0}
      ],"Fixture_Unknown_ID":{"Kept":true}
    }
    """#.utf8))

    static func snapshotWithObservedMotion(
        capability: ObservedMotionCapability = .enabled,
        capabilitySequence: Int = 1
    ) -> SnapshotResponse {
        var value = snapshot
        value.observedMotion = observedMotion
        value.observedMotionCapability = capability
        value.observedMotionCapabilitySequence = capabilitySequence
        value.observedMotionOrigin = .network
        return value
    }

    static let snapshot: SnapshotResponse = decode(SnapshotResponse.self, """
    {
      "route": {
        "name": "LFMD LFML",
        "waypoints": [
          { "icao": "LFMD", "name": "Cannes-Mandelieu", "lat": 43.542, "lon": 6.953 },
          { "icao": "LFML", "name": "Marseille Provence", "lat": 43.436, "lon": 5.215 }
        ],
        "cruise_altitude_ft": 8000,
        "flight_ceiling_ft": 13000,
        "flight_duration_hours": 1.5
      },
      "target_date": "2099-07-01",
      "days_out": 1,
      "departure_time": "2099-07-01T09:00:00Z",
      "analyses": [
        {
          "waypoint": { "icao": "LFMD", "name": "Cannes-Mandelieu", "lat": 43.542, "lon": 6.953 },
          "target_time": "2099-07-01T09:00:00Z",
          "wind_components": {
            "ecmwf": { "wind_speed_kt": 8.0, "wind_direction_deg": 200.0, "track_deg": 262.0, "headwind_kt": 7.0, "crosswind_kt": 4.0 },
            "gfs": { "wind_speed_kt": 10.0, "wind_direction_deg": 210.0, "track_deg": 262.0, "headwind_kt": 8.0, "crosswind_kt": 5.0 }
          },
          "sounding": {
            "ecmwf": { "indices": { "freezing_level_ft": 11500.0, "cape_surface_jkg": 600.0, "sounding_ceiling_ft": 4500.0, "nwp_ceiling_ft": 4300.0 }, "cloud_cover_low_pct": 30.0, "cloud_cover_mid_pct": 20.0, "cloud_cover_high_pct": 10.0 },
            "gfs": { "indices": { "freezing_level_ft": 9000.0, "cape_surface_jkg": 1400.0, "sounding_ceiling_ft": 4300.0, "nwp_ceiling_ft": 4200.0 }, "cloud_cover_low_pct": 35.0, "cloud_cover_mid_pct": 25.0, "cloud_cover_high_pct": 15.0 }
          }
        },
        {
          "waypoint": { "icao": "LFML", "name": "Marseille Provence", "lat": 43.436, "lon": 5.215 },
          "target_time": "2099-07-01T10:30:00Z",
          "wind_components": {
            "ecmwf": { "wind_speed_kt": 12.0, "wind_direction_deg": 300.0, "track_deg": 262.0, "headwind_kt": 10.0, "crosswind_kt": 6.0 },
            "gfs": { "wind_speed_kt": 13.0, "wind_direction_deg": 305.0, "track_deg": 262.0, "headwind_kt": 11.0, "crosswind_kt": 6.0 }
          },
          "sounding": {
            "ecmwf": { "indices": { "freezing_level_ft": 11800.0, "cape_surface_jkg": 200.0, "sounding_ceiling_ft": 3800.0, "nwp_ceiling_ft": 3600.0 }, "cloud_cover_low_pct": 40.0, "cloud_cover_mid_pct": 15.0, "cloud_cover_high_pct": 5.0 },
            "gfs": { "indices": { "freezing_level_ft": 10500.0, "cape_surface_jkg": 350.0, "sounding_ceiling_ft": 3600.0, "nwp_ceiling_ft": 3500.0 }, "cloud_cover_low_pct": 45.0, "cloud_cover_mid_pct": 20.0, "cloud_cover_high_pct": 5.0 }
          }
        }
      ],
      "route_observations": {
        "corridor_nm": 15.0,
        "fetch_time": "2099-06-30T05:50:00Z",
        "airports_found": 3,
        "airports_with_metar": 3,
        "airports_with_taf": 3,
        "worst_metar_category": "IFR",
        "worst_taf_category": "IFR",
        "has_conflicts": true,
        "phenomena_along_route": ["TSRA", "BR"],
        "airports": [
          {
            "icao": "LFMD", "name": "Cannes-Mandelieu", "distance_from_route_nm": 0.0,
            "enroute_distance_nm": 0.0, "nearest_waypoint_icao": "LFMD",
            "metar_raw": "LFMD 300550Z 20008KT 9999 FEW045 SCT090 21/15 Q1016",
            "metar_time": "2099-06-30T05:50:00Z",
            "metar_flight_category": "VFR", "metar_ceiling_ft": 9000, "metar_visibility_m": 9999,
            "metar_wind_dir": 200, "metar_wind_speed_kt": 8,
            "metar_wind_gust_kt": null, "metar_weather": [], "metar_temperature_c": 21, "metar_dewpoint_c": 15,
            "metar_qnh": 1016.0,
            "taf_raw": "LFMD 300500Z 3006/3018 20010KT 9999 SCT040\\nTEMPO 3012/3016 VRB15G25KT 4000 TSRA BKN030CB",
            "taf_flight_category_at_eta": "MVFR", "taf_trend_type": "TEMPO",
            "taf_wind_dir": 200, "taf_wind_speed_kt": 10, "taf_wind_gust_kt": null,
            "taf_applicable_lines": [0, 1],
            "metar_wind_advisory": "green", "metar_best_runway_id": "17", "metar_crosswind_kt": 3.0,
            "metar_headwind_kt": 7.4,
            "taf_wind_advisory": "green", "taf_best_runway_id": "17", "taf_crosswind_kt": 4.0,
            "taf_headwind_kt": 9.2,
            "has_metar": true, "has_taf": true, "eta_hour_offset": 0
          },
          {
            "icao": "LFML", "name": "Marseille Provence", "distance_from_route_nm": 2.0,
            "enroute_distance_nm": 78.0, "nearest_waypoint_icao": "LFML",
            "metar_raw": "LFML 300600Z 30012G18KT 9999 BKN038 20/13 Q1017",
            "metar_time": "2099-06-30T06:00:00Z",
            "metar_flight_category": "VFR", "metar_ceiling_ft": 3800, "metar_visibility_m": 9999,
            "metar_wind_dir": 300, "metar_wind_speed_kt": 12,
            "metar_wind_gust_kt": 18, "metar_weather": [], "metar_temperature_c": 20, "metar_dewpoint_c": 13,
            "metar_qnh": 1017.0,
            "taf_raw": "LFML 300500Z 3006/3018 30012KT 9999 BKN035",
            "taf_flight_category_at_eta": "VFR", "taf_trend_type": null,
            "taf_wind_dir": 300, "taf_wind_speed_kt": 12, "taf_wind_gust_kt": null,
            "taf_applicable_lines": [0],
            "metar_wind_advisory": "amber", "metar_best_runway_id": "31L", "metar_crosswind_kt": 11.0,
            "metar_headwind_kt": 4.8,
            "taf_wind_advisory": "green", "taf_best_runway_id": "31L", "taf_crosswind_kt": 6.0,
            "taf_headwind_kt": 10.4,
            "has_metar": true, "has_taf": true, "eta_hour_offset": 1
          },
          {
            "icao": "LFTH", "name": "Toulon-Hyeres", "distance_from_route_nm": 11.0,
            "enroute_distance_nm": 41.0, "nearest_waypoint_icao": "LFTH",
            "metar_raw": "LFTH 300600Z 09018G28KT 2500 BR OVC008 17/16 Q1015",
            "metar_time": "2099-06-30T06:00:00Z",
            "metar_flight_category": "IFR", "metar_ceiling_ft": 800, "metar_visibility_m": 2500,
            "metar_wind_dir": 90, "metar_wind_speed_kt": 18,
            "metar_wind_gust_kt": 28, "metar_weather": ["BR"], "metar_temperature_c": 17, "metar_dewpoint_c": 16,
            "metar_qnh": 1015.0,
            "taf_raw": "LFTH 300500Z 3006/3018 09018G28KT 3000 BR OVC010\\nBECMG 3009/3011 09012KT 9999 SCT025",
            "taf_flight_category_at_eta": "IFR", "taf_trend_type": "BECMG",
            "taf_wind_dir": 90, "taf_wind_speed_kt": 18, "taf_wind_gust_kt": 28,
            "taf_applicable_lines": [0],
            "metar_wind_advisory": "red", "metar_best_runway_id": "05", "metar_crosswind_kt": 17.0,
            "metar_headwind_kt": 6.1,
            "taf_wind_advisory": "red", "taf_best_runway_id": "05", "taf_crosswind_kt": 17.0,
            "taf_headwind_kt": 6.1,
            "has_metar": true, "has_taf": true, "eta_hour_offset": 1
          }
        ],
        "comparisons": [
          {
            "icao": "LFMD", "obs_category": "VFR", "model_category": "VFR",
            "category_match": "CONFIRMING",
            "ceiling_delta_ft": -400, "visibility_delta_m": 0.0, "wind_speed_delta_kt": 1.0,
            "model_wind_dir": 205.0, "model_wind_speed_kt": 9.0, "model_wind_gust_kt": null,
            "model_wind_advisory": "green", "model_best_runway_id": "17", "model_crosswind_kt": 4.0,
            "wind_advisory_match": "CONFIRMING",
            "detail": "Model agrees with the observation."
          },
          {
            "icao": "LFML", "obs_category": "VFR", "model_category": "MVFR",
            "category_match": "SIGNIFICANT",
            "ceiling_delta_ft": 900, "visibility_delta_m": -1500.0, "wind_speed_delta_kt": -2.0,
            "model_wind_dir": 295.0, "model_wind_speed_kt": 10.0, "model_wind_gust_kt": null,
            "model_wind_advisory": "green", "model_best_runway_id": "31L", "model_crosswind_kt": 5.0,
            "wind_advisory_match": "SIGNIFICANT",
            "detail": "Model is one category more pessimistic than the METAR (ceiling 2900ft vs 3800ft observed)."
          },
          {
            "icao": "LFTH", "obs_category": "IFR", "model_category": "VFR",
            "category_match": "CONFLICTING",
            "ceiling_delta_ft": 3200, "visibility_delta_m": 7499.0, "wind_speed_delta_kt": -8.0,
            "model_wind_dir": 100.0, "model_wind_speed_kt": 10.0, "model_wind_gust_kt": null,
            "model_wind_advisory": "amber", "model_best_runway_id": "05", "model_crosswind_kt": 9.0,
            "wind_advisory_match": "CONFLICTING",
            "detail": "Model shows VFR but the METAR reports OVC008 in mist — the model is missing a shallow coastal stratus/fog layer."
          }
        ]
      },
      "route_sigmets": {
        "corridor_nm": 50.0,
        "fetch_time": "2099-06-30T05:52:00Z",
        "altitude_low_ft": 0,
        "altitude_high_ft": 13000,
        "time_window_from": "2099-06-30T05:52:00Z",
        "time_window_to": "2099-06-30T23:59:59Z",
        "route_firs": ["LFMM", "LFFF"],
        "count": 2,
        "hazards": ["TS", "TURB"],
        "has_severe": true,
        "sigmets": [
          {
            "fir_id": "LFMM", "fir_name": "LFMM MARSEILLE", "hazard": "TS", "qualifier": "EMBD",
            "base_ft": null, "top_ft": 37000,
            "valid_from": "2099-06-30T05:00:00Z", "valid_to": "2099-06-30T09:00:00Z",
            "direction": "NE", "speed_kt": 25,
            "raw_text": "WSFR31 LFPW 300452\\nLFMM SIGMET T03 VALID 300500/300900 LFPW-\\nLFMM MARSEILLE FIR EMBD TS OBS WI N4330 E00530 - N4345 E00700 - N4300\\nE00715 - N4330 E00530 TOP FL370 MOV NE 25KT NC=",
            "matched_firs": ["LFMM"],
            "min_distance_nm": 4.2,
            "enroute_distance_from_nm": 12.0, "enroute_distance_to_nm": 48.0,
            "coords": [[5.5, 43.5], [7.0, 43.75], [7.25, 43.0], [5.5, 43.5]]
          },
          {
            "fir_id": "LFMM", "fir_name": "LFMM MARSEILLE", "hazard": "TURB", "qualifier": "SEV",
            "base_ft": 5000, "top_ft": 18000,
            "valid_from": "2099-06-30T06:00:00Z", "valid_to": "2099-06-30T12:00:00Z",
            "direction": null, "speed_kt": null,
            "raw_text": "WSFR31 LFPW 300551\\nLFMM SIGMET T04 VALID 300600/301200 LFPW-\\nLFMM MARSEILLE FIR SEV TURB FCST WI N4315 E00445 - N4400 E00600 -\\nN4245 E00615 - N4315 E00445 SFC/FL180 STNR NC=",
            "matched_firs": ["LFMM"],
            "min_distance_nm": 0.0,
            "enroute_distance_from_nm": 30.0, "enroute_distance_to_nm": 78.0,
            "coords": [[4.75, 43.25], [6.0, 44.0], [6.25, 42.75], [4.75, 43.25]]
          }
        ]
      }
    }
    """)

    // MARK: - Route analyses (5 points × ecmwf/gfs — drives the cross-section)

    static let routeAnalyses: RouteAnalysesResponse = decode(RouteAnalysesResponse.self, routeAnalysesJSON)

    // MARK: - Elevation (terrain profile)

    static let elevation: ElevationResponse = decode(ElevationResponse.self, """
    {
      "route_name": "LFMD LFML",
      "max_elevation_ft": 2400.0,
      "total_distance_nm": 78.0,
      "points": [
        { "distance_nm": 0.0, "elevation_ft": 12.0, "lat": 43.542, "lon": 6.953 },
        { "distance_nm": 20.0, "elevation_ft": 1800.0, "lat": 43.516, "lon": 6.520 },
        { "distance_nm": 39.0, "elevation_ft": 2400.0, "lat": 43.489, "lon": 6.084 },
        { "distance_nm": 59.0, "elevation_ft": 900.0, "lat": 43.463, "lon": 5.649 },
        { "distance_nm": 78.0, "elevation_ft": 74.0, "lat": 43.436, "lon": 5.215 }
      ]
    }
    """)
}
#endif
