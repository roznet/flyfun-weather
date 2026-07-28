import SwiftUI

/// Departure/arrival airport conditions from advisories response.
struct AirportConditionsView: View {
    let viewModel: BriefingViewModel

    /// A card needs roughly this much width to hold `model · badge · wind · runway`
    /// on one line at default type. Below it the grid falls back to one column
    /// rather than squeezing (#494). Measured against the widest real row — a
    /// gusting wind and a three-character runway ("ECMWF · VFR · 300@12G18kt ·
    /// Rwy 31R" ≈ 270pt) — plus the card's own padding.
    private static let minCardWidth: CGFloat = 320

    var body: some View {
        switch viewModel.advisoriesState {
        case .loaded(let response):
            if let conditions = response.airportConditions {
                // Width-driven, not size-class-driven (#494): `.regular` is true for
                // an iPad detail pane of *any* width — including a half-screen
                // multitasking slot — so the old size-class HStack squeezed the two
                // cards until their rows wrapped one character per line. An adaptive
                // grid goes two-across only when two cards genuinely fit, which is
                // also the idiom the advisory grid above already uses.
                LazyVGrid(columns: [GridItem(.adaptive(minimum: Self.minCardWidth), spacing: 12)],
                          alignment: .leading, spacing: 12) {
                    AirportConditionCard(title: "Departure", summary: conditions.departure)
                    AirportConditionCard(title: "Arrival", summary: conditions.arrival)
                }
                .padding(.horizontal)
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

    /// The worst (most restrictive) per-model condition — drives the collapsed
    /// summary line. Severity: LIFR > IFR > MVFR > VFR.
    private var worstCondition: AirportModelCondition? {
        let order = ["lifr": 3, "ifr": 2, "mvfr": 1, "vfr": 0]
        return summary.conditions.max {
            (order[$0.flightCategory.lowercased()] ?? -1) < (order[$1.flightCategory.lowercased()] ?? -1)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation { isExpanded.toggle() }
            } label: {
                HStack {
                    // The section label and ICAO are short and must never break —
                    // unpinned they hyphenated ("Depar-/ture") once the header was
                    // squeezed (#494). The airport name is the one cell allowed to
                    // give way, and it truncates rather than wrapping.
                    Text(title)
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: true, vertical: false)
                    Text(summary.icao)
                        .font(.headline)
                        .fixedSize(horizontal: true, vertical: false)
                    if !summary.name.isEmpty {
                        Text(summary.name)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                    Spacer()
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded {
                ForEach(summary.conditions) { condition in
                    conditionRow(condition)
                }
            } else if let worst = worstCondition {
                // Collapsed: keep the card glanceable — worst rating + its wind
                // and runway, like the web's top-line summary (#4, iOS feedback).
                conditionRow(worst, showModel: false)
            }
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    /// One condition line: model (optional) · category · wind · best runway.
    /// Shared by the expanded per-model list and the collapsed summary line.
    ///
    /// Every cell is pinned to a single line (#494). Left to its own devices
    /// SwiftUI resolves an over-full `HStack` by compressing its `Text` children
    /// all the way down to one character per line; with the cells pinned, a row
    /// that still doesn't fit degrades by truncating the runway — the least
    /// load-bearing cell — instead of exploding vertically.
    @ViewBuilder
    private func conditionRow(_ condition: AirportModelCondition, showModel: Bool = true) -> some View {
        HStack(spacing: 12) {
            if showModel {
                // `minWidth`, not a hard `width`: short names still line up in a
                // column across rows, but a long one grows rather than overflowing
                // a 60pt box.
                Text(condition.model.shortModelName)
                    .font(.caption.bold())
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                    .frame(minWidth: 52, alignment: .leading)
            }

            FlightCategoryBadge(category: condition.flightCategory)

            if let wind = WindFormat.wind(speed: condition.windSpeedKt,
                                          dir: condition.windDirectionDeg,
                                          gust: condition.windGustKt) {
                Label("\(wind)kt", systemImage: "wind")
                    .font(.caption)
                    .lineLimit(1)
                    .layoutPriority(1)
            }

            if let rw = condition.bestRunway {
                Text("Rwy \(rw.runwayId)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 0)
        }
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
        // Pinned to one line: a squeezed HStack would otherwise break the badge
        // mid-word ("V" / "FR") rather than let a neighbouring cell give way (#494).
        Text(category.uppercased())
            .font(.caption.bold())
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
            .foregroundStyle(.white)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color, in: Capsule())
    }
}
