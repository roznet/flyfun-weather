import SwiftUI

/// Departure/arrival airport conditions from advisories response.
struct AirportConditionsView: View {
    let viewModel: BriefingViewModel
    @Environment(\.horizontalSizeClass) private var sizeClass

    var body: some View {
        switch viewModel.advisoriesState {
        case .loaded(let response):
            if let conditions = response.airportConditions {
                let cards = Group {
                    AirportConditionCard(title: "Departure", summary: conditions.departure)
                    AirportConditionCard(title: "Arrival", summary: conditions.arrival)
                }
                if sizeClass == .regular {
                    HStack(alignment: .top, spacing: 12) {
                        cards
                    }
                    .padding(.horizontal)
                } else {
                    VStack(spacing: 12) {
                        cards
                    }
                    .padding(.horizontal)
                }
            }
        default:
            EmptyView()
        }
    }
}

private struct AirportConditionCard: View {
    let title: String
    let summary: AirportConditionsSummary
    /// Collapsible (#4, iOS feedback): expanded shows one line per model;
    /// collapsed shows just the worst-category summary badge — like the web's
    /// top-line rating. Defaults expanded.
    @State private var isExpanded = true

    /// Worst (most restrictive) flight category across the models, for the
    /// collapsed summary badge. Severity: LIFR > IFR > MVFR > VFR.
    private var worstCategory: String? {
        let order = ["lifr": 3, "ifr": 2, "mvfr": 1, "vfr": 0]
        return summary.conditions
            .map(\.flightCategory)
            .max { (order[$0.lowercased()] ?? -1) < (order[$1.lowercased()] ?? -1) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation { isExpanded.toggle() }
            } label: {
                HStack {
                    Text(title)
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                    Text(summary.icao)
                        .font(.headline)
                    if !summary.name.isEmpty {
                        Text(summary.name)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    Spacer()
                    // Collapsed: surface the worst rating so the card is still
                    // glanceable without expanding.
                    if !isExpanded, let worst = worstCategory {
                        FlightCategoryBadge(category: worst)
                    }
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded {
                ForEach(summary.conditions) { condition in
                    HStack(spacing: 12) {
                        Text(condition.model.shortModelName)
                            .font(.caption.bold())
                            .frame(width: 60, alignment: .leading)

                        FlightCategoryBadge(category: condition.flightCategory)

                        if let wind = condition.windSpeedKt, let dir = condition.windDirectionDeg {
                            Label("\(Int(dir))@\(Int(wind))kt", systemImage: "wind")
                                .font(.caption)
                        }

                        if let rw = condition.bestRunway {
                            Text("Rwy \(rw.runwayId)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }

                        Spacer()
                    }
                }
            }
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
    }
}

/// VFR/MVFR/IFR/LIFR colored badge.
struct FlightCategoryBadge: View {
    let category: String

    private var color: Color {
        switch category.lowercased() {
        case "vfr": .green
        case "mvfr": .blue
        case "ifr": .red
        case "lifr": .purple
        default: .gray
        }
    }

    var body: some View {
        Text(category.uppercased())
            .font(.caption.bold())
            .foregroundStyle(.white)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color, in: Capsule())
    }
}
