import AppIntents
import CoreSpotlight
import Foundation
import UniformTypeIdentifiers

/// An App Intents / Spotlight representation of a saved flight.
///
/// Backed by `FlightResponse` from the repository (cache-first, so it resolves
/// offline). Conforms to `IndexedEntity` so flights are Spotlight-searchable and
/// Siri's own resolution of "my flight to Cannes" improves — the on-device
/// semantic index does more matching before our `EntityStringQuery` even runs.
struct FlightEntity: AppEntity, IndexedEntity {
    /// Flight id (the repository's stable identifier).
    let id: String
    /// "ORIGIN → DEST", e.g. "LFMD → LFML".
    let title: String
    /// Departure date + assessment, e.g. "Tue 9 Jul · AMBER".
    let subtitle: String
    /// First waypoint ICAO (uppercased) — used by the deterministic resolver.
    let originIcao: String?
    /// Last waypoint ICAO (uppercased) — used by the deterministic resolver.
    let destinationIcao: String?
    /// Departure instant (ISO-8601), for relative-date matching ("tomorrow").
    let departureISO: String?
    /// GREEN / AMBER / RED (uppercased), or nil when only a long-range outlook
    /// exists / never briefed.
    let assessment: String?

    static var typeDisplayRepresentation: TypeDisplayRepresentation {
        TypeDisplayRepresentation(name: "Flight")
    }

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(
            title: "\(title)",
            subtitle: "\(subtitle)",
            image: .init(systemName: "airplane")
        )
    }

    static var defaultQuery = FlightEntityQuery()

    /// Spotlight metadata for the donated flight.
    var attributeSet: CSSearchableItemAttributeSet {
        let attrs = CSSearchableItemAttributeSet(contentType: .content)
        attrs.title = title
        attrs.contentDescription = subtitle
        attrs.keywords = [originIcao, destinationIcao].compactMap { $0 }
        return attrs
    }
}

extension FlightEntity {
    /// Build from a repository flight. Assessment / date come from the inlined
    /// `latestBriefing` summary so the entity needs no extra round-trip.
    init(_ flight: FlightResponse) {
        self.id = flight.id
        self.title = flight.shortTitle
        self.originIcao = flight.waypoints.first?.uppercased()
        self.destinationIcao = flight.waypoints.last?.uppercased()
        self.departureISO = flight.departureTime
        self.assessment = flight.latestBriefing?.assessment?.uppercased()
        self.subtitle = FlightEntity.makeSubtitle(flight)
    }

    private static func makeSubtitle(_ flight: FlightResponse) -> String {
        var parts: [String] = []
        if let date = flight.departureDate {
            parts.append(Self.dateFormatter.string(from: date))
        }
        if let assessment = flight.latestBriefing?.assessment?.uppercased() {
            parts.append(assessment)
        } else if let outlook = flight.latestBriefing?.outlook {
            parts.append(outlook.replacingOccurrences(of: "_", with: " ").capitalized)
        }
        return parts.joined(separator: " · ")
    }

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.setLocalizedDateFormatFromTemplate("EEE d MMM")
        return f
    }()
}
