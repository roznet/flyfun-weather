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

extension JSONEncoder {
    /// Shared encoder matching the WeatherBrief decoder (snake_case keys, ISO 8601 dates).
    static nonisolated let weatherBrief: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        return encoder
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

extension Date {
    /// Parse an ISO-8601 timestamp, tolerating the optional fractional seconds
    /// the server includes. The server sends `datetime.isoformat()`, which
    /// carries microseconds (e.g. `2026-06-28T08:46:00.123456+00:00`), and the
    /// default `ISO8601DateFormatter` rejects fractional seconds — so try the
    /// fractional parser first, then plain. Shared so callers don't reimplement
    /// the fractional-then-plain fallback (BriefingViewModel, CachingBriefingRepository).
    static func parseISO8601(_ s: String) -> Date? {
        isoParserFractional.date(from: s) ?? isoParserPlain.date(from: s)
    }

    private static let isoParserFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoParserPlain = ISO8601DateFormatter()
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
