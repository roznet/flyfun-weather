import Foundation

extension JSONDecoder {
    /// Shared decoder configured for the WeatherBrief API (snake_case keys, ISO 8601 dates).
    static nonisolated let weatherBrief: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()
}

extension DateFormatter {
    /// Short date format for flight cards (e.g. "Mar 15").
    static let shortDate: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM d"
        return f
    }()

    /// Time in UTC (e.g. "09:00Z").
    static let utcTime: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm'Z'"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()
}

extension String {
    /// Short display name for weather models — used in compact badges where space is tight.
    var shortModelName: String {
        switch self.lowercased() {
        case "meteofrance": "MF"
        case "best_match": "BEST"
        case "ecmwf": "ECMWF"
        default: self.uppercased()
        }
    }
}

extension URL {
    /// Extract a query parameter value by name.
    func queryParam(_ name: String) -> String? {
        URLComponents(url: self, resolvingAgainstBaseURL: false)?
            .queryItems?
            .first(where: { $0.name == name })?
            .value
    }
}
