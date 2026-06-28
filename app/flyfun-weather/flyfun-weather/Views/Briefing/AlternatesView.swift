import SwiftUI

/// Weather-based divert candidates (#310 / #210). Mirrors the web
/// `renderRouteAlternates`: a destination header, planning-grade caveat,
/// nearest-improving picks, and a ranked candidate list. Defaults to the
/// concise near-destination view (hide majors, ≤ 100 nm); the pilot can relax
/// both. Rendered only when `snapshot.alternates` is present (D-2 inward).
struct AlternatesView: View {
    let viewModel: BriefingViewModel

    @State private var hideMajor = true
    @State private var maxDistNm: Double? = 100

    private var alternates: RouteAlternates? {
        if case .loaded(let snapshot) = viewModel.snapshotState { return snapshot.alternates }
        return nil
    }

    var body: some View {
        if let alt = alternates {
            VStack(alignment: .leading, spacing: Theme.spacingM) {
                header(alt)
                Text("Planning-grade divert candidates that improve on the destination weather — not an operational alternate (no fuel, minima, NOTAM, customs or PPR check).")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)

                requirementBanner(alt.alternateRequirement)

                if alt.approachFilterRelaxed == true {
                    Label("No published-approach data was available, so non-VFR fields could not be filtered by approach — confirm an approach independently.",
                          systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(Theme.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }

                picks(alt)
                controls
                candidateList(alt)
                summaryLine(alt)
            }
            .padding(.horizontal, Theme.cardPadding)
        }
    }

    // MARK: Header

    @ViewBuilder
    private func header(_ alt: RouteAlternates) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            Text("Weather Alternates")
                .font(.headline)
                .foregroundStyle(Theme.text)
            HStack(spacing: Theme.spacingS) {
                Text("Destination \(alt.destinationIcao)")
                    .font(.subheadline)
                    .foregroundStyle(Theme.textMuted)
                FlightCategoryBadge(category: alt.destinationCategory)
                if let xw = alt.destinationCrosswindKt {
                    Text("\(Int(xw.rounded()))kt xwind")
                        .font(.caption)
                        .foregroundStyle(Theme.textMuted)
                }
            }
        }
    }

    // MARK: Destination "alternate required?" banner

    @ViewBuilder
    private func requirementBanner(_ req: AlternateRequirement?) -> some View {
        if let req, req.faa != nil || req.easa != nil {
            HStack(spacing: Theme.spacingM) {
                if let faa = req.faa { regimeChip(label: "FAA", trigger: faa) }
                if let easa = req.easa { regimeChip(label: "EASA", trigger: easa) }
                Spacer(minLength: 0)
            }
            .padding(Theme.spacingS)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.border, lineWidth: 0.5))
        }
    }

    private func regimeChip(label: String, trigger: RegAlternateTrigger) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(label) alternate")
                .font(.caption2)
                .foregroundStyle(Theme.textMuted)
            Text(verdictLabel(trigger.status))
                .font(.caption.weight(.semibold))
                .foregroundStyle(verdictColor(trigger.status))
        }
    }

    // MARK: Nearest-improving picks

    @ViewBuilder
    private func picks(_ alt: RouteAlternates) -> some View {
        let valid = alt.nearestImproving.filter { $0.icao != nil }
        if valid.isEmpty {
            Text("No weather alternate improves on the destination across the evaluated candidates.")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
        } else {
            VStack(alignment: .leading, spacing: Theme.spacingXS) {
                ForEach(valid) { pick in
                    HStack(spacing: Theme.spacingXS) {
                        Image(systemName: "arrow.triangle.branch").font(.caption2).foregroundStyle(Theme.primary)
                        Text("Nearest \(pick.axisLabel): ")
                            .font(.caption).foregroundStyle(Theme.textMuted)
                        + Text(pick.icao ?? "—").font(.caption.weight(.semibold)).foregroundStyle(Theme.text)
                        + Text(pick.distanceFromDestNm.map { " \(Int($0.rounded()))nm" } ?? "")
                            .font(.caption).foregroundStyle(Theme.textMuted)
                        + Text(pick.position.map { " \($0)" } ?? "")
                            .font(.caption).foregroundStyle(Theme.textMuted)
                    }
                }
            }
        }
    }

    // MARK: View controls (hide major / within distance)

    private var controls: some View {
        HStack(spacing: Theme.spacingM) {
            Toggle(isOn: $hideMajor) {
                Text("Hide major").font(.caption)
            }
            .toggleStyle(.button)
            .controlSize(.small)

            Picker("Within", selection: Binding(
                get: { maxDistNm ?? -1 },
                set: { maxDistNm = $0 < 0 ? nil : $0 }
            )) {
                Text("50 nm").tag(50.0)
                Text("100 nm").tag(100.0)
                Text("All").tag(-1.0)
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 220)
            Spacer(minLength: 0)
        }
    }

    // MARK: Candidate list

    private func visibleCandidates(_ alt: RouteAlternates) -> [AlternateAirport] {
        alt.alternates.filter { apt in
            let majorOK = !hideMajor || (apt.isMajor != true)
            let distOK = maxDistNm == nil || apt.distanceFromDestNm <= (maxDistNm ?? .infinity)
            return majorOK && distOK
        }
    }

    @ViewBuilder
    private func candidateList(_ alt: RouteAlternates) -> some View {
        let visible = visibleCandidates(alt)
        if visible.isEmpty {
            Text("No candidates match the current filters — try “Hide major” off or a larger distance.")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
        } else {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 300), spacing: Theme.spacingM)],
                      alignment: .leading, spacing: Theme.spacingM) {
                ForEach(visible) { apt in
                    AlternateCard(apt: apt)
                }
            }
        }
    }

    @ViewBuilder
    private func summaryLine(_ alt: RouteAlternates) -> some View {
        let visible = visibleCandidates(alt)
        let evaluated = alt.candidatesEvaluated ?? alt.alternates.count
        let distLabel = maxDistNm.map { " within \(Int($0))nm" } ?? ""
        Text("\(evaluated) evaluated · \(visible.count) shown\(distLabel)")
            .font(.caption2)
            .foregroundStyle(Theme.textMuted)
    }

    // MARK: Verdict helpers

    private func verdictLabel(_ status: String) -> String {
        switch status {
        case "required": "Required"
        case "not_required": "Not required"
        case "likely": "Likely"
        case "marginal": "Marginal"
        case "unlikely": "Unlikely"
        default: status.capitalized
        }
    }

    private func verdictColor(_ status: String) -> Color {
        switch status {
        case "required", "unlikely": Theme.red
        case "marginal": Theme.amber
        case "not_required", "likely": Theme.green
        default: Theme.textMuted
        }
    }
}

/// One divert candidate as a card. Geometry (before/after + detour), consensus
/// category + winds, approach tier, regulatory qual, and the vs-dest "Better"
/// badge.
private struct AlternateCard: View {
    let apt: AlternateAirport

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            HStack(spacing: Theme.spacingS) {
                Text(apt.icao)
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                if apt.isMajor == true {
                    Text("MAJOR")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(Theme.textMuted)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(Theme.border, in: Capsule())
                }
                Spacer()
                if apt.dominatesDestination == true {
                    Text("Better")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Theme.green, in: Capsule())
                }
            }

            if let name = apt.name, !name.isEmpty {
                Text(name)
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                    .lineLimit(1)
            }

            HStack(spacing: Theme.spacingS) {
                FlightCategoryBadge(category: apt.flightCategory)
                if let agree = apt.agreement?["flight_category"] {
                    Text(agree)
                        .font(.caption2)
                        .foregroundStyle(Theme.textMuted)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(Theme.surface, in: Capsule())
                        .overlay(Capsule().stroke(Theme.border, lineWidth: 0.5))
                }
                Spacer()
                Text("\(Int(apt.distanceFromDestNm.rounded()))nm")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(Theme.textMuted)
            }

            // Geometry: before/after + detour pair.
            HStack(spacing: Theme.spacingXS) {
                Text(apt.position.capitalized)
                    .font(.caption)
                    .foregroundStyle(Theme.text)
                if let detour = apt.detourLabel {
                    Text("(\(detour))")
                        .font(.caption)
                        .foregroundStyle(Theme.textMuted)
                }
            }

            // Winds + approach.
            HStack(spacing: Theme.spacingM) {
                if let wind = apt.windSpeedKt {
                    Label("\(Int(wind.rounded()))kt", systemImage: "wind").font(.caption)
                }
                if let xw = apt.crosswindKt {
                    Label("\(Int(xw.rounded()))kt xw", systemImage: "arrow.left.and.right").font(.caption)
                }
                if apt.hasInstrumentApproach == true {
                    Text(apt.bestApproachType ?? "IAP")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(Theme.primary)
                }
                Spacer(minLength: 0)
            }
            .foregroundStyle(Theme.textMuted)

            // Regulatory qual chips.
            if apt.faa != nil || apt.easa != nil {
                HStack(spacing: Theme.spacingS) {
                    if let faa = apt.faa { qualChip("FAA", faa.verdict) }
                    if let easa = apt.easa { qualChip("EASA", easa.verdict) }
                    Spacer(minLength: 0)
                }
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    private func qualChip(_ label: String, _ verdict: String) -> some View {
        let color: Color = switch verdict {
        case "likely": Theme.green
        case "marginal": Theme.amber
        case "unlikely": Theme.red
        default: Theme.textMuted
        }
        return Text("\(label): \(verdict.capitalized)")
            .font(.caption2.weight(.medium))
            .foregroundStyle(color)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.12), in: Capsule())
    }
}
