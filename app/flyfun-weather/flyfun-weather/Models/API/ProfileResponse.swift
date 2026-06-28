import Foundation

/// A flight profile from `GET /api/user/profiles`. A profile presets the flight
/// parameters (cruise altitude, ceiling, speed) and the weather-model choices.
/// The create/edit form only needs identity + the flight parameters; the decoder
/// ignores the many other settings keys (model selection, methods, advisories).
struct ProfileResponse: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let name: String
    let isDefault: Bool
    let settings: ProfileSettings
}

/// The subset of profile settings the flight form applies. Property names rely on
/// the shared decoder's `.convertFromSnakeCase`; unlisted keys are ignored.
struct ProfileSettings: Codable, Hashable, Sendable {
    let cruiseAltitudeFt: Int?
    let flightCeilingFt: Int?
    let speedKt: Int?
}

extension [ProfileResponse] {
    /// Default profile first, then alphabetical — matches the web selector order.
    func sortedForPicker() -> [ProfileResponse] {
        sorted { lhs, rhs in
            if lhs.isDefault != rhs.isDefault { return lhs.isDefault && !rhs.isDefault }
            return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
        }
    }
}
