import SwiftUI

/// The flight's basics in words (#996): where from and to, when, how long, how
/// high, in what. Pure so the strip and its tests read one formatter.
///
/// SYNC: mirrored by Android `FlightSummary.kt` — same lines, same order.
nonisolated enum FlightSummary {
    struct Lines: Equatable {
        /// "EGTF 10:00Z → LFAT 11:25Z", or with live progress
        /// "EGTF off 10:12Z → LFAT est 11:36Z" / "… → LFAT landed 11:40Z".
        var main: String
        /// "Sat 21 Nov · 1h25 · FL065 · DA42"
        var detail: String
        /// The route between the ends ("MID DCT SFD"), nil for a direct flight.
        var route: String?
    }

    static func lines(flight: FlightResponse, progress: LiveProgress? = nil) -> Lines {
        let from = flight.waypoints.first?.uppercased() ?? ""
        let to = flight.waypoints.last?.uppercased() ?? ""
        let departure = flight.departureDate
        let plannedArrival = departure?.addingTimeInterval(flight.flightDurationHours * 3600)

        var fromText = from
        if let off = progress?.departedAt.flatMap(Date.parseISO8601) {
            fromText += " off \(hhmm(off))"
        } else if let departure {
            fromText += " \(hhmm(departure))"
        }
        var toText = to
        if let landed = progress?.landedAt.flatMap(Date.parseISO8601) {
            toText += " landed \(hhmm(landed))"
        } else if progress?.departedAt != nil, let eta = progress?.etaDestination.flatMap(Date.parseISO8601) {
            toText += " est \(hhmm(eta))"
        } else if let plannedArrival {
            toText += " \(hhmm(plannedArrival))"
        }

        var detail: [String] = []
        if let departure { detail.append(dayFormatter.string(from: departure)) }
        if flight.flightDurationHours > 0 { detail.append(durationLabel(flight.flightDurationHours)) }
        if flight.cruiseAltitudeFt > 0 { detail.append(String(format: "FL%03d", flight.cruiseAltitudeFt / 100)) }
        if let aircraft = flight.aircraft { detail.append(aircraft.displayName) }

        return Lines(main: "\(fromText) → \(toText)",
                     detail: detail.joined(separator: " · "),
                     route: route(flight))
    }

    /// The pilot's own route text when one was captured, else the waypoints
    /// between the ends. nil when there is nothing between them.
    static func route(_ flight: FlightResponse) -> String? {
        if let raw = flight.rawRoute?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty {
            return raw
        }
        guard flight.waypoints.count > 2 else { return nil }
        return flight.waypoints.dropFirst().dropLast().map { $0.uppercased() }.joined(separator: " ")
    }

    /// "1h25", "0h45", "2h00" — exact minutes, not the form's quarter-hour steps.
    static func durationLabel(_ hours: Double) -> String {
        let minutes = Int((hours * 60).rounded())
        return String(format: "%dh%02d", minutes / 60, minutes % 60)
    }

    static func hhmm(_ date: Date) -> String { timeFormatter.string(from: date) + "Z" }

    private static let timeFormatter: DateFormatter = utc("HH:mm")
    private static let dayFormatter: DateFormatter = utc("EEE d MMM")

    private static func utc(_ format: String) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = format
        return f
    }
}

/// One quiet line at the top of the Advisory tab: the flight's ends and times,
/// then date, duration, level and aircraft; the route under it, one line until
/// tapped. No card: it scrolls away with the content.
struct FlightSummaryStrip: View {
    let flight: FlightResponse
    var progress: LiveProgress?
    @State private var routeExpanded = false

    var body: some View {
        let lines = FlightSummary.lines(flight: flight, progress: progress)
        VStack(alignment: .leading, spacing: 2) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
                    Text(lines.main).font(.subheadline.weight(.semibold))
                    Spacer(minLength: 0)
                    Text(lines.detail).font(.caption).foregroundStyle(Theme.textMuted)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(lines.main).font(.subheadline.weight(.semibold))
                    Text(lines.detail).font(.caption).foregroundStyle(Theme.textMuted)
                }
            }
            .monospacedDigit()
            if let route = lines.route {
                Button {
                    routeExpanded.toggle()
                } label: {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text("via \(route)")
                            .lineLimit(routeExpanded ? nil : 1)
                            .truncationMode(.tail)
                            .multilineTextAlignment(.leading)
                        Image(systemName: routeExpanded ? "chevron.up" : "chevron.down")
                            .font(.caption2)
                    }
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(routeExpanded ? "Route: \(route)" : "Show full route")
            }
        }
        .foregroundStyle(Theme.text)
        .padding(.horizontal, Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("flightSummaryStrip")
    }
}
