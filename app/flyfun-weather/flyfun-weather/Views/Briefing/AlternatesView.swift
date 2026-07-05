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
                    HStack(alignment: .firstTextBaseline, spacing: Theme.spacingXS) {
                        Image(systemName: "arrow.triangle.branch").font(.caption2).foregroundStyle(Theme.primary)
                        (
                            Text("Nearest \(pick.axisLabel): ")
                                .font(.caption).foregroundStyle(Theme.textMuted)
                            + Text(pick.icao ?? "—").font(.caption.weight(.semibold)).foregroundStyle(Theme.text)
                            + Text(pick.distanceFromDestNm.map { " \(Int($0.rounded()))nm" } ?? "")
                                .font(.caption).foregroundStyle(Theme.textMuted)
                            + Text(pick.position.map { " \($0)" } ?? "")
                                .font(.caption).foregroundStyle(Theme.textMuted)
                        )
                        // One layout unit; let it wrap instead of truncating on narrow widths.
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
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

    private func verdictColor(_ status: String) -> Color { alternateVerdictColor(status) }
}

/// Shared verdict→color mapping for the destination requirement banner and the
/// per-candidate FAA/EASA qual chips, so the color signal can't diverge between
/// them. Covers both the trigger statuses (required/not_required) and the
/// per-candidate band verdicts (likely/marginal/unlikely).
fileprivate func alternateVerdictColor(_ status: String) -> Color {
    switch status {
    case "required", "unlikely": Theme.red
    case "marginal": Theme.amber
    case "not_required", "likely": Theme.green
    default: Theme.textMuted
    }
}

/// Severity→color for operational-friction flags (#344). Mirrors the web
/// `.alt-flag-{severity}` palette: amber/red only, deliberately never green —
/// an operational flag only ever raises friction, it never clears it.
fileprivate func operationalFlagColor(_ severity: String) -> Color {
    severity == "red" ? Theme.red : Theme.amber
}

/// One divert candidate as a collapsible card. Collapsed (default) shows only
/// the identity + at-a-glance signals: ICAO, consensus flight category, and the
/// FAA/EASA alternate-minima tags. Expanding reveals the rest — name, MAJOR /
/// "Better" flags, distance + agreement, geometry (before/after + detour),
/// winds, and approach tier. Defaults collapsed so more candidates fit on screen.
private struct AlternateCard: View {
    let apt: AlternateAirport

    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            // Header (always visible) — tap anywhere to expand/collapse.
            Button {
                withAnimation { expanded.toggle() }
            } label: {
                collapsedHeader
            }
            .buttonStyle(.plain)

            if expanded {
                expandedDetail
                    .transition(.opacity)
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: Collapsed summary — category + FAA/EASA tags + ICAO identity.

    @ViewBuilder
    private var collapsedHeader: some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            HStack(spacing: Theme.spacingS) {
                Text(apt.icao)
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                FlightCategoryBadge(category: apt.flightCategory)
                Spacer(minLength: 0)
                Image(systemName: expanded ? "chevron.up" : "chevron.down")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }

            // Operational-friction flags (#344) — always-visible severity ⚠ chips
            // (e.g. Cross-border). The full `detail` lives in "Operational notes"
            // under the expanded card; expand to read why. Never green.
            if let flags = apt.operationalFlags, !flags.isEmpty {
                HStack(spacing: Theme.spacingXS) {
                    ForEach(flags) { flag in flagChip(flag) }
                    Spacer(minLength: 0)
                }
            }

            // Regulatory qual chips — the FAA/EASA alternate tags.
            if apt.faa != nil || apt.easa != nil {
                HStack(spacing: Theme.spacingS) {
                    if let faa = apt.faa { qualChip("FAA", faa.verdict) }
                    if let easa = apt.easa { qualChip("EASA", easa.verdict) }
                    Spacer(minLength: 0)
                }
            }
        }
        .contentShape(Rectangle())
    }

    // MARK: Expanded detail — everything else.

    @ViewBuilder
    private var expandedDetail: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            // Operational notes (#344) — the detail behind the collapsed ⚠ chips.
            if let flags = apt.operationalFlags, !flags.isEmpty {
                VStack(alignment: .leading, spacing: Theme.spacingXS) {
                    ForEach(flags) { flag in
                        HStack(alignment: .top, spacing: Theme.spacingXS) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .font(.caption2)
                                .foregroundStyle(operationalFlagColor(flag.severity))
                            (
                                Text("\(flag.label): ").font(.caption.weight(.semibold)).foregroundStyle(Theme.text)
                                + Text(flag.detail).font(.caption).foregroundStyle(Theme.textMuted)
                            )
                            .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }

            if (apt.name != nil && !(apt.name ?? "").isEmpty) || apt.isMajor == true || apt.dominatesDestination == true {
                HStack(spacing: Theme.spacingS) {
                    if let name = apt.name, !name.isEmpty {
                        Text(name)
                            .font(.caption)
                            .foregroundStyle(Theme.textMuted)
                            .lineLimit(1)
                    }
                    if apt.isMajor == true {
                        Text("MAJOR")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(Theme.textMuted)
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Theme.border, in: Capsule())
                    }
                    Spacer(minLength: 0)
                    if apt.dominatesDestination == true {
                        Text("Better")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(Theme.green, in: Capsule())
                    }
                }
            }

            HStack(spacing: Theme.spacingS) {
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
        }
    }

    private func qualChip(_ label: String, _ verdict: String) -> some View {
        let color = alternateVerdictColor(verdict)
        return Text("\(label): \(verdict.capitalized)")
            .font(.caption2.weight(.medium))
            .foregroundStyle(color)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.12), in: Capsule())
    }

    /// Operational-friction chip (#344) — ⚠ + label in the flag's severity color.
    /// Web analogue: `.alt-flag-chip` (glyph-only there; iOS shows the label too
    /// since there's no hover tooltip). Detail is read by expanding the card.
    private func flagChip(_ flag: OperationalFlag) -> some View {
        let color = operationalFlagColor(flag.severity)
        return HStack(spacing: 3) {
            Image(systemName: "exclamationmark.triangle.fill").font(.caption2)
            Text(flag.label).font(.caption2.weight(.semibold))
        }
        .foregroundStyle(color)
        .padding(.horizontal, 6).padding(.vertical, 2)
        .background(color.opacity(0.12), in: Capsule())
    }
}
