import Foundation
import MapKit
import Testing
@testable import flyfun_weather

func observedMotionFixture(
    revision: Int,
    status: String = "available",
    routeGeometryID: String = "route-1",
    runID: String? = "run-1",
    computedAt: String = "2026-09-05T10:04:00Z",
    cutoffAt: String = "2026-09-05T10:00:00Z",
    expiresAt: String? = "2026-09-05T10:15:00Z",
    extraRoot: String = ""
) -> RawObservedMotion {
    let available = status == "available"
    let run = runID.map { "\"\($0)\"" } ?? "null"
    let expiry = expiresAt.map { "\"\($0)\"" } ?? "null"
    let features = available ? #"""
    [{
      "feature_id":"radar-1","source_id":"radar","family":"radar_echo",
      "definition":{"quantity":"reflectivity","operator":"gte","threshold":5,"unit":"dBZ"},
      "reference_at":"2026-09-05T10:00:00Z","reference_frame_id":"frame-3",
      "frame_ids":["frame-1","frame-2","frame-3"],
      "display_geometry":{"status":"available","reason_codes":[],"geometry":{"type":"MultiPolygon","coordinates":[[[[1,50],[2,50],[2,51],[1,51],[1,50]],[[1.2,50.2],[1.8,50.2],[1.8,50.8],[1.2,50.8],[1.2,50.2]]]]},"provenance":"grid_contour","simplification_tolerance_m":500},
      "trail":[{"frame_id":"frame-1","observed_at":"2026-09-05T09:40:00Z","center":[1.2,50.3]},{"frame_id":"frame-2","observed_at":"2026-09-05T09:50:00Z","center":[1.3,50.4]},{"frame_id":"frame-3","observed_at":"2026-09-05T10:00:00Z","center":[1.5,50.5]}],
      "observations":[{"kind":"rain_rate_max","status":"available","reason_codes":[],"value":3.5,"unit":"mm_h","source_id":"rate","frame_id":"rate-1","observed_at":"2026-09-05T09:59:00Z","comparison_at":"2026-09-05T09:59:00Z","acquisition_window":{"start_at":"2026-09-05T09:59:00Z","end_at":"2026-09-05T09:59:00Z"},"alignment_method":"observed","sample_id":"rate-sample","sample_position":[1.5,50.5],"paired_temperature_k":null,"coverage":{"status":"available","reason_codes":[],"scope":"feature_contour","known_cells":20,"total_cells":20,"known_fraction":1}}],
      "lightning_evidence":{"status":"unavailable","reason_codes":["missing_source"],"source_id":null,"frame_ids":[],"evaluated_window":null,"reported_detection_count":null,"emitted_marker_count":0,"evaluation_complete":false},
      "coverage":{"status":"available","reason_codes":[],"scope":"feature_contour","known_cells":20,"total_cells":20,"known_fraction":1},
      "geolocation":{"status":"validated","reason_codes":[],"evidence_id":"geo-1","method_version":"geo-v1","applicability_id":"grid-1"},
      "motion":{"status":"accepted","reason_codes":[],"ground_speed_kt":18,"bearing_deg_true":90,"velocity_reference_point":[1.5,50.5],"velocity_method":"inverse_aeqd_geodesic_1s","pair_diagnostics":[{"from_frame_id":"frame-1","to_frame_id":"frame-2","elapsed_seconds":600,"status":"available","reason_codes":[],"patches":[{"direction":"forward","center_column":10,"center_row":20,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":1,"dy_cells":0,"refinement":"quadratic"},{"direction":"forward","center_column":13,"center_row":23,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":1,"dy_cells":0,"refinement":"quadratic"},{"direction":"reverse","center_column":10,"center_row":20,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":-1,"dy_cells":0,"refinement":"quadratic"},{"direction":"reverse","center_column":13,"center_row":23,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":-1,"dy_cells":0,"refinement":"quadratic"}],"forward_dx_cells":1,"forward_dy_cells":0,"patch_disagreement_cells":0,"reverse_residual_cells":0,"next_observation_residual_cells":0.2,"common_support_iou":0.8,"area_ratio":1,"plausible_parent_count":1,"plausible_child_count":1,"lineage_complete":true},{"from_frame_id":"frame-2","to_frame_id":"frame-3","elapsed_seconds":600,"status":"available","reason_codes":[],"patches":[{"direction":"forward","center_column":10,"center_row":20,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":1,"dy_cells":0,"refinement":"quadratic"},{"direction":"forward","center_column":13,"center_row":23,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":1,"dy_cells":0,"refinement":"quadratic"},{"direction":"reverse","center_column":10,"center_row":20,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":-1,"dy_cells":0,"refinement":"quadratic"},{"direction":"reverse","center_column":13,"center_row":23,"status":"available","reason_codes":[],"support_fraction":1,"ncc":0.9,"competing_peak_margin":0.2,"dx_cells":-1,"dy_cells":0,"refinement":"quadratic"}],"forward_dx_cells":1,"forward_dy_cells":0,"patch_disagreement_cells":0,"reverse_residual_cells":0,"next_observation_residual_cells":null,"common_support_iou":0.8,"area_ratio":1,"plausible_parent_count":1,"plausible_child_count":1,"lineage_complete":true}],"fit_rms_residual_cells":0.2},
      "projection_end_at":"2026-09-05T10:15:00Z",
      "projections":[{"at":"2026-09-05T10:05:00Z","status":"available","reason_codes":[],"display_geometry":{"status":"available","reason_codes":["projected_translation"],"geometry":{"type":"MultiPolygon","coordinates":[[[[1.05,50],[2.05,50],[2.05,51],[1.05,51],[1.05,50]],[[1.25,50.2],[1.85,50.2],[1.85,50.8],[1.25,50.8],[1.25,50.2]]]]},"provenance":"grid_contour","simplification_tolerance_m":500}},{"at":"2026-09-05T10:10:00Z","status":"available","reason_codes":[],"display_geometry":{"status":"available","reason_codes":["projected_translation"],"geometry":{"type":"MultiPolygon","coordinates":[[[[1.1,50],[2.1,50],[2.1,51],[1.1,51],[1.1,50]],[[1.3,50.2],[1.9,50.2],[1.9,50.8],[1.3,50.8],[1.3,50.2]]]]},"provenance":"grid_contour","simplification_tolerance_m":500}},{"at":"2026-09-05T10:15:00Z","status":"available","reason_codes":[],"display_geometry":{"status":"available","reason_codes":["projected_translation"],"geometry":{"type":"MultiPolygon","coordinates":[[[[1.15,50],[2.15,50],[2.15,51],[1.15,51],[1.15,50]],[[1.35,50.2],[1.95,50.2],[1.95,50.8],[1.35,50.8],[1.35,50.2]]]]},"provenance":"grid_contour","simplification_tolerance_m":500}}],
      "route_rows":[{"leg_id":"route-1:0","leg_index":0,"from_label":"LFMD","to_label":"LFML","at":"2026-09-05T10:05:00Z","status":"available","reason_codes":[],"distance_nm":4.2,"closure_kt":7,"closure_interval":{"start_at":"2026-09-05T10:04:30Z","end_at":"2026-09-05T10:05:30Z"},"relationship":"approaching","planned_time_method":"distance_proportional_planned","planned_time_status":"available","planned_time_reason_codes":[],"planned_overlap_at_time":false}],
      "planned_overlap":{"status":"available","reason_codes":[],"method":"relative_segment_contour_intersection","planned_time_method":"distance_proportional_planned","evaluated_interval":{"start_at":"2026-09-05T10:00:00Z","end_at":"2026-09-05T10:15:00Z"},"intervals":[{"leg_id":"route-1:0","leg_index":0,"start_at":"2026-09-05T10:07:00Z","end_at":"2026-09-05T10:09:00Z","contact":"interval","approximate":true}],"complete":true},
      "reason_codes":[]
    }]
    """# : "[]"
    let projections = available ? #"["2026-09-05T10:05:00Z","2026-09-05T10:10:00Z","2026-09-05T10:15:00Z"]"# : "[]"
    let sources = available ? #"[{"source_id":"radar","status":"available","reason_codes":[],"frames":[{"frame_id":"frame-1","content_id":"c1","product_id":"DBZH","decoder_version":"d1","grid_id":"grid-1","valid_at":"2026-09-05T09:40:00Z","received_at":"2026-09-05T09:40:00Z","acquisition_window":{"start_at":"2026-09-05T09:40:00Z","end_at":"2026-09-05T09:40:00Z"},"reference_at":"2026-09-05T09:40:00Z"},{"frame_id":"frame-2","content_id":"c2","product_id":"DBZH","decoder_version":"d1","grid_id":"grid-1","valid_at":"2026-09-05T09:50:00Z","received_at":"2026-09-05T09:50:00Z","acquisition_window":{"start_at":"2026-09-05T09:50:00Z","end_at":"2026-09-05T09:50:00Z"},"reference_at":"2026-09-05T09:50:00Z"},{"frame_id":"frame-3","content_id":"c3","product_id":"DBZH","decoder_version":"d1","grid_id":"grid-1","valid_at":"2026-09-05T10:00:00Z","received_at":"2026-09-05T10:00:00Z","acquisition_window":{"start_at":"2026-09-05T10:00:00Z","end_at":"2026-09-05T10:00:00Z"},"reference_at":"2026-09-05T10:00:00Z"}],"gaps":[],"attribution":"OPERA","coverage":{"status":"available","reason_codes":[],"scope":"analysis_domain","known_cells":100,"total_cells":100,"known_fraction":1},"geolocation":{"status":"validated","reason_codes":[],"evidence_id":"geo-1","method_version":"geo-v1","applicability_id":"grid-1"}},{"source_id":"rate","status":"available","reason_codes":[],"frames":[{"frame_id":"rate-1","content_id":"rate-c1","product_id":"RATE","decoder_version":"d1","grid_id":"grid-1","valid_at":"2026-09-05T09:59:00Z","received_at":"2026-09-05T09:59:00Z","acquisition_window":{"start_at":"2026-09-05T09:59:00Z","end_at":"2026-09-05T09:59:00Z"},"reference_at":"2026-09-05T09:59:00Z"}],"gaps":[],"attribution":"RATE","coverage":{"status":"available","reason_codes":[],"scope":"analysis_domain","known_cells":100,"total_cells":100,"known_fraction":1},"geolocation":{"status":"validated","reason_codes":[],"evidence_id":"geo-1","method_version":"geo-v1","applicability_id":"grid-1"}}]"# : "[]"
    let domain = available ? #"{"center":[1.5,50.5],"crs":"+proj=aeqd +lat_0=50.5 +lon_0=1.5 +datum=WGS84 +units=m +no_defs","cell_size_m":2000,"width_cells":100,"height_cells":100,"origin_x_m":-100000,"origin_y_m":-100000,"bounds":[1,50,2,51],"reason_codes":[]}"# : "null"
    let reasons = available ? "[]" : #"["compute_failed"]"#
    let completeness = #"["regions","input_frames","small_detections","candidates","features","geometry","associations","lightning","legs","route_rows","overlap_intervals"]"#
    let completenessStatus = status == "disabled" ? "not_evaluated" : "complete"
    let completenessReasons = status == "disabled" ? #"["not_evaluated"]"# : "[]"
    let consideredCount = status == "disabled" ? "null" : "0"
    let omittedCount = status == "disabled" ? "null" : "0"
    let records = completeness.dropFirst().dropLast().split(separator: ",").map {
        "{\"category\":\($0),\"status\":\"\(completenessStatus)\",\"reason_codes\":\(completenessReasons),\"considered_count\":\(consideredCount),\"emitted_count\":0,\"omitted_count\":\(omittedCount)}"
    }.joined(separator: ",")
    let json = """
    {"schema_version":1,"status":"\(status)","reason_codes":\(reasons),"revision":\(revision),"run_id":\(run),"route_geometry_id":"\(routeGeometryID)","planned_timing_id":"timing-1","computed_at":"\(computedAt)","cutoff_at":"\(cutoffAt)","expires_at":\(expiry),"method_version":"masked_contour_translation_v1","policy_version":"observed_motion_policy_v1","analysis_domain":\(domain),"sources":\(sources),"features":\(features),"associations":[],"lightning":[],"projection_times":\(projections),"completeness":[\(records)]\(extraRoot)}
    """
    return RawObservedMotion(rawJSON: Data(json.utf8))
}

private func snapshotFixture(motion: RawObservedMotion?) -> Data {
    let raw = motion.flatMap { String(data: $0.rawJSON, encoding: .utf8) } ?? "null"
    return Data("""
    {"route":{"name":"LFMD LFML","waypoints":[{"icao":"LFMD","name":"Cannes","lat":43.55,"lon":7.02},{"icao":"LFML","name":"Marseille","lat":43.44,"lon":5.21}],"cruise_altitude_ft":8000,"flight_ceiling_ft":13000,"flight_duration_hours":2},"target_date":"2026-09-05","days_out":0,"departure_time":"2026-09-05T10:00:00Z","analyses":null,"route_observations":null,"route_sigmets":null,"observed_conditions":null,"alternates":null,"observed_motion":\(raw),"future_snapshot_key":{"Keep_Spelling":true}}
    """.utf8)
}

/// Authored regression fixtures; Swift execution remains deferred.
private func editedMotionFixture(_ edit: (inout [String: Any]) -> Void) throws -> RawObservedMotion {
    var root = try #require(JSONSerialization.jsonObject(with: observedMotionFixture(revision: 12).rawJSON) as? [String: Any])
    edit(&root)
    return RawObservedMotion(rawJSON: try JSONSerialization.data(withJSONObject: root))
}

private func cloudOnlyUnavailableFixture() throws -> RawObservedMotion {
    try editedMotionFixture { root in
        root["status"] = "unavailable"
        root["reason_codes"] = ["geolocation_unverified"]
        root["expires_at"] = NSNull()
        root["projection_times"] = [String]()
        var source = (root["sources"] as! [[String: Any]])[0]
        let geolocation: [String: Any] = ["status": "unverified", "reason_codes": ["geolocation_unverified"], "evidence_id": NSNull(), "method_version": NSNull(), "applicability_id": NSNull()]
        source["source_id"] = "cloud"
        source["geolocation"] = geolocation
        root["sources"] = [source]
        var feature = (root["features"] as! [[String: Any]])[0]
        feature["source_id"] = "cloud"
        feature["family"] = "high_cloud_top"
        feature["definition"] = ["quantity": "geometric_cloud_top_height", "operator": "gte", "threshold": 4572, "unit": "m_msl"] as [String: Any]
        feature["geolocation"] = geolocation
        feature["motion"] = ["status": "unavailable", "reason_codes": ["geolocation_unverified"], "ground_speed_kt": NSNull(), "bearing_deg_true": NSNull(), "velocity_reference_point": NSNull(), "velocity_method": NSNull(), "pair_diagnostics": [], "fit_rms_residual_cells": NSNull()] as [String: Any]
        feature["projection_end_at"] = NSNull()
        feature["projections"] = [Any]()
        feature["route_rows"] = [Any]()
        feature["planned_overlap"] = ["status": "unavailable", "reason_codes": ["geolocation_unverified"], "method": "relative_segment_contour_intersection", "planned_time_method": "distance_proportional_planned", "evaluated_interval": NSNull(), "intervals": [], "complete": false] as [String: Any]
        var observation = (feature["observations"] as! [[String: Any]])[0]
        observation.merge(["kind": "cloud_top_max", "value": 9000, "unit": "m_msl", "source_id": "cloud", "frame_id": "frame-3", "observed_at": "2026-09-05T10:00:00Z", "comparison_at": "2026-09-05T10:00:00Z", "sample_position": NSNull()] as [String: Any]) { _, new in new }
        observation["acquisition_window"] = ["start_at": "2026-09-05T10:00:00Z", "end_at": "2026-09-05T10:00:00Z"]
        feature["observations"] = [observation]
        root["features"] = [feature]
        root["Future_Context"] = ["keep": true]
    }
}

@Suite("Observed motion raw boundary")
struct ObservedMotionRawTests {
    @Test(arguments: [16, 20]) func acceptedHistoricalMotionRetainsZeroFutureLead(ageMinutes: Int) throws {
        let raw = try editedMotionFixture { root in
            root["cutoff_at"] = "2026-09-05T10:\(ageMinutes):00Z"
            root["computed_at"] = "2026-09-05T10:\(ageMinutes):01Z"
            root["projection_times"] = [String]()
            var features = root["features"] as! [[String: Any]]
            features[0]["projections"] = [Any]()
            features[0]["route_rows"] = [Any]()
            features[0]["planned_overlap"] = ["status": "unavailable", "reason_codes": ["no_future_lead"], "method": "relative_segment_contour_intersection", "planned_time_method": "distance_proportional_planned", "evaluated_interval": NSNull(), "intervals": [], "complete": false] as [String: Any]
            root["features"] = features
        }
        let typed = try #require(raw.typed)
        #expect(typed.projectionTimes.isEmpty)
        #expect(typed.features.first?.motion.groundSpeedKt == 18)
        #expect(typed.features.first?.referenceAt == "2026-09-05T10:00:00Z")
        #expect(typed.expiresAt == "2026-09-05T10:15:00Z")
    }

    @Test func capabilityHeaderAcceptsOnlyExplicitZeroOrOne() throws {
        let url = try #require(URL(string: "https://example.invalid/snapshot"))
        let enabled = try #require(HTTPURLResponse(
            url: url, statusCode: 200, httpVersion: nil,
            headerFields: ["X-Observed-Motion-Enabled": "1"]))
        let disabled = try #require(HTTPURLResponse(
            url: url, statusCode: 200, httpVersion: nil,
            headerFields: ["X-Observed-Motion-Enabled": "0"]))
        let unknown = try #require(HTTPURLResponse(
            url: url, statusCode: 200, httpVersion: nil, headerFields: [:]))
        #expect(APIClient.observedMotionCapability(from: enabled) == .enabled)
        #expect(APIClient.observedMotionCapability(from: disabled) == .disabled)
        #expect(APIClient.observedMotionCapability(from: unknown) == nil)
    }

    @Test func unknownRootFieldSurvivesSnapshotRoundTripWithoutKeyMutation() throws {
        let raw = observedMotionFixture(revision: 8, extraRoot: #", "Future_ID_Key":{"MiXeD_Value":7}"#)
        let snapshot = try SnapshotResponse.decodePreservingObservedMotion(
            from: snapshotFixture(motion: raw), capability: .enabled, origin: .network)
        let extracted = try #require(snapshot.observedMotion)
        #expect(String(decoding: extracted.rawJSON, as: UTF8.self).contains(#""Future_ID_Key":{"MiXeD_Value":7}"#))
        let encoded = try snapshot.encodePreservingObservedMotion()
        let reparsed = try SnapshotResponse.decodePreservingObservedMotion(
            from: encoded, capability: nil, origin: .stored(packTimestamp: "p1"))
        #expect(reparsed.observedMotion?.rawJSON == extracted.rawJSON)
    }

    @Test func malformedMotionDoesNotFailWholeSnapshot() throws {
        let data = Data(String(decoding: snapshotFixture(motion: nil), as: UTF8.self)
            .replacingOccurrences(of: #""observed_motion":null"#, with: #""observed_motion":{"schema_version":1,"revision":"bad"}"#).utf8)
        let snapshot = try SnapshotResponse.decodePreservingObservedMotion(from: data)
        #expect(snapshot.route.name == "LFMD LFML")
        #expect(snapshot.observedMotion?.typed == nil)
        #expect(snapshot.observedMotion?.presentationIssue == .malformed)
    }

    @Test func unsupportedSchemaRetainsRawButDoesNotRender() {
        let raw = RawObservedMotion(rawJSON: Data(#"{"schema_version":2,"status":"available","revision":9,"route_geometry_id":"route-1"}"#.utf8))
        #expect(raw.revision == 9)
        #expect(raw.typed == nil)
        #expect(raw.presentationIssue == .unsupportedSchema)
    }

    @Test func fullVersionOneFixtureValidatesButMalformedAcceptedDiagnosticsDoNotRender() throws {
        let full = observedMotionFixture(revision: 12)
        #expect(full.typed?.features.first?.motion.pairDiagnostics.count == 2)
        let malformed = RawObservedMotion(rawJSON: Data(
            String(decoding: full.rawJSON, as: UTF8.self)
                .replacingOccurrences(of: #""velocity_method":"inverse_aeqd_geodesic_1s"#,
                                      with: #""velocity_method":"client_guess"#).utf8))
        #expect(malformed.typed == nil)
        #expect(malformed.presentationIssue == .malformed)
    }

    @Test func acceptedMotionRejectsContourOperatorOtherThanGTE() {
        let malformed = RawObservedMotion(rawJSON: Data(
            String(decoding: observedMotionFixture(revision: 12).rawJSON, as: UTF8.self)
                .replacingOccurrences(of: #""operator":"gte""#,
                                      with: #""operator":"lte""#).utf8))
        #expect(malformed.typed == nil)
        #expect(malformed.presentationIssue == .malformed)
    }

    @Test func acceptedMotionRejectsMissingContourOperator() {
        let malformed = RawObservedMotion(rawJSON: Data(
            String(decoding: observedMotionFixture(revision: 12).rawJSON, as: UTF8.self)
                .replacingOccurrences(of: #""operator":"gte","#,
                                      with: "").utf8))
        #expect(malformed.typed == nil)
        #expect(malformed.presentationIssue == .malformed)
    }

    @Test func acceptedMotionRejectsOutOfRangeTrueBearing() {
        let malformed = RawObservedMotion(rawJSON: Data(
            String(decoding: observedMotionFixture(revision: 12).rawJSON, as: UTF8.self)
                .replacingOccurrences(of: #""bearing_deg_true":90"#,
                                      with: #""bearing_deg_true":400"#).utf8))
        #expect(malformed.typed == nil)
        #expect(malformed.presentationIssue == .malformed)
    }

    @Test func duplicateAdvertisedProjectionTimeDoesNotProduceTypedMotion() {
        // This catches removal of the v1 contract's sorted-*unique* projection
        // time check: controls must never have two choices for the same instant.
        let duplicate = RawObservedMotion(rawJSON: Data(
            String(decoding: observedMotionFixture(revision: 12).rawJSON, as: UTF8.self)
                .replacingOccurrences(
                    of: #""projection_times":["2026-09-05T10:05:00Z"]"#,
                    with: #""projection_times":["2026-09-05T10:05:00Z","2026-09-05T10:05:00Z"]"#)
                .utf8))
        #expect(duplicate.typed == nil)
        #expect(duplicate.presentationIssue == .malformed)
    }

    @Test func unavailableEnvelopeCannotContainAcceptedMotion() throws {
        // This catches accepting a completed unavailable result as an active
        // prediction merely because it retains inspectable observed features.
        var raw = observedMotionFixture(revision: 12).rawJSON
        raw = try RawJSONDocument(raw).replacingMember(
            named: "status", with: Data(#""unavailable""#.utf8))
        raw = try RawJSONDocument(raw).replacingMember(
            named: "reason_codes", with: Data(#"["compute_failed"]"#.utf8))
        let unavailable = RawObservedMotion(rawJSON: raw)
        #expect(unavailable.typed == nil)
        #expect(unavailable.presentationIssue == .malformed)
    }

    @Test func disabledEnvelopeMustHaveOnlyNotEvaluatedEmptyShape() throws {
        let disabled = observedMotionFixture(
            revision: 12, status: "disabled", runID: nil, expiresAt: nil)
        #expect(disabled.typed != nil)

        let expired = RawObservedMotion(rawJSON: try RawJSONDocument(disabled.rawJSON).replacingMember(
            named: "expires_at", with: Data(#""2026-09-05T10:15:00Z""#.utf8)))
        #expect(expired.typed == nil)

        let available = observedMotionFixture(revision: 13)
        let sources = try #require(RawJSONDocument(available.rawJSON).member(named: "sources"))
        let diagnosticSource = RawObservedMotion(rawJSON: try RawJSONDocument(disabled.rawJSON).replacingMember(
            named: "sources", with: sources))
        #expect(diagnosticSource.typed == nil)
    }

    @Test func acceptedMotionRejectsSourceReceiptAfterCutoff() {
        // This catches dropping receipt-time/cutoff validation while retaining a
        // plausible-looking accepted contour and projection.
        let lateReceipt = RawObservedMotion(rawJSON: Data(
            String(decoding: observedMotionFixture(revision: 12).rawJSON, as: UTF8.self)
                .replacingOccurrences(
                    of: #""received_at":"2026-09-05T10:00:00Z""#,
                    with: #""received_at":"2026-09-05T10:00:01Z""#)
                .utf8))
        #expect(lateReceipt.typed == nil)
    }

    @Test func acceptedMotionRejectsNonAdjacentPairChain() {
        // This catches accepting two diagnostics that do not connect each
        // adjacent frame in the declared three-frame history.
        let disconnected = RawObservedMotion(rawJSON: Data(
            String(decoding: observedMotionFixture(revision: 12).rawJSON, as: UTF8.self)
                .replacingOccurrences(
                    of: #""to_frame_id":"frame-3""#,
                    with: #""to_frame_id":"frame-1""#)
                .utf8))
        #expect(disconnected.typed == nil)
    }

    @Test func acceptedMotionRejectsPatchCentreOutsideAnalysisDomain() {
        // This catches rendering accepted diagnostics whose declared template
        // centres fall outside the envelope's own grid dimensions.
        let outsideDomain = RawObservedMotion(rawJSON: Data(
            String(decoding: observedMotionFixture(revision: 12).rawJSON, as: UTF8.self)
                .replacingOccurrences(
                    of: #""center_column":13"#,
                    with: #""center_column":100"#)
                .utf8))
        #expect(outsideDomain.typed == nil)
    }
}

@MainActor
@Suite("Observed motion state")
struct ObservedMotionStateTests {
    @Test @MainActor func disconnectRetainsRawButFencesLateAuthorityThroughReconnect() throws {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight/pack", routeGeometryID: "route-1")
        state.modeEnabled = true
        let raw = observedMotionFixture(revision: 12)
        let now = try #require(Date.parseISO8601("2026-09-05T10:01:00Z"))
        state.accept(raw: raw, capability: .enabled, capabilitySequence: 1, generation: state.generation, now: now)
        state.selectProjection("2026-09-05T10:05:00Z", now: now)
        #expect(state.canPresentActivePrediction)
        let oldGeneration = state.generation
        state.setConnectivity(false)
        #expect(state.canPresentActivePrediction == false)
        #expect(state.presentationReasons.contains("stored_analysis"))
        #expect(state.isStoredPresentation)
        #expect(state.raw == raw)
        #expect(state.selectedProjectionTime == "2026-09-05T10:05:00Z")
        state.accept(raw: raw, capability: .enabled, capabilitySequence: 2, generation: oldGeneration, now: now)
        #expect(state.capability == .unknown)
        state.setConnectivity(true)
        // Ordinary late refresh replies without a lifecycle token also cannot
        // restore authority before the fresh bounded reconnect read.
        state.accept(raw: raw, capability: .enabled, capabilitySequence: 3, now: now)
        #expect(state.canPresentActivePrediction == false)
        let generation = state.beginCapabilityCheck()
        state.accept(raw: raw, capability: .enabled, capabilitySequence: 4, generation: generation, now: now)
        #expect(state.canPresentActivePrediction)
        #expect(state.isStoredPresentation == false)
    }

    @Test func sourceVisibilityIsIndependent() {
        let state = ObservedMotionState()
        state.enabledFamilies.remove(.radarEcho)
        #expect(state.enabledFamilies.contains(.radarEcho) == false)
        #expect(state.enabledFamilies.contains(.highCloudTop))
    }

    @Test func unknownCapabilityDoesNotAuthorizeCachedPrediction() {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        state.accept(raw: observedMotionFixture(revision: 8))
        #expect(state.canPresentActivePrediction == false)
    }

    @Test func newerUnavailableReplacesOlderReadyAndOlderCannotReturn() {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        let generation = state.generation
        state.observeCapability(.enabled, generation: generation)
        state.accept(raw: observedMotionFixture(revision: 8), generation: generation)
        state.accept(raw: observedMotionFixture(revision: 9, status: "unavailable", runID: nil, expiresAt: nil), generation: generation)
        state.accept(raw: observedMotionFixture(revision: 8), generation: generation)
        #expect(state.raw?.revision == 9)
        #expect(state.envelopeStatus == "unavailable")
        #expect(state.canPresentActivePrediction == false)
    }

    @Test func missingFreshBlockDoesNotAuthorizeRetainedStoredPrediction() {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        state.accept(raw: observedMotionFixture(revision: 8), origin: .stored(packTimestamp: "pack-1"))
        state.accept(raw: nil, capability: .enabled, origin: .network)
        #expect(state.raw?.revision == 8)
        #expect(state.canPresentActivePrediction == false)
        #expect(state.presentationReasons.contains("refresh_needed"))
    }

    @Test func missingLegacyStoredBlockIsRefreshNeededRatherThanClearEvidence() {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        state.accept(raw: nil, origin: .stored(packTimestamp: "pack-1"))
        #expect(state.presentationReasons.contains("refresh_needed"))
        #expect(state.canPresentActivePrediction == false)
    }

    @Test func olderCapabilityResponseCannotRestoreEnabledState() {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        state.accept(
            raw: observedMotionFixture(revision: 8), capability: .disabled,
            capabilitySequence: 12, origin: .network)
        state.accept(
            raw: observedMotionFixture(revision: 8), capability: .enabled,
            capabilitySequence: 11, origin: .network)
        #expect(state.capability == .disabled)
        #expect(state.canPresentActivePrediction == false)
    }

    @Test func olderResponseCannotClearNewerMissingMotionState() {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        state.accept(
            raw: nil, capability: .enabled, capabilitySequence: 12, origin: .network)
        state.accept(
            raw: observedMotionFixture(revision: 8), capability: .enabled,
            capabilitySequence: 11, origin: .network)
        #expect(state.raw?.revision == 8)
        #expect(state.currentResponseMissingMotion)
        #expect(state.presentationReasons.contains("refresh_needed"))
    }

    @Test func newerMissingCapabilityHeaderReturnsAuthorityToUnknown() {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        let raw = observedMotionFixture(revision: 8)
        state.accept(raw: raw, capability: .enabled, capabilitySequence: 10, origin: .network)
        state.accept(raw: raw, capability: nil, capabilitySequence: 11, origin: .network)
        #expect(state.capability == .unknown)
    }

    @Test func sameRevisionConflictKeepsFirstRawEnvelopeAndDisablesPresentation() {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        let first = observedMotionFixture(revision: 8)
        state.accept(raw: first)
        state.accept(raw: observedMotionFixture(revision: 8, computedAt: "2026-09-05T10:05:00Z"))
        #expect(state.raw == first)
        #expect(state.contractIssue == .sameRevisionConflict)
    }

    @Test func sourceExpiryHidesActiveProjectionButLeavesDatedSelection() throws {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        state.observeCapability(.enabled, generation: state.generation)
        state.accept(raw: observedMotionFixture(revision: 8), generation: state.generation)
        let selected = "2026-09-05T10:05:00Z"
        state.selectProjection(selected, now: try #require(Date.parseISO8601("2026-09-05T10:01:00Z")))
        #expect(state.canPresentActivePrediction)
        state.updateClock(try #require(Date.parseISO8601("2026-09-05T10:21:00Z")))
        #expect(state.canPresentActivePrediction == false)
        #expect(state.selectedProjectionTime == selected)
        #expect(state.presentationReasons.contains("expired"))
    }

    @Test func deviceClockBeforeCutoffDisablesFutureClaimAndUsesUTCDate() throws {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        state.observeCapability(.enabled, generation: state.generation)
        state.accept(raw: observedMotionFixture(revision: 8), generation: state.generation)
        state.selectProjection("2026-09-05T10:05:00Z",
                               now: try #require(Date.parseISO8601("2026-09-04T23:59:00Z")))
        #expect(state.canPresentActivePrediction == false)
        #expect(state.presentationReasons.contains("clock_uncertain"))
        #expect(state.projectionLabel.contains("05 Sep 2026"))
    }

    @Test func advertisedProjectionSpellingSurvivesDateParsingForActiveMapGeometry() throws {
        let advertised = "2026-09-05T10:05:00.000Z"
        let raw = RawObservedMotion(rawJSON: Data(
            String(decoding: observedMotionFixture(revision: 8).rawJSON, as: UTF8.self)
                .replacingOccurrences(of: "2026-09-05T10:05:00Z", with: advertised).utf8))
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        state.observeCapability(.enabled, generation: state.generation)
        state.accept(raw: raw, generation: state.generation,
                     now: try #require(Date.parseISO8601("2026-09-05T10:01:00Z")))
        state.selectProjection(advertised,
                               now: try #require(Date.parseISO8601("2026-09-05T10:01:00Z")))
        #expect(state.selectedProjectionTime == advertised)
        #expect(state.canPresentActivePrediction)
        let snapshot = ObservedMotionOverlay.build(
            envelope: try #require(state.envelope), selectedProjectionTime: state.selectedProjectionTime,
            enabledFamilies: Set(ObservedMotionFamily.allCases), selectedFeatureID: "radar-1")
        #expect(snapshot.overlays.compactMap { $0 as? ObservedMotionPolygon }.contains { $0.isProjected })
    }

    @Test func navigationRejectsLateCapabilityAndClearsSelection() throws {
        let state = ObservedMotionState()
        state.start(packIdentity: "flight-1/pack-1", routeGeometryID: "route-1")
        let oldGeneration = state.generation
        state.accept(raw: observedMotionFixture(revision: 8), generation: oldGeneration)
        state.selectFeature("radar-1")
        state.start(packIdentity: "flight-1/pack-2", routeGeometryID: "route-2")
        state.observeCapability(.enabled, generation: oldGeneration)
        #expect(state.capability == .unknown)
        #expect(state.selectedFeatureID == nil)
        #expect(state.raw == nil)
    }
}

@Suite("Observed motion MapKit overlays")
struct ObservedMotionOverlayTests {
    @Test func unavailableCloudOnlyKeepsObservedContourAndEvidenceButNeverProjects() throws {
        let raw = try cloudOnlyUnavailableFixture()
        let typed = try #require(raw.typed)
        #expect(typed.features.first?.family == .highCloudTop)
        #expect(typed.features.first?.observations.first?.value == 9000)
        #expect(typed.features.first?.motion.groundSpeedKt == nil)
        #expect(String(decoding: raw.rawJSON, as: UTF8.self).contains("Future_Context"))
        let observed = ObservedMotionOverlay.build(envelope: typed, selectedProjectionTime: nil,
            enabledFamilies: Set(ObservedMotionFamily.allCases), selectedFeatureID: nil)
        let polygons = observed.overlays.compactMap { $0 as? ObservedMotionPolygon }
        #expect(polygons.count == 1)
        #expect(polygons.first?.isProjected == false)
        #expect(polygons.first?.interiorPolygons?.count == 1)
        let future = ObservedMotionOverlay.build(envelope: typed, selectedProjectionTime: "2026-09-05T10:05:00Z",
            enabledFamilies: Set(ObservedMotionFamily.allCases), selectedFeatureID: nil)
        #expect(future.overlays.isEmpty)
    }

    @Test func polygonKeepsInteriorHoleAndProjectionStyle() throws {
        let raw = observedMotionFixture(revision: 8)
        let typed = try #require(raw.typed)
        let snapshot = ObservedMotionOverlay.build(
            envelope: typed, selectedProjectionTime: "2026-09-05T10:05:00Z",
            enabledFamilies: Set(ObservedMotionFamily.allCases), selectedFeatureID: "radar-1")
        let polygon = try #require(snapshot.overlays.compactMap { $0 as? ObservedMotionPolygon }.first)
        #expect(polygon.interiorPolygons?.count == 1)
        #expect(polygon.isProjected)
        #expect(polygon.isSelected)
    }

    @Test func routeAndWeatherOwnershipStayIndependent() {
        var coordinate = CLLocationCoordinate2D(latitude: 50, longitude: 1)
        let route = ColoredPolyline(coordinates: &coordinate, count: 1)
        let weather = ObservedMotionPolyline(coordinates: &coordinate, count: 1)
        let overlays: [MKOverlay] = [route, weather]
        #expect(RouteMapKitView.routeOwnedOverlays(in: overlays).count == 1)
        #expect(RouteMapKitView.weatherOwnedOverlays(in: overlays).count == 1)
    }
}

@Suite("Observed motion card evaluated scope")
@MainActor
struct ObservedMotionCardScopeTests {
    @Test func positiveLightningDisplaysAbsoluteWindowCountsAndLowerBound() {
        let evidence = ObservedMotionFeatureLightning(status: "available", reasonCodes: ["selection_limit"],
            evaluatedWindow: .init(startAt: "2020-02-01T23:55:00Z", endAt: "2020-02-02T00:00:00Z"),
            reportedDetectionCount: 300, emittedMarkerCount: 0, evaluationComplete: false)
        let text = ObservedMotionView.lightningLines(evidence).joined(separator: " ")
        #expect(text.contains("300 reported detections; 0 map markers"))
        #expect(text.contains("partial/lower bound"))
        #expect(text.contains("01 Feb 2020 23:55Z–02 Feb 2020 00:00Z"))
        #expect(text.contains("selection limit"))
    }

    @Test func completeEmptyAndUnavailablePlannedResultsRemainDistinct() {
        let window = ObservedMotionInterval(startAt: "2020-02-01T23:55:00Z", endAt: "2020-02-02T00:00:00Z")
        let empty = ObservedMotionPlannedOverlap(status: "available", reasonCodes: [], evaluatedInterval: window, intervals: [], complete: true)
        let emptyText = ObservedMotionView.plannedOverlapLines(empty).joined(separator: " ")
        #expect(emptyText.contains("No overlap calculated for this tracked contour under this model"))
        #expect(emptyText.contains("01 Feb 2020 23:55Z–02 Feb 2020 00:00Z"))
        let unavailable = ObservedMotionPlannedOverlap(status: "unavailable", reasonCodes: ["outside_planned_interval"], evaluatedInterval: window, intervals: [], complete: false)
        let unavailableText = ObservedMotionView.plannedOverlapLines(unavailable).joined(separator: " ")
        #expect(unavailableText.contains("Unavailable: outside planned interval"))
        #expect(!unavailableText.contains("No overlap"))
    }

    @Test func completenessDistinguishesKnownOmissionsFromUnenumeratedTotals() {
        let partial = ObservedMotionCompleteness(category: "small_detections", status: "partial", reasonCodes: ["selection_limit"], consideredCount: 9, emittedCount: 2, omittedCount: 7)
        #expect(ObservedMotionView.completenessLine(partial).contains("small detections (untracked): partial; considered 9, emitted 2, omitted 7"))
        let unknown = ObservedMotionCompleteness(category: "candidates", status: "not_evaluated", reasonCodes: ["not_evaluated"], consideredCount: nil, emittedCount: 0, omittedCount: nil)
        #expect(ObservedMotionView.completenessLine(unknown).contains("considered unknown, emitted 0, omitted unknown"))
    }
}
