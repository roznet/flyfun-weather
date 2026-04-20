import Foundation

/// Subset of the server's user preferences that the iOS app cares about.
/// Additional server fields are silently ignored by the decoder.
struct PreferencesResponse: Codable, Sendable {
    let pirepCanView: Bool
    let pirepCanPublish: Bool
}
