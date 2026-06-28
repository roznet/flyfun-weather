import Foundation

/// One recent route from the user's linked Autorouter account
/// (`GET /api/flights/autorouter-routes`). Selecting one feeds its ICAO flight
/// plan through the same parse→fill path as "Paste Flight Plan".
struct AutorouterRoute: Codable, Identifiable, Hashable, Sendable {
    var id: String { routeid }

    let routeid: String
    let departure: String
    let destination: String
    let departureName: String?
    let destinationName: String?
    let departureTime: String?
    let fplan: String
    let routeDistanceNm: Int?
    let aircraftDescription: String?
    let callsign: String?

    /// "EGTF → LSGS" headline for the picker row.
    var title: String { "\(departure) \u{2192} \(destination)" }

    /// Secondary line: date · distance · aircraft, omitting any missing parts.
    var subtitle: String {
        var parts: [String] = []
        if let departureTime, let date = Self.isoParser.date(from: departureTime) {
            parts.append(Self.dayFormatter.string(from: date))
        }
        if let routeDistanceNm { parts.append("\(routeDistanceNm) nm") }
        if let aircraftDescription, !aircraftDescription.isEmpty { parts.append(aircraftDescription) }
        else if let callsign, !callsign.isEmpty { parts.append(callsign) }
        return parts.joined(separator: " \u{00b7} ")
    }

    private static let isoParser = ISO8601DateFormatter()
    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "d MMM"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()
}

struct AutorouterRoutesResponse: Codable, Sendable {
    let routes: [AutorouterRoute]
}
