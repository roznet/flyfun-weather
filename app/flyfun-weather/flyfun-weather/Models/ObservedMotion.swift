import Foundation
import CoreFoundation

enum ObservedMotionCapability: String, Sendable {
    case unknown
    case enabled
    case disabled
}

enum ObservedMotionOrigin: Equatable, Sendable {
    case network
    case stored(packTimestamp: String)

    var isStoredOnly: Bool {
        if case .stored = self { return true }
        return false
    }
}

enum ObservedMotionPresentationIssue: String, Error, Equatable, Sendable {
    case malformed
    case unsupportedSchema = "unsupported_schema"
    case unsupportedStatus = "unsupported_status"
    case identityMismatch = "identity_mismatch"
    case sameRevisionConflict = "same_revision_conflict"
}

/// The exact JSON value received for `observed_motion`. The original bytes are
/// retained for cache writes; typed access is an independent, conservative view.
struct RawObservedMotion: Equatable, Sendable {
    let rawJSON: Data

    init(rawJSON: Data) {
        self.rawJSON = rawJSON
    }

    var revision: Int? {
        guard let object = try? JSONSerialization.jsonObject(with: rawJSON) as? [String: Any],
              let number = object["revision"] as? NSNumber,
              Self.isJSONInteger(number)
        else { return nil }
        let value = number.int64Value
        guard value >= 1, value <= 9_007_199_254_740_991,
              number.doubleValue == Double(value), value <= Int64(Int.max)
        else { return nil }
        return Int(value)
    }

    var schemaVersion: Int? {
        guard let object = try? JSONSerialization.jsonObject(with: rawJSON) as? [String: Any],
              let number = object["schema_version"] as? NSNumber,
              Self.isJSONInteger(number), number.doubleValue == 1
        else { return nil }
        return 1
    }

    var status: String? {
        (try? JSONSerialization.jsonObject(with: rawJSON) as? [String: Any])?["status"] as? String
    }

    var typed: ObservedMotionEnvelope? {
        guard presentationIssue == nil else { return nil }
        guard let decoded = ObservedMotionEnvelope.decodeTolerantly(from: rawJSON),
              decoded.isValidVersionOne
        else { return nil }
        return decoded
    }

    var presentationIssue: ObservedMotionPresentationIssue? {
        guard let object = try? JSONSerialization.jsonObject(with: rawJSON) as? [String: Any],
              revision != nil
        else { return .malformed }
        guard let schema = object["schema_version"] as? NSNumber,
              Self.isJSONInteger(schema)
        else { return .malformed }
        guard schema.intValue == 1, schema.doubleValue == 1 else { return .unsupportedSchema }
        guard let status = object["status"] as? String else { return .malformed }
        guard ["available", "unavailable", "disabled"].contains(status) else { return .unsupportedStatus }
        guard ObservedMotionEnvelope.decodeTolerantly(from: rawJSON)?.isValidVersionOne == true
        else { return .malformed }
        return nil
    }

    private static func isJSONInteger(_ number: NSNumber) -> Bool {
        guard CFGetTypeID(number) != CFBooleanGetTypeID() else { return false }
        return !["f", "d"].contains(String(cString: number.objCType))
    }

    /// Equality for revision idempotence is semantic JSON equality, while the
    /// retained value remains the exact first-seen byte spelling.
    func hasSameJSONValue(as other: RawObservedMotion) -> Bool {
        guard let lhs = try? JSONSerialization.jsonObject(with: rawJSON),
              let rhs = try? JSONSerialization.jsonObject(with: other.rawJSON),
              let left = try? JSONSerialization.data(withJSONObject: lhs, options: [.sortedKeys]),
              let right = try? JSONSerialization.data(withJSONObject: rhs, options: [.sortedKeys])
        else { return rawJSON == other.rawJSON }
        return left == right
    }
}

enum ObservedMotionFamily: String, Codable, CaseIterable, Hashable, Sendable {
    case radarEcho = "radar_echo"
    case highCloudTop = "high_cloud_top"

    var label: String {
        switch self {
        case .radarEcho: "Radar 5 dBZ"
        case .highCloudTop: "Cloud top 15,000 ft MSL"
        }
    }
}

struct ObservedMotionEnvelope: Decodable, Sendable {
    let schemaVersion: Int
    let status: String
    let reasonCodes: [String]
    let revision: Int
    let runID: String?
    let routeGeometryID: String
    let plannedTimingID: String?
    let computedAt: String
    let cutoffAt: String
    let expiresAt: String?
    let methodVersion: String
    let policyVersion: String
    let analysisDomain: ObservedMotionAnalysisDomain?
    let sources: [ObservedMotionSource]
    let features: [ObservedMotionFeature]
    let associations: [ObservedMotionAssociation]
    let lightning: [ObservedMotionLightning]
    let projectionTimes: [String]
    let completeness: [ObservedMotionCompleteness]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", status, reasonCodes = "reason_codes", revision
        case runID = "run_id", routeGeometryID = "route_geometry_id", plannedTimingID = "planned_timing_id"
        case computedAt = "computed_at", cutoffAt = "cutoff_at", expiresAt = "expires_at"
        case methodVersion = "method_version", policyVersion = "policy_version"
        case analysisDomain = "analysis_domain", sources, features, associations, lightning
        case projectionTimes = "projection_times", completeness
    }

    var computedDate: Date? { Date.parseISO8601(computedAt) }
    var cutoffDate: Date? { Date.parseISO8601(cutoffAt) }
    var expiryDate: Date? { expiresAt.flatMap(Date.parseISO8601) }
    var projectionDates: [Date] { projectionTimes.compactMap(Date.parseISO8601) }

    var isValidVersionOne: Bool {
        guard schemaVersion == 1,
              ["available", "unavailable", "disabled"].contains(status),
              revision > 0, revision <= 9_007_199_254_740_991,
              !routeGeometryID.isEmpty,
              computedDate != nil, cutoffDate != nil,
              expiresAt == nil || expiryDate != nil,
              methodVersion == "masked_contour_translation_v1",
              policyVersion == "observed_motion_policy_v1",
              projectionDates.count == projectionTimes.count,
              projectionDates == projectionDates.sorted(),
              Set(projectionTimes).count == projectionTimes.count,
              Set(completeness.map(\.category)) == Self.requiredCompleteness,
              completeness.count == Self.requiredCompleteness.count
        else { return false }
        let sourcesByID = Dictionary(sources.map { ($0.sourceID, $0) }, uniquingKeysWith: { first, _ in first })
        guard sourcesByID.count == sources.count,
              sources.allSatisfy({ $0.isValid(at: cutoffDate!) }),
              features.allSatisfy({
                  $0.isValid(
                      in: sourcesByID, projectionTimes: projectionTimes,
                      cutoff: cutoffDate!, domain: analysisDomain)
              })
        else { return false }
        let featuresByID = Dictionary(features.map { ($0.featureID, $0) }, uniquingKeysWith: { first, _ in first })
        guard featuresByID.count == features.count,
              associations.allSatisfy({ association in
                  guard let radar = featuresByID[association.radarFeatureID],
                        let cloud = featuresByID[association.cloudFeatureID]
                  else { return false }
                  return radar.family == .radarEcho && cloud.family == .highCloudTop
              }),
              lightning.allSatisfy({ record in
                  record.associatedFeatureIDs?.allSatisfy { featuresByID[$0] != nil } ?? true
              })
        else { return false }
        if status == "available" {
            return runID?.isEmpty == false && analysisDomain != nil
                && features.contains { $0.motion.status == "accepted" }
        }
        if status == "disabled" {
            return !reasonCodes.isEmpty
                && runID == nil && analysisDomain == nil && expiresAt == nil
                && sources.isEmpty && features.isEmpty && associations.isEmpty
                && lightning.isEmpty && projectionTimes.isEmpty
                && completeness.allSatisfy {
                    $0.status == "not_evaluated" && !$0.reasonCodes.isEmpty
                        && $0.consideredCount == nil && $0.emittedCount == 0
                        && $0.omittedCount == nil
                }
        }
        // An unavailable publication may retain observed-only context, but must
        // never carry accepted motion, an available projected geometry, or an
        // available route-motion row from an earlier run.
        return !reasonCodes.isEmpty
            && features.allSatisfy { feature in
                feature.motion.status == "unavailable"
                    && feature.projections.allSatisfy { $0.status != "available" }
                    && feature.routeRows.allSatisfy { $0.status != "available" }
            }
    }

    private static let requiredCompleteness = Set([
        "regions", "input_frames", "small_detections", "candidates", "features", "geometry",
        "associations", "lightning", "legs", "route_rows", "overlap_intervals",
    ])

    static func decodeTolerantly(from data: Data) -> ObservedMotionEnvelope? {
        guard var root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              root["sources"] is [Any], root["features"] is [Any],
              root["associations"] is [Any], root["lightning"] is [Any],
              root["projection_times"] is [Any], root["completeness"] is [Any]
        else { return nil }
        root["sources"] = sanitizedSources(root["sources"])
        root["features"] = sanitizedFeatures(root["features"])
        root["associations"] = sanitizedArray(root["associations"], as: ObservedMotionAssociation.self)
        root["lightning"] = sanitizedArray(root["lightning"], as: ObservedMotionLightning.self)
        root["completeness"] = sanitizedArray(root["completeness"], as: ObservedMotionCompleteness.self)
        guard JSONSerialization.isValidJSONObject(root),
              let cleaned = try? JSONSerialization.data(withJSONObject: root)
        else { return nil }
        return try? JSONDecoder().decode(ObservedMotionEnvelope.self, from: cleaned)
    }

    private static func sanitizedSources(_ value: Any?) -> [[String: Any]] {
        guard let records = value as? [[String: Any]] else { return [] }
        return records.compactMap { record in
            var copy = record
            copy["frames"] = sanitizedArray(record["frames"], as: ObservedMotionFrame.self)
            guard decodes(copy, as: ObservedMotionSource.self) else { return nil }
            return copy
        }
    }

    private static func sanitizedFeatures(_ value: Any?) -> [[String: Any]] {
        guard let records = value as? [[String: Any]] else { return [] }
        return records.compactMap { record in
            var copy = record
            copy["observations"] = sanitizedArray(record["observations"], as: ObservedMotionObservation.self)
            copy["projections"] = sanitizedArray(record["projections"], as: ObservedMotionProjection.self)
            copy["route_rows"] = sanitizedArray(record["route_rows"], as: ObservedMotionRouteRow.self)
            if var motion = record["motion"] as? [String: Any] {
                motion["pair_diagnostics"] = sanitizedArray(
                    motion["pair_diagnostics"], as: ObservedMotionPairDiagnostics.self)
                copy["motion"] = motion
            }
            guard decodes(copy, as: ObservedMotionFeature.self) else { return nil }
            return copy
        }
    }

    private static func sanitizedArray<T: Decodable>(_ value: Any?, as type: T.Type) -> [Any] {
        guard let values = value as? [Any] else { return [] }
        return values.filter { decodes($0, as: type) }
    }

    private static func decodes<T: Decodable>(_ value: Any, as type: T.Type) -> Bool {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value)
        else { return false }
        return (try? JSONDecoder().decode(type, from: data)) != nil
    }
}

struct ObservedMotionAnalysisDomain: Decodable, Sendable {
    let center: [Double]
    let crs: String
    let cellSizeM: Double
    let widthCells: Int
    let heightCells: Int
    let originXM: Double
    let originYM: Double
    let bounds: [Double]
    let reasonCodes: [String]
    enum CodingKeys: String, CodingKey {
        case center, crs, cellSizeM = "cell_size_m", widthCells = "width_cells", heightCells = "height_cells"
        case originXM = "origin_x_m", originYM = "origin_y_m", bounds, reasonCodes = "reason_codes"
    }
}

struct ObservedMotionSupport: Decodable, Sendable {
    let status: String
    let reasonCodes: [String]
    let scope: String
    let knownCells: Int?
    let totalCells: Int?
    let knownFraction: Double?
    enum CodingKeys: String, CodingKey {
        case status, reasonCodes = "reason_codes", scope, knownCells = "known_cells"
        case totalCells = "total_cells", knownFraction = "known_fraction"
    }
}

struct ObservedMotionGeolocation: Decodable, Sendable {
    let status: String
    let reasonCodes: [String]
    let evidenceID: String?
    let methodVersion: String?
    let applicabilityID: String?
    enum CodingKeys: String, CodingKey {
        case status, reasonCodes = "reason_codes", evidenceID = "evidence_id"
        case methodVersion = "method_version", applicabilityID = "applicability_id"
    }
}

struct ObservedMotionInterval: Decodable, Sendable {
    let startAt: String
    let endAt: String
    enum CodingKeys: String, CodingKey { case startAt = "start_at", endAt = "end_at" }
}

struct ObservedMotionFrame: Decodable, Sendable {
    let frameID: String
    let validAt: String
    let receivedAt: String
    let acquisitionWindow: ObservedMotionInterval
    let referenceAt: String
    enum CodingKeys: String, CodingKey {
        case frameID = "frame_id", validAt = "valid_at", receivedAt = "received_at"
        case acquisitionWindow = "acquisition_window", referenceAt = "reference_at"
    }
}

struct ObservedMotionSource: Decodable, Identifiable, Sendable {
    let sourceID: String
    let status: String
    let reasonCodes: [String]
    let frames: [ObservedMotionFrame]
    let attribution: String
    let coverage: ObservedMotionSupport
    let geolocation: ObservedMotionGeolocation
    var id: String { sourceID }
    enum CodingKeys: String, CodingKey {
        case sourceID = "source_id", status, reasonCodes = "reason_codes", frames, attribution, coverage, geolocation
    }

    func isValid(at cutoff: Date) -> Bool {
        guard Set(frames.map(\.frameID)).count == frames.count else { return false }
        var previous: Date?
        for frame in frames {
            guard let valid = Date.parseISO8601(frame.validAt),
                  let received = Date.parseISO8601(frame.receivedAt),
                  let reference = Date.parseISO8601(frame.referenceAt),
                  let start = Date.parseISO8601(frame.acquisitionWindow.startAt),
                  let end = Date.parseISO8601(frame.acquisitionWindow.endAt),
                  reference == valid, start <= end, end <= received,
                  valid <= received, received <= cutoff,
                  previous.map({ $0 < valid }) ?? true
            else { return false }
            previous = valid
        }
        return true
    }
}

struct ObservedMotionDefinition: Decodable, Sendable {
    let quantity: String
    let comparisonOperator: String
    let threshold: Double
    let unit: String

    enum CodingKeys: String, CodingKey {
        case quantity, comparisonOperator = "operator", threshold, unit
    }
}

struct ObservedMotionMultiPolygon: Decodable, Sendable {
    let type: String
    let coordinates: [[[[Double]]]]

    var isValid: Bool {
        type == "MultiPolygon" && !coordinates.isEmpty && coordinates.allSatisfy { polygon in
            guard let exterior = polygon.first, exterior.count >= 4,
                  exterior.first?.count == 2, exterior.last?.count == 2,
                  exterior.first == exterior.last
            else { return false }
            return polygon.allSatisfy { ring in
                ring.count >= 4 && ring.allSatisfy { point in
                    point.count == 2 && point[0].isFinite && point[1].isFinite
                        && (-180...180).contains(point[0]) && (-90...90).contains(point[1])
                }
            }
        }
    }
}

struct ObservedMotionGeometry: Decodable, Sendable {
    let status: String
    let reasonCodes: [String]
    let geometry: ObservedMotionMultiPolygon?
    let provenance: String
    let simplificationToleranceM: Double
    enum CodingKeys: String, CodingKey {
        case status, reasonCodes = "reason_codes", geometry, provenance
        case simplificationToleranceM = "simplification_tolerance_m"
    }
}

struct ObservedMotionTrailSample: Decodable, Sendable {
    let frameID: String
    let observedAt: String
    let center: [Double]
    enum CodingKeys: String, CodingKey { case frameID = "frame_id", observedAt = "observed_at", center }
}

struct ObservedMotionObservation: Decodable, Identifiable, Sendable {
    let kind: String
    let status: String
    let reasonCodes: [String]
    let value: Double?
    let unit: String
    let sourceID: String?
    let frameID: String?
    let observedAt: String?
    let comparisonAt: String?
    let acquisitionWindow: ObservedMotionInterval?
    let alignmentMethod: String?
    let sampleID: String?
    let samplePosition: [Double]?
    let pairedTemperatureK: Double?
    let coverage: ObservedMotionSupport
    var id: String { "\(kind)-\(observedAt ?? "unknown")" }
    enum CodingKeys: String, CodingKey {
        case kind, status, reasonCodes = "reason_codes", value, unit, sourceID = "source_id", frameID = "frame_id"
        case observedAt = "observed_at", comparisonAt = "comparison_at"
        case acquisitionWindow = "acquisition_window", alignmentMethod = "alignment_method"
        case sampleID = "sample_id", samplePosition = "sample_position", pairedTemperatureK = "paired_temperature_k", coverage
    }
}

struct ObservedMotionFeatureLightning: Decodable, Sendable {
    let status: String
    let reasonCodes: [String]
    let evaluatedWindow: ObservedMotionInterval?
    let reportedDetectionCount: Int?
    let emittedMarkerCount: Int
    let evaluationComplete: Bool
    enum CodingKeys: String, CodingKey {
        case status, reasonCodes = "reason_codes", evaluatedWindow = "evaluated_window"
        case reportedDetectionCount = "reported_detection_count", emittedMarkerCount = "emitted_marker_count"
        case evaluationComplete = "evaluation_complete"
    }
}

struct ObservedMotionVector: Decodable, Sendable {
    let status: String
    let reasonCodes: [String]
    let groundSpeedKt: Double?
    let bearingDegTrue: Double?
    let velocityReferencePoint: [Double]?
    let velocityMethod: String?
    let pairDiagnostics: [ObservedMotionPairDiagnostics]
    let fitRMSResidualCells: Double?
    enum CodingKeys: String, CodingKey {
        case status, reasonCodes = "reason_codes", groundSpeedKt = "ground_speed_kt"
        case bearingDegTrue = "bearing_deg_true", fitRMSResidualCells = "fit_rms_residual_cells"
        case velocityReferencePoint = "velocity_reference_point", velocityMethod = "velocity_method"
        case pairDiagnostics = "pair_diagnostics"
    }
}

struct ObservedMotionPatchDiagnostics: Decodable, Sendable {
    let direction: String
    let centerColumn: Int
    let centerRow: Int
    let status: String
    let reasonCodes: [String]
    let supportFraction: Double?
    let ncc: Double?
    let competingPeakMargin: Double?
    let dxCells: Double?
    let dyCells: Double?
    let refinement: String?
    enum CodingKeys: String, CodingKey {
        case direction, centerColumn = "center_column", centerRow = "center_row"
        case status, reasonCodes = "reason_codes", supportFraction = "support_fraction", ncc
        case competingPeakMargin = "competing_peak_margin", dxCells = "dx_cells", dyCells = "dy_cells", refinement
    }

    func isValidAvailable(in domain: ObservedMotionAnalysisDomain?) -> Bool {
        status == "available" && reasonCodes.isEmpty
            && ["forward", "reverse"].contains(direction)
            && domain.map { centerColumn >= 0 && centerColumn < $0.widthCells
                && centerRow >= 0 && centerRow < $0.heightCells } == true
            && supportFraction.map { (0...1).contains($0) } == true
            && ncc.map { (-1...1).contains($0) } == true
            && competingPeakMargin.map { $0 >= 0 } == true
            && dxCells?.isFinite == true && dyCells?.isFinite == true
            && ["quadratic", "integer"].contains(refinement ?? "")
    }
}

struct ObservedMotionPairDiagnostics: Decodable, Identifiable, Sendable {
    let fromFrameID: String
    let toFrameID: String
    let elapsedSeconds: Double
    let status: String
    let reasonCodes: [String]
    let patches: [ObservedMotionPatchDiagnostics]
    let forwardDXCells: Double?
    let forwardDYCells: Double?
    let patchDisagreementCells: Double?
    let reverseResidualCells: Double?
    let nextObservationResidualCells: Double?
    let commonSupportIOU: Double?
    let areaRatio: Double?
    let plausibleParentCount: Int?
    let plausibleChildCount: Int?
    let lineageComplete: Bool
    var id: String { "\(fromFrameID)-\(toFrameID)" }
    enum CodingKeys: String, CodingKey {
        case fromFrameID = "from_frame_id", toFrameID = "to_frame_id", elapsedSeconds = "elapsed_seconds"
        case status, reasonCodes = "reason_codes", patches, forwardDXCells = "forward_dx_cells"
        case forwardDYCells = "forward_dy_cells", patchDisagreementCells = "patch_disagreement_cells"
        case reverseResidualCells = "reverse_residual_cells", nextObservationResidualCells = "next_observation_residual_cells"
        case commonSupportIOU = "common_support_iou", areaRatio = "area_ratio"
        case plausibleParentCount = "plausible_parent_count", plausibleChildCount = "plausible_child_count"
        case lineageComplete = "lineage_complete"
    }

    func isValidAvailable(in domain: ObservedMotionAnalysisDomain?) -> Bool {
        status == "available" && reasonCodes.isEmpty && elapsedSeconds > 0
            && patches.count == 4 && patches.allSatisfy { $0.isValidAvailable(in: domain) }
            && patches.filter { $0.direction == "forward" }.count == 2
            && patches.filter { $0.direction == "reverse" }.count == 2
            && forwardDXCells?.isFinite == true && forwardDYCells?.isFinite == true
            && patchDisagreementCells.map { $0 >= 0 } == true
            && reverseResidualCells.map { $0 >= 0 } == true
            && commonSupportIOU.map { (0...1).contains($0) } == true
            && areaRatio.map { $0 > 0 } == true
            && plausibleParentCount.map { $0 >= 0 } == true
            && plausibleChildCount.map { $0 >= 0 } == true
            && lineageComplete
    }
}

struct ObservedMotionProjection: Decodable, Identifiable, Sendable {
    let at: String
    let status: String
    let reasonCodes: [String]
    let displayGeometry: ObservedMotionGeometry
    var id: String { at }
    enum CodingKeys: String, CodingKey {
        case at, status, reasonCodes = "reason_codes", displayGeometry = "display_geometry"
    }
}

struct ObservedMotionRouteRow: Decodable, Identifiable, Sendable {
    let legID: String
    let legIndex: Int
    let fromLabel: String
    let toLabel: String
    let at: String
    let status: String
    let reasonCodes: [String]
    let distanceNM: Double?
    let closureKt: Double?
    let relationship: String
    let plannedOverlapAtTime: Bool?
    var id: String { "\(legID)-\(at)" }
    enum CodingKeys: String, CodingKey {
        case legID = "leg_id", legIndex = "leg_index", fromLabel = "from_label", toLabel = "to_label"
        case at, status, reasonCodes = "reason_codes", distanceNM = "distance_nm", closureKt = "closure_kt"
        case relationship, plannedOverlapAtTime = "planned_overlap_at_time"
    }
}

struct ObservedMotionOverlapInterval: Decodable, Identifiable, Sendable {
    let legID: String
    let legIndex: Int
    let startAt: String
    let endAt: String
    let contact: String
    let approximate: Bool
    var id: String { "\(legID)-\(startAt)-\(endAt)" }
    enum CodingKeys: String, CodingKey {
        case legID = "leg_id", legIndex = "leg_index", startAt = "start_at", endAt = "end_at", contact, approximate
    }
}

struct ObservedMotionPlannedOverlap: Decodable, Sendable {
    let status: String
    let reasonCodes: [String]
    let evaluatedInterval: ObservedMotionInterval?
    let intervals: [ObservedMotionOverlapInterval]
    let complete: Bool
    enum CodingKeys: String, CodingKey {
        case status, reasonCodes = "reason_codes", evaluatedInterval = "evaluated_interval", intervals, complete
    }
}

struct ObservedMotionFeature: Decodable, Identifiable, Sendable {
    let featureID: String
    let sourceID: String
    let family: ObservedMotionFamily
    let definition: ObservedMotionDefinition
    let referenceAt: String
    let referenceFrameID: String
    let frameIDs: [String]
    let displayGeometry: ObservedMotionGeometry
    let trail: [ObservedMotionTrailSample]
    let observations: [ObservedMotionObservation]
    let lightningEvidence: ObservedMotionFeatureLightning
    let coverage: ObservedMotionSupport
    let geolocation: ObservedMotionGeolocation
    let motion: ObservedMotionVector
    let projectionEndAt: String?
    let projections: [ObservedMotionProjection]
    let routeRows: [ObservedMotionRouteRow]
    let plannedOverlap: ObservedMotionPlannedOverlap
    let reasonCodes: [String]
    var id: String { featureID }
    enum CodingKeys: String, CodingKey {
        case featureID = "feature_id", sourceID = "source_id", family, definition
        case referenceAt = "reference_at", referenceFrameID = "reference_frame_id", frameIDs = "frame_ids"
        case displayGeometry = "display_geometry", trail, observations
        case lightningEvidence = "lightning_evidence", coverage, geolocation, motion
        case projectionEndAt = "projection_end_at", projections, routeRows = "route_rows"
        case plannedOverlap = "planned_overlap", reasonCodes = "reason_codes"
    }


    func isValid(
        in sources: [String: ObservedMotionSource], projectionTimes: [String],
        cutoff: Date, domain: ObservedMotionAnalysisDomain?
    ) -> Bool {
        guard let source = sources[sourceID], !featureID.isEmpty,
              source.frames.contains(where: { $0.frameID == referenceFrameID }),
              frameIDs.allSatisfy({ id in source.frames.contains(where: { $0.frameID == id }) }),
              trail.allSatisfy({ sample in frameIDs.contains(sample.frameID) }),
              (displayGeometry.status != "available" || displayGeometry.geometry?.isValid == true),
              projections.allSatisfy({ projection in
                  projectionTimes.contains(projection.at)
                      && (projection.status != "available" || projection.displayGeometry.geometry?.isValid == true)
              })
        else { return false }
        if family == .radarEcho {
            guard definition.quantity == "reflectivity", definition.comparisonOperator == "gte",
                  definition.threshold == 5, definition.unit == "dBZ" else { return false }
        } else {
            guard definition.quantity == "geometric_cloud_top_height", definition.comparisonOperator == "gte",
                  definition.threshold == 4572, definition.unit == "m_msl" else { return false }
        }
        if motion.status == "accepted" {
            let frameByID = Dictionary(source.frames.map { ($0.frameID, $0) }, uniquingKeysWith: { first, _ in first })
            guard frameIDs.count >= 3,
                  source.frames.suffix(frameIDs.count).map(\.frameID) == frameIDs,
                  let referenceFrame = frameByID[referenceFrameID],
                  let referenceDate = Date.parseISO8601(referenceAt),
                  Date.parseISO8601(referenceFrame.validAt) == referenceDate,
                  // Current accepted evidence remains inspectable for 20 minutes;
                  // the independent projection horizon below remains 15 minutes.
                  cutoff.timeIntervalSince(referenceDate) <= 20 * 60,
                  projectionEndAt.flatMap(Date.parseISO8601) == referenceDate.addingTimeInterval(15 * 60),
                  projections.map(\.at) == projectionTimes,
                  motion.pairDiagnostics.count == frameIDs.count - 1,
                  motion.pairDiagnostics.enumerated().allSatisfy({ index, pair in
                      guard pair.fromFrameID == frameIDs[index], pair.toFrameID == frameIDs[index + 1],
                            let from = frameByID[pair.fromFrameID].flatMap({ Date.parseISO8601($0.validAt) }),
                            let to = frameByID[pair.toFrameID].flatMap({ Date.parseISO8601($0.validAt) })
                      else { return false }
                      return pair.elapsedSeconds == to.timeIntervalSince(from)
                          && ((index == motion.pairDiagnostics.count - 1)
                              == (pair.nextObservationResidualCells == nil))
                  }),
                  observations.allSatisfy({ observation in
                      guard let sourceID = observation.sourceID, let frameID = observation.frameID else {
                          return observation.status == "unavailable"
                      }
                      guard sources[sourceID]?.frames.contains(where: { $0.frameID == frameID }) == true else {
                          return false
                      }
                      if observation.alignmentMethod == "observed" {
                          return observation.observedAt == observation.comparisonAt
                      }
                      return true
                  })
            else { return false }
            guard motion.reasonCodes.isEmpty,
                  let speed = motion.groundSpeedKt, speed >= 0, speed.isFinite,
                  motion.velocityMethod == "inverse_aeqd_geodesic_1s",
                  let velocityReferencePoint = motion.velocityReferencePoint,
                  velocityReferencePoint.count == 2,
                  velocityReferencePoint[0].isFinite, velocityReferencePoint[1].isFinite,
                  (-180...180).contains(velocityReferencePoint[0]),
                  (-90...90).contains(velocityReferencePoint[1]),
                  geolocation.status == "validated", coverage.status == "available",
                  !motion.pairDiagnostics.isEmpty,
                  motion.pairDiagnostics.allSatisfy({ $0.isValidAvailable(in: domain) }),
                  motion.pairDiagnostics.allSatisfy({ pair in
                      frameIDs.contains(pair.fromFrameID) && frameIDs.contains(pair.toFrameID)
                  })
            else { return false }
            if speed == 0 { return motion.bearingDegTrue == nil }
            return motion.bearingDegTrue.map { $0.isFinite && (0..<360).contains($0) } == true
        }
        return motion.status == "unavailable" && !motion.reasonCodes.isEmpty
    }
}

struct ObservedMotionAssociation: Decodable, Identifiable, Sendable {
    let associationID: String
    let radarFeatureID: String
    let cloudFeatureID: String
    let status: String
    let reasonCodes: [String]
    let relation: String?
    let comparisonAt: String?
    let intersectionAreaKM2: Double?
    let radarOverlapFraction: Double?
    let cloudOverlapFraction: Double?
    let edgeDistanceNM: Double?
    var id: String { associationID }
    enum CodingKeys: String, CodingKey {
        case associationID = "association_id", radarFeatureID = "radar_feature_id", cloudFeatureID = "cloud_feature_id"
        case status, reasonCodes = "reason_codes", relation, comparisonAt = "comparison_at"
        case intersectionAreaKM2 = "intersection_area_km2", radarOverlapFraction = "radar_overlap_fraction"
        case cloudOverlapFraction = "cloud_overlap_fraction", edgeDistanceNM = "edge_distance_nm"
    }
}

struct ObservedMotionLightning: Decodable, Identifiable, Sendable {
    let detectionID: String
    let position: [Double]
    let timePrecision: String
    let eventAt: String?
    let acquisitionWindow: ObservedMotionInterval
    let reasonCodes: [String]
    let associationStatus: String
    let associationReasonCodes: [String]
    let associatedFeatureIDs: [String]?
    var id: String { detectionID }
    enum CodingKeys: String, CodingKey {
        case detectionID = "detection_id", position, timePrecision = "time_precision", eventAt = "event_at"
        case acquisitionWindow = "acquisition_window", reasonCodes = "reason_codes"
        case associationStatus = "association_status", associationReasonCodes = "association_reason_codes"
        case associatedFeatureIDs = "associated_feature_ids"
    }
}

struct ObservedMotionCompleteness: Decodable, Identifiable, Sendable {
    let category: String
    let status: String
    let reasonCodes: [String]
    let consideredCount: Int?
    let emittedCount: Int
    let omittedCount: Int?
    var id: String { category }
    enum CodingKeys: String, CodingKey {
        case category, status, reasonCodes = "reason_codes", consideredCount = "considered_count"
        case emittedCount = "emitted_count", omittedCount = "omitted_count"
    }
}

/// Byte-oriented top-level JSON object access. It slices values from the source
/// without decoding/re-encoding them, so opaque identifier keys and future fields
/// keep their original spelling in cache round trips.
struct RawJSONDocument: Sendable {
    let data: Data

    init(_ data: Data) { self.data = data }

    func member(named wanted: String) -> Data? {
        members().first(where: { $0.key == wanted })?.value
    }

    func members() -> [(key: String, value: Data)] {
        let bytes = [UInt8](data)
        var index = 0
        skipWhitespace(bytes, &index)
        guard index < bytes.count, bytes[index] == 0x7b else { return [] }
        index += 1
        var result: [(String, Data)] = []
        while index < bytes.count {
            skipWhitespace(bytes, &index)
            if index < bytes.count, bytes[index] == 0x7d { return result }
            guard let keyRange = scanString(bytes, &index),
                  let key = decodeJSONString(data.subdata(in: keyRange))
            else { return [] }
            skipWhitespace(bytes, &index)
            guard index < bytes.count, bytes[index] == 0x3a else { return [] }
            index += 1
            skipWhitespace(bytes, &index)
            let start = index
            guard scanValue(bytes, &index) else { return [] }
            result.append((key, data.subdata(in: start..<index)))
            skipWhitespace(bytes, &index)
            if index < bytes.count, bytes[index] == 0x2c { index += 1; continue }
            if index < bytes.count, bytes[index] == 0x7d { return result }
            return []
        }
        return []
    }

    func replacingMember(named wanted: String, with replacement: Data?) throws -> Data {
        let bytes = [UInt8](data)
        var index = 0
        skipWhitespace(bytes, &index)
        guard index < bytes.count, bytes[index] == 0x7b else { throw CocoaError(.coderReadCorrupt) }
        index += 1
        while index < bytes.count {
            skipWhitespace(bytes, &index)
            if index < bytes.count, bytes[index] == 0x7d {
                guard let replacement else { return data }
                let key = try JSONSerialization.data(withJSONObject: wanted, options: [.fragmentsAllowed])
                let prefix = data.subdata(in: 0..<index)
                let comma = members().isEmpty ? Data() : Data(",".utf8)
                return prefix + comma + key + Data(":".utf8) + replacement + data.subdata(in: index..<data.count)
            }
            guard let keyRange = scanString(bytes, &index),
                  let key = decodeJSONString(data.subdata(in: keyRange))
            else { throw CocoaError(.coderReadCorrupt) }
            skipWhitespace(bytes, &index)
            guard index < bytes.count, bytes[index] == 0x3a else { throw CocoaError(.coderReadCorrupt) }
            index += 1
            skipWhitespace(bytes, &index)
            let valueStart = index
            guard scanValue(bytes, &index) else { throw CocoaError(.coderReadCorrupt) }
            if key == wanted {
                guard let replacement else { return data }
                return data.subdata(in: 0..<valueStart) + replacement + data.subdata(in: index..<data.count)
            }
            skipWhitespace(bytes, &index)
            if index < bytes.count, bytes[index] == 0x2c { index += 1; continue }
            if index < bytes.count, bytes[index] == 0x7d {
                guard let replacement else { return data }
                let encodedKey = try JSONSerialization.data(
                    withJSONObject: wanted, options: [.fragmentsAllowed])
                return data.subdata(in: 0..<index)
                    + Data(",".utf8) + encodedKey + Data(":".utf8) + replacement
                    + data.subdata(in: index..<data.count)
            }
            throw CocoaError(.coderReadCorrupt)
        }
        return data
    }

    private func skipWhitespace(_ bytes: [UInt8], _ index: inout Int) {
        while index < bytes.count, [0x20, 0x09, 0x0a, 0x0d].contains(bytes[index]) { index += 1 }
    }

    private func scanString(_ bytes: [UInt8], _ index: inout Int) -> Range<Int>? {
        guard index < bytes.count, bytes[index] == 0x22 else { return nil }
        let start = index
        index += 1
        var escaped = false
        while index < bytes.count {
            let byte = bytes[index]
            index += 1
            if escaped { escaped = false; continue }
            if byte == 0x5c { escaped = true; continue }
            if byte == 0x22 { return start..<index }
        }
        return nil
    }

    private func scanValue(_ bytes: [UInt8], _ index: inout Int) -> Bool {
        guard index < bytes.count else { return false }
        if bytes[index] == 0x22 { return scanString(bytes, &index) != nil }
        if bytes[index] == 0x7b || bytes[index] == 0x5b {
            var stack: [UInt8] = []
            while index < bytes.count {
                if bytes[index] == 0x22 {
                    guard scanString(bytes, &index) != nil else { return false }
                    continue
                }
                if bytes[index] == 0x7b { stack.append(0x7d); index += 1; continue }
                if bytes[index] == 0x5b { stack.append(0x5d); index += 1; continue }
                if bytes[index] == 0x7d || bytes[index] == 0x5d {
                    guard stack.last == bytes[index] else { return false }
                    stack.removeLast()
                    index += 1
                    if stack.isEmpty { return true }
                    continue
                }
                index += 1
            }
            return false
        }
        let start = index
        while index < bytes.count, ![0x2c, 0x7d, 0x5d, 0x20, 0x09, 0x0a, 0x0d].contains(bytes[index]) { index += 1 }
        return index > start
    }

    private func decodeJSONString(_ data: Data) -> String? {
        guard let value = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) else {
            return nil
        }
        return value as? String
    }
}
