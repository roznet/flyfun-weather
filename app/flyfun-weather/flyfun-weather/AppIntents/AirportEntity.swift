import AppIntents
import Foundation

/// An App Intents representation of an airport (ICAO + name), backed by the local
/// `AirportDatabase` (RZFlight `KnownAirports`). Used by `AirportWeatherIntent`
/// and to expand spoken place names ("Fairoaks") to an ICAO (`EGTF`).
struct AirportEntity: AppEntity {
    /// ICAO code, uppercased — the stable id.
    let id: String
    /// Airport name for display / disambiguation.
    let name: String

    var icao: String { id }

    static var typeDisplayRepresentation: TypeDisplayRepresentation {
        TypeDisplayRepresentation(name: "Airport")
    }

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(
            title: "\(id)",
            subtitle: "\(name)",
            image: .init(systemName: "mappin.and.ellipse")
        )
    }

    static var defaultQuery = AirportEntityQuery()
}

/// Resolves an `AirportEntity` from a spoken/typed place name or ICAO via the
/// local ranked ICAO/name search — never hand-rolled matching (per the design's
/// reuse note).
struct AirportEntityQuery: EntityStringQuery {
    func entities(matching string: String) async throws -> [AirportEntity] {
        await MainActor.run {
            IntentSupport.ensureAirportDatabase()
            return AirportDatabase.shared.search(needle: string, limit: 8)
                .map { AirportEntity(id: $0.icao.uppercased(), name: $0.name) }
        }
    }

    func entities(for identifiers: [AirportEntity.ID]) async throws -> [AirportEntity] {
        await MainActor.run {
            IntentSupport.ensureAirportDatabase()
            return identifiers.compactMap { icao in
                AirportDatabase.shared.airport(icao: icao)
                    .map { AirportEntity(id: $0.icao.uppercased(), name: $0.name) }
            }
        }
    }

    /// Suggest the airports the user actually flies to/from (their flights'
    /// endpoints), so the Shortcuts picker is short and relevant instead of the
    /// full ~thousand-airport DB.
    func suggestedEntities() async throws -> [AirportEntity] {
        guard let flights = try? await FlightEntityQuery.loadFlights() else { return [] }
        let icaos = flights.flatMap { [$0.waypoints.first, $0.waypoints.last] }
            .compactMap { $0?.uppercased() }
        var seen = Set<String>()
        let unique = icaos.filter { seen.insert($0).inserted }
        return await MainActor.run {
            IntentSupport.ensureAirportDatabase()
            return unique.compactMap { icao in
                let name = AirportDatabase.shared.airport(icao: icao)?.name ?? icao
                return AirportEntity(id: icao, name: name)
            }
        }
    }
}
