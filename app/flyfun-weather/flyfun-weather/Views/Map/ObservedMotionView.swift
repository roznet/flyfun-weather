import SwiftUI

struct ObservedMotionView: View {
    @Bindable var state: ObservedMotionState
    let refreshCapability: () async -> Void
    let leaveMode: () -> Void

    var body: some View {
        VStack(spacing: Theme.spacingS) {
            controls
            Spacer()
            details
        }
        .padding(Theme.spacingM)
        .accessibilityIdentifier("observedMotionControls")
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                if !Task.isCancelled { state.updateClock() }
            }
        }
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("Experimental motion", systemImage: "move.3d")
                    .font(.caption.weight(.semibold))
                Spacer()
                Button("Check availability") { Task { await refreshCapability() } }
                    .font(.caption)
                Button(action: leaveMode) {
                    Image(systemName: "xmark.circle.fill")
                }
                .accessibilityLabel("Close experimental motion")
            }
            HStack(spacing: 8) {
                Button("Observed") { state.selectObserved() }
                    .buttonStyle(.borderedProminent)
                    .tint(state.selectedProjection == nil ? .accentColor : .gray)
                ForEach(state.envelope?.projectionTimes ?? [], id: \.self) { time in
                    if let date = Date.parseISO8601(time) {
                        Button(Self.utcProjection.string(from: date)) { state.selectProjection(time) }
                            .buttonStyle(.bordered)
                            .disabled(state.isClockUncertain)
                    }
                }
            }
            .font(.caption)
            HStack {
                ForEach(ObservedMotionFamily.allCases, id: \.self) { family in
                    Toggle(family.label, isOn: familyBinding(family))
                        .toggleStyle(.button)
                        .disabled(sourceUnavailable(family))
                }
            }
            ForEach(ObservedMotionFamily.allCases, id: \.self) { family in
                if sourceUnavailable(family) {
                    Text("\(family.label): no supported feature in this analysis")
                        .font(.caption2).foregroundStyle(Theme.textMuted)
                }
            }
            ForEach(state.envelope?.sources.filter { $0.status != "available" } ?? []) { source in
                Text("\(source.attribution): \(source.reasonCodes.isEmpty ? "unavailable" : source.reasonCodes.joined(separator: ", "))")
                    .font(.caption2).foregroundStyle(Theme.textMuted)
            }
            statusLine
        }
        .padding(10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder private var statusLine: some View {
        if !state.presentationReasons.isEmpty {
            Text(state.presentationReasons.map(Self.reasonLabel).joined(separator: " · "))
                .font(.caption2).foregroundStyle(Theme.textMuted)
        } else if state.selectedProjection != nil {
            Text("Experimental constant-motion projection · \(state.projectionLabel)")
                .font(.caption2).foregroundStyle(Theme.textMuted)
        } else {
            Text("Observed outlines and source-timed trails")
                .font(.caption2).foregroundStyle(Theme.textMuted)
        }
    }

    private var details: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(visibleFeatures) { feature in
                    Button { state.selectFeature(feature.featureID) } label: {
                        featureRow(feature)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("observedMotionFeature-\(feature.featureID)")
                }
                if let selected = state.selectedFeature { featureCard(selected) }
                if let association = state.selectedAssociation { associationCard(association) }
                associationRows
            }
            .padding(10)
        }
        .frame(maxHeight: 330)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
        .accessibilityIdentifier("observedMotionFeatureList")
    }

    private var visibleFeatures: [ObservedMotionFeature] {
        (state.envelope?.features ?? []).filter { state.enabledFamilies.contains($0.family) }
    }

    private func featureRow(_ feature: ObservedMotionFeature) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(feature.family.label).font(.caption.weight(.semibold))
                Text("Observed \(Self.utcDateTime.string(from: Date.parseISO8601(feature.referenceAt) ?? .distantPast))")
                    .font(.caption2).foregroundStyle(Theme.textMuted)
            }
            Spacer()
            if feature.motion.status == "accepted", let speed = feature.motion.groundSpeedKt {
                Text("\(speed, format: .number.precision(.fractionLength(0))) kt")
                    .font(.tabularData(.caption))
            } else {
                Text("Motion unavailable").font(.caption2).foregroundStyle(Theme.textMuted)
            }
        }
        .padding(8)
        .background(state.selectedFeatureID == feature.featureID ? Color.accentColor.opacity(0.15) : .clear,
                    in: RoundedRectangle(cornerRadius: 8))
    }

    private func featureCard(_ feature: ObservedMotionFeature) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Selected evidence").font(.caption.weight(.bold))
            if let speed = feature.motion.groundSpeedKt {
                Text("Ground velocity: \(speed, format: .number.precision(.fractionLength(0))) kt\(bearing(feature.motion.bearingDegTrue))")
            }
            if let projectionIssue = projectionIssue(for: feature) {
                Text(projectionIssue).font(.caption).foregroundStyle(Theme.textMuted)
            }
            Text("Support: \(feature.coverage.status)\(supportFraction(feature.coverage)) · registration: \(feature.geolocation.status)")
                .font(.caption)
            if let residual = feature.motion.fitRMSResidualCells {
                Text("In-sample fit RMS: \(residual.formatted(.number.precision(.fractionLength(2)))) grid cells")
                    .font(.caption)
            }
            ForEach(feature.motion.pairDiagnostics) { pair in
                Text(pairDiagnosticsLine(pair)).font(.caption2)
            }
            ForEach(feature.observations) { observation in
                Text(observationLine(observation)).font(.caption)
            }
            if let count = feature.lightningEvidence.reportedDetectionCount {
                Text("Reported lightning detections: \(count) in evaluated source window")
                    .font(.caption)
            } else {
                Text("Lightning evidence unavailable").font(.caption)
            }
            ForEach(rows(for: feature)) { row in
                Text("\(row.fromLabel)–\(row.toLabel) at \(rowTime(row)): \(distance(row)) · \(closure(row)) · \(plannedOverlap(row))")
                    .font(.caption)
            }
            if feature.plannedOverlap.status == "available" {
                ForEach(feature.plannedOverlap.intervals) { interval in
                    Text("Approximate planned overlap \(Self.shortInterval(interval))")
                        .font(.caption)
                }
            }
            let warnings = feature.reasonCodes + feature.coverage.reasonCodes
                + feature.geolocation.reasonCodes + feature.motion.reasonCodes
            if !warnings.isEmpty {
                Text("Limitations: \(warnings.joined(separator: ", "))")
                    .font(.caption2).foregroundStyle(Theme.textMuted)
            }
            let incomplete = (state.envelope?.completeness ?? []).filter { $0.status != "complete" }
            if !incomplete.isEmpty {
                Text("Incomplete/truncated: \(incomplete.map(\.category).joined(separator: ", "))")
                    .font(.caption2).foregroundStyle(Theme.textMuted)
            }
            Text("Analysis context only — not a route verdict, alarm, clearance, probability or forecast-skill claim.")
                .font(.caption2).foregroundStyle(Theme.textMuted)
        }
        .padding(8)
    }

    @ViewBuilder private var associationRows: some View {
        ForEach(state.envelope?.associations.filter { $0.status == "available" } ?? []) { association in
            Button {
                state.selectAssociation(association.associationID)
            } label: {
                Text("Radar/cloud \(association.relation ?? "association") at \(association.comparisonAt ?? "unknown time")")
                    .font(.caption)
            }
        }
    }

    private func associationCard(_ association: ObservedMotionAssociation) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Source-timed radar/cloud association").font(.caption.weight(.bold))
            Text("\((association.relation ?? "unavailable").capitalized) at \(association.comparisonAt ?? "unknown time")")
                .font(.caption)
            if let edge = association.edgeDistanceNM {
                Text("Contour edge distance: \(edge.formatted(.number.precision(.fractionLength(1)))) NM")
                    .font(.caption)
            }
            if let area = association.intersectionAreaKM2 {
                Text("Supported contour intersection: \(area.formatted(.number.precision(.fractionLength(1)))) km²")
                    .font(.caption)
            }
            Text("Contours retain their independent observed vectors.")
                .font(.caption2).foregroundStyle(Theme.textMuted)
        }
        .padding(8)
    }

    private func familyBinding(_ family: ObservedMotionFamily) -> Binding<Bool> {
        Binding(get: { state.enabledFamilies.contains(family) }, set: { enabled in
            if enabled { state.enabledFamilies.insert(family) } else { state.enabledFamilies.remove(family) }
        })
    }

    private func sourceUnavailable(_ family: ObservedMotionFamily) -> Bool {
        let matching = state.envelope?.features.contains(where: { $0.family == family }) ?? false
        return !matching
    }

    private func rows(for feature: ObservedMotionFeature) -> [ObservedMotionRouteRow] {
        guard let selected = state.selectedProjectionTime else {
            return feature.routeRows.filter { $0.at == feature.referenceAt }
        }
        return feature.routeRows.filter { $0.at == selected }
    }

    private func projectionIssue(for feature: ObservedMotionFeature) -> String? {
        guard let selected = state.selectedProjectionTime else { return nil }
        guard let projection = feature.projections.first(where: { $0.at == selected }) else {
            return "Projection unavailable: unsupported time"
        }
        guard projection.status != "available" else { return nil }
        let reasons = projection.reasonCodes.isEmpty ? ["unavailable"] : projection.reasonCodes
        return "Projection unavailable: \(reasons.map(Self.reasonLabel).joined(separator: ", "))"
    }

    private func supportFraction(_ support: ObservedMotionSupport) -> String {
        support.knownFraction.map {
            " (\(($0 * 100).formatted(.number.precision(.fractionLength(0))))% known cells)"
        } ?? ""
    }

    private func pairDiagnosticsLine(_ pair: ObservedMotionPairDiagnostics) -> String {
        var parts = ["\(pair.fromFrameID)→\(pair.toFrameID)", "\(Int(pair.elapsedSeconds.rounded())) s"]
        if let dx = pair.forwardDXCells, let dy = pair.forwardDYCells {
            parts.append("match Δ \(dx.formatted(.number.precision(.fractionLength(2)))), \(dy.formatted(.number.precision(.fractionLength(2)))) cells")
        }
        if let disagreement = pair.patchDisagreementCells {
            parts.append("patch spread \(disagreement.formatted(.number.precision(.fractionLength(2)))) cells")
        }
        if let reverse = pair.reverseResidualCells {
            parts.append("reverse residual \(reverse.formatted(.number.precision(.fractionLength(2)))) cells")
        }
        if let next = pair.nextObservationResidualCells {
            parts.append("next-observation residual \(next.formatted(.number.precision(.fractionLength(2)))) cells")
        }
        parts.append(pair.lineageComplete ? "lineage evaluated" : "lineage incomplete")
        return parts.joined(separator: " · ")
    }

    private func rowTime(_ row: ObservedMotionRouteRow) -> String {
        Date.parseISO8601(row.at).map { Self.utcDateTime.string(from: $0) } ?? row.at
    }

    private func plannedOverlap(_ row: ObservedMotionRouteRow) -> String {
        switch row.plannedOverlapAtTime {
        case true: "planned position overlaps at this instant"
        case false: "no planned overlap at this instant"
        case nil: "planned overlap unavailable"
        }
    }

    private func bearing(_ value: Double?) -> String {
        value.map { " @ \(Int($0.rounded()).paddedHeading)° true" } ?? ""
    }

    private func observationLine(_ observation: ObservedMotionObservation) -> String {
        guard observation.status == "available", let value = observation.value else {
            return "\(observation.kind.replacingOccurrences(of: "_", with: " ")) unavailable"
        }
        let time = observation.observedAt.flatMap(Date.parseISO8601)
            .map { Self.utcDateTime.string(from: $0) } ?? "unknown time"
        var line = "\(observation.kind.replacingOccurrences(of: "_", with: " ")): \(value.formatted(.number.precision(.fractionLength(1)))) \(observation.unit) at \(time)"
        if let temperature = observation.pairedTemperatureK {
            line += " (paired \((temperature - 273.15).formatted(.number.precision(.fractionLength(0)))) °C)"
        }
        return line
    }

    private func distance(_ row: ObservedMotionRouteRow) -> String {
        row.distanceNM.map { "\($0.formatted(.number.precision(.fractionLength(1)))) NM" } ?? "distance unavailable"
    }

    private func closure(_ row: ObservedMotionRouteRow) -> String {
        row.closureKt.map { "\($0.formatted(.number.precision(.fractionLength(0)))) kt closure (\(row.relationship))" }
            ?? row.relationship.replacingOccurrences(of: "_", with: " ")
    }

    private static func shortInterval(_ interval: ObservedMotionOverlapInterval) -> String {
        guard let start = Date.parseISO8601(interval.startAt), let end = Date.parseISO8601(interval.endAt) else {
            return "time unavailable"
        }
        return "\(utcDateTime.string(from: start))–\(utcDateTime.string(from: end)) (\(interval.contact))"
    }

    private static func reasonLabel(_ code: String) -> String {
        switch code {
        case "capability_unknown": "Live capability unknown — stored analysis only"
        case "stored_analysis": "Stored analysis"
        case "clock_uncertain": "Device clock is inconsistent with source time"
        case "expired": "Selected projection has expired"
        case "observed_disabled": "Experimental motion is disabled by the server"
        case "refresh_failed": "Availability check failed — stored analysis remains dated"
        case "refresh_needed": "This server response has no motion block — refresh needed"
        default: code.replacingOccurrences(of: "_", with: " ")
        }
    }

    private static let utcProjection: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_GB_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "dd MMM yyyy HH:mm'Z'"
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
