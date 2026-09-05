import Foundation
import Observation

@Observable
@MainActor
final class ObservedMotionState {
    private(set) var raw: RawObservedMotion?
    private(set) var capability: ObservedMotionCapability = .unknown
    private(set) var origin: ObservedMotionOrigin = .network
    private(set) var packIdentity: String?
    private(set) var routeGeometryID: String?
    private(set) var generation = 0
    private(set) var contractIssue: ObservedMotionPresentationIssue?
    private(set) var refreshFailure: String?
    private(set) var currentResponseMissingMotion = false
    private(set) var now = Date()
    private var latestNetworkResponseSequence = 0

    var modeEnabled = false
    var enabledFamilies = Set(ObservedMotionFamily.allCases)
    private(set) var selectedFeatureID: String?
    private(set) var selectedAssociationID: String?
    private(set) var selectedProjection: Date?

    var envelope: ObservedMotionEnvelope? { raw?.typed }
    var envelopeStatus: String? { envelope?.status ?? raw?.status }

    func start(packIdentity: String, routeGeometryID: String?) {
        generation &+= 1
        latestNetworkResponseSequence = 0
        self.packIdentity = packIdentity
        self.routeGeometryID = routeGeometryID
        capability = .unknown
        raw = nil
        origin = .network
        contractIssue = nil
        refreshFailure = nil
        currentResponseMissingMotion = false
        resetSelection()
    }

    @discardableResult
    func ensureContext(packIdentity: String, routeGeometryID: String?) -> Bool {
        guard self.packIdentity != packIdentity || self.routeGeometryID != routeGeometryID else { return false }
        start(packIdentity: packIdentity, routeGeometryID: routeGeometryID)
        return true
    }

    func beginCapabilityCheck() -> Int {
        generation &+= 1
        capability = .unknown
        return generation
    }

    func cancelLifecycleCallbacks() {
        generation &+= 1
        if modeEnabled { capability = .unknown }
    }

    func accept(
        raw candidate: RawObservedMotion?,
        capability newCapability: ObservedMotionCapability? = nil,
        capabilitySequence: Int? = nil,
        origin: ObservedMotionOrigin = .network,
        generation expectedGeneration: Int? = nil,
        now: Date = Date()
    ) {
        if let expectedGeneration, expectedGeneration != generation { return }
        self.now = now
        var isCurrentNetworkResponse = true
        if origin == .network, let capabilitySequence {
            isCurrentNetworkResponse = capabilitySequence >= latestNetworkResponseSequence
            if isCurrentNetworkResponse {
                latestNetworkResponseSequence = capabilitySequence
                capability = newCapability ?? .unknown
            }
        } else if let newCapability {
            capability = newCapability
        }
        guard let candidate else {
            if origin != .network || isCurrentNetworkResponse {
                if origin == .network { refreshFailure = nil }
                currentResponseMissingMotion = true
            }
            return
        }
        guard let candidateRevision = candidate.revision else {
            guard isCurrentNetworkResponse else { return }
            if isCurrentNetworkResponse {
                self.origin = origin
                refreshFailure = nil
                currentResponseMissingMotion = false
            }
            contractIssue = .malformed
            return
        }
        if let routeGeometryID,
           let candidateRoute = candidate.typed?.routeGeometryID,
           candidateRoute != routeGeometryID {
            contractIssue = .identityMismatch
            return
        }
        if routeGeometryID == nil, let suppliedRoute = candidate.typed?.routeGeometryID {
            routeGeometryID = suppliedRoute
        }
        if let current = raw, let currentRevision = current.revision {
            if candidateRevision < currentRevision { return }
            if candidateRevision == currentRevision {
                guard current.hasSameJSONValue(as: candidate) else {
                    contractIssue = .sameRevisionConflict
                    return
                }
                if isCurrentNetworkResponse { self.origin = origin }
                contractIssue = candidate.presentationIssue
                if isCurrentNetworkResponse {
                    refreshFailure = nil
                    currentResponseMissingMotion = false
                }
                return
            }
        }
        let previousIdentity = raw?.typed.map { "\($0.revision)|\($0.runID ?? "nil")|\($0.routeGeometryID)" }
        raw = candidate
        if isCurrentNetworkResponse { self.origin = origin }
        contractIssue = candidate.presentationIssue
        if isCurrentNetworkResponse {
            refreshFailure = nil
            currentResponseMissingMotion = false
        }
        let nextIdentity = candidate.typed.map { "\($0.revision)|\($0.runID ?? "nil")|\($0.routeGeometryID)" }
        if previousIdentity != nextIdentity { resetSelection() }
    }

    func observeCapability(_ capability: ObservedMotionCapability, generation expectedGeneration: Int) {
        guard expectedGeneration == generation else { return }
        self.capability = capability
    }

    func markCapabilityFailure(_ error: Error?, generation expectedGeneration: Int) {
        guard expectedGeneration == generation else { return }
        capability = .unknown
        if let error { refreshFailure = error.localizedDescription }
    }

    func markRefreshFailure(_ message: String) {
        refreshFailure = message
    }

    func selectFeature(_ id: String?) {
        selectedFeatureID = id
        selectedAssociationID = nil
    }

    func selectAssociation(_ id: String?) {
        selectedAssociationID = id
        guard let association = envelope?.associations.first(where: { $0.id == id }) else { return }
        selectedFeatureID = association.radarFeatureID
    }

    func selectObserved() { selectedProjection = nil }

    func selectProjection(_ date: Date, now: Date = Date()) {
        selectedProjection = date
        updateClock(now)
    }

    func updateClock(_ date: Date = Date()) { now = date }

    var selectedFeature: ObservedMotionFeature? {
        guard let selectedFeatureID else { return nil }
        return envelope?.features.first { $0.featureID == selectedFeatureID }
    }

    var selectedAssociation: ObservedMotionAssociation? {
        guard let selectedAssociationID else { return nil }
        return envelope?.associations.first { $0.associationID == selectedAssociationID }
    }

    var canPresentActivePrediction: Bool {
        guard modeEnabled || selectedProjection != nil,
              capability == .enabled, origin.isStoredOnly == false,
              let envelope, envelope.status == "available",
              contractIssue == nil, refreshFailure == nil, !currentResponseMissingMotion, !isClockUncertain,
              let selectedProjection,
              selectedProjection > now,
              let expiry = envelope.expiryDate, now <= expiry,
              selectedProjection <= expiry
        else { return false }
        return envelope.features.contains { feature in
            enabledFamilies.contains(feature.family)
                && feature.projections.contains { $0.at == isoString(selectedProjection) && $0.status == "available" }
        }
    }

    var canInspectStoredAnalysis: Bool { envelope != nil }

    var isClockUncertain: Bool {
        guard let cutoff = envelope?.cutoffDate else { return false }
        return now < cutoff.addingTimeInterval(-60)
    }

    var presentationReasons: [String] {
        var reasons: [String] = []
        if origin.isStoredOnly { reasons.append("stored_analysis") }
        if capability == .unknown { reasons.append("capability_unknown") }
        if capability == .disabled { reasons.append("observed_disabled") }
        if let envelope, envelope.status != "available" {
            reasons.append(contentsOf: envelope.reasonCodes.isEmpty ? [envelope.status] : envelope.reasonCodes)
        }
        if isClockUncertain { reasons.append("clock_uncertain") }
        if let selectedProjection, selectedProjection <= now { reasons.append("expired") }
        if let expiry = envelope?.expiryDate, now > expiry { reasons.append("expired") }
        if let contractIssue { reasons.append(contractIssue.rawValue) }
        if refreshFailure != nil { reasons.append("refresh_failed") }
        if currentResponseMissingMotion { reasons.append("refresh_needed") }
        return Array(Set(reasons)).sorted()
    }

    var projectionLabel: String {
        guard let selectedProjection else { return "Observed" }
        return Self.utcDateTime.string(from: selectedProjection)
    }

    private func resetSelection() {
        selectedFeatureID = nil
        selectedAssociationID = nil
        selectedProjection = nil
    }

    private func isoString(_ date: Date) -> String {
        Self.iso8601.string(from: date)
    }

    private static let iso8601: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let utcDateTime: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_GB_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "dd MMM yyyy HH:mm'Z'"
        return formatter
    }()
}
