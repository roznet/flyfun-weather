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

extension Int {
    /// Zero-pad a compass heading/bearing to 3 digits (10° → "010"). Does NOT
    /// append the ° symbol — call sites add °/@ as they do today. Single source
    /// of truth for heading display (AirportConditionsView, PirepListView).
    var paddedHeading: String {
        String(format: "%03d", self)
    }
}

/// Wind formatting shared across iOS surfaces — mirrors web `formatWind` /
/// `formatWindComponent` in `web/ts/units.ts`. Standard `dir@speedGgust`
/// notation with a zero-padded 3-digit direction; the gust is dropped when
/// absent or within `gustDisplayMinExcessKt` kt of the sustained value.
enum WindFormat {
    /// A gust is shown only when it exceeds the sustained value by at least this
    /// many knots (comparing rounded, displayed numbers). A gust within a few kt
    /// of the mean isn't operationally meaningful and just adds noise ("5G6" →
    /// "5"). Keep in sync with web `GUST_DISPLAY_MIN_EXCESS_KT`.
    static let gustDisplayMinExcessKt = 4

    /// `Ggust` suffix for a wind of sustained value `base`, or "" when the gust
    /// is absent or too close to `base`.
    private static func gustSuffix(base: Double, gust: Double?) -> String {
        guard let gust else { return "" }
        let g = Int(gust.rounded())
        return g - Int(base.rounded()) >= gustDisplayMinExcessKt ? "G\(g)" : ""
    }

    /// `dir@speedGgust`, e.g. "273@15G22". Missing direction drops the `dir@`
    /// prefix; missing speed returns nil. Units (kt) are appended by the caller.
    static func wind(speed: Double?, dir: Double?, gust: Double?) -> String? {
        guard let speed else { return nil }
        let dirStr = dir.map { "\(Int($0.rounded()).paddedHeading)@" } ?? ""
        return "\(dirStr)\(Int(speed.rounded()))\(gustSuffix(base: speed, gust: gust))"
    }

    /// A single wind component (crosswind/headwind) as `valueGgust`, e.g.
    /// "12G18", using the same gust-suppression rule. Missing value returns nil.
    static func component(_ value: Double?, gust: Double?) -> String? {
        guard let value else { return nil }
        return "\(Int(value.rounded()))\(gustSuffix(base: value, gust: gust))"
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
